"""FastAPI health check endpoints for Render monitoring.

Endpoints:
    GET /health              — liveness probe (Render uses this)
    GET /health/ledger       — last settlement status
    GET /health/model        — latest model Brier score + age
    GET /health/drift        — current CUSUM drift status + Kelly multiplier
    GET /health/disk         — persistent disk usage
    GET /health/readiness    — deep readiness for signal generation
    GET /health/active       — active mode status, last run timestamps, 24h stats
    GET /health/canary       — read-only production-loop verification
    GET /health/all          — all checks combined (for dashboards)
    POST /trigger            — немедленно запустить отчёт и отправить в Telegram
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from glob import glob
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
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
    _log.info(
        "TELEGRAM_STATUS_REPORTS_ENABLED=%s",
        os.environ.get("TELEGRAM_STATUS_REPORTS_ENABLED", "true"),
    )
    _log.info(
        "TELEGRAM_SIGNAL_ALERTS_ENABLED=%s",
        os.environ.get("TELEGRAM_SIGNAL_ALERTS_ENABLED", "true"),
    )
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


def _required_leagues() -> list[str]:
    raw = os.environ.get("LEAGUES", "EPL,BUNDESLIGA,LALIGA,SERIEA,LIGUE1")
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def _production_model_leagues() -> set[str]:
    leagues: set[str] = set()
    for f in MODEL_DIR.glob("dc_*.meta.json"):
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if meta.get("status") == "production" and meta.get("league"):
            leagues.add(str(meta["league"]).upper())
    return leagues


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
        payload = json.dumps(
            {
                "chat_id": chat_id,
                "text": msg,
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
        _log.info("[health_app] Startup Telegram notification sent")
    except Exception as exc:
        _log.warning(
            "[health_app] Startup Telegram notification failed: %s", _sanitize_error_text(str(exc))
        )


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    _log_startup_env()
    if ACTIVE_MODE:
        # Bootstrap: train models on first deploy if none exist
        if not _has_production_models():
            _log.info(
                "[health_app] No production models — starting bootstrap training in background"
            )
            _bootstrap_models_in_background()
        try:
            from src.services.scheduler import start as scheduler_start

            scheduler_start()
            _log.info("[health_app] Active mode scheduler started OK")
        except Exception as exc:
            _log.error("[health_app] Scheduler start FAILED: %s", exc, exc_info=True)
        pass  # startup Telegram notification disabled
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


def _sanitize_error_text(text: str) -> str:
    safe = re.sub(r"(?i)(apiKey|api_key|key)=([^&\s]+)", r"\1=[REDACTED]", str(text))
    safe = re.sub(r"/bot[^/\s]+/", "/bot[REDACTED]/", safe)
    for name, value in os.environ.items():
        if not value or len(value) < 4:
            continue
        if any(marker in name.upper() for marker in ("KEY", "TOKEN", "SECRET", "DATABASE_URL")):
            safe = safe.replace(value, "[REDACTED]")
    return safe


def _debug_routes_enabled() -> bool:
    return _env_bool("ENABLE_DEBUG_ROUTES", False)


def require_admin(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None),
) -> None:
    expected = os.environ.get("ADMIN_API_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=401, detail="admin auth is not configured")
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    elif x_admin_token:
        token = x_admin_token.strip()
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid admin token")


def require_debug_enabled() -> None:
    if not _debug_routes_enabled():
        raise HTTPException(status_code=404, detail="not found")


# ──────────────────────────────────────────────────────────────────
# /health — liveness
# ──────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"status": "ok"}


# ──────────────────────────────────────────────────────────────────
# /health/ledger
# ──────────────────────────────────────────────────────────────────


@app.get("/health/ledger", dependencies=[Depends(require_admin)])
def health_ledger():
    try:
        from src.infrastructure.persistent_ledger import load_ledger

        ledger = load_ledger(LEDGER_PATH)
        entries = list(ledger.entries().values())
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


@app.get("/health/model", dependencies=[Depends(require_admin)])
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


@app.get("/health/drift", dependencies=[Depends(require_admin)])
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


@app.get("/health/disk", dependencies=[Depends(require_admin)])
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


@app.get("/health/readiness", dependencies=[Depends(require_admin)])
def health_readiness():
    """Check whether the system is ready to generate and deliver signals."""
    checks: dict[str, dict] = {}

    # 1. Production model exists for every enabled league
    required_leagues = _required_leagues()
    prod_model_leagues = _production_model_leagues()
    missing_model_leagues = sorted(set(required_leagues) - prod_model_leagues)
    checks["model"] = {
        "ready": bool(required_leagues) and not missing_model_leagues,
        "detail": (
            f"production models present for {', '.join(required_leagues)}"
            if required_leagues and not missing_model_leagues
            else f"missing production model(s): {', '.join(missing_model_leagues)}"
        ),
    }

    # 2. Authoritative ledger backend reachable
    from src.infrastructure.persistent_ledger import ledger_healthcheck

    ledger_health = ledger_healthcheck(LEDGER_PATH)
    checks["ledger"] = {
        "ready": bool(ledger_health.get("ok")),
        "detail": str(ledger_health.get("backend", "unknown")),
    }

    # 3. Staging data exists
    staging_dir = DATA_DIR / "staging"
    staging_files = list(staging_dir.glob("*.csv")) if staging_dir.exists() else []
    checks["staging_data"] = {
        "ready": bool(staging_files),
        "detail": (
            f"{len(staging_files)} CSV file(s) in staging"
            if staging_files
            else "No staged data — run daily-trainer cron first"
        ),
    }

    # 4. Telegram bot configured
    tg_token = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
    tg_chat = bool(os.environ.get("TELEGRAM_CHAT_ID"))
    checks["telegram_bot"] = {
        "ready": tg_token and tg_chat,
        "detail": (
            "Configured"
            if (tg_token and tg_chat)
            else "Missing TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID (dry-run mode only)"
        ),
    }

    # 5. Odds provider configured
    odds_key = bool(os.environ.get("THE_ODDS_API_KEY"))
    checks["odds_provider"] = {
        "ready": odds_key,
        "detail": (
            "THE_ODDS_API_KEY set"
            if odds_key
            else "No THE_ODDS_API_KEY — signals will use staged data only"
        ),
    }

    # 6. Last cron run timestamps from reports
    def _last_report(pattern: str) -> str | None:
        files = (
            sorted((DATA_DIR / "reports").glob(pattern), reverse=True)
            if (DATA_DIR / "reports").exists()
            else []
        )
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
                "detail": (
                    "No drift"
                    if drift_ok
                    else f"Drift detected — kelly={drift.get('kelly_multiplier')}"
                ),
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
            "detail": (
                "Scheduler running"
                if sched_running
                else "ACTIVE_MODE=true but scheduler not running — check startup logs"
            ),
        }

    # 9. Telegram config required when status reports enabled
    status_reports_on = _env_bool("TELEGRAM_STATUS_REPORTS_ENABLED", True)
    if ACTIVE_MODE and status_reports_on:
        tg_ok = bool(os.environ.get("TELEGRAM_BOT_TOKEN")) and bool(
            os.environ.get("TELEGRAM_CHAT_ID")
        )
        checks["telegram_config"] = {
            "ready": tg_ok,
            "detail": (
                "Telegram configured"
                if tg_ok
                else "Status reports enabled but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing"
            ),
        }

    # 10. Production control-plane config must be present, but never echoed.
    app_env = os.environ.get("APP_ENV", "development").strip().lower()
    if app_env == "production":
        required_env = [
            "DATABASE_URL",
            "ADMIN_API_TOKEN",
            "TELEGRAM_WEBHOOK_SECRET",
            "TELEGRAM_ALLOWED_CHAT_IDS",
        ]
        missing = [name for name in required_env if not os.environ.get(name, "").strip()]
        checks["production_config"] = {
            "ready": not missing,
            "detail": (
                "required production control-plane config present"
                if not missing
                else f"missing required production config: {', '.join(missing)}"
            ),
        }

    # 11. Active report overdue (> 4 hours since last run)
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
                    "detail": f"Last report {age_h:.1f}h ago"
                    + (" — OVERDUE (>4h)" if overdue else ""),
                }
            else:
                checks["active_report_freshness"] = {
                    "ready": True,
                    "detail": "no report yet (ok on first run)",
                }
        except Exception:
            pass

    # 12. Repeated Telegram delivery failures
    tg_delivery = _read_tg_delivery_status()
    if tg_delivery.get("last_status") == "failed":
        checks["telegram_delivery"] = {
            "ready": False,
            "detail": f"Last Telegram delivery failed: {tg_delivery.get('last_error', 'unknown')}",
        }

    critical_checks = ["model", "ledger", "staging_data"]
    for optional_critical in ("scheduler", "telegram_config", "production_config"):
        if optional_critical in checks:
            critical_checks.append(optional_critical)
    ready_for_signals = all(checks[k]["ready"] for k in critical_checks)
    degraded = not all(c.get("ready", True) for c in checks.values())

    return {
        "ready_for_signals": ready_for_signals,
        "degraded": degraded,
        "checks": checks,
        "ts": _utcnow(),
    }


@app.get("/ready")
def ready():
    from src.infrastructure.persistent_ledger import ledger_healthcheck

    readiness = health_readiness()
    ledger = ledger_healthcheck(LEDGER_PATH)
    ready_ok = bool(readiness.get("ready_for_signals")) and bool(ledger.get("ok"))
    payload = {
        "status": "ok" if ready_ok else "not_ready",
        "ledger": {"backend": ledger.get("backend"), "ok": ledger.get("ok")},
        "degraded": readiness.get("degraded"),
        "checks": {
            name: {"ready": bool(check.get("ready"))}
            for name, check in dict(readiness.get("checks", {})).items()
        },
        "ts": _utcnow(),
    }
    return JSONResponse(payload, status_code=200 if ready_ok else 503)


# ──────────────────────────────────────────────────────────────────
# /health/active — active monitoring status
# ──────────────────────────────────────────────────────────────────


def _read_tg_delivery_status() -> dict:
    """Read last Telegram delivery status from disk."""
    path = REPORTS_DIR / "tg_delivery_status.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("last_error"):
                data["last_error"] = _sanitize_error_text(str(data["last_error"]))
            return data
        except Exception:
            pass
    return {}


@app.get("/health/active", dependencies=[Depends(require_admin)])
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
    staging_csvs = (
        sorted((DATA_DIR / "staging").glob("*.csv")) if (DATA_DIR / "staging").exists() else []
    )
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
# /health/quota — API quota usage (prevent overspending)
# ──────────────────────────────────────────────────────────────────


@app.get("/health/quota", dependencies=[Depends(require_admin)])
def health_quota():
    """Return API quota usage summary."""
    try:
        from src.monitoring.api_quota_monitor import get_usage_summary

        usage = get_usage_summary()

        # Check if any API is above warning threshold (80%)
        warnings = []
        for api_name, stats in usage.items():
            if stats["pct_used"] > 80:
                warnings.append(
                    f"{api_name}: {stats['pct_used']:.0f}% used ({stats['used']}/{stats['limit']})"
                )

        status = "warning" if warnings else "ok"
        return {
            "status": status,
            "usage": usage,
            "warnings": warnings,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        safe_error = _sanitize_error_text(str(exc))
        _log.warning("[quota] health check failed: %s", safe_error)
        return {
            "status": "unknown",
            "error": safe_error,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }


@app.get("/health/canary", dependencies=[Depends(require_admin)])
def health_canary():
    """Read-only production canary: verifies runtime assembly without spending quota."""
    from src.ops.production_canary import run_production_canary

    result = run_production_canary(data_dir=DATA_DIR, model_dir=MODEL_DIR, ledger_path=LEDGER_PATH)
    return JSONResponse(result, status_code=200 if result["status"] in {"pass", "warn"} else 503)


# /health/all — combined (для Render dashboard / внешних мониторов)
# ──────────────────────────────────────────────────────────────────


@app.get("/health/all", dependencies=[Depends(require_admin)])
def health_all():
    ledger = health_ledger()
    model = health_model()
    drift = health_drift()
    disk = health_disk()
    readiness = health_readiness()
    active = health_active()
    quota = health_quota()
    canary = health_canary()

    def _body(resp):
        if hasattr(resp, "body"):
            return json.loads(resp.body)
        return resp

    checks = {
        "ledger": _body(ledger),
        "model": _body(model),
        "drift": _body(drift),
        "disk": _body(disk),
        "quota": _body(quota),
        "canary": _body(canary),
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


@app.post("/trigger", dependencies=[Depends(require_admin)])
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


@app.get("/debug/tennis", dependencies=[Depends(require_admin), Depends(require_debug_enabled)])
def debug_tennis():
    """Диагностика теннисного сканера — что реально возвращает Odds API.

    Показывает:
      - сколько матчей нашлось
      - почему сигналы не генерируются (нет edge, нет матчей, нет модели)
      - edge для каждого матча без порога фильтрации

    Использование: GET /debug/tennis
    """
    from pathlib import Path

    api_key = os.environ.get("THE_ODDS_API_KEY", "")
    odds_io_key = os.environ.get("ODDS_API_IO_KEY", "")
    model_dir = Path(os.environ.get("MODEL_DIR", "data/models"))
    model_path = model_dir / "tennis_elo_atp_latest.pkl"

    if not api_key and not odds_io_key:
        return {
            "error": "No tennis odds key configured (need THE_ODDS_API_KEY or ODDS_API_IO_KEY)",
            "signals": [],
        }

    if not model_path.exists():
        return {"error": f"No model at {model_path}", "signals": []}

    try:
        from src.signals.tennis_signal_scan import scan_tennis_debug

        result = scan_tennis_debug(model_path=model_path, api_key=api_key)
        return {
            "ts": _utcnow(),
            "model_players": result.get("model_players", 0),
            "n_events": result.get("n_events", 0),
            "top_edges": result.get("rows", [])[:20],
            "error": result.get("error"),
        }
    except Exception as exc:
        _log.exception("[debug/tennis] failed: %s", exc)
        return {"error": str(exc), "ts": _utcnow()}


@app.post("/trigger/tennis-scan", dependencies=[Depends(require_admin)])
def trigger_tennis_scan():
    """Немедленно запустить теннисный скан сигналов.

    curl -X POST https://your-app.onrender.com/trigger/tennis-scan
    """
    import threading

    def _run():
        try:
            from pathlib import Path
            from src.cron.run_signals import _run_tennis

            model_dir = Path(os.environ.get("MODEL_DIR", "data/models"))
            signals = _run_tennis(model_dir)
            _log.info("[trigger/tennis-scan] Done: %d signals", len(signals))
            if signals:
                from src.cron.run_signals import _notify_telegram
                from datetime import date

                _notify_telegram(signals, date.today())
            else:
                _log.warning("[trigger/tennis-scan] 0 signals — check /debug/tennis-raw")
        except Exception as exc:
            _log.exception("[trigger/tennis-scan] failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name="tennis-scan-trigger").start()
    return {
        "status": "triggered",
        "message": "Теннисный скан запущен — результаты придут в Telegram через ~30 сек",
        "ts": _utcnow(),
    }


@app.get("/debug/tennis-raw", dependencies=[Depends(require_admin), Depends(require_debug_enabled)])
def debug_tennis_raw():
    """Показывает сырой ответ Odds API для теннисных ключей.

    Диагностика: есть ли вообще теннисные события в API.
    curl https://your-app.onrender.com/debug/tennis-raw
    """
    api_key = os.environ.get("THE_ODDS_API_KEY", "")
    odds_io_key = os.environ.get("ODDS_API_IO_KEY", "")

    results = {
        "the_odds_api_key_set": bool(api_key),
        "odds_api_io_key_set": bool(odds_io_key),
    }

    # If odds-api.io is configured, run full diagnostic
    if odds_io_key:
        try:
            from src.ingest.oddsapiio_tennis import diagnostic_info

            diag = diagnostic_info()
            results["odds_api_io"] = diag
        except Exception as exc:
            results["odds_api_io"] = {"error": str(exc)}

    if not api_key:
        results["the_odds_api_status"] = "skipped (THE_ODDS_API_KEY not set)"
        return results

    from src.services.odds_gateway import fetch_the_odds_api_json

    sport_keys = ["tennis_atp", "tennis_wta"]

    # Also check which sports are active
    try:
        sports_resp = fetch_the_odds_api_json(
            "/sports",
            api_key=api_key,
            query={},
            source="health_app.debug_tennis_raw.sports",
        )
        all_sports = sports_resp.payload
        tennis_sports = [
            s
            for s in all_sports
            if "tennis" in s.get("key", "").lower() or "tennis" in s.get("title", "").lower()
        ]
        results["available_tennis_sports"] = tennis_sports
        results["total_active_sports"] = len([s for s in all_sports if s.get("active")])
    except Exception as exc:
        results["sports_list_error"] = str(exc)

    # Try active tennis keys discovered from API
    sport_keys = [s["key"] for s in results.get("available_tennis_sports", [])]
    if not sport_keys:
        sport_keys = ["tennis_atp_french_open", "tennis_wta_french_open"]

    # Try each tennis sport key
    for sport_key in sport_keys:
        try:
            events_resp = fetch_the_odds_api_json(
                f"/sports/{sport_key}/odds",
                api_key=api_key,
                query={
                    "regions": "eu,uk,us",
                    "markets": "h2h",
                    "oddsFormat": "decimal",
                    "dateFormat": "iso",
                },
                source="health_app.debug_tennis_raw.odds",
                sport_key=sport_key,
                markets=["h2h"],
                regions=["eu", "uk", "us"],
            )
            events = events_resp.payload if isinstance(events_resp.payload, list) else []
            results[sport_key] = {
                "n_events": len(events),
                "first_3": [
                    {
                        "home": e.get("home_team"),
                        "away": e.get("away_team"),
                        "time": e.get("commence_time"),
                        "title": e.get("sport_title"),
                        "n_bookmakers": len(e.get("bookmakers", [])),
                    }
                    for e in events[:3]
                ],
            }
        except Exception as exc:
            results[sport_key] = {"error": str(exc)}

    results["ts"] = _utcnow()
    return results


@app.post("/trigger/morning-digest", dependencies=[Depends(require_admin)])
def trigger_morning_digest():
    """Немедленно запустить утренний дайджест и отправить в Telegram.

    curl -X POST https://your-app.onrender.com/trigger/morning-digest
    """
    import threading

    def _run():
        try:
            from src.cron.run_active_report import send_morning_digest

            send_morning_digest()
        except Exception as exc:
            _log.exception("[trigger/morning-digest] failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name="morning-digest-trigger").start()
    return {
        "status": "triggered",
        "message": "Дайджест 'Ставки на сегодня' запущен — придёт в Telegram через ~30 сек",
        "ts": _utcnow(),
    }


# ──────────────────────────────────────────────────────────────────
# Telegram bot webhook
# ──────────────────────────────────────────────────────────────────


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Telegram sends all updates here. Register with /webhook/telegram/setup."""
    expected_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if expected_secret:
        actual = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(actual, expected_secret):
            return JSONResponse({"ok": False, "error": "invalid webhook secret"}, status_code=401)
    elif os.environ.get("APP_ENV", "development").lower() == "production":
        return JSONResponse(
            {"ok": False, "error": "webhook secret not configured"}, status_code=503
        )
    try:
        update = await request.json()
        import threading

        threading.Thread(
            target=_handle_bot_update,
            args=(update,),
            daemon=True,
        ).start()
    except Exception as exc:
        _log.error("[webhook] Failed to parse update: %s", exc)
    return {"ok": True}


