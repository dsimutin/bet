"""FastAPI web dashboard for Sports Betting Analytics MVP."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pickle
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.web.render_scheduler import PipelineRunStatus, render_scheduler_enabled, scheduler_loop

_ROOT = Path(__file__).parent.parent.parent
_DATA = _ROOT / "data"
_TEMPLATES = Path(__file__).parent / "templates"
_log = logging.getLogger(__name__)

_tg_stop: asyncio.Event | None = None
_tg_task: asyncio.Task | None = None
_pipeline_stop: asyncio.Event | None = None
_pipeline_task: asyncio.Task | None = None
_pipeline_status = PipelineRunStatus()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _tg_stop, _tg_task, _pipeline_stop, _pipeline_task
    _tg_stop = asyncio.Event()
    _tg_task = asyncio.create_task(_start_telegram_collector(_tg_stop))
    if render_scheduler_enabled():
        _pipeline_stop = asyncio.Event()
        _pipeline_task = asyncio.create_task(
            scheduler_loop(_ROOT, _pipeline_status, _pipeline_stop)
        )
    yield
    if _tg_stop:
        _tg_stop.set()
    if _pipeline_stop:
        _pipeline_stop.set()
    if _tg_task:
        try:
            await asyncio.wait_for(_tg_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
    if _pipeline_task:
        try:
            await asyncio.wait_for(_pipeline_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass


async def _start_telegram_collector(stop_event: asyncio.Event) -> None:
    from src.ingest.telegram_collector import is_configured, run_collector

    if not is_configured():
        _log.info(
            "Telegram collector not configured — set TELEGRAM_API_ID, "
            "TELEGRAM_API_HASH, TELEGRAM_SESSION_STR to enable."
        )
        return
    output = _DATA / "staging" / "free_sources" / "telegram_live.jsonl"
    try:
        await run_collector(output_path=output, stop_event=stop_event)
    except Exception as exc:
        _log.error("Telegram collector crashed: %s", exc, exc_info=True)


app = FastAPI(title="Betting Analytics Dashboard", version="0.1.0", lifespan=lifespan)
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
        return {
            "total_signals": 0,
            "open_signals": 0,
            "settled_signals": 0,
            "win_rate": 0,
            "roi_pct": 0,
            "pnl_units": 0,
        }
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
            result.append(
                {
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
                }
            )
        except Exception:
            pass
    return result


def _daily_readiness() -> dict[str, Any] | None:
    return _load_json(_DATA / "reports" / "daily_bot_readiness.json")


def _module_audit() -> dict[str, Any] | None:
    return _load_json(_DATA / "reports" / "module_audit.json")


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


@app.get("/api/collector/status")
def api_collector_status() -> dict[str, Any]:
    from src.ingest.telegram_collector import is_configured

    live_path = _DATA / "staging" / "free_sources" / "telegram_live.jsonl"
    line_count = 0
    last_message: str | None = None
    if live_path.exists():
        lines = live_path.read_text(encoding="utf-8").splitlines()
        line_count = len(lines)
        if lines:
            try:
                last = json.loads(lines[-1])
                last_message = f"{last.get('channel','')} @ {last.get('date','')[:19]}"
            except Exception:
                pass
    return {
        "configured": is_configured(),
        "running": _tg_task is not None and not _tg_task.done(),
        "channels": [
            c.strip() for c in os.environ.get("TELEGRAM_CHANNELS", "").split(",") if c.strip()
        ],
        "messages_collected": line_count,
        "last_message": last_message,
    }


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


@app.get("/api/modules")
def api_modules() -> dict[str, Any]:
    if audit := _module_audit():
        return audit
    from src.system.module_audit import run_module_audit

    return run_module_audit().to_dict()


@app.get("/api/pipeline/status")
def api_pipeline_status() -> dict[str, Any]:
    payload = _pipeline_status.to_dict()
    payload["enabled"] = render_scheduler_enabled()
    payload["running"] = _pipeline_task is not None and not _pipeline_task.done()
    return payload


# ── HTML dashboard ─────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    from src.ingest.telegram_collector import is_configured as tg_configured

    summary = _ledger_summary()
    signals = _recent_signals(30)
    models = _model_status()
    readiness = _daily_readiness()

    live_path = _DATA / "staging" / "free_sources" / "telegram_live.jsonl"
    tg_status = {
        "configured": tg_configured(),
        "running": _tg_task is not None and not _tg_task.done(),
        "messages_collected": (
            sum(1 for _ in live_path.open(encoding="utf-8")) if live_path.exists() else 0
        ),
        "channels": [
            c.strip() for c in os.environ.get("TELEGRAM_CHANNELS", "").split(",") if c.strip()
        ],
    }

    open_sigs = [s for s in signals if s.get("ledger_status") == "open"]
    settled_sigs = [s for s in signals if s.get("ledger_status") == "settled"]
    prod_model = next((m for m in models if m["status"] == "production"), None)

    return templates.TemplateResponse(
        request,
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
            "tg_status": tg_status,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        },
    )
