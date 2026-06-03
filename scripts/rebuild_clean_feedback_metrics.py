"""Migrate the paper signal ledger to clean feedback-eligible state.

What this script does:
  1. Loads the existing ledger (from local JSON and/or Supabase).
  2. Creates a timestamped backup of the local JSON ledger before any changes.
  3. Marks legacy tennis spreads/totals signals as is_feedback_eligible=False
     (those markets are experimental and not suitable for the learning loop).
  4. Marks void/push signals as is_feedback_eligible=False.
  5. Writes a clean_feedback_metrics_<timestamp>.json report to data/reports/.
  6. Saves the updated ledger through the authoritative backend.

Run once when upgrading from a pre-hardening ledger:
    python scripts/rebuild_clean_feedback_metrics.py
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
_log = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
LEDGER_PATH = Path(
    os.environ.get("LEDGER_PATH", DATA_DIR / "core" / "paper_signal_ledger.json")
)
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", DATA_DIR / "reports"))
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

_EXPERIMENTAL_MARKETS = frozenset({"spreads", "totals", "spread", "total"})
_NON_ELIGIBLE_STATUSES = frozenset({"void", "push", "expired"})


def _is_legacy_experimental(entry: dict) -> bool:
    market = str(entry.get("market_key") or entry.get("market") or "").lower()
    return market in _EXPERIMENTAL_MARKETS


def _is_non_eligible_status(entry: dict) -> bool:
    return entry.get("ledger_status", "") in _NON_ELIGIBLE_STATUSES


def _is_non_eligible_delivery(entry: dict) -> bool:
    delivery = str(entry.get("delivery_status", "")).lower()
    tier = str(entry.get("tier", "")).lower()
    return delivery == "blocked" or tier in {"blocked", "experimental"}


def main() -> None:
    _log.info("=== rebuild_clean_feedback_metrics: starting ===")

    # 1. Backup local JSON ledger before modification
    if LEDGER_PATH.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = LEDGER_PATH.with_suffix(f".bak.{ts}.json")
        shutil.copy2(LEDGER_PATH, backup_path)
        _log.info("Backup created: %s", backup_path)

    # 2. Load ledger (uses explicit backend including Supabase if configured)
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src.infrastructure.persistent_ledger import load_ledger, save_ledger

    ledger = load_ledger(LEDGER_PATH)
    entries = ledger.entries()
    _log.info("Loaded %d ledger entries", len(entries))

    # 3. Mark ineligible entries
    stats = {
        "total": len(entries),
        "already_ineligible": 0,
        "marked_experimental_market": 0,
        "marked_non_eligible_status": 0,
        "marked_non_eligible_delivery": 0,
        "already_eligible": 0,
    }

    for signal_id, entry in entries.items():
        already = entry.get("is_feedback_eligible") is False

        reasons = []
        if _is_legacy_experimental(entry):
            reasons.append("experimental_market")
            stats["marked_experimental_market"] += 1
        if _is_non_eligible_status(entry):
            reasons.append("non_eligible_status")
            stats["marked_non_eligible_status"] += 1
        if _is_non_eligible_delivery(entry):
            reasons.append("non_eligible_delivery")
            stats["marked_non_eligible_delivery"] += 1

        if reasons:
            if not already:
                entry["is_feedback_eligible"] = False
                entry["feedback_ineligible_reasons"] = reasons
                entry["ledger_updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        else:
            if entry.get("is_feedback_eligible") is None:
                entry["is_feedback_eligible"] = True
            if not already:
                stats["already_eligible"] += 1

        if already:
            stats["already_ineligible"] += 1

    # 4. Save updated ledger
    save_ledger(ledger, LEDGER_PATH)
    _log.info("Ledger saved through authoritative backend")

    # 5. Write metrics report
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ledger_path": str(LEDGER_PATH),
        "stats": stats,
        "eligible_count": sum(
            1 for e in entries.values() if e.get("is_feedback_eligible") is True
        ),
        "ineligible_count": sum(
            1 for e in entries.values() if e.get("is_feedback_eligible") is False
        ),
    }
    report_path = REPORTS_DIR / f"clean_feedback_metrics_{ts}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _log.info("Report written: %s", report_path)
    _log.info("=== Summary: %s ===", stats)


if __name__ == "__main__":
    main()
