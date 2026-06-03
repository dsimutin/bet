from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.cron.run_signals import _partition_by_timestamp_policy


def _signal(**overrides):
    snapshot = datetime.now(timezone.utc).replace(microsecond=0)
    event_time = snapshot + timedelta(hours=2)
    base = {
        "signal_id": "s1",
        "sport": "tennis",
        "snapshot_ts_utc": snapshot.isoformat(),
        "event_time_utc": event_time.isoformat(),
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
    snapshot = datetime.now(timezone.utc).replace(microsecond=0)
    event_time = snapshot - timedelta(minutes=5)
    allowed, blocked = _partition_by_timestamp_policy(
        [_signal(snapshot_ts_utc=snapshot.isoformat(), event_time_utc=event_time.isoformat())]
    )
    assert allowed == []
    assert blocked[0][1] == "odds snapshot is not earlier than event start"


def test_tennis_missing_snapshot_is_not_priority() -> None:
    allowed, blocked = _partition_by_timestamp_policy([_signal(snapshot_ts_utc="")])
    assert allowed == []
    assert blocked[0][1] == "missing verified pre-match timestamps"
