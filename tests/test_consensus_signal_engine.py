from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.models.consensus_signal_engine import ConsensusConfig, ConsensusSignalEngine
from src.models.historical_value_model import HistoricalValueModel, HistoricalValueModelConfig
from src.models.poisson_team_model import PoissonTeamModel, PoissonTeamModelConfig


def _team_history(n_rounds: int = 80) -> pd.DataFrame:
    start = date(2025, 1, 1)
    teams = ["Alpha", "Beta", "Gamma", "Delta"]
    rows = []
    day = 0
    for idx in range(n_rounds):
        fixtures = [("Alpha", "Beta"), ("Gamma", "Delta"), ("Beta", "Gamma"), ("Delta", "Alpha")]
        home, away = fixtures[idx % len(fixtures)]
        home_strong = home == "Alpha" or away == "Delta"
        if home == "Alpha":
            fthg, ftag, ftr = 3, 0, "H"
            odds_h, odds_d, odds_a = 2.30, 3.40, 3.20
        elif away == "Alpha":
            fthg, ftag, ftr = 0, 2, "A"
            odds_h, odds_d, odds_a = 3.40, 3.30, 2.25
        elif home_strong:
            fthg, ftag, ftr = 2, 1, "H"
            odds_h, odds_d, odds_a = 2.05, 3.30, 3.60
        else:
            fthg, ftag, ftr = 1, 1, "D"
            odds_h, odds_d, odds_a = 2.70, 3.10, 2.80
        rows.append(
            {
                "Date": (start + timedelta(days=day)).strftime("%d/%m/%Y"),
                "HomeTeam": home,
                "AwayTeam": away,
                "FTHG": fthg,
                "FTAG": ftag,
                "FTR": ftr,
                "B365H": odds_h,
                "B365D": odds_d,
                "B365A": odds_a,
            }
        )
        day += 1
    return pd.DataFrame(rows)


def _upcoming_alpha_home() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Date": "01/06/2025",
                "HomeTeam": "Alpha",
                "AwayTeam": "Delta",
                "B365H": 2.35,
                "B365D": 3.40,
                "B365A": 3.30,
            }
        ]
    )


def test_poisson_model_predicts_probabilities_that_sum_to_one() -> None:
    model = PoissonTeamModel(PoissonTeamModelConfig(min_train_matches=20))
    history = model.prepare_matches(_team_history())
    model.fit(history)
    row = model.prepare_prediction_matches(_upcoming_alpha_home()).iloc[0]

    predictions = model.predict_match(row)

    assert len(predictions) == 3
    assert abs(sum(item.probability for item in predictions) - 1.0) < 1e-4
    assert max(predictions, key=lambda item: item.probability).selection == "home"


def test_consensus_engine_generates_telegram_ready_signal() -> None:
    engine = ConsensusSignalEngine(
        value_model=HistoricalValueModel(
            HistoricalValueModelConfig(
                min_train_matches=20,
                min_edge_pct=1.0,
                min_signal_probability=0.4,
                require_recent_quality=False,
                smoothing_alpha=5.0,
            )
        ),
        poisson_model=PoissonTeamModel(
            PoissonTeamModelConfig(
                min_train_matches=20,
                min_edge_pct=1.0,
                min_signal_probability=0.4,
            )
        ),
        config=ConsensusConfig(
            min_edge_pct=1.0,
            min_probability=0.4,
            require_quality_gates=False,
        ),
    )

    signals = engine.generate_signals(_team_history(), _upcoming_alpha_home())

    assert signals
    signal = signals[0]
    assert signal["strategy_id"] == "consensus_value_poisson_v1"
    assert signal["selection"] == "home"
    assert signal["status"] == "paper"
    assert signal["poisson_probability"] >= 0.4
    assert signal["value_probability"] >= 0.4
    assert "expected_home_goals" in signal


def test_consensus_quality_gate_can_block_signals() -> None:
    engine = ConsensusSignalEngine(
        value_model=HistoricalValueModel(
            HistoricalValueModelConfig(
                min_train_matches=20,
                require_recent_quality=False,
                min_quality_bets=999,
            )
        ),
        poisson_model=PoissonTeamModel(
            PoissonTeamModelConfig(
                min_train_matches=20,
                min_quality_bets=999,
            )
        ),
        config=ConsensusConfig(require_quality_gates=True),
    )

    gate = engine.quality_gate_report(_team_history())
    signals = engine.generate_signals(_team_history(), _upcoming_alpha_home())

    assert gate["passed"] is False
    assert signals == []


def test_consensus_engine_respects_max_entry_odds() -> None:
    engine = ConsensusSignalEngine(
        value_model=HistoricalValueModel(
            HistoricalValueModelConfig(
                min_train_matches=20,
                min_edge_pct=1.0,
                min_signal_probability=0.4,
                require_recent_quality=False,
                smoothing_alpha=5.0,
                max_signal_odds=1.85,
            )
        ),
        poisson_model=PoissonTeamModel(
            PoissonTeamModelConfig(
                min_train_matches=20,
                min_edge_pct=1.0,
                min_signal_probability=0.4,
                max_signal_odds=1.85,
            )
        ),
        config=ConsensusConfig(
            min_edge_pct=1.0,
            min_probability=0.4,
            max_entry_odds=1.85,
            require_quality_gates=False,
        ),
    )

    signals = engine.generate_signals(_team_history(), _upcoming_alpha_home())

    assert signals == []
