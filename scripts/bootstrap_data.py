"""Bootstrap script for Render: download real data and train model if not present."""

from __future__ import annotations

import sys
import warnings
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

warnings.filterwarnings("ignore", category=RuntimeWarning)


def main() -> None:
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

    # Check if a production model already exists
    registry = ModelRegistry(models_dir)
    try:
        existing = registry.load_latest("EPL")
        print(f"[bootstrap] Production model already exists: {existing.model_id}, skipping training.")
        return
    except FileNotFoundError:
        pass

    # Download EPL history from OpenFootball (free, no API key)
    print("[bootstrap] Downloading EPL history from OpenFootball...")
    loader = OpenFootballLoader(project_root=_ROOT)
    result = loader.build(
        leagues=["EPL"],
        seasons=["2021-22", "2022-23", "2023-24", "2024-25"],
        use_cache=True,
    )
    if result.dataframe.empty:
        print("[bootstrap] WARNING: no data downloaded, skipping model training.")
        return

    csv_path = loader.save_combined(result.dataframe, staging_dir, "epl_openfootball.csv")
    print(f"[bootstrap] Saved {len(result.dataframe)} matches to {csv_path}")

    # Train Dixon-Coles
    print("[bootstrap] Training Dixon-Coles model...")
    import pandas as pd
    matches = pd.read_csv(csv_path)
    trainer = DailyTrainer(
        registry=registry,
        staging_dir=staging_dir,
        config=DixonColesConfig(league="EPL", max_iterations=500),
        max_brier_score=0.65,
        max_log_loss=1.30,
    )
    training_result = trainer.run_on_dataframe("EPL", cutoff_date=date.today(), matches=matches)
    print(
        f"[bootstrap] Trained model {training_result.model_id}: "
        f"brier={training_result.brier_score:.4f}, "
        f"log_loss={training_result.log_loss:.4f}, "
        f"promoted={training_result.promoted}"
    )


if __name__ == "__main__":
    main()
