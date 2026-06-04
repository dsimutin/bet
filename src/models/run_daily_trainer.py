"""CLI for daily Dixon-Coles training and model registry promotion."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pandas as pd

from src.ingest.football_data_dataset import FootballDataDatasetBuilder
from src.ingest.openfootball import OpenFootballLoader
from src.models.dixon_coles import DixonColesConfig
from src.models.model_registry import ModelRegistry
from src.models.trainer import DailyTrainer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run daily Dixon-Coles trainer.")
    parser.add_argument("--league", required=True)
    parser.add_argument("--cutoff-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--staging-dir", type=Path, default=Path("data/staging"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--download-football-data", action="store_true")
    parser.add_argument("--football-data-leagues", default="E0")
    parser.add_argument("--download-openfootball", action="store_true")
    parser.add_argument("--openfootball-leagues", default="EPL")
    parser.add_argument("--seasons", default="2122,2223,2324,2425,2526")
    parser.add_argument("--openfootball-seasons", default="2021-22,2022-23,2023-24,2024-25,2025-26")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--model-dir", type=Path, default=Path("data/models"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/reports"))
    parser.add_argument("--min-matches", type=int, default=20)
    parser.add_argument("--max-iterations", type=int, default=200)
    parser.add_argument("--max-goals", type=int, default=10)
    parser.add_argument("--max-brier-score", type=float, default=0.75)
    parser.add_argument("--max-log-loss", type=float, default=1.20)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    trainer = DailyTrainer(
        registry=ModelRegistry(args.model_dir),
        staging_dir=args.staging_dir,
        max_brier_score=args.max_brier_score,
        max_log_loss=args.max_log_loss,
        config=DixonColesConfig(
            league=args.league,
            min_matches=args.min_matches,
            max_iterations=args.max_iterations,
            max_goals=args.max_goals,
        ),
    )
    matches = _load_training_matches(args)
    result = trainer.run_on_dataframe(args.league, cutoff_date=args.cutoff_date, matches=matches)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / f"daily_training_{args.league}.json"
    output_path.write_text(
        json.dumps(asdict(result), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {output_path}")
    print(
        "Training result: "
        f"model_id={result.model_id}, promoted={result.promoted}, "
        f"brier={result.brier_score:.6f}, log_loss={result.log_loss:.6f}, "
        f"n_new={result.n_new_matches}, "
        f"reason={result.promotion_reason}"
    )


def _load_training_matches(args: argparse.Namespace) -> pd.DataFrame:
    if args.download_openfootball:
        loader = OpenFootballLoader(project_root=Path("."))
        openfootball_result = loader.build(
            leagues=_split_csv_arg(args.openfootball_leagues),
            seasons=_split_csv_arg(args.openfootball_seasons),
            use_cache=not args.no_cache,
        )
        if openfootball_result.dataframe.empty:
            skipped = (
                "; ".join(openfootball_result.skipped)
                if openfootball_result.skipped
                else "no files"
            )
            raise SystemExit(f"No OpenFootball rows loaded: {skipped}")
        loader.save_combined(openfootball_result.dataframe, args.output_dir)
        return openfootball_result.dataframe
    if args.download_football_data:
        builder = FootballDataDatasetBuilder(project_root=Path("."))
        football_data_result = builder.build(
            leagues=_split_csv_arg(args.football_data_leagues),
            seasons=_split_csv_arg(args.seasons),
            use_cache=not args.no_cache,
        )
        if football_data_result.dataframe.empty:
            skipped = (
                "; ".join(football_data_result.skipped)
                if football_data_result.skipped
                else "no files"
            )
            raise SystemExit(f"No football-data rows loaded: {skipped}")
        return football_data_result.dataframe
    if args.input is not None:
        return pd.read_csv(args.input, encoding="latin-1")
    return DailyTrainer(staging_dir=args.staging_dir)._load_matches(args.league)


def _split_csv_arg(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    main()
