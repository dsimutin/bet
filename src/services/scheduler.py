"""Quota-efficient APScheduler for the Render free-tier web service.

The scheduler runs inside FastAPI while the service is awake. An external GitHub
Actions wake-up workflow pings Render shortly before important jobs. Football and
tennis share one runtime scan, one Supabase ledger and one Odds API cache.
"""

from __future__ import annotations
import asyncio
import logging
import os
import subprocess
import sys
from typing import Any, Callable

_log = logging.getLogger("scheduler")
_scheduler: Any = None


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _hours(name: str, default: str) -> str:
    raw = os.environ.get(name, default)
    values = []
    for item in raw.split(","):
        item = item.strip()
        if item.isdigit() and 0 <= int(item) <= 23:
            values.append(str(int(item)))
    return ",".join(values) or default


ACTIVE_MODE = _env_bool("ACTIVE_MODE", False)


def get_scheduler() -> Any:
    global _scheduler
    if _scheduler is None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


def start(loop: asyncio.AbstractEventLoop | None = None) -> None:
    if not ACTIVE_MODE:
        _log.info("[scheduler] DISABLED: ACTIVE_MODE=false")
        return
    sched = get_scheduler()
    if sched.running:
        return

    scan_hours = _hours("RUNTIME_SCAN_HOURS_UTC", "7,10,13,15")
    settlement_hours = _hours("SETTLEMENT_HOURS_UTC", "7,15,22")
    sched.add_job(
        _job_signal_scan,
        "cron",
        hour=scan_hours,
        minute=0,
        id="signal_scan",
        replace_existing=True,
        misfire_grace_time=900,
    )
    sched.add_job(
        _job_settlement,
        "cron",
        hour=settlement_hours,
        minute=30,
        id="settlement",
        replace_existing=True,
        misfire_grace_time=1800,
    )
    sched.add_job(
        _job_today_digest,
        "cron",
        hour=7,
        minute=10,
        id="today_digest",
        replace_existing=True,
        misfire_grace_time=900,
    )
    sched.add_job(
        _job_today_digest,
        "cron",
        hour=15,
        minute=10,
        id="today_digest_refresh",
        replace_existing=True,
        misfire_grace_time=900,
    )
    sched.add_job(
        _job_training_check,
        "cron",
        hour=6,
        minute=40,
        id="training_check",
        replace_existing=True,
        misfire_grace_time=1800,
    )
    sched.add_job(
        _job_tennis_refresh,
        "cron",
        hour=6,
        minute=15,
        id="tennis_refresh",
        replace_existing=True,
        misfire_grace_time=1800,
    )
    sched.add_job(
        _job_tennis_retrain,
        "cron",
        day_of_week="mon",
        hour=6,
        minute=20,
        id="tennis_retrain_quick",
        replace_existing=True,
        misfire_grace_time=3600,
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

    # Weekly performance report: every Monday at 09:00 UTC
    sched.add_job(
        _job_weekly_report,
        "cron",
        day_of_week="mon",
        hour=9,
        minute=0,
        id="weekly_report",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    sched.start()
    _log.info(
        "[scheduler] ACTIVE SCHEDULER STARTED with %d jobs | scan_hours=%s | settlement_hours=%s",
        len(sched.get_jobs()),
        scan_hours,
        settlement_hours,
    )
    for job in sched.get_jobs():
        _log.info("[scheduler] %-20s next_run=%s", job.id, job.next_run_time)


def stop() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)


async def _run_in_executor(fn: Callable[[], Any], job_name: str) -> None:
    from src.services.job_guard import job_guard

    with job_guard(job_name) as acquired:
        if not acquired:
            _log.info("[scheduler] %s skipped: already_running", job_name)
            return
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, fn)
        except Exception as exc:
            _log.exception("[scheduler] %s failed: %s", job_name, exc)


async def _run_in_executor_unguarded(fn: Callable[[], Any], job_name: str) -> None:
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, fn)
    except Exception as exc:
        _log.exception("[scheduler] %s failed: %s", job_name, exc)


async def _job_signal_scan() -> None:
    from src.cron.run_signals import main

    await _run_in_executor(main, "signal_scan")


async def _job_settlement() -> None:
    from src.cron.run_settle import main

    await _run_in_executor(main, "settlement")


async def _job_today_digest() -> None:
    from src.web.telegram_bot import send_today_digest_default_chat

    await _run_in_executor(send_today_digest_default_chat, "today_digest")


async def _job_training_check() -> None:
    def _run() -> None:
        try:
            from src.cron.run_active_report import _run_training_check

            result = _run_training_check()
            _log.info("[scheduler] training check: %s", result)
        except Exception as exc:
            _log.warning("[scheduler] football training check skipped: %s", exc)

    await _run_in_executor(_run, "training_check")


async def _job_tennis_refresh() -> None:
    def _run() -> None:
        from datetime import datetime
        from pathlib import Path
        from src.ingest.tennis_atp import download_atp_season

        data_dir = Path(os.environ.get("DATA_DIR", "data"))
        cache_dir = data_dir / "raw" / "tennis_atp"
        download_atp_season(datetime.utcnow().year, cache_dir, use_cache=False)

    await _run_in_executor(_run, "tennis_refresh")


async def _job_tennis_retrain() -> None:
    def _run() -> None:
        subprocess.run(
            [sys.executable, "-m", "src.models.train_tennis_elo", "--no-cache"], check=True
        )

    await _run_in_executor(_run, "tennis_retrain")


async def _job_weekly_report() -> None:
    """Send weekly performance report to default Telegram chat."""
    import os

    def _run() -> None:
        from src.web.today_picks import build_weekly_report_text
        from src.web.telegram_bot import _send

        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not chat_id:
            _log.info("[scheduler] weekly_report: TELEGRAM_CHAT_ID not set, skipping")
            return
        text = build_weekly_report_text()
        _send(chat_id, text)
        _log.info("[scheduler] weekly_report: sent to chat_id=%s", chat_id)

    await _run_in_executor(_run, "weekly_report")


async def _job_tennis_daily_refresh() -> None:
    """Re-download current-year ATP CSV; retrain ELO model if new matches found."""

    def _run() -> None:
        from datetime import datetime
        from pathlib import Path
        from src.ingest.tennis_atp import download_atp_season

        data_dir = Path(os.environ.get("DATA_DIR", "data"))
        cache_dir = data_dir / "raw" / "tennis_atp"
        prev_size = sum(f.stat().st_size for f in cache_dir.glob("*.csv")) if cache_dir.exists() else 0
        download_atp_season(datetime.utcnow().year, cache_dir, use_cache=False)
        new_size = sum(f.stat().st_size for f in cache_dir.glob("*.csv")) if cache_dir.exists() else 0
        if new_size > prev_size:
            _log.info("[scheduler] tennis_daily_refresh: new ATP data detected, retraining ELO")
            subprocess.run(
                [sys.executable, "-m", "src.models.train_tennis_elo"], check=False
            )

    await _run_in_executor(_run, "tennis_daily_refresh")
