from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.models.dixon_coles import DixonColesConfig, DixonColesModel
from src.models.feature_builder import FeatureBuilder, MatchInfo
from src.models.predictor import ModelValuePredictor


def _matches(rounds: int = 12) -> pd.DataFrame:
    start = date(2025, 1, 1)
    teams = ["Strong", "Average", "Weak", "Poor"]
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
                    "home_team": "Average",
                    "away_team": "Poor",
                    "home_goals": 2,
                    "away_goals": 1,
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
                    "home_team": "Poor",
                    "away_team": "Average",
                    "home_goals": 1,
                    "away_goals": 1,
                },
            ]
        )
    assert set(teams)
    return pd.DataFrame(rows)


def _model() -> DixonColesModel:
    model = DixonColesModel(
        DixonColesConfig(
            league="EPL",
            min_matches=12,
            max_iterations=80,
            max_goals=7,
        )
    )
    model.fit(_matches(), warm_start=False)
    return model


def test_probabilities_sum_to_one() -> None:
    probs = _model().predict_1x2("Strong", "Weak")

    assert abs(sum(probs) - 1.0) < 1e-9
    assert all(0.0 <= item <= 1.0 for item in probs)


def test_home_advantage_positive() -> None:
    params = _model().params

    assert params is not None
    assert params.home_advantage > 0


def test_stronger_team_higher_win_prob() -> None:
    model = _model()
    strong_home = model.predict_1x2("Strong", "Weak")[0]
    weak_home = model.predict_1x2("Weak", "Strong")[0]

    assert strong_home > weak_home


def test_temporal_weights_decay() -> None:
    model = DixonColesModel(DixonColesConfig())
    today = date(2026, 1, 1)

    assert model.temporal_weight(today - timedelta(days=30), today) > model.temporal_weight(
        today - timedelta(days=365), today
    )


def test_no_lookahead_in_partial_fit() -> None:
    model = _model()
    original_hash = model.params.dataset_hash if model.params else ""
    old_rows = _matches(rounds=2)

    model.partial_fit(old_rows)

    assert model.params is not None
    assert model.params.dataset_hash == original_hash


def test_partial_fit_accepts_future_rows() -> None:
    """partial_fit must incorporate genuinely new matches and update hash/n_matches."""
    model = _model()  # trained on rounds=12, last match date 2025-01-12
    assert model.params is not None
    original_hash = model.params.dataset_hash
    original_n = model.params.n_matches

    # Rows AFTER training period — the day after the last training match
    last_date = model.params.trained_on_dates[1]
    start = last_date + timedelta(days=1)
    future_rows = pd.DataFrame(
        [
            {
                "date": start + timedelta(days=i),
                "home_team": "Strong",
                "away_team": "Weak",
                "home_goals": 2,
                "away_goals": 0,
            }
            for i in range(20)
        ]
    )
    model.partial_fit(future_rows)

    assert model.params is not None
    assert (
        model.params.dataset_hash != original_hash
    ), "dataset_hash must change when new future matches are incorporated"
    assert (
        model.params.n_matches > original_n
    ), "n_matches must increase after partial_fit with future data"


def test_partial_fit_accepts_unseen_same_day_rows() -> None:
    model = _model()
    assert model.params is not None
    original_n = model.params.n_matches
    same_day = model.params.trained_on_dates[1]
    new_rows = pd.DataFrame(
        [
            {
                "date": same_day,
                "home_team": "Strong",
                "away_team": "Average",
                "home_goals": 2,
                "away_goals": 1,
            }
        ]
        * 12
    )

    model.partial_fit(new_rows)

    assert model.params is not None
    assert model.params.n_matches > original_n


def test_dataset_hash_in_output() -> None:
    params = _model().params

    assert params is not None
    assert params.dataset_hash.startswith("sha256:")
    assert params.n_matches == len(_matches())


def test_predict_ou_and_btts_are_valid_probabilities() -> None:
    model = _model()

    assert abs(sum(model.predict_ou("Strong", "Weak", total=2.5)) - 1.0) < 1e-9
    assert abs(sum(model.predict_btts("Strong", "Weak")) - 1.0) < 1e-9


def test_feature_builder_uses_only_matches_before_cutoff() -> None:
    # Model must be trained on data that ends strictly before the cutoff.
    cutoff = pd.Timestamp("2025-01-05T12:00:00").to_pydatetime()
    matches_before_cutoff = _matches(rounds=4)  # 2025-01-01 through 2025-01-04
    model_pre_cutoff = DixonColesModel(
        DixonColesConfig(league="EPL", min_matches=12, max_iterations=80, max_goals=7)
    )
    model_pre_cutoff.fit(matches_before_cutoff, warm_start=False)
    features = FeatureBuilder(model_pre_cutoff, _matches()).build(
        MatchInfo("Strong", "Weak", date(2025, 1, 10)),
        cutoff_ts=cutoff,
    )

    assert features.home_attack > features.away_attack
    assert features.days_since_last_match_home == 1


def test_feature_builder_rejects_future_trained_model() -> None:
    with pytest.raises(ValueError, match="leak future-trained parameters"):
        FeatureBuilder(_model(), _matches()).build(
            MatchInfo("Strong", "Weak", date(2025, 1, 10)),
            cutoff_ts=pd.Timestamp("2025-01-05T12:00:00").to_pydatetime(),
        )


def test_model_value_predictor_uses_model_edge_not_arbitrage() -> None:
    model = _model()
    model.model_id = "dc_EPL_test"
    candidates = ModelValuePredictor(model).signals(
        "Strong",
        "Weak",
        odds_1x2=(2.2, 3.4, 3.8),
        min_edge_pct=1.0,
        min_model_prob=0.4,
    )

    assert candidates
    assert candidates[0].model_id == "dc_EPL_test"
    assert candidates[0].edge_vs_fair > 0.01
