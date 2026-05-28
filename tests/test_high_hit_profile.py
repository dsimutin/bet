from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.models.high_hit_profile import (
    HighHitProfileSearchConfig,
    find_high_hit_profile,
    write_high_hit_profile_report,
)
from src.models.historical_value_model import HistoricalValueModelConfig


def _matches(n: int = 130) -> pd.DataFrame:
    start = date(2025, 1, 1)
    rows = []
    for idx in range(n):
        rows.append(
            {
                "Date": (start + timedelta(days=idx)).strftime("%d/%m/%Y"),
                "HomeTeam": f"Home {idx}",
                "AwayTeam": f"Away {idx}",
                "FTR": "H",
                "B365H": 1.75,
                "B365D": 3.70,
                "B365A": 5.50,
            }
        )
    return pd.DataFrame(rows)


def test_find_high_hit_profile_selects_profile_that_meets_target() -> None:
    report = find_high_hit_profile(
        _matches(),
        HistoricalValueModelConfig(
            min_train_matches=30,
            test_window_days=30,
            smoothing_alpha=5.0,
        ),
        HighHitProfileSearchConfig(
            target_win_rate=0.8,
            min_bets=20,
            probability_grid=(0.5, 0.55),
            max_odds_grid=(1.85,),
            min_edge_grid=(1.0,),
        ),
    )

    assert report["passed"] is True
    assert report["selected"]["win_rate"] >= 0.8
    assert report["selected"]["n_bets"] >= 20


def test_high_hit_profile_report_is_written(tmp_path) -> None:
    path = write_high_hit_profile_report({"passed": True}, tmp_path)

    assert path.name == "high_hit_profile_report.json"
    assert '"passed": true' in path.read_text(encoding="utf-8")
