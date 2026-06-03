from __future__ import annotations

from src.features.match_context import apply_context_to_signal


def test_context_adjustment_recomputes_probability_edge_fair_odds_confidence_and_stake() -> None:
    signal = {
        "selection": "home",
        "entry_odds": 2.0,
        "model_probability": 0.52,
        "market_probability": 0.5,
        "edge_pct": 4.0,
        "reference_fair_odds": 1.923,
        "stake_units": 1.0,
    }
    adjusted = apply_context_to_signal(signal, {"prob_adj_home": -0.05, "prob_adj_away": 0.0})
    assert adjusted["model_probability"] == 0.47
    assert adjusted["reference_fair_odds"] == 2.1277
    assert adjusted["edge_pct"] == -6.0
    assert adjusted["edge_vs_market_pct"] == -3.0
    assert adjusted["stake_units"] == 0.0
    assert adjusted["injuries_context_mode"] == "informational_only"
