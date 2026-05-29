"""Run history — append-only JSONL audit trail for cron job runs.

Each run appends one JSON line to data/reports/cron_runs.jsonl.
Records are lightweight and never modified after writing.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_DEFAULT_JSONL = Path(os.environ.get("REPORTS_DIR", "data/reports")) / "cron_runs.jsonl"


def _default_path() -> Path:
    return Path(os.environ.get("REPORTS_DIR", "data/reports")) / "cron_runs.jsonl"


def write_run(
    run_type: str,
    status: str,
    started_at: str,
    finished_at: str,
    duration_seconds: float,
    *,
    sports: list[str] | None = None,
    leagues: list[str] | None = None,
    matches_count: int = 0,
    odds_count: int = 0,
    signals_count: int = 0,
    sent_count: int = 0,
    settled_count: int = 0,
    trained: bool = False,
    training_reason: str = "",
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
    extra: dict[str, Any] | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Append a run record to the JSONL history file and return it."""
    record: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "run_type": run_type,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(duration_seconds, 2),
        "status": status,
        "sports": sports or ["football"],
        "leagues": leagues or [],
        "matches_count": matches_count,
        "odds_count": odds_count,
        "signals_count": signals_count,
        "sent_count": sent_count,
        "settled_count": settled_count,
        "trained": trained,
        "training_reason": training_reason,
        "errors": errors or [],
        "warnings": warnings or [],
        **(extra or {}),
    }

    out = path or _default_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record


def read_recent(n: int = 200, path: Path | None = None) -> list[dict[str, Any]]:
    """Return the last *n* run records (oldest first)."""
    out = path or _default_path()
    if not out.exists():
        return []
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    records: list[dict[str, Any]] = []
    for line in lines[-n:]:
        try:
            records.append(json.loads(line))
        except Exception:
            pass
    return records


def read_last_run(run_type: str, path: Path | None = None) -> dict[str, Any] | None:
    """Return the most recent record of a given run_type, or None."""
    for rec in reversed(read_recent(500, path=path)):
        if rec.get("run_type") == run_type:
            return rec
    return None


def runs_since(dt: datetime, run_type: str | None = None, path: Path | None = None) -> list[dict[str, Any]]:
    """Return all runs started after *dt* (timezone-aware UTC)."""
    result = []
    for rec in read_recent(500, path=path):
        if run_type and rec.get("run_type") != run_type:
            continue
        try:
            started = datetime.fromisoformat(rec["started_at"].replace("Z", "+00:00"))
            if started > dt:
                result.append(rec)
        except Exception:
            pass
    return result
