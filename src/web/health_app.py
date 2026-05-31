"""FastAPI health check endpoints for Render monitoring.

Endpoints:
    GET /health              — liveness probe (Render uses this)
    GET /health/ledger       — last settlement status
    GET /health/model        — latest model Brier score + age
    GET /health/drift        — current CUSUM drift status + Kelly multiplier
    GET /health/disk         — persistent disk usage
    GET /health/readiness    — deep readiness for signal generation
    GET /health/active       — active mode status, last run timestamps, 24h stats
    GET /health/all          — all checks combined (for dashboards)
    POST /trigger            — немедленно запустить отчёт и отправить в Telegram
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from glob import glob
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

# Configure root logger so all app INFO messages appear in Render / uvicorn logs.
# Must happen before any module-level logger is used.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)

_log = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
MODEL_DIR = Path(os.environ.get("MODEL_DIR", DATA_DIR / "models"))
LEDGER_PATH = Path(os.environ.get("LEDGER_PATH", DATA_DIR / "core" / "paper_signal_ledger.json"))
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", DATA_DIR / "reports"))


def _env_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).strip().lower() in ("1", "true", "yes")


ACTIVE_MODE = _env_bool("ACTIVE_MODE", False)


# ──────────────────────────────────────────────────────────────────
# Lifespan: start/stop APScheduler when ACTIVE_MODE=true
# ──────────────────────────────────────────────────────────────────

def _log_startup_env() -> None:
    """Log env var presence at startup — safe (no secret values)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    masked = ("***" + chat_id[-4:]) if len(chat_id) >= 4 else ("***" if chat_id else "(empty)")
    _log.info("=== STARTUP DIAGNOSTICS ===")
    _log.info("ACTIVE_MODE=%s", os.environ.get("ACTIVE_MODE", "false"))
    _log.info("TELEGRAM_STATUS_REPORTS_ENABLED=%s",
              os.environ.get("TELEGRAM_STATUS_REPORTS_ENABLED", "true"))
    _log.info("TELEGRAM_SIGNAL_ALERTS_ENABLED=%s",
              os.environ.get("TELEGRAM_SIGNAL_ALERTS_ENABLED", "true"))
    _log.info("TELEGRAM_BOT_TOKEN_PRESENT=%s", bool(token))
    _log.info("TELEGRAM_BOT_TOKEN_LENGTH=%d", len(token))
    _log.info("TELEGRAM_CHAT_ID_PRESENT=%s", bool(chat_id))
    _log.info("TELEGRAM_CHAT_ID_MASKED=%s", masked)
    _log.info("THE_ODDS_API_KEY_PRESENT=%s", bool(os.environ.get("THE_ODDS_API_KEY", "")))
    _log.info("DATA_DIR=%s", os.environ.get("DATA_DIR", "data"))
    _log.info("SCHEDULER_ENABLED=%s", ACTIVE_MODE)
    _log.info("=== END DIAGNOSTICS ===")


def _has_production_models() -> bool:
    """Return True if at least one production model exists on disk."""
    for f in MODEL_DIR.glob("dc_*.meta.json"):
        try:
            import json as _json
            meta = _json.loads(f.read_text(encoding="utf-8"))
            if meta.get("status") == "production":
                return True
        except Exception:
            pass
    return False


def _bootstrap_models_in_background() -> None:
    """Run run_trainer in a daemon thread so the web service starts immediately."""
    import threading

    def _run():
        _log.info("[health_app] No production models found — running bootstrap training")
        try:
            from src.cron import run_trainer
            run_trainer.main()
            _log.info("[health_app] Bootstrap training complete")
        except Exception as exc:
            _log.error("[health_app] Bootstrap training failed: %s", exc)

    t = threading.Thread(target=_run, daemon=True, name="bootstrap-trainer")
    t.start()


