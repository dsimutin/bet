"""Point-by-point Markov chain model for ATP tennis match probability.

Mathematics:
  Given p = P(server wins a point on serve), all match probabilities
  are computed exactly via recurrence (no approximations except
  independence of points, which holds empirically ~95%).

  p_serve is estimated from Sackmann rolling stats:
    p = (1stWon + 2ndWon) / svpt

  Blend with ELO for players with sparse serve data.

Reference: Klaassen & Magnus (2001), "Are Points in Tennis Independent?"
"""

from __future__ import annotations

import math
import pickle
import logging
import json
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, date, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

_log = logging.getLogger(__name__)

# Minimum serve points to trust rolling serve stats
MIN_SERVE_POINTS = 200
# Rolling window: last N surface matches for serve stats
STATS_WINDOW = 60
SURFACES = ("clay", "grass", "hard", "carpet")
MIN_MATCHES_FOR_SIGNAL = 15

# Blend weight: how much to trust Markov vs ELO
# When serve data is rich, Markov dominates; otherwise fall back to ELO
MARKOV_WEIGHT_MAX = 0.70   # when player has lots of serve data
MARKOV_WEIGHT_MIN = 0.20   # when serve data is sparse


# ---------------------------------------------------------------------------
# Core Markov math  (all @lru_cache for speed)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _p_win_game(p: float) -> float:
    """P(server wins game) given p = P(server wins point on serve)."""
    q = 1.0 - p
    # Win without reaching deuce: 4-0, 4-1, 4-2
    no_deuce = p**4 * (1.0 + 4.0*q + 10.0*q**2)
    # Reach deuce: C(6,3)=20 ways both reach 3-3
    p_deuce = 20.0 * (p * q)**3
    # Win from deuce (geometric series): p² / (p² + q²)
    p_win_deuce = p**2 / (p**2 + q**2)
    return no_deuce + p_deuce * p_win_deuce


@lru_cache(maxsize=None)
def _p_win_tiebreak(p_eff: float) -> float:
    """P(player wins tiebreak) using effective point win probability.

    p_eff ≈ (p_a_serve + p_a_return) / 2  — average over alternating service.
    First to 7 points with 2-point lead.
    """
    q = 1.0 - p_eff
    # Win 7-k for k = 0..5
    total = sum(
        math.comb(6 + k, k) * p_eff**7 * q**k
        for k in range(6)
    )
    # Reach 6-6 sudden death: C(12,6) * (pq)^6
    p_sd = math.comb(12, 6) * (p_eff * q)**6
    p_sd_win = p_eff**2 / (p_eff**2 + q**2)
    return total + p_sd * p_sd_win


def _p_win_set(p_serve: float, p_return: float, a_serves_first: bool = True) -> float:
    """P(player A wins set).

    p_serve  = P(A wins game when A serves)
    p_return = P(A wins game when B serves)
    Service alternates each game; A serves first by default.
    """
    # DP: state (games_a, games_b, a_serves_next)
    # Use iterative DP (avoid recursion limit)
    memo: dict[tuple, float] = {}

    def dp(ga: int, gb: int, a_serves: bool) -> float:
        key = (ga, gb, a_serves)
        if key in memo:
            return memo[key]

        # Terminal: standard set ends at 6 or 7
        if ga >= 6 and ga - gb >= 2:
            return 1.0
        if gb >= 6 and gb - ga >= 2:
            return 0.0
        if ga == 7 and gb == 6:
            return 1.0
        if gb == 7 and ga == 6:
            return 0.0
        if ga == 6 and gb == 6:
            # Tiebreak: effective point prob is average of both players' perspectives
            p_tb = (p_serve + p_return) / 2.0
            result = _p_win_tiebreak(round(p_tb, 4))
            memo[key] = result
            return result

        p_game = p_serve if a_serves else p_return
        result = (p_game * dp(ga + 1, gb, not a_serves)
                  + (1.0 - p_game) * dp(ga, gb + 1, not a_serves))
        memo[key] = result
        return result

    return dp(0, 0, a_serves_first)


def _p_win_match(p_serve: float, p_return: float,
                 best_of: int = 3, a_serves_first: bool = True) -> float:
    """P(player A wins match) via Markov chain.

    best_of: 3 (regular tour) or 5 (Grand Slams)
    """
    sets_needed = (best_of + 1) // 2  # 2 for BO3, 3 for BO5

    # p_win_set with service alternating at set level
    # First set: A serves first game. Second set: B served last game of set 1
    # → we alternate who serves first game of each set
    # Approximate: A serves first game of odd sets, B of even sets

    memo: dict[tuple, float] = {}

    def dp(sa: int, sb: int, a_starts_set: bool) -> float:
        key = (sa, sb, a_starts_set)
        if key in memo:
            return memo[key]
        if sa == sets_needed:
            return 1.0
        if sb == sets_needed:
            return 0.0
        ps = _p_win_set(p_serve, p_return, a_starts_set)
        # Who starts next set? In most tours the loser of last game serves next.
        # Approximate: alternate who serves first in each set
        result = (ps * dp(sa + 1, sb, not a_starts_set)
                  + (1.0 - ps) * dp(sa, sb + 1, not a_starts_set))
        memo[key] = result
        return result

    return dp(0, 0, a_serves_first)


