from __future__ import annotations

import json

from src.models.model_quality_gate import ModelQualityGate


def _write_benchmark(tmp_path, metrics: list[dict]) -> object:
    path = tmp_path / "benchmark.json"
    path.write_text(
        json.dumps({"overall_metrics": metrics}, indent=2),
        encoding="utf-8",
    )
    return path


def _write_windowed_benchmark(tmp_path) -> object:
    path = tmp_path / "benchmark.json"
    path.write_text(
        json.dumps(
            {
                "overall_metrics": [
                    {
                        "model_name": "market_implied",
                        "n_predictions": 200,
                        "brier_score": 0.58,
                        "log_loss": 0.98,
                    },
                    {
                        "model_name": "historical_calibration",
                        "n_predictions": 200,
                        "brier_score": 0.55,
                        "log_loss": 0.94,
                    },
                ],
                "windows": [
                    {
                        "window_name": "old",
                        "metrics": [
                            {
                                "model_name": "market_implied",
                                "n_predictions": 100,
                                "brier_score": 0.59,
                                "log_loss": 0.99,
                            },
                            {
                                "model_name": "historical_calibration",
                                "n_predictions": 100,
                                "brier_score": 0.54,
                                "log_loss": 0.93,
                            },
                        ],
                    },
                    {
                        "window_name": "latest",
                        "metrics": [
                            {
                                "model_name": "market_implied",
                                "n_predictions": 100,
                                "brier_score": 0.57,
                                "log_loss": 0.97,
                            },
                            {
                                "model_name": "historical_calibration",
                                "n_predictions": 100,
                                "brier_score": 0.60,
                                "log_loss": 1.03,
                            },
                        ],
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def test_model_quality_gate_allows_model_that_beats_market(tmp_path) -> None:
    path = _write_benchmark(
        tmp_path,
        [
            {
                "model_name": "market_implied",
                "n_predictions": 200,
                "brier_score": 0.58,
                "log_loss": 0.98,
            },
            {
                "model_name": "historical_calibration",
                "n_predictions": 200,
                "brier_score": 0.55,
                "log_loss": 0.94,
            },
        ],
    )

    report = ModelQualityGate(
        path,
        candidate_models=["historical_calibration"],
        min_predictions=100,
        scope="overall",
    ).evaluate()

    assert report["passed"] is True
    assert report["allowed_models"] == ["historical_calibration"]
    assert report["candidates"][0]["brier_improvement"] > 0


def test_model_quality_gate_blocks_when_no_model_beats_market(tmp_path) -> None:
    path = _write_benchmark(
        tmp_path,
        [
            {
                "model_name": "market_implied",
                "n_predictions": 200,
                "brier_score": 0.58,
                "log_loss": 0.98,
            },
            {
                "model_name": "historical_calibration",
                "n_predictions": 200,
                "brier_score": 0.59,
                "log_loss": 1.02,
            },
        ],
    )

    report = ModelQualityGate(
        path,
        candidate_models=["historical_calibration"],
        min_predictions=100,
        scope="overall",
    ).evaluate()

    assert report["passed"] is False
    assert report["allowed_models"] == []
    assert "not_all_required_models_beat_market_baseline" == report["reason"]


def test_model_quality_gate_blocks_missing_benchmark(tmp_path) -> None:
    report = ModelQualityGate(tmp_path / "missing.json").evaluate()

    assert report["passed"] is False
    assert report["allowed_models"] == []
    assert "benchmark_not_found" in report["reason"]


def test_model_quality_gate_defaults_to_latest_window(tmp_path) -> None:
    path = _write_windowed_benchmark(tmp_path)

    report = ModelQualityGate(
        path,
        candidate_models=["historical_calibration"],
        min_predictions=100,
    ).evaluate()

    assert report["scope"] == "latest_window"
    assert report["passed"] is False
    assert report["allowed_models"] == []


def test_model_quality_gate_can_use_overall_scope(tmp_path) -> None:
    path = _write_windowed_benchmark(tmp_path)

    report = ModelQualityGate(
        path,
        candidate_models=["historical_calibration"],
        min_predictions=100,
        scope="overall",
    ).evaluate()

    assert report["scope"] == "overall"
    assert report["passed"] is True
    assert report["allowed_models"] == ["historical_calibration"]


def test_model_quality_gate_all_mode_requires_every_candidate(tmp_path) -> None:
    path = _write_benchmark(
        tmp_path,
        [
            {
                "model_name": "market_implied",
                "n_predictions": 200,
                "brier_score": 0.58,
                "log_loss": 0.98,
            },
            {
                "model_name": "historical_calibration",
                "n_predictions": 200,
                "brier_score": 0.55,
                "log_loss": 0.94,
            },
            {
                "model_name": "poisson_team_strength",
                "n_predictions": 200,
                "brier_score": 0.62,
                "log_loss": 1.05,
            },
        ],
    )

    report = ModelQualityGate(
        path,
        candidate_models=["historical_calibration", "poisson_team_strength"],
        min_predictions=100,
        scope="overall",
        mode="all",
    ).evaluate()

    assert report["passed"] is False
    assert report["allowed_models"] == ["historical_calibration"]
    assert report["reason"] == "not_all_required_models_beat_market_baseline"


def test_model_quality_gate_any_mode_allows_one_candidate(tmp_path) -> None:
    path = _write_benchmark(
        tmp_path,
        [
            {
                "model_name": "market_implied",
                "n_predictions": 200,
                "brier_score": 0.58,
                "log_loss": 0.98,
            },
            {
                "model_name": "historical_calibration",
                "n_predictions": 200,
                "brier_score": 0.55,
                "log_loss": 0.94,
            },
            {
                "model_name": "poisson_team_strength",
                "n_predictions": 200,
                "brier_score": 0.62,
                "log_loss": 1.05,
            },
        ],
    )

    report = ModelQualityGate(
        path,
        candidate_models=["historical_calibration", "poisson_team_strength"],
        min_predictions=100,
        scope="overall",
        mode="any",
    ).evaluate()

    assert report["passed"] is True
    assert report["allowed_models"] == ["historical_calibration"]
