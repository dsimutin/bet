from __future__ import annotations

from src.web.today_picks import _format_pick, _trust_score


def _signal(**overrides):
    base = {
        "sport": "football",
        "home_team": "A",
        "away_team": "B",
        "selection": "home",
        "league": "EPL",
        "entry_odds": 2.1,
        "model_probability": 0.55,
        "fair_market_probability": 0.48,
        "edge_pct": 8.0,
        "reference_fair_odds": 1.82,
        "stake_units": 1.0,
        "event_time_utc": "2026-06-04T18:00:00+00:00",
        "timestamp_verification_status": "verified_pre_match",
        "odds_freshness_tier": "priority",
        "odds_snapshot_age_seconds": 120,
        "recommendation_tier": "priority",
        "market": "h2h",
    }
    base.update(overrides)
    return base


def test_pick_card_contains_actionable_fields() -> None:
    lines = _format_pick(_signal(), priority=True)
    text = "\n".join(lines)

    assert "🎯 ЧТО СТАВИТЬ:" in text
    assert "💰 Коэффициент:" in text
    assert "📊 Edge:" in text
    assert "🔍 Mostbet:" in text


def test_trust_score_penalizes_experimental_stale_signal() -> None:
    good = _trust_score(_signal())
    weak = _trust_score(
        _signal(
            timestamp_verification_status="post_start",
            odds_freshness_tier="stale_blocked",
            recommendation_tier="watchlist",
            experimental_market=True,
            market="totals",
        )
    )

    assert good >= 85
    assert weak < 50
