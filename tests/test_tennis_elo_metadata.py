from __future__ import annotations

import json

import pandas as pd

from src.models.tennis_elo import TennisEloModel


def test_tennis_elo_metadata_does_not_claim_rating_decay(tmp_path) -> None:
    model = TennisEloModel()
    model.fit(
        pd.DataFrame(
            {
                "winner_name": ["Alice", "Bob"],
                "loser_name": ["Bob", "Alice"],
                "surface": ["hard", "hard"],
                "match_date": [pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-03")],
            }
        )
    )

    meta_path = tmp_path / "tennis.meta.json"
    model.save_meta(meta_path, tmp_path / "tennis.pkl")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    assert meta["rating_decay_applied"] is False
    assert "decay_half_life_days" not in meta
    assert meta["h2h_half_life_days"] == 365
