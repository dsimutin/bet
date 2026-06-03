from __future__ import annotations

from src.ingest.live_odds_adapter import LiveOddsFootballDataAdapter


def _live_event() -> dict:
    return {
        "id": "evt_live_1",
        "sport_key": "soccer_epl",
        "commence_time": "2026-06-01T18:30:00Z",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "last_update": "2026-05-27T12:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-05-27T12:00:00Z",
                        "outcomes": [
                            {"name": "Arsenal", "price": 1.8},
                            {"name": "Draw", "price": 3.6},
                            {"name": "Chelsea", "price": 4.5},
                        ],
                    }
                ],
            },
            {
                "key": "bet365",
                "title": "Bet365",
                "last_update": "2026-05-27T12:01:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-05-27T12:01:00Z",
                        "outcomes": [
                            {"name": "Arsenal", "price": 1.83},
                            {"name": "Draw", "price": 3.5},
                            {"name": "Chelsea", "price": 4.4},
                        ],
                    }
                ],
            },
        ],
    }


def test_live_odds_adapter_prefers_configured_bookmaker() -> None:
    adapter = LiveOddsFootballDataAdapter(
        bookmaker_prefix="B365",
        preferred_bookmakers=["bet365", "pinnacle"],
    )

    result = adapter.convert([_live_event()])

    assert result.converted_events == 1
    assert result.skipped_events == []
    row = result.dataframe.iloc[0]
    assert row["Date"] == "01/06/2026"
    assert row["HomeTeam"] == "Arsenal"
    assert row["AwayTeam"] == "Chelsea"
    assert row["B365H"] == 1.83
    assert row["B365D"] == 3.5
    assert row["B365A"] == 4.4
    assert row["source_event_id"] == "evt_live_1"
    assert row["source_bookmaker_key"] == "bet365"
    assert row["event_time_utc"] == "2026-06-01T18:30:00+00:00"
    assert row["snapshot_ts_utc"]
    assert row["source_last_update_utc"] == "2026-05-27T12:01:00Z"
    assert row["source_market_key"] == "h2h"


def test_live_odds_adapter_skips_incomplete_h2h_market() -> None:
    event = _live_event()
    event["bookmakers"][1]["markets"][0]["outcomes"] = [
        {"name": "Arsenal", "price": 1.83},
        {"name": "Chelsea", "price": 4.4},
    ]
    adapter = LiveOddsFootballDataAdapter(
        preferred_bookmakers=["bet365"],
        allow_bookmaker_fallback=False,
    )

    result = adapter.convert([event])

    assert result.converted_events == 0
    assert "no_complete_h2h_bookmaker" in result.skipped_events[0]


def test_live_adapter_rejects_invalid_commence_time() -> None:
    event = _live_event()
    event["commence_time"] = "not-a-date"
    result = LiveOddsFootballDataAdapter().convert([event])
    assert result.converted_events == 0
    assert "invalid_commence_time" in result.skipped_events[0]
