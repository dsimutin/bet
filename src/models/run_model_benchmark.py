"""CLI entrypoint for walk-forward model probability benchmarks."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.ingest.football_data_dataset import FootballDataDatasetBuilder
from src.models.model_benchmark import BenchmarkConfig, ProbabilityBenchmark


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run walk-forward 1X2 probability benchmark.")
    parser.add_argument("--input", type=Path, help="football-data.co.uk CSV file")
    parser.add_argument("--output-dir", default=Path("data/reports"), type=Path)
    parser.add_argument("--download-football-data", action="store_true")
    parser.add_argument("--leagues", default="E0,SP1,D1,I1,F1")
    parser.add_argument("--seasons", default="2122,2223,2324,2425,2526")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--bookmaker-prefix", default="B365")
    parser.add_argument("--min-train-matches", default=120, type=int)
    parser.add_argument("--test-window-days", default=30, type=int)
    parser.add_argument("--max-windows", type=int)
    parser.add_argument("--dixon-coles-max-iterations", default=80, type=int)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    matches = _load_history_dataframe(args)
    benchmark = ProbabilityBenchmark(
        BenchmarkConfig(
            bookmaker_prefix=args.bookmaker_prefix,
            min_train_matches=args.min_train_matches,
            test_window_days=args.test_window_days,
            max_windows=args.max_windows,
            dixon_coles_max_iterations=args.dixon_coles_max_iterations,
        )
    )
    report = benchmark.run(matches)
    json_path, md_path = benchmark.write_report(report, args.output_dir)
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


def _load_history_dataframe(args: argparse.Namespace) -> pd.DataFrame:
    if args.download_football_data:
        builder = FootballDataDatasetBuilder(project_root=Path("."))
        result = builder.build(
            leagues=_split_csv_arg(args.leagues),
            seasons=_split_csv_arg(args.seasons),
            use_cache=not args.no_cache,
        )
        if result.dataframe.empty:
            skipped = "; ".join(result.skipped) if result.skipped else "no files"
            raise SystemExit(f"No football-data rows loaded: {skipped}")
        output_path = builder.save_combined(result.dataframe, args.output_dir)
        print(
            f"Wrote {output_path} " f"({len(result.dataframe)} rows, skipped={len(result.skipped)})"
        )
        if result.skipped:
            print("Skipped: " + "; ".join(result.skipped))
        return result.dataframe

    if args.input is None:
        raise SystemExit("--input is required unless --download-football-data is used")
    return pd.read_csv(args.input, encoding="latin-1")


def _split_csv_arg(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    main()
