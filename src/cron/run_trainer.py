"""Cron entrypoint: daily Dixon-Coles trainer.

Invoked by Render Cron Job 'daily-trainer' at 06:00 UTC.
Replaces GitHub Actions daily-trainer.yml for production.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> None:
    t0 = time.perf_counter()
    leagues = os.environ.get("LEAGUES", "EPL,BUNDESLIGA,LALIGA,SERIEA").split(",")
    seasons = os.environ.get("OPENFOOTBALL_SEASONS", "2021-22,2022-23,2023-24,2024-25").split(",")
    model_dir = Path(os.environ.get("MODEL_DIR", "data/models"))
    staging_dir = Path(os.environ.get("STAGING_DIR", "data/staging"))
    reports_dir = Path(os.environ.get("REPORTS_DIR", "data/reports"))
    cutoff = date.today()

    print(f"[trainer] Starting at {datetime.now(timezone.utc).isoformat()}")
    print(f"[trainer] Leagues: {leagues} | Cutoff: {cutoff}")

    results: list[dict] = []
    for league in leagues:
        try:
            result = _train_league(
                league=league,
                seasons=seasons,
                model_dir=model_dir,
                staging_dir=staging_dir,
                cutoff=cutoff,
            )
            results.append(result)
            _log_model_meta(result)
        except Exception as e:
            print(f"[trainer] {league}: FAILED — {e}", file=sys.stderr)
            results.append({"league": league, "status": "error", "error": str(e)})

    elapsed = round(time.perf_counter() - t0, 1)
    promoted = sum(1 for r in results if r.get("promoted"))
    print(f"\n[trainer] Done in {elapsed}s | {promoted}/{len(leagues)} promoted")

    _log_run("daily-trainer", "success" if promoted > 0 else "no_promotion",
             elapsed, f"{promoted} leagues promoted", {"results": results})


def _train_league(
    league: str,
    seasons: list[str],
    model_dir: Path,
    staging_dir: Path,
    cutoff: date,
) -> dict:
    from src.ingest.openfootball import OpenFootballLoader
    from src.models.dixon_coles import DixonColesConfig
    from src.models.model_registry import ModelRegistry
    from src.models.trainer import DailyTrainer

    print(f"\n[trainer] {league}: downloading data...")
    loader = OpenFootballLoader()
    result = loader.build(leagues=[league], seasons=seasons, use_cache=True)
    if result.dataframe.empty:
        print(f"[trainer] {league}: no data — skipping")
        return {"league": league, "status": "skip", "reason": "no data"}

    csv_path = loader.save_combined(
        result.dataframe, staging_dir, f"{league}_latest.csv"
    )
    print(f"[trainer] {league}: {len(result.dataframe)} matches → {csv_path}")

    registry = ModelRegistry(model_dir)
    trainer = DailyTrainer(
        registry=registry,
        staging_dir=staging_dir,
        config=DixonColesConfig(),
    )

    import pandas as pd
    df = pd.read_csv(csv_path, encoding="latin-1")
    train_result = trainer.run(df, league=league, cutoff_date=cutoff)

    promoted = getattr(train_result, "promoted", False)
    brier = getattr(train_result, "brier_score", None)
    model_id = getattr(train_result, "model_id", "")

    status = "promoted" if promoted else "candidate"
    print(f"[trainer] {league}: {status} | model={model_id} brier={brier}")
    return {
        "league": league,
        "model_id": model_id,
        "status": status,
        "promoted": promoted,
        "brier_score": brier,
    }


def _log_model_meta(result: dict) -> None:
    try:
        from src.infrastructure.render_db import get_db
        get_db().log_model_version({
            **result,
            "triggered_by": "daily-trainer-render",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass


def _log_run(job: str, status: str, duration_s: float, message: str, meta: dict | None = None):
    try:
        from src.infrastructure.render_db import get_db
        get_db().log_cron_run(job, status, duration_s, message, meta)
    except Exception:
        pass


if __name__ == "__main__":
    main()