# ---------------------------------------------------------------------------
# Player serve stats tracker
# ---------------------------------------------------------------------------

def _default_list() -> list:
    return []


@dataclass
class MarkovParams:
    n_matches: int = 0
    n_players: int = 0
    trained_at_utc: str = ""
    dataset_hash: str = ""


class TennisMarkovModel:
    """Match probability via point-by-point Markov chain + ELO blend."""

    def __init__(self) -> None:
        # Rolling serve stats: player → surface → list of {p_serve, n_points, date}
        self._serve: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(_default_list))
        # ELO ratings (simple, used for blend when serve data is sparse)
        self._elo: dict[str, float] = {}
        self._elo_surface: dict[str, dict[str, float]] = {s: {} for s in SURFACES}
        self._match_count: dict[str, int] = {}
        self.params = MarkovParams()

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def fit(self, matches: pd.DataFrame) -> None:
        """Train on historical matches in chronological order."""
        df = matches.sort_values("match_date", na_position="last").copy()
        n = 0
        for _, row in df.iterrows():
            winner = str(row["winner_name"]).strip()
            loser = str(row["loser_name"]).strip()
            if not winner or not loser or winner == loser:
                continue
            surface = str(row.get("surface", "hard")).lower()
            if surface not in SURFACES:
                surface = "hard"
            match_date = row.get("match_date")
            if hasattr(match_date, "date"):
                match_date = match_date.date()

            self._update_serve(winner, surface, row, is_winner=True, match_date=match_date)
            self._update_serve(loser, surface, row, is_winner=False, match_date=match_date)
            self._update_elo(winner, loser, surface)
            self._match_count[winner] = self._match_count.get(winner, 0) + 1
            self._match_count[loser] = self._match_count.get(loser, 0) + 1
            n += 1

        dataset_hash = hashlib.sha256(
            df[["winner_name", "loser_name", "match_date"]].to_csv(index=False).encode()
        ).hexdigest()[:16]
        self.params = MarkovParams(
            n_matches=n,
            n_players=len(self._match_count),
            trained_at_utc=datetime.now(timezone.utc).isoformat(),
            dataset_hash=dataset_hash,
        )
        _log.info("[markov] Fitted %d matches, %d players", n, len(self._match_count))

    def _update_serve(
        self, player: str, surface: str, row: Any,
        is_winner: bool, match_date: date
    ) -> None:
        prefix = "w_" if is_winner else "l_"
        svpt = _safe_int(row, f"{prefix}svpt")
        first_in = _safe_int(row, f"{prefix}1stIn")
        first_won = _safe_int(row, f"{prefix}1stWon")
        second_won = _safe_int(row, f"{prefix}2ndWon")
        if svpt is None or svpt < 10:
            return
        p_serve = (first_won + second_won) / svpt if (first_won is not None and second_won is not None) else None
        if p_serve is None or not (0.2 <= p_serve <= 0.95):
            return
        entry = {"p": p_serve, "n": svpt, "date": match_date}
        lst = self._serve[player][surface]
        lst.append(entry)
        if len(lst) > STATS_WINDOW:
            self._serve[player][surface] = lst[-STATS_WINDOW:]

    def _update_elo(self, winner: str, loser: str, surface: str) -> None:
        k = 32.0
        rw = self._elo.get(winner, 1500.0)
        rl = self._elo.get(loser, 1500.0)
        exp_w = 1.0 / (1.0 + 10 ** ((rl - rw) / 400.0))
        self._elo[winner] = rw + k * (1.0 - exp_w)
        self._elo[loser] = rl + k * (0.0 - (1.0 - exp_w))
        rw_s = self._elo_surface[surface].get(winner, 1500.0)
        rl_s = self._elo_surface[surface].get(loser, 1500.0)
        exp_ws = 1.0 / (1.0 + 10 ** ((rl_s - rw_s) / 400.0))
        self._elo_surface[surface][winner] = rw_s + k * (1.0 - exp_ws)
        self._elo_surface[surface][loser] = rl_s + k * (0.0 - (1.0 - exp_ws))

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def get_serve_prob(self, player: str, surface: str) -> float | None:
        """Weighted average p_serve over rolling window. None if too few points."""
        lst = self._serve.get(player, {}).get(surface, [])
        if not lst:
            return None
        total_n = sum(e["n"] for e in lst)
        if total_n < MIN_SERVE_POINTS:
            return None
        return sum(e["p"] * e["n"] for e in lst) / total_n

    def _elo_prob(self, player1: str, player2: str, surface: str) -> float:
        r1 = self._elo_surface[surface].get(player1) or self._elo.get(player1, 1500.0)
        r2 = self._elo_surface[surface].get(player2) or self._elo.get(player2, 1500.0)
        return 1.0 / (1.0 + 10 ** ((r2 - r1) / 400.0))

    def predict_proba(
        self,
        player1: str,
        player2: str,
        surface: str = "hard",
        best_of: int = 3,
    ) -> float:
        """P(player1 beats player2) blending Markov + ELO."""
        surface = surface.lower()
        if surface not in SURFACES:
            surface = "hard"

        p1_serve = self.get_serve_prob(player1, surface)
        p2_serve = self.get_serve_prob(player2, surface)

        elo_prob = self._elo_prob(player1, player2, surface)

        if p1_serve is not None and p2_serve is not None:
            # Full Markov calculation
            # p_serve  = P(player1 wins game when player1 serves)
            p_game_serve = _p_win_game(round(p1_serve, 4))
            # p_return = P(player1 wins game when player2 serves)
            #          = 1 - P(player2 wins game when player2 serves)
            p_game_return = 1.0 - _p_win_game(round(p2_serve, 4))

            markov_prob = _p_win_match(
                p_serve=p_game_serve,
                p_return=p_game_return,
                best_of=best_of,
            )

            # Weight Markov higher when more data
            n1 = sum(e["n"] for e in self._serve.get(player1, {}).get(surface, []))
            n2 = sum(e["n"] for e in self._serve.get(player2, {}).get(surface, []))
            min_n = min(n1, n2)
            # Scale weight: MIN at 200pts, MAX at 1000pts
            weight = MARKOV_WEIGHT_MIN + (MARKOV_WEIGHT_MAX - MARKOV_WEIGHT_MIN) * min(
                1.0, max(0.0, (min_n - MIN_SERVE_POINTS) / (1000 - MIN_SERVE_POINTS))
            )
            prob = weight * markov_prob + (1.0 - weight) * elo_prob
        else:
            # Fall back to ELO only
            prob = elo_prob

        return max(0.05, min(0.95, prob))

    def predict_proba_breakdown(
        self, player1: str, player2: str, surface: str = "hard", best_of: int = 3
    ) -> dict[str, Any]:
        surface = surface.lower()
        if surface not in SURFACES:
            surface = "hard"
        p1_serve = self.get_serve_prob(player1, surface)
        p2_serve = self.get_serve_prob(player2, surface)
        elo_prob = self._elo_prob(player1, player2, surface)
        markov_prob = None
        if p1_serve is not None and p2_serve is not None:
            pg_s = _p_win_game(round(p1_serve, 4))
            pg_r = 1.0 - _p_win_game(round(p2_serve, 4))
            markov_prob = _p_win_match(pg_s, pg_r, best_of)
        prob = self.predict_proba(player1, player2, surface, best_of)
        return {
            "prob": round(prob, 4),
            "elo_prob": round(elo_prob, 4),
            "markov_prob": round(markov_prob, 4) if markov_prob is not None else None,
            "p1_serve": round(p1_serve, 4) if p1_serve else None,
            "p2_serve": round(p2_serve, 4) if p2_serve else None,
            "surface": surface,
        }

    def has_enough_data(self, player: str, min_matches: int = MIN_MATCHES_FOR_SIGNAL) -> bool:
        return self._match_count.get(player, 0) >= min_matches

    def known_players(self) -> list[str]:
        return list(self._match_count.keys())

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Convert defaultdicts to regular dicts for clean pickling
        self._serve = {p: dict(s) for p, s in self._serve.items()}
        with open(path, "wb") as f:
            pickle.dump(self, f)
        _log.info("[markov] Saved → %s", path)

    @classmethod
    def load(cls, path: Path) -> "TennisMarkovModel":
        with open(path, "rb") as f:
            return pickle.load(f)

    def save_meta(self, meta_path: Path, model_path: Path) -> None:
        meta = {
            "model_type": "tennis_markov_v1",
            "sport": "tennis",
            "tour": "ATP",
            "status": "production",
            "n_matches": self.params.n_matches,
            "n_players": self.params.n_players,
            "trained_at_utc": self.params.trained_at_utc,
            "dataset_hash": self.params.dataset_hash,
            "model_path": str(model_path),
            "features": ["serve_point_win_pct", "elo_blend", "markov_chain"],
            "markov_weight_range": [MARKOV_WEIGHT_MIN, MARKOV_WEIGHT_MAX],
        }
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_int(row: Any, col: str) -> int | None:
    try:
        v = row[col]
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return int(v)
    except (KeyError, ValueError, TypeError):
        return None
