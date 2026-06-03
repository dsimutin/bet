from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

import src.services.runtime_odds as runtime_odds


def _snapshot(age_seconds: int, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return {
        "events": events or [{"id": "cached-event"}],
        "fetched_at_utc": fetched_at.isoformat(),
    }


def test_cache_storage_ttl_does_not_imply_priority_freshness(monkeypatch) -> None:
    monkeypatch.setenv("PRIORITY_ODDS_MAX_AGE_SECONDS", "900")
    monkeypatch.setenv("WATCHLIST_ODDS_MAX_AGE_SECONDS", "3600")

    assert runtime_odds._freshness_tier(_snapshot(1800)["fetched_at_utc"]) == "watchlist"


def test_cache_hit_does_not_increment_quota(monkeypatch) -> None:
    monkeypatch.setattr(runtime_odds.odds_cache, "get", lambda key: _snapshot(30))
    monkeypatch.setattr(runtime_odds.odds_cache, "set", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runtime_odds,
        "_fetch_odds",
        lambda *args, **kwargs: pytest.fail("fresh cache hit must not fetch provider"),
    )

    events = runtime_odds.get_football_h2h_odds("soccer_epl", "test-key")

    assert events == [
        {
            "id": "cached-event",
            "_odds_snapshot_ts_utc": events[0]["_odds_snapshot_ts_utc"],
        }
    ]


def test_stale_priority_snapshot_is_refreshed_when_quota_allows(monkeypatch) -> None:
    cached = _snapshot(1800, [{"id": "old-event"}])
    saved: list[dict[str, Any]] = []
    monkeypatch.setenv("PRIORITY_ODDS_MAX_AGE_SECONDS", "900")
    monkeypatch.setattr(runtime_odds.odds_cache, "get", lambda key: cached)
    monkeypatch.setattr(runtime_odds.odds_cache, "set", lambda key, value, ttl: saved.append(value))
    monkeypatch.setattr(
        runtime_odds,
        "_fetch_odds",
        lambda *args, **kwargs: ([{"id": "fresh-event"}], {"remaining": "100"}),
    )

    events = runtime_odds.get_football_h2h_odds("soccer_epl", "test-key")

    assert events[0]["id"] == "fresh-event"
    assert saved and saved[0]["events"] == [{"id": "fresh-event"}]


def test_stale_priority_snapshot_is_downgraded_when_refresh_unavailable(monkeypatch) -> None:
    cached = _snapshot(1800, [{"id": "watchlist-event"}])
    monkeypatch.setenv("PRIORITY_ODDS_MAX_AGE_SECONDS", "900")
    monkeypatch.setenv("WATCHLIST_ODDS_MAX_AGE_SECONDS", "3600")
    monkeypatch.setattr(runtime_odds.odds_cache, "get", lambda key: cached)
    monkeypatch.setattr(runtime_odds.odds_cache, "set", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runtime_odds,
        "_fetch_odds",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("quota hard stop")),
    )

    events = runtime_odds.get_football_h2h_odds("soccer_epl", "test-key")

    assert events[0]["id"] == "watchlist-event"
    assert runtime_odds._freshness_tier(events[0]["_odds_snapshot_ts_utc"]) == "watchlist"


def test_too_old_snapshot_is_blocked(monkeypatch) -> None:
    cached = _snapshot(7200, [{"id": "stale-event"}])
    monkeypatch.setenv("PRIORITY_ODDS_MAX_AGE_SECONDS", "900")
    monkeypatch.setenv("WATCHLIST_ODDS_MAX_AGE_SECONDS", "3600")
    monkeypatch.setattr(runtime_odds.odds_cache, "get", lambda key: cached)
    monkeypatch.setattr(runtime_odds.odds_cache, "set", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runtime_odds,
        "_fetch_odds",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("quota hard stop")),
    )

    events = runtime_odds.get_football_h2h_odds("soccer_epl", "test-key")

    assert events[0]["id"] == "stale-event"
    assert runtime_odds._freshness_tier(events[0]["_odds_snapshot_ts_utc"]) == "stale_blocked"
