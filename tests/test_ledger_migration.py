from __future__ import annotations

from src.models.ledger_migrations import migrate_ledger_payload


def test_ledger_migration_is_idempotent() -> None:
    payload = {"entries": {"s1": {"market": "h2h", "foo": "bar"}}}
    once, _ = migrate_ledger_payload(payload)
    twice, _ = migrate_ledger_payload(once)
    assert once == twice


def test_legacy_total_marked_invalid() -> None:
    migrated, report = migrate_ledger_payload({"entries": {"s1": {"market": "totals"}}})
    assert migrated["entries"]["s1"]["legacy_invalid"] is True
    assert migrated["entries"]["s1"]["feedback_eligible"] is False
    assert report["legacy_invalid_marked"] == 1


def test_migration_does_not_delete_entries() -> None:
    migrated, _ = migrate_ledger_payload(
        {"entries": {"s1": {"market": "totals"}, "s2": {"market": "h2h"}}}
    )
    assert set(migrated["entries"]) == {"s1", "s2"}


def test_migration_preserves_existing_valid_fields() -> None:
    migrated, _ = migrate_ledger_payload({"entries": {"s1": {"market": "h2h", "edge_pct": 3.2}}})
    assert migrated["entries"]["s1"]["edge_pct"] == 3.2
