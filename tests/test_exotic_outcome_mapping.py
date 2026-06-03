from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.signals.exotic_zero_shot_scan import _best_h2h_odds, _score_event


def _event() -> dict:
    return {
        "id": "evt1",
        "home_team": "Home FC",
        "away_team": "Away United",
        "commence_time": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Draw", "price": 8.0},
                            {"name": "Away United", "price": 4.5},
                            {"name": "Home FC", "price": 1.6},
                        ],
                    }
                ],
            }
        ],
    }


def test_exotic_h2h_outcomes_are_mapped_by_name_not_position() -> None:
    home, draw, away, bookmaker = _best_h2h_odds(_event())

    assert home == 1.6
    assert draw == 8.0
    assert away == 4.5
    assert bookmaker == "Pinnacle"


def test_exotic_signal_uses_named_outcomes_and_remains_watchlist() -> None:
    signal = _score_event(
        _event(),
        "soccer_usa_mls",
        {"region": "americas", "league_code": "MLS", "name": "MLS"},
        {"home": 0.435, "draw": 0.270, "away": 0.295},
        datetime.now(timezone.utc),
    )

    assert signal is not None
    assert signal["selection"] == "draw"
    assert signal["entry_odds"] == 8.0
    assert signal["recommendation_tier"] == "watchlist"
    assert signal["confidence"] == "low"
    assert signal["experimental"] is True
    assert signal["feedback_eligible"] is False
