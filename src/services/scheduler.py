"""APScheduler-based in-process scheduler for the Render Web Service.

Replaces separate Render Cron Jobs. Runs inside the FastAPI process so
no additional Render services are needed.

Schedule (UTC):
    0  */3 * * *  signal_scan     — check odds, generate value signals, send alerts
    20 */3 * * *  settlement      — settle finished matches, update ledger + drift
    40 */3 * * *  training_check  — smart retrain if MIN_NEW_SETTLED_MATCHES_FOR_TRAINING reached
    50 */3 * * *  active_report   — send comprehensive 3-hour status to Telegram

Activated only when ACTIVE_MODE=true (safe default: false).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

_log = logging.getLogger("scheduler")

# Lazy import — apscheduler only loaded when ACTIVE_MODE=true
_scheduler: Any = None


def _env_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).strip().lower() in ("1", "true", "yes")


ACTIVE_MODE = _env_bool("ACTIVE_MODE", False)


def get_scheduler() -> Any:
    """Return (and lazily create) the global AsyncIOScheduler instance."""
    global _scheduler
    if _scheduler is None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


def _log_startup_diagnostics() -> None:
    """Log env var presence at startup without exposing secret values."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    masked_chat = ("***" + chat_id[-4:]) if len(chat_id) >= 4 else ("***" if chat_id else "(empty)")
    _log.info("=== STARTUP DIAGNOSTICS ===")
    _log.info("ACTIVE_MODE=%s", os.environ.get("ACTIVE_MODE", "false"))
    _log.info("TELEGRAM_STATUS_REPORTS_ENABLED=%s", os.environ.get("TELEGRAM_STATUS_REPORTS_ENABLED", "true"))
    _log.info("TELEGRAM_SIGNAL_ALERTS_ENABLED=%s", os.environ.get("TELEGRAM_SIGNAL_ALERTS_ENABLED", "true"))
    _log.info("TELEGRAM_BOT_TOKEN_PRESENT=%s", bool(token))
    _log.info("TELEGRAM_BOT_TOKEN_LENGTH=%d", len(token))
    _log.info("TELEGRAM_CHAT_ID_PRESENT=%s", bool(chat_id))
    _log.info("TELEGRAM_CHAT_ID_MASKED=%s", masked_chat)
    _log.info("THE_ODDS_API_KEY_PRESENT=%s", bool(os.environ.get("THE_ODDS_API_KEY", "")))
    _log.info("DATA_DIR=%s", os.environ.get("DATA_DIR", "data"))
    _log.info("SCHEDULER_ENABLED=%s", ACTIVE_MODE)
    _log.info("=== END DIAGNOSTICS ===")


