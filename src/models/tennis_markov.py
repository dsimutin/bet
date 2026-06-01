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
MARKOV_WEIGHT_MAX = 0.40   # when player has lots of serve data
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


def _set_score_probs(p_serve: float, p_return: float,
                     a_serves_first: bool = True) -> dict[tuple[int, int], float]:
    """P(set ends with score ga-gb) for all reachable scores.

    Returns dict mapping (games_a, games_b) → probability.
    Used to compute expected total games per set.
    """
    # DP: state → probability of reaching it
    from collections import defaultdict as _dd
    probs: dict[tuple, float] = {(0, 0, a_serves_first): 1.0}
    result: dict[tuple[int, int], float] = _dd(float)

    queue = [(0, 0, a_serves_first)]
    seen = set(queue)

    while queue:
        next_q = []
        for state in queue:
            ga, gb, a_serves = state
            p = probs[state]
            # Check terminal
            if (ga >= 6 and ga - gb >= 2) or (ga == 7 and gb == 6):
                result[(ga, gb)] += p
                continue
            if (gb >= 6 and gb - ga >= 2) or (gb == 7 and ga == 6):
                result[(ga, gb)] += p
                continue
            if ga == 6 and gb == 6:
                # Tiebreak
                p_tb = (p_serve + p_return) / 2.0
                p_win_tb = _p_win_tiebreak(round(p_tb, 4))
                result[(7, 6)] += p * p_win_tb
                result[(6, 7)] += p * (1.0 - p_win_tb)
                continue

            p_game = p_serve if a_serves else p_return
            s_win = (ga + 1, gb, not a_serves)
            s_lose = (ga, gb + 1, not a_serves)
            for s, w in ((s_win, p_game), (s_lose, 1.0 - p_game)):
                probs[s] = probs.get(s, 0.0) + p * w
                if s not in seen:
                    seen.add(s)
                    next_q.append(s)
        queue = next_q

    return dict(result)


def _expected_games_per_set(p_serve: float, p_return: float,
                             a_serves_first: bool = True) -> float:
    """Expected number of games in a single set."""
    dist = _set_score_probs(p_serve, p_return, a_serves_first)
    return sum((ga + gb) * prob for (ga, gb), prob in dist.items())


def _p_win_set_with_games(p_serve: float, p_return: float,
                           a_serves_first: bool = True) -> tuple[float, float, float]:
    """Returns (P(A wins set), E[games | A wins], E[games | B wins])."""
    dist = _set_score_probs(p_serve, p_return, a_serves_first)
    p_a_wins = 0.0
    games_a_wins = 0.0
    games_b_wins = 0.0
    for (ga, gb), prob in dist.items():
        total = ga + gb
        if ga > gb:
            p_a_wins += prob
            games_a_wins += prob * total
        else:
            games_b_wins += prob * total
    p_b_wins = 1.0 - p_a_wins
    e_games_a = games_a_wins / p_a_wins if p_a_wins > 0 else 0.0
    e_games_b = games_b_wins / p_b_wins if p_b_wins > 0 else 0.0
    return p_a_wins, e_games_a, e_games_b


