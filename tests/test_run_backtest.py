"""Tests for walk-forward backtesting framework."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.cron.run_backtest import run_backtest, _calculate_sharpe_ratio, _bootstrap_p_value


class TestBacktestFramework:
    """Test walk-forward backtesting pipeline."""

    def test_backtest_returns_error_for_missing_data(self, tmp_path: Path) -> None:
        """Should return error dict when CSV not found."""
        result = run_backtest(
            league="NONEXISTENT",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 30),
            staging_dir=tmp_path,
            fold_weeks=4,
        )
        assert "error" in result
        assert "No historical data found" in result["error"]

    def test_backtest_handles_empty_date_range(self, tmp_path: Path) -> None:
        """Should return error when date range has no data."""
        # Create empty CSV with correct columns
        csv_path = tmp_path / "EPL_latest.csv"
        df = pd.DataFrame(
            {
                "Date": ["2023-01-01"],
                "HomeTeam": ["Arsenal"],
                "AwayTeam": ["Man United"],
                "FTR": ["H"],
            }
        )
        df.to_csv(csv_path, index=False)

        result = run_backtest(
            league="EPL",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 30),
            staging_dir=tmp_path,
            fold_weeks=4,
        )
        assert "error" in result
        assert "No data in range" in result["error"]

    def test_backtest_runs_with_valid_data(self, tmp_path: Path) -> None:
        """Should run backtest and return metrics for valid data."""
        # Create sample football data
        csv_path = tmp_path / "EPL_latest.csv"
        dates = pd.date_range("2024-01-01", periods=50, freq="D")
        df = pd.DataFrame(
            {
                "Date": dates.strftime("%d/%m/%Y"),
                "HomeTeam": ["Arsenal"] * 50,
                "AwayTeam": ["Man United"] * 50,
                "FTR": ["H", "D", "A"] * 16 + ["H", "H"],  # Rotate results
            }
        )
        df.to_csv(csv_path, index=False)

        result = run_backtest(
            league="EPL",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 29),
            staging_dir=tmp_path,
            fold_weeks=2,
        )

        assert result["status"] == "success"
        assert result["league"] == "EPL"
        assert isinstance(result["roi_pct"], (int, float))
        assert isinstance(result["sharpe_ratio"], (int, float))
        assert isinstance(result["max_drawdown_pct"], (int, float))
        assert isinstance(result["clv_mean_pct"], (int, float))
        assert "p_value" in result
        assert "generated_at_utc" in result

    def test_sharpe_ratio_calculation(self) -> None:
        """Should calculate Sharpe ratio correctly."""
        returns = [0.01, 0.02, -0.01, 0.03, 0.005]
        sharpe = _calculate_sharpe_ratio(returns)
        assert isinstance(sharpe, float)
        assert sharpe >= 0.0

    def test_sharpe_ratio_empty_list(self) -> None:
        """Should return 0.0 for empty or too-short list."""
        assert _calculate_sharpe_ratio([]) == 0.0
        assert _calculate_sharpe_ratio([0.01]) == 0.0

    def test_bootstrap_p_value(self) -> None:
        """Should estimate p-value using bootstrap."""
        returns = [0.01, 0.02, 0.015, 0.005, 0.03]
        p_val = _bootstrap_p_value(returns, n_bootstrap=100)
        assert 0.0 <= p_val <= 1.0

    def test_bootstrap_p_value_negative_returns(self) -> None:
        """Should return 1.0 for negative mean returns."""
        returns = [-0.01, -0.02, -0.015]
        p_val = _bootstrap_p_value(returns)
        assert p_val == 1.0

    def test_backtest_metrics_structure(self, tmp_path: Path) -> None:
        """Should return properly structured metrics dict."""
        csv_path = tmp_path / "BUNDESLIGA_latest.csv"
        dates = pd.date_range("2024-01-01", periods=100, freq="D")
        df = pd.DataFrame(
            {
                "Date": dates.strftime("%d/%m/%Y"),
                "HomeTeam": ["Bayern"] * 100,
                "AwayTeam": ["Dortmund"] * 100,
                "FTR": (["H", "D", "A"] * 33) + ["H"],
            }
        )
        df.to_csv(csv_path, index=False)

        result = run_backtest(
            league="BUNDESLIGA",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
            staging_dir=tmp_path,
            fold_weeks=4,
        )

        required_keys = [
            "league",
            "status",
            "total_matches",
            "total_folds",
            "total_signals",
            "roi_pct",
            "sharpe_ratio",
            "max_drawdown_pct",
            "clv_mean_pct",
            "p_value",
            "generated_at_utc",
        ]
        for key in required_keys:
            assert key in result, f"Missing required key: {key}"
