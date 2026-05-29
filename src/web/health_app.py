"""FastAPI health check endpoints for Render monitoring.

Endpoints:
    GET /health              — liveness probe (Render uses this)
    GET /health/ledger       — last settlement status
    GET /health/model        — latest model Brier score + age
    GET /health/drift        — current CUSUM drift status + Kelly multiplier
    GET /health/disk         — persistent disk usage
    GET /health/all          — all checks combined (for dashboards)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from glob import glob
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(
    title="Bet Health API",
    description="Sports Betting Analytics — system health checks",
    version="0.1.0",
)

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
MODEL_DIR = Path(os.environ.get("MODEL_DIR", DATA_DIR / "models"))
LEDGER_PATH = Path(os.environ.get("LEDGER_PATH", DATA_DIR / "core" / "paper_signal_ledger.json"))
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", DATA_DIR / "reports"))


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
        files = sorted((DATA_DIR / "reports").glob(pattern), reverse=True) if (DATA_DIR / "reports").exists() else []
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
# /health/all — combined (для Render dashboard / внешних мониторов)
# ──────────────────────────────────────────────────────────────────

@app.get("/health/all")
def health_all():
    ledger = health_ledger()
    model = health_model()
    drift = health_drift()
    disk = health_disk()
    readiness = health_readiness()

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
        "ts": _utcnow(),
    }