def _send_startup_telegram(msg: str) -> None:
    """Send a plain-text message to Telegram. Fires-and-forgets; never raises."""
    import urllib.request
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        payload = json.dumps({
            "chat_id": chat_id,
            "text": msg,
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
        _log.info("[health_app] Startup Telegram notification sent")
    except Exception as exc:
        _log.warning("[health_app] Startup Telegram notification failed: %s", exc)


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    _log_startup_env()
    if ACTIVE_MODE:
        # Bootstrap: train models on first deploy if none exist
        if not _has_production_models():
            _log.info("[health_app] No production models — starting bootstrap training in background")
            _bootstrap_models_in_background()
        try:
            from src.services.scheduler import start as scheduler_start
            scheduler_start()
            _log.info("[health_app] Active mode scheduler started OK")
        except Exception as exc:
            _log.error("[health_app] Scheduler start FAILED: %s", exc, exc_info=True)
        # Notify Telegram that the bot restarted (verifies credentials on every deploy)
        _send_startup_telegram(
            f"🤖 Бот запущен / Bot started\n"
            f"ACTIVE_MODE=true | Scheduler running\n"
            f"Leagues: {os.environ.get('LEAGUES', 'EPL,BUNDESLIGA,LALIGA,SERIEA')}\n"
            f"Первый отчёт / Next report: через ~10 мин (к следующему часу)\n"
            f"Сигналы будут если задан THE_ODDS_API_KEY\n"
            f"Запущен: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
    else:
        _log.info("[health_app] ACTIVE_MODE=false — scheduler not started")
    yield
    if ACTIVE_MODE:
        try:
            from src.services.scheduler import stop as scheduler_stop
            scheduler_stop()
        except Exception:
            pass


app = FastAPI(
    title="Bet Health API",
    description="Sports Betting Analytics — system health checks",
    version="0.2.0",
    lifespan=lifespan,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────────
# /health — liveness
# ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "ts": _utcnow()}


# ──────────────────────────────────────────────────────────────────
# /health/ledger
# ──────────────────────────────────────────────────────────────────

@app.get("/health/ledger")
def health_ledger():
    if not LEDGER_PATH.exists():
        return JSONResponse({"status": "no_ledger", "ts": _utcnow()}, status_code=404)

    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        entries = list(data.get("entries", {}).values())
        settled = [e for e in entries if e.get("ledger_status") == "settled"]
        open_ = [e for e in entries if e.get("ledger_status") == "open"]
        wins = sum(1 for e in settled if e.get("result") == "win")

        report_path = REPORTS_DIR / "signal_ledger_settlement_report.json"
        last_settlement = None
        if report_path.exists():
            rp = json.loads(report_path.read_text(encoding="utf-8"))
            last_settlement = rp.get("settled_at_utc") or rp.get("generated_at_utc")

        return {
            "status": "ok",
            "total_entries": len(entries),
            "settled": len(settled),
            "open": len(open_),
            "wins": wins,
            "losses": len(settled) - wins,
            "win_rate_pct": round(wins / len(settled) * 100, 1) if settled else None,
            "last_settlement_utc": last_settlement,
            "ts": _utcnow(),
        }
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e), "ts": _utcnow()}, status_code=500)


# ──────────────────────────────────────────────────────────────────
# /health/model
# ──────────────────────────────────────────────────────────────────

@app.get("/health/model")
def health_model():
    meta_files = sorted(glob(str(MODEL_DIR / "dc_*.meta.json")))
    if not meta_files:
        return JSONResponse({"status": "no_models", "ts": _utcnow()}, status_code=404)

    try:
        latest_path = meta_files[-1]
        meta = json.loads(Path(latest_path).read_text(encoding="utf-8"))
        created = meta.get("created_at_utc", "")
        age_hours = None
        if created:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_hours = round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)

        return {
            "status": "ok",
            "model_id": meta.get("model_id"),
            "league": meta.get("league"),
            "model_status": meta.get("status", "candidate"),
            "brier_score": meta.get("brier_score"),
            "log_loss": meta.get("log_loss"),
            "n_matches": meta.get("n_matches"),
            "age_hours": age_hours,
            "created_at_utc": created,
            "total_versions": len(meta_files),
            "ts": _utcnow(),
        }
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e), "ts": _utcnow()}, status_code=500)


# ──────────────────────────────────────────────────────────────────
# /health/drift
# ──────────────────────────────────────────────────────────────────

