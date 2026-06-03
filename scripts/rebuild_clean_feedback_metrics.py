"""Mark legacy-invalid feedback rows and write a feedback eligibility report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.models.feedback_policy import is_feedback_eligible
from src.models.ledger_migrations import migrate_ledger_payload


def main() -> None:
    ledger_path = Path("data/core/paper_signal_ledger.json")
    report_dir = Path("data/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = (
        json.loads(ledger_path.read_text(encoding="utf-8"))
        if ledger_path.exists()
        else {"entries": {}}
    )
    migrated, migration_report = migrate_ledger_payload(payload)
    entries = migrated.get("entries", {})
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_entries": len(entries),
        "feedback_eligible": 0,
        "excluded_blocked": 0,
        "excluded_experimental": 0,
        "excluded_unverified_timestamp": 0,
        "excluded_legacy_invalid_totals_spreads": 0,
        "excluded_void_push_expired": 0,
        "migration": migration_report,
    }
    for entry in entries.values():
        if is_feedback_eligible(entry):
            report["feedback_eligible"] += 1
            continue
        if entry.get("delivery_status") == "blocked":
            report["excluded_blocked"] += 1
        if entry.get("experimental") or entry.get("experimental_market"):
            report["excluded_experimental"] += 1
        if entry.get("timestamp_verification_status") != "verified_pre_match":
            report["excluded_unverified_timestamp"] += 1
        if entry.get("legacy_invalid"):
            report["excluded_legacy_invalid_totals_spreads"] += 1
        if entry.get("result") in {"void", "push"} or entry.get("ledger_status") == "expired":
            report["excluded_void_push_expired"] += 1

    out = (
        report_dir
        / f"clean_feedback_metrics_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
