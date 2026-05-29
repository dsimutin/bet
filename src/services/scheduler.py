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


def start(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Register all jobs and start the scheduler. No-op if ACTIVE_MODE is false."""
    if not ACTIVE_MODE:
        _log.info("[scheduler] ACTIVE_MODE=false — scheduler not started")
        return

    sched = get_scheduler()
    if sched.running:
        _log.warning("[scheduler] Already running — skipping duplicate start")
        return

    interval_h = int(os.environ.get("ACTIVE_REPORT_INTERVAL_HOURS", "3"))

    # ── Job registrations ──────────────────────────────────────────────
    # Signal scan:     top of every Nth hour
    sched.add_job(
        _job_signal_scan,
        "cron",
        hour=f"*/{interval_h}",
        minute=0,
        id="signal_scan",
        replace_existing=True,
        misfire_grace_time=300,
    )
    # Settlement:      20 min into every Nth hour
    sched.add_job(
        _job_settlement,
        "cron",
        hour=f"*/{interval_h}",
        minute=20,
        id="settlement",
        replace_existing=True,
        misfire_grace_time=300,
    )
    # Training check:  40 min into every Nth hour
    sched.add_job(
        _job_training_check,
        "cron",
        hour=f"*/{interval_h}",
        minute=40,
        id="training_check",
        replace_existing=True,
        misfire_grace_time=600,
    )
    # Active report:   50 min into every Nth hour
    sched.add_job(
        _job_active_report,
        "cron",
        hour=f"*/{interval_h}",
        minute=50,
        id="active_report",
        replace_existing=True,
        misfire_grace_time=300,
    )

    sched.start()
    _log.info(
        "[scheduler] Started. Jobs: signal_scan@:00, settlement@:20, "
        "training_check@:40, active_report@:50 every %dh UTC",
        interval_h,
    )


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