@app.get("/health/drift")
def health_drift():
    drift_path = REPORTS_DIR / "drift_report.json"
    if not drift_path.exists():
        return JSONResponse({"status": "no_drift_report", "ts": _utcnow()}, status_code=404)

    try:
        report = json.loads(drift_path.read_text(encoding="utf-8"))
        drift_detected = report.get("drift_detected", False)
        kelly = report.get("kelly_multiplier", 1.0)

        return {
            "status": "drift" if drift_detected else "ok",
            "drift_detected": drift_detected,
            "kelly_multiplier": kelly,
            "recent_accuracy": report.get("recent_accuracy"),
            "baseline_accuracy": report.get("baseline_accuracy"),
            "cusum_value": report.get("cusum_value"),
            "threshold": report.get("threshold"),
            "retrain_recommended": report.get("retrain_recommended", False),
            "reason": report.get("reason", ""),
            "generated_at_utc": report.get("generated_at_utc"),
            "ts": _utcnow(),
        }
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e), "ts": _utcnow()}, status_code=500)


# ──────────────────────────────────────────────────────────────────
# /health/disk
# ──────────────────────────────────────────────────────────────────

@app.get("/health/disk")
def health_disk():
    def _mb(p: Path) -> float:
        if not p.exists():
            return 0.0
        return round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1_048_576, 2)

    return {
        "status": "ok",
        "models_mb": _mb(MODEL_DIR),
        "staging_mb": _mb(DATA_DIR / "staging"),
        "reports_mb": _mb(REPORTS_DIR),
        "total_mb": _mb(DATA_DIR),
        "disk_mounted": DATA_DIR.exists(),
        "ts": _utcnow(),
    }


# ──────────────────────────────────────────────────────────────────
# /health/readiness — deep readiness for signal generation
# ──────────────────────────────────────────────────────────────────

@app.get("/health/readiness")
def health_readiness():
    """Check whether the system is ready to generate and deliver signals."""
    checks: dict[str, dict] = {}

    # 1. Production model exists
    meta_files = sorted(glob(str(MODEL_DIR / "dc_*.meta.json")))
    prod_models = [p for p in meta_files
                   if json.loads(Path(p).read_text())
                   .get("status") == "production"]
    checks["model"] = {
        "ready": bool(prod_models),
        "detail": f"{len(prod_models)} production model(s) found" if prod_models
                  else "No production model — run daily-trainer cron first",
    }

    # 2. Ledger exists
    checks["ledger"] = {
        "ready": LEDGER_PATH.exists(),
        "detail": str(LEDGER_PATH) if LEDGER_PATH.exists() else "Ledger file not found",
    }

    # 3. Staging data exists
    staging_dir = DATA_DIR / "staging"
    staging_files = list(staging_dir.glob("*.csv")) if staging_dir.exists() else []
    checks["staging_data"] = {
        "ready": bool(staging_files),
        "detail": f"{len(staging_files)} CSV file(s) in staging" if staging_files
                  else "No staged data — run daily-trainer cron first",
    }

    # 4. Telegram bot configured
    tg_token = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
    tg_chat = bool(os.environ.get("TELEGRAM_CHAT_ID"))
    checks["telegram_bot"] = {
        "ready": tg_token and tg_chat,
        "detail": "Configured" if (tg_token and tg_chat)
                  else "Missing TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID (dry-run mode only)",
    }

    # 5. Odds provider configured
    odds_key = bool(os.environ.get("THE_ODDS_API_KEY"))
    checks["odds_provider"] = {
        "ready": odds_key,
        "detail": "THE_ODDS_API_KEY set" if odds_key
                  else "No THE_ODDS_API_KEY — signals will use staged data only",
    }

    # 6. Last cron run timestamps from reports
    def _last_report(pattern: str) -> str | None:
        files = sorted((DATA_DIR / "reports").glob(pattern), reverse=True) \
                if (DATA_DIR / "reports").exists() else []
        return files[0].name if files else None

    checks["last_settlement"] = {
        "ready": True,
        "detail": _last_report("settlement_*.json") or "never",
    }
    checks["last_signals"] = {
        "ready": True,
        "detail": _last_report("*signals*.json") or "never",
    }

    # 7. Drift status
    drift_path = REPORTS_DIR / "drift_report.json"
    if drift_path.exists():
        try:
            drift = json.loads(drift_path.read_text(encoding="utf-8"))
            drift_ok = not drift.get("drift_detected", False)
            checks["drift"] = {
                "ready": drift_ok,
                "detail": "No drift" if drift_ok else f"Drift detected — kelly={drift.get('kelly_multiplier')}",
            }
        except Exception:
            checks["drift"] = {"ready": True, "detail": "drift_report unreadable"}
    else:
        checks["drift"] = {"ready": True, "detail": "no drift_report yet (ok on first run)"}

    # 8. Scheduler state (only relevant when ACTIVE_MODE=true)
    if ACTIVE_MODE:
        sched_running = False
        try:
            from src.services.scheduler import get_scheduler
            sched_running = get_scheduler().running
        except Exception:
            pass
        checks["scheduler"] = {
            "ready": sched_running,
            "detail": "Scheduler running" if sched_running
                      else "ACTIVE_MODE=true but scheduler not running — check startup logs",
        }

    # 9. Telegram config required when status reports enabled
    status_reports_on = _env_bool("TELEGRAM_STATUS_REPORTS_ENABLED", True)
    if ACTIVE_MODE and status_reports_on:
        tg_ok = bool(os.environ.get("TELEGRAM_BOT_TOKEN")) and bool(os.environ.get("TELEGRAM_CHAT_ID"))
        checks["telegram_config"] = {
            "ready": tg_ok,
            "detail": "Telegram configured" if tg_ok
                      else "Status reports enabled but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing",
        }

    # 10. Active report overdue (> 4 hours since last run)
    if ACTIVE_MODE:
        try:
            from src.models.run_history import read_last_run
            last = read_last_run("active_report")
            if last:
                last_dt = datetime.fromisoformat(last["started_at"].replace("Z", "+00:00"))
                age_h = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
                overdue = age_h > 4.0
                checks["active_report_freshness"] = {
                    "ready": not overdue,
                    "detail": f"Last report {age_h:.1f}h ago" + (" — OVERDUE (>4h)" if overdue else ""),
                }
            else:
                checks["active_report_freshness"] = {"ready": True, "detail": "no report yet (ok on first run)"}
        except Exception:
            pass

    # 11. Repeated Telegram delivery failures
    tg_delivery = _read_tg_delivery_status()
    if tg_delivery.get("last_status") == "failed":
        checks["telegram_delivery"] = {
            "ready": False,
            "detail": f"Last Telegram delivery failed: {tg_delivery.get('last_error', 'unknown')}",
        }

    ready_for_signals = all(
        checks[k]["ready"] for k in ("model", "ledger", "staging_data")
    )
    degraded = not all(c.get("ready", True) for c in checks.values())

    return {
        "ready_for_signals": ready_for_signals,
        "degraded": degraded,
        "checks": checks,
        "ts": _utcnow(),
    }


