from __future__ import annotations

import argparse
import json

from src.models.run_historical_value_model import (
    _ledger_delivery_gate,
    _model_quality_gate,
    _production_model_gate,
)
from src.models.signal_ledger import SignalLedger


def _args(require_ledger_quality: bool) -> argparse.Namespace:
    return argparse.Namespace(
        require_ledger_quality=require_ledger_quality,
        min_ledger_settled=2,
        min_ledger_win_rate=0.55,
        min_ledger_roi_pct=0.0,
    )


def _model_args(tmp_path, require_model_quality: bool) -> argparse.Namespace:
    return argparse.Namespace(
        require_model_quality=require_model_quality,
        benchmark_report=tmp_path / "benchmark.json",
        model_quality_scope="latest_window",
        model_quality_candidates=None,
        model_quality_mode="all",
        consensus=False,
        production_dixon_coles=False,
        output_dir=tmp_path,
        min_benchmark_predictions=2,
        min_brier_improvement=0.0,
        min_log_loss_improvement=0.0,
    )


def _production_args(tmp_path) -> argparse.Namespace:
    return argparse.Namespace(
        production_model_dir=tmp_path / "models",
        production_league="EPL",
        min_edge_pct=2.0,
        min_signal_probability=0.45,
        max_entry_odds=None,
        max_signals=10,
        bookmaker_prefix="B365",
    )


def _signal(signal_id: str) -> dict:
    return {
        "signal_id": signal_id,
        "strategy_id": "consensus_value_poisson_v1",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "bookmaker": "bet365",
        "market_key": "h2h",
        "selection": "home",
        "entry_odds": 1.8,
        "reference_fair_odds": 1.7,
        "edge_pct": 5.88,
        "timestamp_utc": "2026-05-27T12:00:00+00:00",
        "status": "paper",
        "dataset_hash": "sha256:abc123test",
    }


def _poor_delivered_ledger() -> SignalLedger:
    ledger = SignalLedger()
    ledger.add_signal(_signal("sig_win"))
    ledger.add_signal(_signal("sig_loss"))
    ledger.mark_delivery("sig_win", status="sent")
    ledger.mark_delivery("sig_loss", status="sent")
    ledger.update_result("sig_win", result="win")
    ledger.update_result("sig_loss", result="loss")
    return ledger


def test_ledger_delivery_gate_blocks_when_enforced_and_quality_is_poor() -> None:
    report = _ledger_delivery_gate(_poor_delivered_ledger(), _args(require_ledger_quality=True))

    assert report["enforced"] is True
    assert report["passed"] is False
    assert "win_rate" in report["reason"]


def test_ledger_delivery_gate_reports_but_does_not_block_when_not_enforced() -> None:
    report = _ledger_delivery_gate(_poor_delivered_ledger(), _args(require_ledger_quality=False))

    assert report["enforced"] is False
    assert report["passed"] is True
    assert report["summary"]["win_rate"] == 0.5


def test_model_quality_gate_blocks_when_enforced_and_model_loses_to_market(tmp_path) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text(
        json.dumps(
            {
                "overall_metrics": [
                    {
                        "model_name": "market_implied",
                        "n_predictions": 10,
                        "brier_score": 0.58,
                        "log_loss": 0.98,
                    },
                    {
                        "model_name": "historical_calibration",
                        "n_predictions": 10,
                        "brier_score": 0.59,
                        "log_loss": 1.01,
                    },
                ],
                "windows": [
                    {
                        "metrics": [
                            {
                                "model_name": "market_implied",
                                "n_predictions": 10,
                                "brier_score": 0.58,
                                "log_loss": 0.98,
                            },
                            {
                                "model_name": "historical_calibration",
                                "n_predictions": 10,
                                "brier_score": 0.59,
                                "log_loss": 1.01,
                            },
                        ]
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = _model_quality_gate(_model_args(tmp_path, require_model_quality=True))

    assert report["enforced"] is True
    assert report["passed"] is False


def test_model_quality_gate_reports_but_does_not_block_when_not_enforced(tmp_path) -> None:
    report = _model_quality_gate(_model_args(tmp_path, require_model_quality=False))

    assert report["enforced"] is False
    assert report["passed"] is True
    assert "benchmark_not_found" in report["reason"]


def test_model_quality_gate_defaults_to_consensus_models(tmp_path) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text(
        json.dumps(
            {
                "overall_metrics": [
                    {
                        "model_name": "market_implied",
                        "n_predictions": 10,
                        "brier_score": 0.58,
                        "log_loss": 0.98,
                    },
                    {
                        "model_name": "historical_calibration",
                        "n_predictions": 10,
                        "brier_score": 0.55,
                        "log_loss": 0.94,
                    },
                    {
                        "model_name": "poisson_team_strength",
                        "n_predictions": 10,
                        "brier_score": 0.62,
                        "log_loss": 1.05,
                    },
                ],
                "windows": [
                    {
                        "metrics": [
                            {
                                "model_name": "market_implied",
                                "n_predictions": 10,
                                "brier_score": 0.58,
                                "log_loss": 0.98,
                            },
                            {
                                "model_name": "historical_calibration",
                                "n_predictions": 10,
                                "brier_score": 0.55,
                                "log_loss": 0.94,
                            },
                            {
                                "model_name": "poisson_team_strength",
                                "n_predictions": 10,
                                "brier_score": 0.62,
                                "log_loss": 1.05,
                            },
                        ]
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    args = _model_args(tmp_path, require_model_quality=True)
    args.consensus = True

    report = _model_quality_gate(args)

    assert report["enforced"] is True
    assert report["passed"] is False
    assert report["allowed_models"] == ["historical_calibration"]
    assert report["reason"] == "not_all_required_models_beat_market_baseline"


def test_production_model_gate_blocks_without_promoted_model(tmp_path) -> None:
    engine, report = _production_model_gate(_production_args(tmp_path))

    assert engine is None
    assert report["passed"] is False
    assert report["reason"] == "no_promoted_production_model"
    assert report["league"] == "EPL"
