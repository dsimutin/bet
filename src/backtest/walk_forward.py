"""Walk-forward backtest CLI with optional per-match retraining.

Usage:
    python -m src.backtest.walk_forward --league EPL --start-date 2023-08-01 --end-date 2024-05-31
    python -m src.backtest.walk_forward --league EPL --retrain-after-each-match --verbose
    python -m src.backtest.walk_forward --league EPL --fold-weeks 4 --output data/reports/wf_EPL.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FoldResult:
    fold_index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    n_train: int
    n_test: int
    brier: float | None
    log_loss: float | None
    retrained: bool
    retrain_matches: int
    elapsed_s: float


@dataclass
class WalkForwardResult:
    league: str
    start_date: str
    end_date: str
    fold_weeks: int
    retrain_after_each_match: bool
    n_folds: int
    folds: list[FoldResult]
    brier_mean: float | None
    brier_std: float | None
    log_loss_mean: float | None
    total_elapsed_s: float


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _load_matches(league: str, data_dir: Path) -> pd.DataFrame:
    """Load EPL-style CSVs from data/raw or data/staging."""
    frames = []

    # Try staging parquet first
    staging = data_dir / "staging" / league
    if staging.exists():
        for f in sorted(staging.glob("**/*.parquet")):
            try:
                frames.append(pd.read_parquet(f))
            except Exception:
                pass

    # Fall back to raw CSV
    raw = data_dir / "raw"
    if raw.exists():
        for f in sorted(raw.glob(f"**/{league}*.csv")) or sorted(raw.glob("**/*.csv")):
            try:
                frames.append(pd.read_csv(f, encoding="utf-8", on_bad_lines="skip"))
            except Exception:
                pass

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)

    # Normalise column names
    remap = {
        "Date": "match_date",
        "date": "match_date",
        "HomeTeam": "home_team",
        "AwayTeam": "away_team",
        "FTHG": "home_goals",
        "FTAG": "away_goals",
        "goals_home_ft": "home_goals",
        "goals_away_ft": "away_goals",
    }
    df = df.rename(columns={k: v for k, v in remap.items() if k in df.columns})

    required = {"match_date", "home_team", "away_team", "home_goals", "away_goals"}
    if not required.issubset(df.columns):
        missing = required - set(df.columns)
        print(f"[WARN] Missing columns: {missing}. Continuing without them.", file=sys.stderr)
        return pd.DataFrame()

    df["match_date"] = pd.to_datetime(df["match_date"], dayfirst=True, errors="coerce").dt.date
    df["home_goals"] = pd.to_numeric(df["home_goals"], errors="coerce")
    df["away_goals"] = pd.to_numeric(df["away_goals"], errors="coerce")
    return df.dropna(subset=list(required)).sort_values("match_date").reset_index(drop=True)


def _brier_log_loss(probs: list[float], outcomes: list[int]) -> tuple[float, float]:
    """Compute Brier score and log-loss from probability/outcome lists."""
    import math

    n = len(probs)
    if n == 0:
        return float("nan"), float("nan")
    brier = sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / n
    eps = 1e-9
    ll = (
        -sum(
            y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
            for p, y in zip(probs, outcomes)
        )
        / n
    )
    return round(brier, 6), round(ll, 6)


def _fit_model(train_df: pd.DataFrame, verbose: bool) -> Any:
    """Train a DixonColesModel on train_df. Returns None on failure."""
    try:
        from src.models.dixon_coles_model import DixonColesModel, DixonColesConfig

        model = DixonColesModel(DixonColesConfig())
        model.fit(train_df)
        if verbose:
            print(
                f"    [MODEL] Fitted on {len(train_df)} matches "
                f"({train_df['match_date'].min()} → {train_df['match_date'].max()})"
            )
        return model
    except Exception as e:
        if verbose:
            print(f"    [MODEL] Fit failed: {e}", file=sys.stderr)
        return None


def _eval_model(
    model: Any, test_df: pd.DataFrame, verbose: bool
) -> tuple[float | None, float | None]:
    """Evaluate model on test_df, returning (brier, log_loss)."""
    if model is None or test_df.empty:
        return None, None

    probs, outcomes = [], []
    for _, row in test_df.iterrows():
        try:
            preds = model.predict_match(row)
            home_prob = next((p.probability for p in preds if p.selection in ("H", "home")), None)
            if home_prob is None and preds:
                home_prob = preds[0].probability
            if home_prob is None:
                continue
            hg, ag = int(row["home_goals"]), int(row["away_goals"])
            outcome = 1 if hg > ag else 0
            probs.append(float(home_prob))
            outcomes.append(outcome)
        except Exception:
            continue

    if not probs:
        return None, None

    brier, ll = _brier_log_loss(probs, outcomes)
    if verbose:
        print(f"    [EVAL] n={len(probs)} Brier={brier:.4f} LogLoss={ll:.4f}")
    return brier, ll


def run_walk_forward(
    league: str,
    start_date: date,
    end_date: date,
    fold_weeks: int,
    retrain_after_each_match: bool,
    data_dir: Path,
    verbose: bool,
) -> WalkForwardResult:
    t0 = time.perf_counter()

    df = _load_matches(league, data_dir)
    if df.empty:
        print(f"[ERROR] No match data found for {league} in {data_dir}", file=sys.stderr)
        sys.exit(1)

    df = df[(df["match_date"] >= start_date) & (df["match_date"] <= end_date)]
    if df.empty:
        print(f"[ERROR] No matches in range {start_date} – {end_date}", file=sys.stderr)
        sys.exit(1)

    if verbose:
        print(f"[INFO] {len(df)} matches loaded for {league} ({start_date} – {end_date})")

    fold_delta = timedelta(weeks=fold_weeks)
    folds: list[FoldResult] = []
    fold_index = 0
    test_start = start_date

    while test_start <= end_date:
        test_end = min(test_start + fold_delta - timedelta(days=1), end_date)
        train_df = df[df["match_date"] < test_start]
        test_df = df[(df["match_date"] >= test_start) & (df["match_date"] <= test_end)]

        if verbose:
            print(
                f"\n[FOLD {fold_index}] train<{test_start} | test {test_start}→{test_end} "
                f"({len(train_df)} train, {len(test_df)} test)"
            )

        fold_t0 = time.perf_counter()
        retrained = False
        retrain_count = 0

        if train_df.empty:
            if verbose:
                print("    [SKIP] No training data before test window.")
            folds.append(
                FoldResult(
                    fold_index=fold_index,
                    train_start=str(df["match_date"].min()),
                    train_end=str(test_start - timedelta(days=1)),
                    test_start=str(test_start),
                    test_end=str(test_end),
                    n_train=0,
                    n_test=len(test_df),
                    brier=None,
                    log_loss=None,
                    retrained=False,
                    retrain_matches=0,
                    elapsed_s=round(time.perf_counter() - fold_t0, 3),
                )
            )
            test_start = test_end + timedelta(days=1)
            fold_index += 1
            continue

        if retrain_after_each_match:
            # Sequential: for each test match, retrain on all prior data then evaluate
            probs_all, outcomes_all = [], []
            running_train = train_df.copy()

            for _, row in test_df.iterrows():
                model = _fit_model(running_train, verbose=False)
                if model is not None:
                    p, _ = _eval_model(model, pd.DataFrame([row]), verbose=False)
                    if p is not None:
                        hg, ag = int(row["home_goals"]), int(row["away_goals"])
                        probs_all.append(p)
                        outcomes_all.append(1 if hg > ag else 0)

                # Add this match to running train set
                running_train = pd.concat([running_train, pd.DataFrame([row])], ignore_index=True)
                retrain_count += 1

            if verbose:
                print(f"    [RETRAIN] partial_fit called with {retrain_count} new matches")

            brier, ll = _brier_log_loss(probs_all, outcomes_all) if probs_all else (None, None)
            if verbose and brier is not None:
                print(f"    [EVAL] n={len(probs_all)} Brier={brier:.4f} LogLoss={ll:.4f}")
            retrained = True
        else:
            # Standard: train once, evaluate on whole fold
            model = _fit_model(train_df, verbose=verbose)
            brier, ll = _eval_model(model, test_df, verbose=verbose)

        folds.append(
            FoldResult(
                fold_index=fold_index,
                train_start=str(train_df["match_date"].min()),
                train_end=str(train_df["match_date"].max()),
                test_start=str(test_start),
                test_end=str(test_end),
                n_train=len(train_df),
                n_test=len(test_df),
                brier=brier,
                log_loss=ll,
                retrained=retrained,
                retrain_matches=retrain_count,
                elapsed_s=round(time.perf_counter() - fold_t0, 3),
            )
        )

        test_start = test_end + timedelta(days=1)
        fold_index += 1

    valid_briers = [f.brier for f in folds if f.brier is not None]
    valid_lls = [f.log_loss for f in folds if f.log_loss is not None]

    import statistics

    result = WalkForwardResult(
        league=league,
        start_date=str(start_date),
        end_date=str(end_date),
        fold_weeks=fold_weeks,
        retrain_after_each_match=retrain_after_each_match,
        n_folds=len(folds),
        folds=folds,
        brier_mean=round(statistics.mean(valid_briers), 6) if valid_briers else None,
        brier_std=round(statistics.stdev(valid_briers), 6) if len(valid_briers) > 1 else None,
        log_loss_mean=round(statistics.mean(valid_lls), 6) if valid_lls else None,
        total_elapsed_s=round(time.perf_counter() - t0, 3),
    )
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Walk-forward backtest with optional per-match retraining."
    )
    parser.add_argument("--league", default="EPL")
    parser.add_argument("--start-date", default="2023-08-01")
    parser.add_argument("--end-date", default="2024-05-31")
    parser.add_argument("--fold-weeks", type=int, default=4)
    parser.add_argument(
        "--retrain-after-each-match",
        action="store_true",
        help="Retrain model before each test match (slow but most realistic)",
    )
    parser.add_argument("--data-dir", default="data", type=Path)
    parser.add_argument("--output", default="", help="Path to write JSON results")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)

    result = run_walk_forward(
        league=args.league,
        start_date=start,
        end_date=end,
        fold_weeks=args.fold_weeks,
        retrain_after_each_match=args.retrain_after_each_match,
        data_dir=args.data_dir,
        verbose=args.verbose,
    )

    print(f"\n{'='*60}")
    print(f"Walk-Forward Backtest — {result.league}")
    print(f"{'='*60}")
    print(f"Folds:         {result.n_folds}")
    print(f"Brier mean:    {result.brier_mean:.4f}" if result.brier_mean else "Brier mean:    N/A")
    print(f"Brier std:     {result.brier_std:.4f}" if result.brier_std else "Brier std:     N/A")
    print(
        f"LogLoss mean:  {result.log_loss_mean:.4f}"
        if result.log_loss_mean
        else "LogLoss mean:  N/A"
    )
    print(f"Total time:    {result.total_elapsed_s:.1f}s")
    print(f"Retrain mode:  {'per-match' if result.retrain_after_each_match else 'per-fold'}")

    if args.verbose:
        print(
            f"\n{'Fold':>4} {'Train end':<12} {'Test range':<25} {'N_test':>6} {'Brier':>8} {'LL':>8} {'Retrained':>10}"
        )
        print("-" * 80)
        for f in result.folds:
            brier_s = f"{f.brier:.4f}" if f.brier is not None else "   N/A"
            ll_s = f"{f.log_loss:.4f}" if f.log_loss is not None else "   N/A"
            retrain_s = f"yes({f.retrain_matches})" if f.retrained else "no"
            print(
                f"{f.fold_index:>4} {f.train_end:<12} {f.test_start}→{f.test_end:<12} "
                f"{f.n_test:>6} {brier_s:>8} {ll_s:>8} {retrain_s:>10}"
            )

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(result)
        out.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        print(f"\nSaved → {out}")


if __name__ == "__main__":
    main()