# ──────────────────────────────────────────────────────────────────
# /health/active — active monitoring status
# ──────────────────────────────────────────────────────────────────

def _read_tg_delivery_status() -> dict:
    """Read last Telegram delivery status from disk."""
    path = REPORTS_DIR / "tg_delivery_status.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


@app.get("/health/active")
def health_active():
    """Active mode status: scheduler, Telegram, per-job next_run, 24h stats."""
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    active_enabled = ACTIVE_MODE
    scheduler_running = False
    jobs_info: dict[str, dict] = {}

    if active_enabled:
        try:
            from src.services.scheduler import get_scheduler
            sched = get_scheduler()
            scheduler_running = sched.running
            if scheduler_running:
                for job in sched.get_jobs():
                    next_run = job.next_run_time
                    jobs_info[job.id] = {
                        "next_run": next_run.isoformat() if next_run else None,
                        "last_run": None,
                        "status": None,
                    }
        except Exception:
            pass

    # Last runs from run history
    run_types = ["signal_scan", "settlement", "training_check", "active_report"]
    errors_24h: list[str] = []
    signals_24h = 0
    settled_24h = 0

    try:
        from src.models.run_history import read_recent, read_last_run
        records = read_recent(500)

        for run_type in run_types:
            rec = read_last_run(run_type)
            if rec:
                ts = rec.get("started_at", "")[:19].replace("T", " ") + " UTC"
                status = rec.get("status", "unknown")
                reason = rec.get("training_reason", "") if run_type == "training_check" else ""
                info: dict = {"last_run": ts, "status": status}
                if reason:
                    info["reason"] = reason
                if run_type in jobs_info:
                    jobs_info[run_type].update(info)
                else:
                    jobs_info[run_type] = {"next_run": None, **info}

        for rec in records:
            try:
                started = datetime.fromisoformat(rec["started_at"].replace("Z", "+00:00"))
                if started >= cutoff_24h:
                    signals_24h += rec.get("signals_count", 0)
                    settled_24h += rec.get("settled_count", 0)
                    errors_24h.extend(rec.get("errors", []))
            except Exception:
                pass
    except Exception:
        pass

    # Telegram section
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    tg_delivery = _read_tg_delivery_status()
    telegram_info = {
        "bot_token_present": bool(token),
        "chat_id_present": bool(chat_id),
        "status_reports_enabled": _env_bool("TELEGRAM_STATUS_REPORTS_ENABLED", True),
        "signal_alerts_enabled": _env_bool("TELEGRAM_SIGNAL_ALERTS_ENABLED", True),
        "last_delivery_status": tg_delivery.get("last_status"),
        "last_delivery_at": tg_delivery.get("last_at"),
        "last_error": tg_delivery.get("last_error"),
    }

    # Model age
    model_age_hours = None
    meta_files = sorted(glob(str(MODEL_DIR / "dc_*.meta.json")))
    for mf in reversed(meta_files):
        try:
            m = json.loads(Path(mf).read_text())
            if m.get("status") == "production":
                dt = datetime.fromisoformat(m["created_at_utc"].replace("Z", "+00:00"))
                model_age_hours = round((now - dt).total_seconds() / 3600, 1)
                break
        except Exception:
            pass

    # Data freshness (last staging CSV mtime)
    data_freshness: str | None = None
    staging_csvs = sorted((DATA_DIR / "staging").glob("*.csv")) if (DATA_DIR / "staging").exists() else []
    if staging_csvs:
        try:
            mtime = max(f.stat().st_mtime for f in staging_csvs)
            dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
            age_h = round((now - dt).total_seconds() / 3600, 1)
            data_freshness = f"{age_h}h ago"
        except Exception:
            pass

    # last_runs: simple backward-compatible dict
    last_runs = {
        run_type: jobs_info.get(run_type, {}).get("last_run")
        for run_type in ["signal_scan", "settlement", "training_check", "active_report"]
    }

    return {
        "active_mode": active_enabled,
        "scheduler_running": scheduler_running,
        "interval_hours": int(os.environ.get("ACTIVE_REPORT_INTERVAL_HOURS", "3")),
        "telegram": telegram_info,
        "jobs": jobs_info,
        "last_runs": last_runs,
        "stats_24h": {
            "signals": signals_24h,
            "settled": settled_24h,
            "errors": len(errors_24h),
            "error_details": list(dict.fromkeys(errors_24h))[:5],
        },
        "model_age_hours": model_age_hours,
        "data_freshness": data_freshness,
        "ts": _utcnow(),
    }


