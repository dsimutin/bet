"""Regression tests for src/backtest/run_backtest.py and src/backtest/walk_forward.py.

These modules had 0% test coverage in the baseline run.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.backtest.run_backtest import (
    BacktestConfig,
    BacktestMetrics,
    BacktestTrade,
    WalkForwardBacktester,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_odds_df(n: int = 60) -> pd.DataFrame:
    """Synthetic odds DataFrame with required columns."""
    base = date(2024, 1, 1)
    rows = []
    for i in range(n):
        d = base + timedelta(days=i)
        for sel, odds in [("home", 2.1), ("draw", 3.2), ("away", 3.5)]:
            rows.append(
                {
                    "event_id": f"evt_{i:03d}",
                    "bookmaker": "B365",
                    "market_key": "h2h",
                    "selection": sel,
                    "odds": odds,
                    "match_date": d,
                }
            )
    return pd.DataFrame(rows)


def _make_results_df(n: int = 60) -> pd.DataFrame:
    """Synthetic results: home wins every other match."""
    rows = []
    for i in range(n):
        for sel, result in [("home", float(i % 2 == 0)), ("draw", 0.0), ("away", float(i % 2 == 1))]:
            rows.append({"event_id": f"evt_{i:03d}", "selection": sel, "result": result})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# BacktestConfig validation
# ---------------------------------------------------------------------------

def test_config_paper_trading_only_cannot_be_false():
    with pytest.raises(Exception):
        BacktestConfig(
            strategy_id="s1",
            sport="football",
            league="EPL",
            market_key="h2h",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
            paper_trading_only=False,
        )


def test_config_end_date_must_be_after_start():
    with pytest.raises(Exception):
        BacktestConfig(
            strategy_id="s1",
            sport="football",
            league="EPL",
            market_key="h2h",
            start_date=date(2024, 3, 31),
            end_date=date(2024, 1, 1),
        )


def test_config_valid():
    cfg = BacktestConfig(
        strategy_id="s1",
        sport="football",
        league="EPL",
        market_key="h2h",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 3, 31),
    )
    assert cfg.paper_trading_only is True
    assert cfg.fold_size_days == 30


# ---------------------------------------------------------------------------
# WalkForwardBacktester
# ---------------------------------------------------------------------------

def test_backtester_missing_odds_column_raises():
    bad_odds = _make_odds_df().drop(columns=["odds"])
    results = _make_results_df()
    cfg = BacktestConfig(
        strategy_id="s1", sport="football", league="EPL", market_key="h2h",
        start_date=date(2024, 1, 1), end_date=date(2024, 3, 1),
    )
    with pytest.raises(KeyError):
        WalkForwardBacktester(cfg, bad_odds, results)


def test_backtester_missing_results_column_raises():
    odds = _make_odds_df()
    bad_results = _make_results_df().drop(columns=["result"])
    cfg = BacktestConfig(
        strategy_id="s1", sport="football", league="EPL", market_key="h2h",
        start_date=date(2024, 1, 1), end_date=date(2024, 3, 1),
    )
    with pytest.raises(KeyError):
        WalkForwardBacktester(cfg, odds, bad_results)


def test_backtester_run_returns_trades_and_metrics():
    odds = _make_odds_df(60)
    results = _make_results_df(60)
    cfg = BacktestConfig(
        strategy_id="value_basic",
        sport="football",
        league="EPL",
        market_key="h2h",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 2, 29),
        fold_size_days=14,
        min_edge_pct=2.0,
    )
    tester = WalkForwardBacktester(cfg, odds, results)
    trades, metrics = tester.run()

    assert isinstance(trades, list)
    assert isinstance(metrics, BacktestMetrics)
    assert metrics.n_bets >= 0


def test_backtester_dataset_hash_reproducible():
    odds = _make_odds_df(30)
    results = _make_results_df(30)
    cfg = BacktestConfig(
        strategy_id="s1", sport="football", league="EPL", market_key="h2h",
        start_date=date(2024, 1, 1), end_date=date(2024, 2, 1),
    )
    bt1 = WalkForwardBacktester(cfg, odds.copy(), results.copy())
    bt2 = WalkForwardBacktester(cfg, odds.copy(), results.copy())
    assert bt1._odds_hash == bt2._odds_hash


def test_backtester_high_edge_threshold_produces_no_trades():
    odds = _make_odds_df(60)
    results = _make_results_df(60)
    cfg = BacktestConfig(
        strategy_id="s1", sport="football", league="EPL", market_key="h2h",
        start_date=date(2024, 1, 1), end_date=date(2024, 2, 29),
        fold_size_days=14,
        min_edge_pct=999.0,
    )
    tester = WalkForwardBacktester(cfg, odds, results)
    trades, metrics = tester.run()
    assert len(trades) == 0
    assert metrics.n_bets == 0


def test_backtester_fold_dates_never_overlap():
    odds = _make_odds_df(120)
    results = _make_results_df(120)
    cfg = BacktestConfig(
        strategy_id="s1", sport="football", league="EPL", market_key="h2h",
        start_date=date(2024, 1, 1), end_date=date(2024, 4, 30),
        fold_size_days=30,
    )
    tester = WalkForwardBacktester(cfg, odds, results)
    folds = tester._compute_folds()
    for i in range(1, len(folds)):
        prev_test_end = folds[i - 1][3]
        curr_test_start = folds[i][2]
        assert curr_test_start > prev_test_end, (
            f"Fold {i} test_start {curr_test_start} overlaps fold {i-1} test_end {prev_test_end}"
        )


def test_backtest_trade_schema():
    trade = BacktestTrade(
        strategy_id="s1",
        event_id="evt_001",
        bookmaker="B365",
        market_key="h2h",
        selection="home",
        entry_odds=2.1,
        reference_fair_odds=1.9,
        edge_pct=10.5,
        bet_date=date(2024, 1, 15),
        match_date=date(2024, 1, 15),
    )
    assert trade.entry_odds == 2.1
    assert trade.edge_pct == 10.5
    assert trade.result is None


# ---------------------------------------------------------------------------
# walk_forward module smoke
# ---------------------------------------------------------------------------

def test_walk_forward_brier_log_loss_helper():
    from src.backtest.walk_forward import _brier_log_loss
    brier, ll = _brier_log_loss([0.5, 0.5], [1, 0])
    assert abs(brier - 0.25) < 1e-6
    assert ll > 0


def test_walk_forward_brier_perfect_prediction():
    from src.backtest.walk_forward import _brier_log_loss
    brier, _ = _brier_log_loss([1.0, 0.0], [1, 0])
    assert brier < 1e-9


def test_walk_forward_brier_random_prediction():
    from src.backtest.walk_forward import _brier_log_loss
    brier, _ = _brier_log_loss([0.5] * 100, [1] * 50 + [0] * 50)
    assert abs(brier - 0.25) < 0.01


def test_walk_forward_empty_input():
    from src.backtest.walk_forward import _brier_log_loss
    import math
    brier, ll = _brier_log_loss([], [])
    assert math.isnan(brier)
    assert math.isnan(ll)
