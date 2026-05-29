from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.models.calibrator import ProbabilityCalibrator
from src.models.dixon_coles import DixonColesConfig, DixonColesModel
from src.models.production_signal_engine import ProductionDixonColesSignalEngine


def _history(rounds: int = 10) -> pd.DataFrame:
    start = date(2025, 1, 1)
    rows = []
    for idx in range(rounds):
        day = start + timedelta(days=idx)
        rows.extend(
            [
                {
                    "date": day,
                    "home_team": "Strong",
                    "away_team": "Weak",
                    "home_goals": 3,
                    "away_goals": 0,
                },
                {
                    "date": day,
                    "home_team": "Weak",
                    "away_team": "Strong",
                    "home_goals": 0,
                    "away_goals": 2,
                },
                {
                    "date": day,
                    "home_team": "Average",
                    "away_team": "Poor",
                    "home_goals": 2,
                    "away_goals": 1,
                },
            ]
        )
    return pd.DataFrame(rows)


def test_production_signal_engine_generates_model_value_signal() -> None:
    model = DixonColesModel(
        DixonColesConfig(league="EPL", min_matches=12, max_iterations=60, max_goals=7)
    )
    model.fit(_history(), warm_start=False)
    model.model_id = "dc_EPL_test"
    upcoming = pd.DataFrame(
        [
            {
                "Date": "20/02/2025",
                "HomeTeam": "Strong",
                "AwayTeam": "Weak",
                "B365H": 2.20,
                "B365D": 3.40,
                "B365A": 3.80,
            }
        ]
    )

    signals = ProductionDixonColesSignalEngine(
        model,
        min_edge_pct=1.0,
        min_model_probability=0.4,
        bookmaker_prefix="B365",
    ).generate_signals(upcoming)

    assert signals
    assert signals[0]["strategy_id"] == "production_dixon_coles_value_v1"
    assert signals[0]["model_id"] == "dc_EPL_test"
    assert signals[0]["model_probability"] > signals[0]["fair_market_probability"]
    assert signals[0]["edge_vs_fair_pct"] > 1.0
    assert 0.0 < signals[0]["paper_stake_units"] <= 2.0
    assert signals[0]["stake_units"] == signals[0]["paper_stake_units"]


def test_production_signal_engine_uses_calibrator() -> None:
    model = DixonColesModel(
        DixonColesConfig(league="EPL", min_matches=12, max_iterations=60, max_goals=7)
    )
    model.fit(_history(), warm_start=False)
    calibrator = ProbabilityCalibrator()
    calibrator.params = calibrator.params.__class__(
        temperature=1.0,
        bias=(1.0, -0.5, -0.5),
    )
    upcoming = pd.DataFrame(
        [
            {
                "Date": "20/02/2025",
                "HomeTeam": "Strong",
                "AwayTeam": "Weak",
                "B365H": 2.20,
                "B365D": 3.40,
                "B365A": 3.80,
            }
        ]
    )

    raw_signal = ProductionDixonColesSignalEngine(
        model,
        min_edge_pct=-100.0,
        min_model_probability=0.0,
    ).generate_signals(upcoming)[0]
    calibrated_signal = ProductionDixonColesSignalEngine(
        model,
        min_edge_pct=-100.0,
        min_model_probability=0.0,
        calibrator=calibrator,
    ).generate_signals(upcoming)[0]

    assert calibrated_signal["model_probability"] != raw_signal["model_probability"]


def test_production_signal_id_is_stable_across_model_versions() -> None:
    model = DixonColesModel(
        DixonColesConfig(league="EPL", min_matches=12, max_iterations=60, max_goals=7)
    )
    model.fit(_history(), warm_start=False)
    upcoming = pd.DataFrame(
        [
            {
                "Date": "20/02/2025",
                "HomeTeam": "Strong",
                "AwayTeam": "Weak",
                "B365H": 2.20,
                "B365D": 3.40,
                "B365A": 3.80,
            }
        ]
    )

    model.model_id = "dc_EPL_v1"
    first = ProductionDixonColesSignalEngine(
        model,
        min_edge_pct=1.0,
        min_model_probability=0.4,
    ).generate_signals(upcoming)[0]
    model.model_id = "dc_EPL_v2"
    second = ProductionDixonColesSignalEngine(
        model,
        min_edge_pct=1.0,
        min_model_probability=0.4,
    ).generate_signals(upcoming)[0]

    assert first["signal_id"] == second["signal_id"]
    assert first["model_id"] != second["model_id"]