def _handle_bot_update(update: dict) -> None:
    try:
        from src.web.telegram_bot import handle_update

        handle_update(update)
    except Exception as exc:
        _log.exception("[webhook] handle_update failed: %s", exc)


@app.post("/webhook/telegram/setup", dependencies=[Depends(require_admin)])
def telegram_webhook_setup():
    """Register webhook URL with Telegram. Run once after deploy.

    curl -X POST https://your-app.onrender.com/webhook/telegram/setup
    """
    from src.web.telegram_bot import setup_webhook, get_webhook_info

    app_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if not app_url:
        return JSONResponse(
            {"error": "RENDER_EXTERNAL_URL env var not set — set it to your Render app URL"},
            status_code=400,
        )
    result = setup_webhook(app_url)
    return {"webhook_setup": result, "current_info": get_webhook_info(), "ts": _utcnow()}


@app.get("/webhook/telegram/info", dependencies=[Depends(require_admin)])
def telegram_webhook_info():
    """Check current Telegram webhook registration.

    curl https://your-app.onrender.com/webhook/telegram/info
    """
    from src.web.telegram_bot import get_webhook_info

    return {"webhook_info": get_webhook_info(), "ts": _utcnow()}


@app.get(
    "/debug/odds-sports", dependencies=[Depends(require_admin), Depends(require_debug_enabled)]
)
def debug_odds_sports():
    """Все активные виды спорта в Odds API для данного ключа.

    curl https://your-app.onrender.com/debug/odds-sports
    """
    api_key = os.environ.get("THE_ODDS_API_KEY", "")
    if not api_key:
        return {"error": "THE_ODDS_API_KEY not set"}

    try:
        from src.services.odds_gateway import fetch_the_odds_api_json

        response = fetch_the_odds_api_json(
            "/sports",
            api_key=api_key,
            query={},
            source="health_app.debug_odds_sports",
        )
        sports = response.payload
        active = [s for s in sports if s.get("active")]
        return {
            "total": len(sports),
            "active": len(active),
            "active_sports": [
                {"key": s["key"], "title": s.get("title"), "group": s.get("group")} for s in active
            ],
            "ts": _utcnow(),
        }
    except Exception as exc:
        return {"error": _sanitize_error_text(str(exc)), "ts": _utcnow()}
