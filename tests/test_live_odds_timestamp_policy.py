from __future__ import annotations

from datetime import datetime, timezone

from src.cron.run_signals import _partition_by_timestamp_policy


def _signal(**overrides):
    base = {
        "signal_id": "s1",
        "sport": "tennis",
        "snapshot_ts_utc": "2026-06-03T10:00:00+00:00",
        "event_time_utc": "2026-06-03T12:00:00+00:00",
        "dataset_hash": "sha256:test",
    }
    base.update(overrides)
    return base


def test_tennis_verified_pre_match_signal_is_allowed() -> None:
    allowed, blocked = _partition_by_timestamp_policy([_signal()])
    assert len(allowed) == 1
    assert blocked == []
    assert allowed[0]["timestamp_verification_status"] == "verified_pre_match"


def test_started_tennis_event_is_rejected() -> None:
    allowed, blocked = _partition_by_timestamp_policy(
        [_signal(snapshot_ts_utc="2026-06-03T13:00:00+00:00")]
    )
    assert allowed == []
    assert blocked[0][1] == "odds snapshot is not earlier than event start"


def test_tennis_missing_snapshot_is_not_priority() -> None:
    allowed, blocked = _partition_by_timestamp_policy([_signal(snapshot_ts_utc="")])
    assert allowed == []
    assert blocked[0][1] == "missing verified pre-match timestamps"
