"""Idempotent in-memory migrations for paper signal ledgers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

LEDGER_SCHEMA_VERSION = 2


def migrate_ledger_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a migrated payload and a small migration report.

    Old entries are never deleted. Legacy tennis totals/spreads are marked invalid for
    production feedback because earlier scanners/settlement used ambiguous fields.
    """
    migrated = dict(payload)
    entries = dict(migrated.get("entries", {}))
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "from_version": migrated.get("ledger_schema_version", migrated.get("version", "unknown")),
        "to_version": LEDGER_SCHEMA_VERSION,
        "total_entries": len(entries),
        "legacy_invalid_marked": 0,
    }
    for signal_id, entry in entries.items():
        updated = dict(entry)
        market = str(updated.get("market", updated.get("market_key", "h2h"))).lower()
        if market in {"spreads", "totals"} and "legacy_invalid" not in updated:
            updated["legacy_invalid"] = True
            updated["feedback_eligible"] = False
            updated["migration_note"] = (
                "legacy tennis alternative market excluded from production feedback"
            )
            report["legacy_invalid_marked"] += 1
        entries[signal_id] = updated
    migrated["ledger_schema_version"] = LEDGER_SCHEMA_VERSION
    migrated["entries"] = entries
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    return migrated, report