def _set_score_match_dist(p_serve: float, p_return: float,
                           best_of: int = 3,
                           a_serves_first: bool = True) -> dict[tuple[int, int], float]:
    """P(match ends sa-sb sets) for each possible set score.

    Returns dict like {(2,0): 0.45, (2,1): 0.30, (0,2): 0.15, (1,2): 0.10}
    """
    sets_needed = (best_of + 1) // 2
    memo: dict[tuple, float] = {}

    def dp(sa: int, sb: int, a_starts: bool) -> dict[tuple[int, int], float]:
        if sa == sets_needed:
            return {(sa, sb): 1.0}
        if sb == sets_needed:
            return {(sa, sb): 1.0}
        key = (sa, sb, a_starts)
        if key in memo:
            return {key: memo[key]}  # can't memoize dicts cleanly, use flat DP

        result: dict[tuple[int, int], float] = {}
        ps = _p_win_set(p_serve, p_return, a_starts)
        for score, prob in dp(sa + 1, sb, not a_starts).items():
            result[score] = result.get(score, 0.0) + ps * prob
        for score, prob in dp(sa, sb + 1, not a_starts).items():
            result[score] = result.get(score, 0.0) + (1.0 - ps) * prob
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
        # Check live stats override first (injected from Tennis Abstract)
        if hasattr(self, "_live_serve") and self._live_serve:
            live = self._live_serve.get(player, {}).get(surface)
            if live is None:
                # Last-name fallback
                last = player.strip().split()[-1].lower()
                for name, surf_map in self._live_serve.items():
                    if name.strip().split()[-1].lower() == last:
                        live = surf_map.get(surface)
                        break
            if live is not None and 0.3 <= live <= 0.85:
                return live

        lst = self._serve.get(player, {}).get(surface, [])
        if not lst:
            return None
        total_n = sum(e["n"] for e in lst)
        if total_n < MIN_SERVE_POINTS:
            return None
        return sum(e["p"] * e["n"] for e in lst) / total_n

    def inject_live_serve_stats(
        self, stats: dict[str, dict[str, float]]
    ) -> None:
        """Override serve stats with live data from Tennis Abstract.

        stats: {player_name: {surface: p_serve}}
        Called before predictions so current-season stats take priority.
        """
        self._live_serve = stats
        _log.info("[markov] Injected live serve stats for %d players", len(stats))

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
            # Discrepancy dampening: if Markov and ELO disagree by >15pp,
            # halve the Markov weight — serve stats alone can't override strong ELO signal
            if abs(markov_prob - elo_prob) > 0.15:
                weight *= 0.5
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

    def predict_set_score_probs(
        self, player1: str, player2: str, surface: str = "hard", best_of: int = 3
    ) -> dict[tuple[int, int], float]:
        """P(match ends sa-sb) for each set score, e.g. {(2,0): 0.45, (2,1): 0.30, ...}"""
        surface = surface.lower()
        if surface not in SURFACES:
            surface = "hard"
        p1_serve = self.get_serve_prob(player1, surface)
        p2_serve = self.get_serve_prob(player2, surface)
        if p1_serve is None or p2_serve is None:
            # Fallback: use ELO-based single probability, assume typical split
            p = self.predict_proba(player1, player2, surface, best_of)
            sets_needed = (best_of + 1) // 2
            if best_of == 3:
                p20 = p * p
                p21 = p * (1 - p) * p * 2
                p02 = (1 - p) ** 2
                p12 = (1 - p) * p * (1 - p) * 2
                return {(2, 0): p20, (2, 1): p21, (0, 2): p02, (1, 2): p12}
            else:
                # BO5 rough approximation
                p30 = p ** 3
                p31 = 3 * p ** 3 * (1 - p)
                p32 = 6 * p ** 3 * (1 - p) ** 2
                q = 1 - p
                return {(3, 0): p30, (3, 1): p31, (3, 2): p32,
                        (0, 3): q**3, (1, 3): 3*q**3*p, (2, 3): 6*q**3*p**2}
        pg_s = _p_win_game(round(p1_serve, 4))
        pg_r = 1.0 - _p_win_game(round(p2_serve, 4))
        return _set_score_match_dist(pg_s, pg_r, best_of=best_of)

    def predict_total_games_over(
        self, threshold: float, player1: str, player2: str,
        surface: str = "hard", best_of: int = 3,
    ) -> float:
        """P(total games in match > threshold).

        Used for over/under totals market (e.g. threshold=37.5).
        """
        surface = surface.lower()
        if surface not in SURFACES:
            surface = "hard"
        p1_serve = self.get_serve_prob(player1, surface)
        p2_serve = self.get_serve_prob(player2, surface)
        if p1_serve is None or p2_serve is None:
            return 0.5  # no data
        pg_s = _p_win_game(round(p1_serve, 4))
        pg_r = 1.0 - _p_win_game(round(p2_serve, 4))
        set_score_dist = _set_score_match_dist(pg_s, pg_r, best_of=best_of)

        # For each possible match outcome, estimate expected total games
        # We approximate: given a set score (sa, sb), the total games =
        # sum of expected games per won/lost set
        p_a_wins_set, e_games_a_win, e_games_b_win = _p_win_set_with_games(pg_s, pg_r)

        p_over = 0.0
        for (sa, sb), p_score in set_score_dist.items():
            n_sets = sa + sb
            # Expected total games for this set score
            e_total = sa * e_games_a_win + sb * e_games_b_win
            # Very rough: treat as certain if e_total >> threshold
            # Better: use the variance. For now, use a soft threshold based on
            # typical set length variance (~3 games std per set)
            import math as _math
            std = _math.sqrt(n_sets) * 3.0  # rough std of total games
            if std < 0.5:
                p_over += p_score * (1.0 if e_total > threshold else 0.0)
            else:
                # Normal approximation
                z = (e_total - threshold) / std
                # Logistic approximation to normal CDF
                p_over += p_score * (1.0 / (1.0 + _math.exp(-1.7 * z)))
        return max(0.05, min(0.95, p_over))

    def predict_set_handicap(
        self, handicap: float, player1: str, player2: str,
        surface: str = "hard", best_of: int = 3,
    ) -> float:
        """P(player1 covers set handicap).

        handicap > 0 means player1 gives sets (favourite, e.g. -1.5).
        Returns P(player1 wins by more than |handicap| sets).
        E.g. handicap=-1.5: P(player1 wins 2-0 in BO3).
        """
        dist = self.predict_set_score_probs(player1, player2, surface, best_of)
        p_cover = 0.0
        for (sa, sb), prob in dist.items():
            net = sa - sb  # positive means player1 is ahead
            if net - handicap > 0:
                p_cover += prob
        return max(0.05, min(0.95, p_cover))

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
