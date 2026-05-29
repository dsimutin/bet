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
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from glob import glob
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

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

@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    if ACTIVE_MODE:
        try:
            from src.services.scheduler import start as scheduler_start
            scheduler_start()
            _log.info("[health_app] Active mode scheduler started")
        except Exception as exc:
            _log.error("[health_app] Scheduler start failed: %s", exc)
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

    ready_for_signals = all(
        checks[k]["ready"] for k in ("model", "ledger", "staging_data")
    )

    return {
        "ready_for_signals": ready_for_signals,
        "checks": checks,
        "ts": _utcnow(),
    }


# ──────────────────────────────────────────────────────────────────
# /health/active — active monitoring status
# ──────────────────────────────────────────────────────────────────

@app.get("/health/active")
def health_active():
    """Active mode status: last run timestamps, 24h stats, scheduler state."""
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    active_enabled = ACTIVE_MODE
    scheduler_running = False
    if active_enabled:
        try:
            from src.services.scheduler import get_scheduler
            scheduler_running = get_scheduler().running
        except Exception:
            pass

    # Last runs from run history
    last_runs: dict[str, str | None] = {
        "signal_scan": None,
        "settlement": None,
        "training_check": None,
        "active_report": None,
    }
    errors_24h: list[str] = []
    signals_24h = 0
    settled_24h = 0

    try:
        from src.models.run_history import read_recent, read_last_run
        records = read_recent(500)

        for run_type in last_runs:
            rec = read_last_run(run_type)
            if rec:
                last_runs[run_type] = rec.get("started_at", "")[:19].replace("T", " ") + " UTC"

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

    return {
        "active_mode": active_enabled,
        "scheduler_running": scheduler_running,
        "interval_hours": int(os.environ.get("ACTIVE_REPORT_INTERVAL_HOURS", "3")),
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
