from __future__ import annotations

from typing import Any

import src.ingest.apifootball_injuries as injuries


class _MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self.values.get(key)

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self.values[key] = value


def test_injury_negative_cache_is_reused(monkeypatch) -> None:
    cache = _MemoryCache()
    fixture_calls = 0

    def fake_fetch_fixture_id(*args, **kwargs):
        nonlocal fixture_calls
        fixture_calls += 1
        return None

    monkeypatch.setenv("API_FOOTBALL_KEY", "test-key")
    monkeypatch.setattr(injuries, "fetch_fixture_id", fake_fetch_fixture_id)
    monkeypatch.setattr(injuries, "fetch_injuries_for_fixture", lambda *args, **kwargs: [])
    monkeypatch.setattr("src.infrastructure.odds_cache.get", cache.get)
    monkeypatch.setattr("src.infrastructure.odds_cache.set", cache.set)

    first = injuries.get_injuries_for_match("Arsenal", "Chelsea", "2026-06-03", "EPL")
    second = injuries.get_injuries_for_match("Arsenal", "Chelsea", "2026-06-03", "EPL")

    assert fixture_calls == 1
    assert first == second
    assert second == {"home": [], "away": [], "available": False, "note": "fixture_not_found"}


def test_injury_positive_cache_is_reused(monkeypatch) -> None:
    cache = _MemoryCache()
    fixture_calls = 0
    injury_calls = 0

    def fake_fetch_fixture_id(*args, **kwargs):
        nonlocal fixture_calls
        fixture_calls += 1
        return 123

    def fake_fetch_injuries_for_fixture(*args, **kwargs):
        nonlocal injury_calls
        injury_calls += 1
        return [
            {
                "player_name": "Example Player",
                "team_name": "Arsenal",
                "reason": "Knee Injury",
            }
        ]

    monkeypatch.setenv("API_FOOTBALL_KEY", "test-key")
    monkeypatch.setattr(injuries, "fetch_fixture_id", fake_fetch_fixture_id)
    monkeypatch.setattr(injuries, "fetch_injuries_for_fixture", fake_fetch_injuries_for_fixture)
    monkeypatch.setattr("src.infrastructure.odds_cache.get", cache.get)
    monkeypatch.setattr("src.infrastructure.odds_cache.set", cache.set)

    first = injuries.get_injuries_for_match("Arsenal", "Chelsea", "2026-06-03", "EPL")
    second = injuries.get_injuries_for_match("Arsenal", "Chelsea", "2026-06-03", "EPL")

    assert fixture_calls == 1
    assert injury_calls == 1
    assert first == second
    assert second["available"] is True
    assert len(second["home"]) == 1
