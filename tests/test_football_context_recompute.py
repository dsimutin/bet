from __future__ import annotations

from datetime import date

import pandas as pd

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


def test_context_adjustment_uses_event_date_not_scan_date(monkeypatch, tmp_path) -> None:
    import src.signals.football_runtime_scan as scan
    import src.ingest.live_odds_adapter as adapter_module
    import src.models.production_signal_engine as engine_module
    import src.features.match_context as context_module

    captured: dict[str, date] = {}

    class FakeAdapter:
        def __init__(self, **kwargs):
            pass

        def convert(self, raw_events):
            return type(
                "Result",
                (),
                {
                    "dataframe": pd.DataFrame(
                        [
                            {
                                "Date": "10/06/2026",
                                "HomeTeam": "Arsenal",
                                "AwayTeam": "Chelsea",
                            }
                        ]
                    )
                },
            )()

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def generate_signals(self, candidates):
            return [
                {
                    "signal_id": "s1",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "selection": "home",
                    "entry_odds": 2.0,
                    "model_probability": 0.55,
                    "event_time_utc": "2026-06-10T18:30:00+00:00",
                }
            ]

    def fake_context(**kwargs):
        captured["match_date"] = kwargs["match_date"]
        return {"prob_adj_home": 0.0, "prob_adj_away": 0.0}

    monkeypatch.setattr(scan, "get_football_h2h_odds", lambda *_args: [{"id": "evt"}])
    monkeypatch.setattr(adapter_module, "LiveOddsFootballDataAdapter", FakeAdapter)
    monkeypatch.setattr(engine_module, "ProductionDixonColesSignalEngine", FakeEngine)
    monkeypatch.setattr(context_module, "compute_match_context", fake_context)

    scan.generate_football_signals_runtime(
        model=object(),
        league="EPL",
        scan_date=date(2026, 6, 3),
        staging_dir=tmp_path,
        odds_api_key="test-key",
    )

    assert captured["match_date"] == date(2026, 6, 10)


def test_context_adjustment_can_downgrade_priority_signal() -> None:
    adjusted = apply_context_to_signal(
        {
            "selection": "home",
            "entry_odds": 2.0,
            "model_probability": 0.53,
            "market_probability": 0.5,
            "recommendation_tier": "priority",
        },
        {"prob_adj_home": -0.08, "prob_adj_away": 0.0},
    )

    assert adjusted["edge_pct"] < 0
    assert adjusted["stake_units"] == 0.0


def test_injuries_are_marked_informational_only() -> None:
    adjusted = apply_context_to_signal(
        {"selection": "draw", "entry_odds": 3.0, "model_probability": 0.34},
        {"prob_adj_home": 0.0, "prob_adj_away": 0.0},
    )

    assert adjusted["injuries_context_mode"] == "informational_only"
