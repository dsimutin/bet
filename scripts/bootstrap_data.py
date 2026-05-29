"""Bootstrap script for Render: download real data and train models for all configured leagues."""

from __future__ import annotations

import sys
import warnings
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

warnings.filterwarnings("ignore", category=RuntimeWarning)

# Leagues to bootstrap on first deploy. Each gets its own Dixon-Coles model.
# RPL not in OpenFootball — add when an alternative data source is available.
BOOTSTRAP_LEAGUES = [
    ("EPL",        ["2021-22", "2022-23", "2023-24", "2024-25"]),
    ("BUNDESLIGA", ["2021-22", "2022-23", "2023-24", "2024-25"]),
    ("LALIGA",     ["2021-22", "2022-23", "2023-24", "2024-25"]),
    ("SERIEA",     ["2021-22", "2022-23", "2023-24", "2024-25"]),
]


def main() -> None:
    import pandas as pd

    from src.ingest.openfootball import OpenFootballLoader
    from src.models.dixon_coles import DixonColesConfig
    from src.models.model_registry import ModelRegistry
    from src.models.trainer import DailyTrainer

    models_dir = _ROOT / "data" / "models"
    staging_dir = _ROOT / "data" / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)
    (_ROOT / "data" / "core").mkdir(parents=True, exist_ok=True)
    (_ROOT / "data" / "reports").mkdir(parents=True, exist_ok=True)

    registry = ModelRegistry(models_dir)
    loader = OpenFootballLoader(project_root=_ROOT)

    for league, seasons in BOOTSTRAP_LEAGUES:
        try:
            existing = registry.load_latest(league)
            print(f"[bootstrap] {league}: model {existing.model_id} already exists, skipping.")
            continue
        except FileNotFoundError:
            pass

        print(f"[bootstrap] {league}: downloading history from OpenFootball...")
        try:
            result = loader.build(leagues=[league], seasons=seasons, use_cache=True)
        except Exception as exc:
            print(f"[bootstrap] {league}: download failed — {exc}")
            continue

        if result.dataframe.empty:
            print(f"[bootstrap] {league}: no data downloaded, skipping.")
            continue

        csv_name = f"{league.lower()}_openfootball.csv"
        csv_path = loader.save_combined(result.dataframe, staging_dir, csv_name)
        print(f"[bootstrap] {league}: saved {len(result.dataframe)} matches to {csv_path}")

        print(f"[bootstrap] {league}: training Dixon-Coles model...")
        try:
            matches = pd.read_csv(csv_path)
            trainer = DailyTrainer(
                registry=registry,
                staging_dir=staging_dir,
                config=DixonColesConfig(league=league, max_iterations=500),
                max_brier_score=0.65,
                max_log_loss=1.30,
            )
            r = trainer.run_on_dataframe(league, cutoff_date=date.today(), matches=matches)
            print(
                f"[bootstrap] {league}: trained {r.model_id} "
                f"brier={r.brier_score:.4f} log_loss={r.log_loss:.4f} promoted={r.promoted}"
            )
        except Exception as exc:
            print(f"[bootstrap] {league}: training failed — {exc}")


if __name__ == "__main__":
    main()
