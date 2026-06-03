"""Shared pre-match timestamp verification for paper signals."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

TimestampStatus = Literal[
    "verified_pre_match",
    "missing_timestamp",
    "invalid_timestamp",
    "post_start",
    "stale_snapshot",
]


def parse_utc_datetime(value: Any) -> datetime | None:
    """Parse an ISO-8601 value and normalize it to UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def verify_pre_match_timestamps(
    signal: dict[str, Any],
    *,
    now: datetime | None = None,
) -> TimestampStatus:
    """Return timestamp verification status for a candidate signal."""
    snapshot = parse_utc_datetime(signal.get("snapshot_ts_utc"))
    event_time = parse_utc_datetime(signal.get("event_time_utc"))
    if snapshot is None or event_time is None:
        return "missing_timestamp"
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if snapshot >= event_time:
        return "post_start"
    if event_time <= current:
        return "post_start"
    return "verified_pre_match"


def apply_timestamp_status(
    signal: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a copy of signal with `timestamp_verification_status` populated."""
    enriched = dict(signal)
    enriched["timestamp_verification_status"] = verify_pre_match_timestamps(enriched, now=now)
    return enriched