def start(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Register all jobs and start the scheduler. No-op if ACTIVE_MODE is false."""
    _log_startup_diagnostics()

    if not ACTIVE_MODE:
        _log.info("[scheduler] ACTIVE SCHEDULER DISABLED: ACTIVE_MODE is false")
        return

    sched = get_scheduler()
    if sched.running:
        _log.warning("[scheduler] Already running — skipping duplicate start")
        return

    interval_h = int(os.environ.get("ACTIVE_REPORT_INTERVAL_HOURS", "6"))

    # Active hours: only run jobs during ACTIVE_HOURS_UTC (saves Render free-tier minutes).
    # Default 07-22 UTC — covers all live tennis/football, skips night.
    # Override via ACTIVE_HOURS_UTC="7-22" env var.
    _active_range = os.environ.get("ACTIVE_HOURS_UTC", "7-22")
    try:
        _ah_start, _ah_end = [int(x) for x in _active_range.split("-")]
    except Exception:
        _ah_start, _ah_end = 7, 22
    _active_hours = ",".join(str(h) for h in range(_ah_start, _ah_end + 1))

    # ── Job registrations ──────────────────────────────────────────────
    # Signal scan: every Nth hour, only during active hours
    sched.add_job(
        _job_signal_scan,
        "cron",
        hour=_active_hours,
        minute=0,
        id="signal_scan",
        replace_existing=True,
        misfire_grace_time=300,
    )
    # Settlement: 20 min into every Nth active hour
    sched.add_job(
        _job_settlement,
        "cron",
        hour=_active_hours,
        minute=20,
        id="settlement",
        replace_existing=True,
        misfire_grace_time=300,
    )
    # Training check: once a day at 07:40 UTC (not every 3h — saves minutes)
    sched.add_job(
        _job_training_check,
        "cron",
        hour=7,
        minute=40,
        id="training_check",
        replace_existing=True,
        misfire_grace_time=600,
    )
    # Active report: once a day at 09:50 UTC
    sched.add_job(
        _job_active_report,
        "cron",
        hour=9,
        minute=50,
        id="active_report",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Keep-alive: only during active hours (Render free plan sleeps outside window).
    # At night the service sleeps → saves ~9h × 60min = ~9 Render hours/day.
    sched.add_job(
        _job_keep_alive,
        "cron",
        hour=_active_hours,
        minute="*/14",
        id="keep_alive",
        replace_existing=True,
    )

    # Tennis ELO retrain: every Monday at 02:30 UTC (full retrain)
    sched.add_job(
        _job_tennis_retrain,
        "cron",
        day_of_week="mon",
        hour=2,
        minute=30,
        id="tennis_retrain",
        replace_existing=True,
        misfire_grace_time=3600,  # 1h grace — retrain any time Monday if missed
    )

    # Tennis daily data refresh: re-download current-year ATP CSV + retrain if new matches
    # Runs every day at 06:00 UTC (early morning, before signal scan at 09:00)
    sched.add_job(
        _job_tennis_daily_refresh,
        "cron",
        hour=6,
        minute=0,
        id="tennis_daily_refresh",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    sched.start()
    _log.info("[scheduler] ACTIVE SCHEDULER STARTED")
    _log.info("[scheduler] Jobs registered: %d", len(sched.get_jobs()))
    for job in sched.get_jobs():
        next_run = job.next_run_time
        next_str = next_run.strftime("%Y-%m-%d %H:%M:%S UTC") if next_run else "not scheduled"
        _log.info("[scheduler] %-15s next_run=%s", job.id, next_str)


def stop() -> None:
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _log.info("[scheduler] Stopped")


# ---------------------------------------------------------------------------
# Job wrappers — all run in thread pool to avoid blocking the event loop
# ---------------------------------------------------------------------------

async def _run_in_executor(fn: Callable, job_name: str) -> None:
    """Run a synchronous cron function in the default thread pool executor."""
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, fn)
    except SystemExit as exc:
        _log.error("[scheduler] %s called sys.exit(%s)", job_name, exc.code)
    except Exception as exc:
        _log.exception("[scheduler] %s raised: %s", job_name, exc)


async def _job_signal_scan() -> None:
    _log.info("[scheduler] → signal_scan starting")
    from src.cron import run_signals
    await _run_in_executor(run_signals.main, "signal_scan")
    _log.info("[scheduler] ← signal_scan done")


async def _job_settlement() -> None:
    _log.info("[scheduler] → settlement starting")
    from src.cron import run_settle
    await _run_in_executor(run_settle.main, "settlement")
    _log.info("[scheduler] ← settlement done")


async def _job_training_check() -> None:
    _log.info("[scheduler] → training_check starting")
    # Import lazily to avoid circular imports at startup
    from src.cron.run_active_report import _run_training_check
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _run_training_check)
        _log.info("[scheduler] ← training_check done: trained=%s", result.get("trained"))
    except Exception as exc:
        _log.exception("[scheduler] training_check raised: %s", exc)


async def _job_active_report() -> None:
    _log.info("[scheduler] → active_report starting")
    from src.cron import run_active_report
    await _run_in_executor(run_active_report.main, "active_report")
    _log.info("[scheduler] ← active_report done")


async def _job_keep_alive() -> None:
    """Self-ping /health to prevent Render starter plan from sleeping."""
    port = os.environ.get("PORT", "10000")
    url = f"http://localhost:{port}/health"
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=5) as resp:
            _log.debug("[scheduler] keep_alive ping %s → %s", url, resp.status)
    except Exception as exc:
        _log.debug("[scheduler] keep_alive ping failed (non-critical): %s", exc)


async def _job_tennis_retrain() -> None:
    """Weekly ATP ELO retrain — runs in thread pool, saves latest.pkl to disk."""
    _log.info("[scheduler] → tennis_retrain starting")

    def _retrain() -> None:
        import os as _os
        from pathlib import Path
        from src.ingest.tennis_atp import build_atp_dataset
        from src.models.tennis_elo import TennisEloModel

        data_dir = Path(_os.environ.get("DATA_DIR", "data"))
        model_dir = Path(_os.environ.get("MODEL_DIR", data_dir / "models"))
        cache_dir = data_dir / "raw" / "tennis_atp"

        from datetime import datetime as _dt
        current_year = _dt.utcnow().year
        years = list(range(2019, current_year + 1))

        _log.info("[tennis_retrain] Downloading ATP data for %s", years)
        df = build_atp_dataset(years, cache_dir=cache_dir, use_cache=False)
        if df.empty:
            _log.warning("[tennis_retrain] No data — skipping retrain")
            return

        import shutil
        _log.info("[tennis_retrain] Training ELO + Markov on %d matches", len(df))

        # ELO model
        model = TennisEloModel()
        model.fit(df)
        tag = model.params.dataset_hash
        elo_path = model_dir / f"tennis_elo_atp_{tag}.pkl"
        model.save(elo_path)
        model.save_meta(model_dir / f"tennis_elo_atp_{tag}.meta.json", elo_path)
        shutil.copy2(elo_path, model_dir / "tennis_elo_atp_latest.pkl")
        _log.info("[tennis_retrain] ELO done: %d players", model.params.n_players)

        # Markov model
        from src.models.tennis_markov import TennisMarkovModel
        markov = TennisMarkovModel()
        markov.fit(df)
        markov_tag = markov.params.dataset_hash
        markov_path = model_dir / f"tennis_markov_atp_{markov_tag}.pkl"
        markov.save(markov_path)
        markov.save_meta(model_dir / f"tennis_markov_atp_{markov_tag}.meta.json", markov_path)
        shutil.copy2(markov_path, model_dir / "tennis_markov_atp_latest.pkl")
        _log.info("[tennis_retrain] Markov done: %d players", markov.params.n_players)

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _retrain)
        _log.info("[scheduler] ← tennis_retrain done")
    except Exception as exc:
        _log.exception("[scheduler] tennis_retrain raised: %s", exc)


async def _job_tennis_daily_refresh() -> None:
    """Daily: re-download current ATP season CSV, retrain if new matches found."""
    _log.info("[scheduler] → tennis_daily_refresh starting")

    def _refresh() -> None:
        import os as _os
        import shutil
        from datetime import datetime as _dt
        from pathlib import Path
        from src.ingest.tennis_atp import download_atp_season, build_atp_dataset
        from src.models.tennis_elo import TennisEloModel
        from src.models.tennis_markov import TennisMarkovModel

        data_dir = Path(_os.environ.get("DATA_DIR", "data"))
        model_dir = Path(_os.environ.get("MODEL_DIR", data_dir / "models"))
        cache_dir = data_dir / "raw" / "tennis_atp"

        current_year = _dt.utcnow().year

        # Force-refresh current year only (cheap, ~30KB CSV)
        fresh = download_atp_season(current_year, cache_dir, use_cache=False)
        if fresh.empty:
            _log.warning("[tennis_daily_refresh] No data for %d — skipping", current_year)
            return

        # Load existing model to compare match count
        latest_pkl = model_dir / "tennis_elo_atp_latest.pkl"
        existing_n = 0
        if latest_pkl.exists():
            try:
                old_model = TennisEloModel.load(latest_pkl)
                existing_n = old_model.params.n_matches
            except Exception:
                pass

        # Build full dataset (other years from cache, only current year fresh)
        years = list(range(2019, current_year + 1))
        df = build_atp_dataset(years, cache_dir=cache_dir, use_cache=True)
        if df.empty:
            return

        new_n = len(df)
        if new_n <= existing_n:
            _log.info("[tennis_daily_refresh] No new matches (%d = %d) — skip retrain", new_n, existing_n)
            return

        _log.info("[tennis_daily_refresh] %d new matches — retraining", new_n - existing_n)

        model = TennisEloModel()
        model.fit(df)
        tag = model.params.dataset_hash
        elo_path = model_dir / f"tennis_elo_atp_{tag}.pkl"
        model.save(elo_path)
        model.save_meta(model_dir / f"tennis_elo_atp_{tag}.meta.json", elo_path)
        shutil.copy2(elo_path, model_dir / "tennis_elo_atp_latest.pkl")
        _log.info("[tennis_daily_refresh] ELO retrained: %d players", model.params.n_players)

        markov = TennisMarkovModel()
        markov.fit(df)
        m_tag = markov.params.dataset_hash
        markov_path = model_dir / f"tennis_markov_atp_{m_tag}.pkl"
        markov.save(markov_path)
        markov.save_meta(model_dir / f"tennis_markov_atp_{m_tag}.meta.json", markov_path)
        shutil.copy2(markov_path, model_dir / "tennis_markov_atp_latest.pkl")
        _log.info("[tennis_daily_refresh] Markov retrained: %d players", markov.params.n_players)

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _refresh)
        _log.info("[scheduler] ← tennis_daily_refresh done")
    except Exception as exc:
        _log.exception("[scheduler] tennis_daily_refresh raised: %s", exc)
