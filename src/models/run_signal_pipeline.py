"""End-to-end signal pipeline: benchmark first, then gated signal generation."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run benchmark-gated football signal pipeline.")
    parser.add_argument("--output-dir", default=Path("data/reports"), type=Path)
    parser.add_argument(
        "--download-football-data",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Download football-data history unless --input is provided.",
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--leagues", default="E0,SP1,D1,I1,F1")
    parser.add_argument("--seasons", default="2122,2223,2324,2425,2526")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--bookmaker-prefix", default="B365")
    parser.add_argument("--min-train-matches", default=120, type=int)
    parser.add_argument("--test-window-days", default=30, type=int)
    parser.add_argument("--max-windows", type=int)
    parser.add_argument("--dixon-coles-max-iterations", default=80, type=int)

    parser.add_argument("--upcoming-input", type=Path)
    parser.add_argument("--free-source-inbox", type=Path)
    parser.add_argument("--live-odds", action="store_true")
    parser.add_argument("--live-sport-keys", default="soccer_epl")
    parser.add_argument("--odds-regions", default="eu,uk")
    parser.add_argument("--preferred-bookmakers", default="bet365,pinnacle")
    parser.add_argument("--no-bookmaker-fallback", action="store_true")
    parser.add_argument(
        "--ledger-path", type=Path, default=Path("data/core/paper_signal_ledger.json")
    )
    parser.add_argument(
        "--settle-ledger",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Settle previous open ledger signals from historical results before new delivery.",
    )
    parser.add_argument(
        "--settle-results-input",
        type=Path,
        help="Optional results CSV for settlement. Defaults to input or downloaded combined CSV.",
    )
    parser.add_argument("--max-signals", default=10, type=int)
    parser.add_argument("--production-dixon-coles", action="store_true")
    parser.add_argument("--production-model-dir", type=Path, default=Path("data/models"))
    parser.add_argument("--production-league", default="EPL")
    parser.add_argument(
        "--train-production-model",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Train/promote the production Dixon-Coles model before production signals.",
    )
    parser.add_argument("--production-trainer-min-matches", default=20, type=int)
    parser.add_argument("--production-trainer-max-iterations", default=120, type=int)
    parser.add_argument("--production-trainer-max-brier-score", default=0.65, type=float)
    parser.add_argument("--production-trainer-max-log-loss", default=1.20, type=float)
    parser.add_argument(
        "--production-train-openfootball",
        action="store_true",
        help="Train production Dixon-Coles from OpenFootball GitHub raw results.",
    )
    parser.add_argument("--openfootball-leagues", default="EPL")
    parser.add_argument("--openfootball-seasons", default="2021-22,2022-23,2023-24,2024-25")
    parser.add_argument("--telegram-payload", action="store_true")
    parser.add_argument("--send-telegram", action="store_true")
    parser.add_argument("--allow-duplicate-signals", action="store_true")

    parser.add_argument("--min-edge-pct", default=2.0, type=float)
    parser.add_argument("--min-signal-probability", default=0.45, type=float)
    parser.add_argument("--max-entry-odds", type=float)
    parser.add_argument(
        "--high-hit-mode",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Prefer fewer, higher-probability signals. Use --no-high-hit-mode for research.",
    )
    parser.add_argument(
        "--auto-high-hit-profile",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Search historical thresholds before delivery.",
    )
    parser.add_argument(
        "--require-auto-high-hit-profile",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Block delivery when no historical high-hit profile meets target.",
    )
    parser.add_argument("--target-hit-rate", default=0.55, type=float)
    parser.add_argument("--min-profile-bets", default=30, type=int)
    parser.add_argument("--disable-quality-gate", action="store_true")
    parser.add_argument("--min-quality-win-rate", default=0.45, type=float)
    parser.add_argument("--min-quality-roi-pct", default=0.0, type=float)
    parser.add_argument(
        "--consensus",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require historical and Poisson models to agree. Use --no-consensus for research.",
    )

    parser.add_argument(
        "--require-ledger-quality",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Gate delivery on settled Telegram signal performance after warmup.",
    )
    parser.add_argument("--min-ledger-settled", default=20, type=int)
    parser.add_argument("--min-ledger-win-rate", default=0.55, type=float)
    parser.add_argument("--min-ledger-roi-pct", default=0.0, type=float)
    parser.add_argument(
        "--model-quality-scope", choices=["latest_window", "overall"], default="latest_window"
    )
    parser.add_argument("--model-quality-candidates")
    parser.add_argument("--model-quality-mode", choices=["all", "any"], default="all")
    parser.add_argument("--min-benchmark-predictions", default=100, type=int)
    parser.add_argument("--min-brier-improvement", default=0.0, type=float)
    parser.add_argument("--min-log-loss-improvement", default=0.0, type=float)
    parser.add_argument(
        "--dry-run", action="store_true", help="Print commands without running them"
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    benchmark_cmd = build_benchmark_command(args)
    settle_cmd = build_settle_command(args)
    train_cmd = build_train_command(args)
    signal_cmd = build_signal_command(args)

    _run_or_print(benchmark_cmd, args.dry_run)
    if settle_cmd is not None:
        _run_or_print(settle_cmd, args.dry_run)
    if train_cmd is not None:
        _run_or_print(train_cmd, args.dry_run)
    _run_or_print(signal_cmd, args.dry_run)


def build_benchmark_command(args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "src.models.run_model_benchmark",
        "--output-dir",
        str(args.output_dir),
        "--bookmaker-prefix",
        args.bookmaker_prefix,
        "--min-train-matches",
        str(args.min_train_matches),
        "--test-window-days",
        str(args.test_window_days),
        "--dixon-coles-max-iterations",
        str(args.dixon_coles_max_iterations),
    ]
    _append_history_args(cmd, args)
    if args.max_windows is not None:
        cmd.extend(["--max-windows", str(args.max_windows)])
    return cmd


def build_settle_command(args: argparse.Namespace) -> list[str] | None:
    if not args.settle_ledger:
        return None

    results_input = args.settle_results_input
    if results_input is None:
        if args.input is not None:
            results_input = args.input
        elif args.download_football_data:
            results_input = args.output_dir / "football_data_combined.csv"

    if results_input is None:
        return None

    return [
        sys.executable,
        "-m",
        "src.models.settle_signal_ledger",
        "--ledger-path",
        str(args.ledger_path),
        "--results-input",
        str(results_input),
        "--report-path",
        str(args.output_dir / "signal_ledger_settlement_report.json"),
    ]


def build_train_command(args: argparse.Namespace) -> list[str] | None:
    if not args.production_dixon_coles or not args.train_production_model:
        return None

    training_input = args.input if args.input is not None else None
    if (
        training_input is None
        and args.download_football_data
        and not args.production_train_openfootball
    ):
        training_input = args.output_dir / "football_data_combined.csv"
    if training_input is None and not args.production_train_openfootball:
        return None

    cmd = [
        sys.executable,
        "-m",
        "src.models.run_daily_trainer",
        "--league",
        args.production_league,
        "--model-dir",
        str(args.production_model_dir),
        "--output-dir",
        str(args.output_dir),
        "--min-matches",
        str(args.production_trainer_min_matches),
        "--max-iterations",
        str(args.production_trainer_max_iterations),
        "--max-brier-score",
        str(args.production_trainer_max_brier_score),
        "--max-log-loss",
        str(args.production_trainer_max_log_loss),
    ]
    if args.production_train_openfootball:
        cmd.extend(
            [
                "--download-openfootball",
                "--openfootball-leagues",
                args.openfootball_leagues,
                "--openfootball-seasons",
                args.openfootball_seasons,
            ]
        )
        if args.no_cache:
            cmd.append("--no-cache")
    elif training_input is not None:
        cmd.extend(["--input", str(training_input)])
    return cmd


def build_signal_command(args: argparse.Namespace) -> list[str]:
    benchmark_path = args.output_dir / "model_probability_benchmark.json"
    cmd = [
        sys.executable,
        "-m",
        "src.models.run_historical_value_model",
        "--output-dir",
        str(args.output_dir),
        "--bookmaker-prefix",
        args.bookmaker_prefix,
        "--min-train-matches",
        str(args.min_train_matches),
        "--test-window-days",
        str(args.test_window_days),
        "--min-edge-pct",
        str(args.min_edge_pct),
        "--min-signal-probability",
        str(args.min_signal_probability),
        "--min-quality-win-rate",
        str(args.min_quality_win_rate),
        "--min-quality-roi-pct",
        str(args.min_quality_roi_pct),
        "--max-signals",
        str(args.max_signals),
        "--ledger-path",
        str(args.ledger_path),
        "--require-model-quality",
        "--benchmark-report",
        str(benchmark_path),
        "--model-quality-scope",
        args.model_quality_scope,
        "--model-quality-mode",
        args.model_quality_mode,
        "--min-benchmark-predictions",
        str(args.min_benchmark_predictions),
        "--min-brier-improvement",
        str(args.min_brier_improvement),
        "--min-log-loss-improvement",
        str(args.min_log_loss_improvement),
    ]
    _append_history_args(cmd, args)
    _append_candidate_args(cmd, args)
    if args.max_entry_odds is not None:
        cmd.extend(["--max-entry-odds", str(args.max_entry_odds)])
    if args.high_hit_mode:
        cmd.append("--high-hit-mode")
    if args.auto_high_hit_profile:
        cmd.extend(
            [
                "--auto-high-hit-profile",
                "--target-hit-rate",
                str(args.target_hit_rate),
                "--min-profile-bets",
                str(args.min_profile_bets),
            ]
        )
        if args.require_auto_high_hit_profile:
            cmd.append("--require-auto-high-hit-profile")
    if args.disable_quality_gate:
        cmd.append("--disable-quality-gate")
    if args.consensus and not args.production_dixon_coles:
        cmd.append("--consensus")
    if args.telegram_payload:
        cmd.append("--telegram-payload")
    if args.send_telegram:
        cmd.append("--send-telegram")
    if args.allow_duplicate_signals:
        cmd.append("--allow-duplicate-signals")
    if args.production_dixon_coles:
        cmd.extend(
            [
                "--production-dixon-coles",
                "--production-model-dir",
                str(args.production_model_dir),
                "--production-league",
                args.production_league,
            ]
        )
    if args.require_ledger_quality:
        cmd.extend(
            [
                "--require-ledger-quality",
                "--min-ledger-settled",
                str(args.min_ledger_settled),
                "--min-ledger-win-rate",
                str(args.min_ledger_win_rate),
                "--min-ledger-roi-pct",
                str(args.min_ledger_roi_pct),
            ]
        )
    if args.model_quality_candidates:
        cmd.extend(["--model-quality-candidates", args.model_quality_candidates])
    return cmd


def _append_history_args(cmd: list[str], args: argparse.Namespace) -> None:
    if args.input is not None:
        cmd.extend(["--input", str(args.input)])
        return
    if args.download_football_data:
        cmd.extend(
            [
                "--download-football-data",
                "--leagues",
                args.leagues,
                "--seasons",
                args.seasons,
            ]
        )
        if args.no_cache:
            cmd.append("--no-cache")


def _append_candidate_args(cmd: list[str], args: argparse.Namespace) -> None:
    if args.upcoming_input is not None:
        cmd.extend(["--upcoming-input", str(args.upcoming_input)])
    if args.free_source_inbox is not None:
        cmd.extend(["--free-source-inbox", str(args.free_source_inbox)])
    if args.live_odds:
        cmd.extend(
            [
                "--live-odds",
                "--live-sport-keys",
                args.live_sport_keys,
                "--odds-regions",
                args.odds_regions,
                "--preferred-bookmakers",
                args.preferred_bookmakers,
            ]
        )
        if args.no_bookmaker_fallback:
            cmd.append("--no-bookmaker-fallback")


def _run_or_print(cmd: list[str], dry_run: bool) -> None:
    print("+ " + " ".join(cmd))
    if not dry_run:
        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