# ──────────────────────────────────────────────────────────────────
# /health/all — combined (для Render dashboard / внешних мониторов)
# ──────────────────────────────────────────────────────────────────

@app.get("/health/all")
def health_all():
    ledger = health_ledger()
    model = health_model()
    drift = health_drift()
    disk = health_disk()
    readiness = health_readiness()
    active = health_active()

    def _body(resp):
        if hasattr(resp, "body"):
            return json.loads(resp.body)
        return resp

    checks = {
        "ledger": _body(ledger),
        "model": _body(model),
        "drift": _body(drift),
        "disk": _body(disk),
    }

    statuses = [c.get("status", "unknown") for c in checks.values()]
    overall = "ok" if all(s in ("ok",) for s in statuses) else "degraded"

    return {
        "overall": overall,
        "checks": checks,
        "readiness": _body(readiness),
        "active": _body(active),
        "ts": _utcnow(),
    }


# ──────────────────────────────────────────────────────────────────
# /trigger — ручной запуск отчёта (для отладки и первого теста)
# ──────────────────────────────────────────────────────────────────

@app.post("/trigger")
def trigger_report():
    """Немедленно запустить active report и отправить в Telegram.

    Используй для проверки что бот работает:
        curl -X POST https://your-app.onrender.com/trigger
    """
    import threading

    result: dict = {"started": False, "error": None}

    def _run():
        try:
            from src.cron.run_active_report import main as report_main
            report_main(force=True)
        except Exception as exc:
            _log.error("[trigger] report failed: %s", exc)

    t = threading.Thread(target=_run, daemon=True, name="manual-trigger")
    t.start()
    result["started"] = True

    return {
        "status": "triggered",
        "message": "Active report запущен в фоне — проверь Telegram через ~10 сек",
        "ts": _utcnow(),
    }
