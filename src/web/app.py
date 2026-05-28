"""FastAPI web dashboard for Sports Betting Analytics MVP."""

from __future__ import annotations

import json
import os
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

_ROOT = Path(__file__).parent.parent.parent
_DATA = _ROOT / "data"
_TEMPLATES = Path(__file__).parent / "templates"

app = FastAPI(title="Betting Analytics Dashboard", version="0.1.0")
templates = Jinja2Templates(directory=str(_TEMPLATES))


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _ledger_summary() -> dict[str, Any]:
    ledger_path = _DATA / "core" / "paper_signal_ledger.json"
    data = _load_json(ledger_path)
    if data is None:
        return {"total_signals": 0, "open_signals": 0, "settled_signals": 0,
                "win_rate": 0, "roi_pct": 0, "pnl_units": 0}
    return data.get("summary", {})


def _recent_signals(n: int = 20) -> list[dict[str, Any]]:
    ledger_path = _DATA / "core" / "paper_signal_ledger.json"
    data = _load_json(ledger_path)
    if data is None:
        return []
    entries = data.get("entries", {})
    rows = sorted(
        entries.values(),
        key=lambda e: e.get("ledger_created_at_utc", ""),
        reverse=True,
    )
    return rows[:n]


def _model_status() -> list[dict[str, Any]]:
    models_dir = _DATA / "models"
    if not models_dir.exists():
        return []
    result = []
    for meta_path in sorted(models_dir.glob("dc_*.meta.json"), reverse=True):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            result.append({
                "model_id": meta.get("model_id", ""),
                "league": meta.get("league", ""),
                "status": meta.get("status", ""),
                "n_matches": meta.get("n_matches", 0),
                "brier_score": meta.get("brier_score"),
                "log_loss": meta.get("log_loss"),
                "converged": meta.get("converged", True),
                "created_at_utc": meta.get("created_at_utc", ""),
                "trained_on": meta.get("trained_on", {}),
                "promotion_reason": meta.get("promotion_reason", ""),
            })
        except Exception:
            pass
    return result


def _daily_readiness() -> dict[str, Any] | None:
    return _load_json(_DATA / "reports" / "daily_bot_readiness.json")


def _latest_signals_report() -> dict[str, Any] | None:
    reports_dir = _DATA / "reports"
    if not reports_dir.exists():
        return None
    candidates = sorted(reports_dir.glob("*signals*.json"), reverse=True)
    for path in candidates:
        data = _load_json(path)
        if data:
            return data
    return None


# ── API routes ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/ledger/summary")
def api_ledger_summary() -> dict[str, Any]:
    return _ledger_summary()


@app.get("/api/signals")
def api_signals(n: int = 20) -> list[dict[str, Any]]:
    return _recent_signals(n)


@app.get("/api/model")
def api_model() -> list[dict[str, Any]]:
    return _model_status()


@app.get("/api/readiness")
def api_readiness() -> dict[str, Any]:
    return _daily_readiness() or {"available": False}


# ── HTML dashboard ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    summary = _ledger_summary()
    signals = _recent_signals(30)
    models = _model_status()
    readiness = _daily_readiness()

    # derive simple stats for template
    open_sigs = [s for s in signals if s.get("ledger_status") == "open"]
    settled_sigs = [s for s in signals if s.get("ledger_status") == "settled"]
    prod_model = next((m for m in models if m["status"] == "production"), None)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "summary": summary,
            "signals": signals,
            "open_signals": open_sigs,
            "settled_signals": settled_sigs,
            "models": models,
            "prod_model": prod_model,
            "readiness": readiness,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        },
    )
