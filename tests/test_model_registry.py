from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.models.calibrator import ProbabilityCalibrator
from src.models.dixon_coles import DixonColesConfig, DixonColesModel
from src.models.model_registry import ModelRegistry
from src.models.trainer import DailyTrainer


def _matches(rounds: int = 8) -> pd.DataFrame:
    start = date(2025, 1, 1)
    rows = []
    for idx in range(rounds):
        day = start + timedelta(days=idx)
        rows.extend(
            [
                {
                    "date": day,
                    "home_team": "A",
                    "away_team": "B",
                    "home_goals": 2,
                    "away_goals": 0,
                },
                {
                    "date": day,
                    "home_team": "C",
                    "away_team": "D",
                    "home_goals": 1,
                    "away_goals": 1,
                },
                {
                    "date": day,
                    "home_team": "B",
                    "away_team": "A",
                    "home_goals": 0,
                    "away_goals": 1,
                },
            ]
        )
    return pd.DataFrame(rows)


def _model() -> DixonColesModel:
    model = DixonColesModel(
        DixonColesConfig(league="EPL", min_matches=12, max_iterations=50, max_goals=6)
    )
    model.fit(_matches(), warm_start=False)
    return model


def test_save_load_roundtrip(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    model_id = registry.save(_model(), "EPL", metrics={"brier_score": 0.22})
    registry.promote(model_id)

    loaded = registry.load_latest("EPL")

    assert loaded.model_id == model_id
    assert loaded.params is not None
    assert loaded.params.dataset_hash.startswith("sha256:")


def test_latest_returns_promoted_only(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    candidate_id = registry.save(_model(), "EPL", metrics={"brier_score": 0.30})
    production_id = registry.save(_model(), "EPL", metrics={"brier_score": 0.20})
    registry.promote(production_id)

    loaded = registry.load_latest("EPL")

    assert candidate_id != production_id
    assert loaded.model_id == production_id


def test_version_metadata_complete(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    model_id = registry.save(_model(), "EPL", metrics={"brier_score": 0.22, "log_loss": 0.98})

    version = registry.list_versions("EPL")[0]

    assert version.model_id == model_id
    assert version.n_matches == len(_matches())
    assert version.dataset_hash.startswith("sha256:")
    assert version.brier_score == 0.22
    assert version.log_loss == 0.98


def test_registry_saves_and_loads_calibrator(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    calibrator = ProbabilityCalibrator()
    model_id = registry.save(_model(), "EPL", calibrator=calibrator)
    registry.promote(model_id)

    model, loaded_calibrator = registry.load_latest_with_calibrator("EPL")

    assert model.model_id == model_id
    assert loaded_calibrator is not None
    assert loaded_calibrator.params.temperature == calibrator.params.temperature


def test_daily_trainer_saves_and_promotes(tmp_path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    _matches(rounds=10).to_csv(staging / "EPL.csv", index=False)
    registry = ModelRegistry(tmp_path / "models")
    trainer = DailyTrainer(
        registry=registry,
        staging_dir=staging,
        config=DixonColesConfig(league="EPL", min_matches=12, max_iterations=40, max_goals=6),
    )

    result = trainer.run("EPL", cutoff_date=date(2025, 12, 31))

    assert result.promoted is True
    assert result.n_new_matches == len(_matches(rounds=10))
    assert result.log_loss >= 0.0
    assert result.promotion_reason
    assert registry.load_latest("EPL").model_id == result.model_id


def test_daily_trainer_does_not_promote_when_absolute_brier_gate_fails(tmp_path) -> None:
    registry = ModelRegistry(tmp_path / "models")
    trainer = DailyTrainer(
        registry=registry,
        config=DixonColesConfig(league="EPL", min_matches=12, max_iterations=20, max_goals=6),
        max_brier_score=0.0,
    )

    result = trainer.run_on_dataframe("EPL", cutoff_date=date(2025, 12, 31), matches=_matches(20))
    versions = registry.list_versions("EPL")

    assert result.promoted is False
    assert "max_brier_score" in result.promotion_reason
    assert versions[0].status == "candidate"


def test_daily_trainer_does_not_promote_when_log_loss_gate_fails(tmp_path) -> None:
    registry = ModelRegistry(tmp_path / "models")
    trainer = DailyTrainer(
        registry=registry,
        config=DixonColesConfig(league="EPL", min_matches=12, max_iterations=20, max_goals=6),
        max_brier_score=2.0,
        max_log_loss=0.0,
    )

    result = trainer.run_on_dataframe("EPL", cutoff_date=date(2025, 12, 31), matches=_matches(20))

    assert result.promoted is False
    assert "max_log_loss" in result.promotion_reason


def test_daily_trainer_oos_validation_requires_pre_holdout_training_data(tmp_path) -> None:
    trainer = DailyTrainer(
        registry=ModelRegistry(tmp_path / "models"),
        config=DixonColesConfig(league="EPL", min_matches=12, max_iterations=20, max_goals=6),
    )
    matches = DixonColesModel.prepare_matches(_matches(rounds=20))

    score, log_loss, calibrator = trainer._validate_oos(matches, league="EPL", weeks=1)

    assert score >= 0.0
    assert score < 2.0
    assert log_loss >= 0.0
    assert calibrator.params.temperature > 0.0
