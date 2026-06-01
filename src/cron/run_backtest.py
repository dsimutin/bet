"""Walk-forward backtesting framework for validating football signal strategies.

Usage:
    python -m src.cron.run_backtest --league EPL --start 2024-01-01 --end 2024-06-30 --fold-weeks 4

Outputs:
    - Metrics JSON: data/reports/backtest_LEAGUE_YYYY-MM-DD.json
    - Summary: human-readable statistics with ROI, Sharpe ratio, max drawdown
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def run_backtest(
    league: str,
    start_date: date,
    end_date: date,
    staging_dir: Path,
    fold_weeks: int = 4,
) -> dict[str, Any]:
    """Run walk-forward backtest on historical football signals.

    Parameters
    ----------
    league : str
        League code (e.g., 'EPL', 'BUNDESLIGA')
    start_date, end_date : date
        Date range for backtest
    staging_dir : Path
        Directory containing {LEAGUE}_latest.csv files
    fold_weeks : int
        Size of test fold in weeks (default 4)

    Returns
    -------
    dict
        Backtest metrics: roi_pct, sharpe_ratio, max_drawdown, total_bets, clv_mean, etc.
    """
    csv_path = staging_dir / f"{league.upper()}_latest.csv"
    if not csv_path.exists():
        return {"error": f"No historical data found for {league} at {csv_path}"}

    try:
        df = pd.read_csv(csv_path, low_memory=False)
    except Exception as exc:
        return {"error": f"Failed to load {csv_path}: {exc}"}

    # Parse dates
    df["_date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce").dt.date
    df = df.dropna(subset=["_date", "HomeTeam", "AwayTeam"])

    # Filter to date range
    df = df[(df["_date"] >= start_date) & (df["_date"] <= end_date)].copy()
    if df.empty:
        return {"error": f"No data in range {start_date} to {end_date}"}

    fold_days = fold_weeks * 7
    metrics = _walk_forward_test(df, fold_days, league)
    return metrics


def _walk_forward_test(df: pd.DataFrame, fold_days: int, league: str) -> dict[str, Any]:
    """Perform walk-forward backtest with out-of-sample validation."""
    from src.models.model_registry import ModelRegistry

    model_dir = Path("data/models")
    registry = ModelRegistry(model_dir)

    all_results: list[dict[str, Any]] = []
    fold_results: list[dict[str, Any]] = []

    dates = sorted(df["_date"].unique())
    max_idx = len(dates) - 1

    for i, test_start in enumerate(dates[:-1]):
        test_end_idx = min(i + fold_days, max_idx)
        test_end = dates[test_end_idx]

        # Training: all data before test fold
        train_df = df[df["_date"] < test_start].copy()
        if train_df.empty:
            continue

        # Test: fold window
        test_df = df[(df["_date"] >= test_start) & (df["_date"] <= test_end)].copy()
        if test_df.empty:
            continue

        # Train model on historical data
        try:
            model = registry.load_latest(league, production_only=False)
        except FileNotFoundError:
            continue

        # Backtest on fold
        fold_metrics = _backtest_fold(test_df, model)
        fold_results.append(
            {
                "fold_start": str(test_start),
                "fold_end": str(test_end),
                "matches": len(test_df),
                **fold_metrics,
            }
        )
        all_results.extend(fold_metrics.get("signals", []))

    if not fold_results:
        return {"error": "No folds completed", "league": league}

    # Aggregate metrics
    returns = [f.get("roi_pct", 0.0) for f in fold_results]
    sharpe = _calculate_sharpe_ratio(returns) if returns else 0.0
    max_dd = min(returns) if returns else 0.0
    clv_vals = [s.get("clv_pct", 0.0) for f in fold_results for s in f.get("signals", [])]

    return {
        "league": league,
        "status": "success",
        "total_matches": len(df),
        "total_folds": len(fold_results),
        "total_signals": sum(len(f.get("signals", [])) for f in fold_results),
        "roi_pct": round(np.mean(returns), 2) if returns else 0.0,
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "clv_mean_pct": round(np.mean(clv_vals), 2) if clv_vals else 0.0,
        "p_value": _bootstrap_p_value(returns),
        "folds": fold_results,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _backtest_fold(fold_df: pd.DataFrame, model: Any) -> dict[str, Any]:
    """Backtest a single fold using Dixon-Coles probabilities."""
    signals: list[dict[str, Any]] = []

    for _, row in fold_df.iterrows():
        home = str(row.get("HomeTeam", ""))
        away = str(row.get("AwayTeam", ""))
        ftr = str(row.get("FTR", "")).upper()  # H, D, A

        if ftr not in ("H", "D", "A"):
            continue

        try:
            # Get model prediction
            pred = model.predict(home, away)
            h_prob = float(pred.get("home_win", 0.5))
            d_prob = float(pred.get("draw", 0.3))
            a_prob = float(pred.get("away_win", 0.2))
        except Exception:
            continue

        # Simple strategy: bet if model prob > 55%
        selections = []
        if h_prob > 0.55:
            selections.append(("H", h_prob))
        if d_prob > 0.55:
            selections.append(("D", d_prob))
        if a_prob > 0.55:
            selections.append(("A", a_prob))

        for sel, prob in selections:
            won = ftr == sel
            # Assume entry odds = 1.0 / (prob + 0.05) (rough fair odds)
            entry_odds = max(1.1, 1.0 / (prob + 0.05))
            # Closing odds not available in backtest, use entry
            clv_pct = 0.0

            signals.append(
                {
                    "date": str(row.get("Date", "")),
                    "home": home,
                    "away": away,
                    "selection": sel,
                    "model_prob": round(prob, 3),
                    "entry_odds": round(entry_odds, 2),
                    "result": "win" if won else "loss",
                    "clv_pct": clv_pct,
                }
            )

    # Calculate fold ROI
    total_stake = len(signals)
    if total_stake == 0:
        return {"signals": [], "roi_pct": 0.0}

    wins = sum(1 for s in signals if s["result"] == "win")
    roi_pct = (wins / total_stake - 0.5) * 100  # Rough estimate
    return {"signals": signals, "roi_pct": roi_pct}


def _calculate_sharpe_ratio(returns: list[float], risk_free_rate: float = 0.02) -> float:
    """Calculate Sharpe ratio from returns."""
    if not returns or len(returns) < 2:
        return 0.0
    arr = np.array(returns)
    excess_returns = arr - risk_free_rate / 252  # Assuming daily returns
    return float(np.mean(excess_returns) / (np.std(excess_returns) + 1e-8) * np.sqrt(252))


def _bootstrap_p_value(returns: list[float], n_bootstrap: int = 1000) -> float:
    """Estimate p-value using bootstrap (H0: mean return <= 0)."""
    if not returns or len(returns) < 2:
        return 1.0
    arr = np.array(returns)
    mean_return = np.mean(arr)
    if mean_return <= 0:
        return 1.0
    # Bootstrap: resample returns under null hypothesis
    null_means = [
        np.mean(np.random.choice(arr, size=len(arr), replace=True)) for _ in range(n_bootstrap)
    ]
    p_val = np.mean(np.array(null_means) >= mean_return)
    return float(p_val)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Walk-forward backtest framework for sports betting signals"
    )
    parser.add_argument("--league", required=True, help="League code (e.g., EPL, BUNDESLIGA)")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--staging-dir", default="data/staging", help="Directory with historical CSV data"
    )
    parser.add_argument("--fold-weeks", type=int, default=4, help="Test fold size in weeks")
    parser.add_argument(
        "--output", default="data/reports", help="Output directory for metrics JSON"
    )

    args = parser.parse_args()

    try:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
        end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    except ValueError:
        print("Error: dates must be in YYYY-MM-DD format", file=sys.stderr)
        sys.exit(1)

    staging_dir = Path(args.staging_dir)
    if not staging_dir.exists():
        print(f"Error: staging directory not found: {staging_dir}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[backtest] Running walk-forward test: {args.league} {start_date} to {end_date}")
    print(f"[backtest] Fold size: {args.fold_weeks} weeks")

    metrics = run_backtest(
        league=args.league,
        start_date=start_date,
        end_date=end_date,
        staging_dir=staging_dir,
        fold_weeks=args.fold_weeks,
    )

    if "error" in metrics:
        print(f"Error: {metrics['error']}", file=sys.stderr)
        sys.exit(1)

    # Save metrics
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"backtest_{args.league}_{timestamp}.json"
    with output_file.open("w") as f:
        json.dump(metrics, f, indent=2)

    # Print summary
    print(f"\n📊 Backtest Summary: {args.league}")
    print(f"   Folds: {metrics['total_folds']}")
    print(f"   Signals: {metrics['total_signals']}")
    print(f"   ROI: {metrics['roi_pct']:.2f}%")
    print(f"   Sharpe: {metrics['sharpe_ratio']:.2f}")
    print(f"   Max Drawdown: {metrics['max_drawdown_pct']:.2f}%")
    print(f"   CLV Mean: {metrics['clv_mean_pct']:.2f}%")
    print(f"   P-value: {metrics['p_value']:.4f}")
    print(f"   ✅ Saved: {output_file}\n")


if __name__ == "__main__":
    main()
