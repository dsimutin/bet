"""Surface-specific ELO model for ATP tennis match probability estimation.

ELO scale: standard chess-derived (K=32, start=1500, scale=400).
Trains two sets of ratings: overall (any surface) and per-surface.
Predictions fall back to overall rating when surface-specific data is thin.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

_log = logging.getLogger(__name__)

ELO_K = 32.0
ELO_START = 1500.0
ELO_SCALE = 400.0
# Require at least this many matches before we trust the rating enough to emit signals
MIN_MATCHES_FOR_SIGNAL = 15
SURFACES = ("clay", "grass", "hard", "carpet")


@dataclass
class TennisEloParams:
    n_matches: int = 0
    n_players: int = 0
    trained_at_utc: str = ""
    dataset_hash: str = ""
    converged: bool = True


class TennisEloModel:
    """Surface-aware ELO model for ATP tennis."""

    def __init__(
        self,
        k: float = ELO_K,
        start: float = ELO_START,
        scale: float = ELO_SCALE,
    ) -> None:
        self.k = k
        self.start = start
        self.scale = scale

        # overall[player] = float
        self._overall: dict[str, float] = {}
        # surface[surface][player] = float
        self._surface: dict[str, dict[str, float]] = {s: {} for s in SURFACES}
        # match counts
        self._total_matches: dict[str, int] = {}
        self._surface_matches: dict[str, dict[str, int]] = {s: {} for s in SURFACES}

        self.params: TennisEloParams = TennisEloParams()

    def fit(self, matches: pd.DataFrame) -> None:
        """Build ELO ratings from historical matches (must include match_date column)."""
        df = matches.sort_values("match_date", na_position="last")
        n = 0
        for _, row in df.iterrows():
            winner = str(row["winner_name"]).strip()
            loser = str(row["loser_name"]).strip()
            if not winner or not loser or winner == loser:
                continue
            surface = str(row.get("surface", "hard")).lower()
            if surface not in SURFACES:
                surface = "hard"
            self._update(winner, loser, surface)
            n += 1

        dataset_hash = hashlib.sha256(
            df[["winner_name", "loser_name", "match_date"]].to_csv(index=False).encode()
        ).hexdigest()[:16]

        self.params = TennisEloParams(
            n_matches=n,
            n_players=len(self._overall),
            trained_at_utc=datetime.now(timezone.utc).isoformat(),
            dataset_hash=dataset_hash,
        )
        _log.info(
            "[tennis_elo] Fitted %d matches, %d players", self.params.n_matches, self.params.n_players
        )

    def predict_proba(self, player1: str, player2: str, surface: str = "hard") -> float:
        """Return P(player1 beats player2). Falls back to overall ELO when surface data is thin."""
        surface = surface.lower()
        if surface not in SURFACES:
            surface = "hard"

        r1_surf = self._surface[surface].get(player1)
        r2_surf = self._surface[surface].get(player2)

        # Use surface-specific rating if both players have enough surface data
        surf_n1 = self._surface_matches[surface].get(player1, 0)
        surf_n2 = self._surface_matches[surface].get(player2, 0)
        if r1_surf is not None and r2_surf is not None and surf_n1 >= 5 and surf_n2 >= 5:
            r1 = r1_surf
            r2 = r2_surf
        else:
            # Fall back to overall
            r1 = self._overall.get(player1, self.start)
            r2 = self._overall.get(player2, self.start)

        return 1.0 / (1.0 + 10.0 ** ((r2 - r1) / self.scale))

    def get_match_count(self, player: str) -> int:
        return self._total_matches.get(player, 0)

    def has_enough_data(self, player: str, min_matches: int = MIN_MATCHES_FOR_SIGNAL) -> bool:
        return self.get_match_count(player) >= min_matches

    def get_overall_rating(self, player: str) -> float:
        return self._overall.get(player, self.start)

    def known_players(self) -> list[str]:
        return list(self._overall.keys())

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)
        _log.info("[tennis_elo] Saved to %s", path)

    @classmethod
    def load(cls, path: Path) -> "TennisEloModel":
        with open(path, "rb") as f:
            return pickle.load(f)

    def save_meta(self, meta_path: Path, model_path: Path) -> None:
        meta = {
            "model_type": "tennis_elo",
            "sport": "tennis",
            "tour": "ATP",
            "status": "production",
            "n_matches": self.params.n_matches,
            "n_players": self.params.n_players,
            "trained_at_utc": self.params.trained_at_utc,
            "dataset_hash": self.params.dataset_hash,
            "model_path": str(model_path),
            "elo_k": self.k,
            "elo_start": self.start,
            "elo_scale": self.scale,
        }
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def _update(self, winner: str, loser: str, surface: str) -> None:
        """Update both overall and surface ELO after one match result."""
        # Overall
        rw = self._overall.get(winner, self.start)
        rl = self._overall.get(loser, self.start)
        exp_w = 1.0 / (1.0 + 10.0 ** ((rl - rw) / self.scale))
        self._overall[winner] = rw + self.k * (1.0 - exp_w)
        self._overall[loser] = rl + self.k * (0.0 - (1.0 - exp_w))

        # Surface
        surf_dict = self._surface[surface]
        rws = surf_dict.get(winner, self.start)
        rls = surf_dict.get(loser, self.start)
        exp_ws = 1.0 / (1.0 + 10.0 ** ((rls - rws) / self.scale))
        surf_dict[winner] = rws + self.k * (1.0 - exp_ws)
        surf_dict[loser] = rls + self.k * (0.0 - (1.0 - exp_ws))

        # Counts
        self._total_matches[winner] = self._total_matches.get(winner, 0) + 1
        self._total_matches[loser] = self._total_matches.get(loser, 0) + 1
        sm = self._surface_matches[surface]
        sm[winner] = sm.get(winner, 0) + 1
        sm[loser] = sm.get(loser, 0) + 1
