"""CLI to train and save the ATP ELO model.

Usage:
    python -m src.models.train_tennis_elo
    python -m src.models.train_tennis_elo --years 2019 2020 2021 2022 2023 2024
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
_log = logging.getLogger("train_tennis_elo")

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
MODEL_DIR = Path(os.environ.get("MODEL_DIR", DATA_DIR / "models"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", default=list(range(2019, 2026)))
    parser.add_argument("--cache-dir", type=Path, default=DATA_DIR / "raw" / "tennis_atp")
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    from src.ingest.tennis_atp import build_atp_dataset
    from src.models.tennis_elo import TennisEloModel

    _log.info("Downloading ATP data for years: %s", args.years)
    df = build_atp_dataset(args.years, cache_dir=args.cache_dir, use_cache=not args.no_cache)
    if df.empty:
        _log.error("No data downloaded — aborting")
        sys.exit(1)

    _log.info("Training ELO model on %d matches...", len(df))
    model = TennisEloModel()
    model.fit(df)

    tag = model.params.dataset_hash
    model_id = f"tennis_elo_atp_{tag}"
    model_path = args.model_dir / f"{model_id}.pkl"
    meta_path = args.model_dir / f"{model_id}.meta.json"

    model.save(model_path)
    model.save_meta(meta_path, model_path)

    _log.info(
        "Saved: %s | players=%d, matches=%d",
        model_path.name,
        model.params.n_players,
        model.params.n_matches,
    )

    # Write stable pointer so the scanner always knows which file to load
    pointer_path = args.model_dir / "tennis_elo_atp_latest.pkl"
    import shutil

    shutil.copy2(model_path, pointer_path)
    _log.info("Latest pointer: %s", pointer_path)


if __name__ == "__main__":
    main()
