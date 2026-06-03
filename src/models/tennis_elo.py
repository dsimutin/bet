"""Surface-specific ELO model for ATP tennis match probability estimation.

V2 enhancements over V1:
- Rolling serve/return statistics per surface (hold%, break%, serve win%)
- H2H record weighted by recency and surface match
- Combined prediction blending ELO + serve/return stats
- Days-since-last-match feature (fatigue/rest signal)

Rating updates are chronological but not time-decayed. Do not claim ELO rating
decay in metadata unless a dated decay update is explicitly implemented and
backtested.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import pickle
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

_log = logging.getLogger(__name__)

ELO_K = 32.0
ELO_START = 1500.0
ELO_SCALE = 400.0
MIN_MATCHES_FOR_SIGNAL = 15
SURFACES = ("clay", "grass", "hard", "carpet")

# Rolling window for serve/return stats
STATS_WINDOW = 50  # last N surface matches for rolling serve/return stats

# H2H weighting: match from N days ago has weight exp(-ln2 * N/365)
H2H_HALF_LIFE_DAYS = 365
# Minimum H2H matches on surface to use surface-specific H2H
MIN_H2H_SURFACE = 3


def _default_surface_dict() -> dict[str, list]:
    return defaultdict(list)


@dataclass
class TennisEloParams:
    n_matches: int = 0
    n_players: int = 0
    trained_at_utc: str = ""
    dataset_hash: str = ""
    converged: bool = True


class TennisEloModel:
    """Surface-aware ELO model with serve/return stats and H2H for ATP tennis."""

    def __init__(
        self,
        k: float = ELO_K,
        start: float = ELO_START,
        scale: float = ELO_SCALE,
        decay_half_life: float | None = None,
    ) -> None:
        self.k = k
        self.start = start
        self.scale = scale
        # Backward-compatible attribute for older pickles. Current ELO rating
        # updates are not time-decayed; only H2H adjustment uses recency weights.
        self.decay_half_life = decay_half_life

        # ELO ratings
        self._overall: dict[str, float] = {}
        self._surface: dict[str, dict[str, float]] = {s: {} for s in SURFACES}

        # Match counts
        self._total_matches: dict[str, int] = {}
        self._surface_matches: dict[str, dict[str, int]] = {s: {} for s in SURFACES}

        # Rolling stats: player → surface → deque of stat dicts
        # Each dict: {svpt_won_pct, hold, break_pct, date}
        self._serve_stats: dict[str, dict[str, list[dict]]] = defaultdict(_default_surface_dict)

        # H2H: (p1, p2) canonical key → list of {winner, surface, date}
        self._h2h: dict[str, list[dict]] = defaultdict(list)

        # Last match date per player
        self._last_match: dict[str, date] = {}

        # Recent form: player → list of {won: bool, surface, tourney_level, date}
        self._recent_form: dict[str, list[dict]] = defaultdict(list)
        # Retirements: player → list of dates when they retired (loser who RET)
        self._retirements: dict[str, list[date]] = defaultdict(list)

        self.params: TennisEloParams = TennisEloParams()

    # ---------------------------------------------------------------------------
    # Training
    # ---------------------------------------------------------------------------

    def fit(self, matches: pd.DataFrame) -> None:
        """Build ratings and stats from historical matches in chronological order."""
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

            self._update_elo(winner, loser, surface)
            self._update_serve_stats(winner, loser, surface, row)
            self._update_h2h(winner, loser, surface, match_date)
            self._update_form(winner, loser, surface, match_date, row)
            self._last_match[winner] = match_date
            self._last_match[loser] = match_date
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
            "[tennis_elo] Fitted %d matches, %d players",
            self.params.n_matches,
            self.params.n_players,
        )

    # ---------------------------------------------------------------------------
    # Prediction
    # ---------------------------------------------------------------------------

    def predict_proba(
        self,
        player1: str,
        player2: str,
        surface: str = "hard",
        match_date: date | None = None,
    ) -> float:
        """
        Return P(player1 beats player2) on surface.

        Combines:
          1. Surface ELO (primary)
          2. Serve/return advantage
          3. H2H adjustment (small, recency-weighted)
        """
        surface = surface.lower()
        if surface not in SURFACES:
            surface = "hard"

        elo_prob = self._elo_prob(player1, player2, surface)
        serve_adj = self._serve_return_adjustment(player1, player2, surface)
        h2h_adj = self._h2h_adjustment(player1, player2, surface)

        # Blend: ELO is primary (weight 0.75), serve adj (0.15), H2H (0.10)
        raw = elo_prob * 0.75 + (elo_prob + serve_adj) * 0.15 + (elo_prob + h2h_adj) * 0.10

        # Apply schedule fatigue adjustment
        ref_date = match_date or date.today()
        fatigue_adj_p1 = self.get_schedule_fatigue_adj(player1, ref_date)
        fatigue_adj_p2 = self.get_schedule_fatigue_adj(player2, ref_date)
        raw += (fatigue_adj_p1 - fatigue_adj_p2) * 0.5

        # Clip to avoid extreme probabilities
        return max(0.05, min(0.95, raw))

    def predict_proba_breakdown(
        self,
        player1: str,
        player2: str,
        surface: str = "hard",
        match_date: date | None = None,
    ) -> dict[str, float]:
        """Return detailed breakdown of probability components."""
        surface = surface.lower()
        if surface not in SURFACES:
            surface = "hard"
        elo_prob = self._elo_prob(player1, player2, surface)
        serve_adj = self._serve_return_adjustment(player1, player2, surface)
        h2h_adj = self._h2h_adjustment(player1, player2, surface)
        final = self.predict_proba(player1, player2, surface, match_date=match_date)
        ref_date = match_date or date.today()
        fatigue_adj = self.get_schedule_fatigue_adj(player1, ref_date)
        opp_fatigue_adj = self.get_schedule_fatigue_adj(player2, ref_date)
        return {
            "elo_prob": round(elo_prob, 4),
            "serve_adj": round(serve_adj, 4),
            "h2h_adj": round(h2h_adj, 4),
            "final_prob": round(final, 4),
            "fatigue_adj": round(fatigue_adj, 4),
            "opp_fatigue_adj": round(opp_fatigue_adj, 4),
        }

    def days_since_last_match(self, player: str, as_of: date | None = None) -> int | None:
        """Return days since player's last recorded match, or None if unknown."""
        last = self._last_match.get(player)
        if last is None:
            return None
        ref = as_of or date.today()
        return (ref - last).days

    def get_matches_last_n_days(self, player: str, reference_date: date, n: int = 7) -> int:
        """Count how many matches the player played in the last n days before reference_date."""
        entries = self._recent_form.get(player)
        if not entries:
            return 0
        cutoff = reference_date - timedelta(days=n)
        count = 0
        for entry in entries:
            entry_date = entry.get("date")
            if entry_date is not None and cutoff < entry_date < reference_date:
                count += 1
        return count

    def get_schedule_fatigue_adj(self, player: str, reference_date: date) -> float:
        """Return a probability adjustment for schedule fatigue: 0.0 to -0.05.

        Rules:
          - 3+ matches in last 4 days → -0.04
          - 2 matches in last 2 days → -0.02
          - else → 0.0
        """
        if self.get_matches_last_n_days(player, reference_date, n=4) >= 3:
            return -0.04
        if self.get_matches_last_n_days(player, reference_date, n=2) >= 2:
            return -0.02
        return 0.0

    def get_hold_rate(self, player: str, surface: str) -> float | None:
        """Return rolling hold rate on surface, or None if insufficient data."""
        stats = self._serve_stats[player][surface]
        if len(stats) < 5:
            return None
        recent = stats[-STATS_WINDOW:]
        holds = [s["hold"] for s in recent if s["hold"] is not None]
        return sum(holds) / len(holds) if holds else None

    def get_serve_win_pct(self, player: str, surface: str) -> float | None:
        """Return rolling % of service points won on surface."""
        stats = self._serve_stats[player][surface]
        if len(stats) < 5:
            return None
        recent = stats[-STATS_WINDOW:]
        vals = [s["svpt_won_pct"] for s in recent if s["svpt_won_pct"] is not None]
        return sum(vals) / len(vals) if vals else None

    def get_match_count(self, player: str) -> int:
        return self._total_matches.get(player, 0)

    def has_enough_data(self, player: str, min_matches: int = MIN_MATCHES_FOR_SIGNAL) -> bool:
        return self.get_match_count(player) >= min_matches

    def get_overall_rating(self, player: str) -> float:
        return self._overall.get(player, self.start)

    def known_players(self) -> list[str]:
        return list(self._overall.keys())

    def get_recent_form(self, player: str, surface: str | None = None, n: int = 10) -> float | None:
        """Win rate over last N matches (optionally surface-filtered). None if <3 matches."""
        entries = self._recent_form.get(player, [])
        if surface:
            entries = [e for e in entries if e["surface"] == surface.lower()]
        if len(entries) < 3:
            return None
        recent = entries[-n:]
        return sum(1 for e in recent if e["won"]) / len(recent)

    def retired_recently(self, player: str, days: int = 14) -> bool:
        """True if player's LAST match was a retirement within `days` days.

        If the player has played any match AFTER the retirement, they've recovered
        and this returns False.
        """
        retirements = self._retirements.get(player, [])
        if not retirements:
            return False
        last_retirement = retirements[-1]
        last_match = self._last_match.get(player)

        # Player played after the retirement → recovered
        if last_match and last_match > last_retirement:
            return False

        # Only flag if retirement was very recent (within `days` days)
        cutoff = date.today() - timedelta(days=days)
        return last_retirement >= cutoff

    def get_tourney_level_winrate(self, player: str, level: str) -> float | None:
        """Win rate on specific tourney level: G=GrandSlam, M=Masters, A=250/500."""
        entries = [e for e in self._recent_form.get(player, []) if e.get("tourney_level") == level]
        if len(entries) < 3:
            return None
        return sum(1 for e in entries if e["won"]) / len(entries)

    # ---------------------------------------------------------------------------
    # Persistence
    # ---------------------------------------------------------------------------

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
            "model_type": "tennis_elo_v2",
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
            "rating_decay_applied": False,
            "h2h_half_life_days": H2H_HALF_LIFE_DAYS,
            "features": ["surface_elo", "serve_return_stats", "h2h_recency", "schedule_fatigue"],
        }
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _update_form(
        self, winner: str, loser: str, surface: str, match_date: date, row: Any
    ) -> None:
        """Track recent match outcomes and retirements."""
        level = str(row.get("tourney_level", "A") or "A")
        score = str(row.get("score", "") or "")
        is_retirement = "RET" in score.upper() or "W/O" in score.upper()

        self._recent_form[winner].append(
            {"won": True, "surface": surface, "tourney_level": level, "date": match_date}
        )
        self._recent_form[loser].append(
            {"won": False, "surface": surface, "tourney_level": level, "date": match_date}
        )
        # Keep only last 50 entries
        if len(self._recent_form[winner]) > 50:
            self._recent_form[winner] = self._recent_form[winner][-50:]
        if len(self._recent_form[loser]) > 50:
            self._recent_form[loser] = self._recent_form[loser][-50:]

        if is_retirement and match_date:
            self._retirements[loser].append(match_date)

    def _elo_prob(self, player1: str, player2: str, surface: str) -> float:
        """Pure ELO win probability with surface fallback."""
        r1_surf = self._surface[surface].get(player1)
        r2_surf = self._surface[surface].get(player2)
        surf_n1 = self._surface_matches[surface].get(player1, 0)
        surf_n2 = self._surface_matches[surface].get(player2, 0)

        if r1_surf is not None and r2_surf is not None and surf_n1 >= 5 and surf_n2 >= 5:
            r1, r2 = r1_surf, r2_surf
        else:
            r1 = self._overall.get(player1, self.start)
            r2 = self._overall.get(player2, self.start)

        return 1.0 / (1.0 + 10.0 ** ((r2 - r1) / self.scale))

    def _serve_return_adjustment(self, player1: str, player2: str, surface: str) -> float:
        """
        Adjustment to win prob based on serve dominance difference.

        Logic: if p1 wins 65% of service points and p2 wins 55%, p1 has an
        advantage beyond what ELO captures (especially for recent form).
        Returns a small prob adjustment (-0.05 to +0.05).
        """
        sv1 = self.get_serve_win_pct(player1, surface)
        sv2 = self.get_serve_win_pct(player2, surface)
        if sv1 is None or sv2 is None:
            return 0.0
        # Difference in serve dominance, scaled conservatively
        diff = sv1 - sv2  # e.g., 0.10 if p1 wins 10pp more service points
        return diff * 0.3  # scale down — serve stats captured partly by ELO already

    def _h2h_adjustment(self, player1: str, player2: str, surface: str) -> float:
        """
        Small probability adjustment from recency-weighted H2H.

        Returns value in range roughly -0.04 to +0.04.
        Only applied when there are ≥3 meetings on this surface.
        """
        key = _h2h_key(player1, player2)
        records = self._h2h.get(key, [])
        if not records:
            return 0.0

        # Filter to this surface if enough data, otherwise use all
        surf_records = [r for r in records if r["surface"] == surface]
        use = surf_records if len(surf_records) >= MIN_H2H_SURFACE else records
        if len(use) < 2:
            return 0.0

        today = date.today()
        p1_wins = 0.0
        total_weight = 0.0
        for r in use:
            match_date = r.get("date")
            if match_date is None:
                w = 1.0
            else:
                days_ago = max(0, (today - match_date).days)
                w = math.exp(-math.log(2) * days_ago / H2H_HALF_LIFE_DAYS)
            total_weight += w
            if r["winner"] == player1:
                p1_wins += w

        if total_weight < 0.01:
            return 0.0

        h2h_rate = p1_wins / total_weight  # weighted win rate for p1
        # Convert to adjustment: h2h_rate=0.5 → 0, h2h_rate=0.7 → small positive
        # Scale conservatively: max ±0.04
        return (h2h_rate - 0.5) * 0.08

    def _update_elo(self, winner: str, loser: str, surface: str) -> None:
        """Update ELO ratings. K-factor slightly boosted for bigger upsets."""
        rw = self._overall.get(winner, self.start)
        rl = self._overall.get(loser, self.start)
        exp_w = 1.0 / (1.0 + 10.0 ** ((rl - rw) / self.scale))

        # Slight K boost for upsets (winner was underdog)
        k_adj = self.k * (1.0 + max(0.0, 0.5 - exp_w))

        self._overall[winner] = rw + k_adj * (1.0 - exp_w)
        self._overall[loser] = rl + k_adj * (0.0 - (1.0 - exp_w))

        surf = self._surface[surface]
        rws = surf.get(winner, self.start)
        rls = surf.get(loser, self.start)
        exp_ws = 1.0 / (1.0 + 10.0 ** ((rls - rws) / self.scale))
        surf[winner] = rws + k_adj * (1.0 - exp_ws)
        surf[loser] = rls + k_adj * (0.0 - (1.0 - exp_ws))

        self._total_matches[winner] = self._total_matches.get(winner, 0) + 1
        self._total_matches[loser] = self._total_matches.get(loser, 0) + 1
        sm = self._surface_matches[surface]
        sm[winner] = sm.get(winner, 0) + 1
        sm[loser] = sm.get(loser, 0) + 1

    def _update_serve_stats(self, winner: str, loser: str, surface: str, row: Any) -> None:
        """Append rolling serve/return stats for both players from one match row."""

        def _stat(player: str, prefix: str) -> dict:
            svpt = _safe_float(row, f"{prefix}_svpt")
            first_in = _safe_float(row, f"{prefix}_1stIn")
            first_won = _safe_float(row, f"{prefix}_1stWon")
            second_won = _safe_float(row, f"{prefix}_2ndWon")
            sv_gms = _safe_float(row, f"{prefix}_SvGms")
            bp_saved = _safe_float(row, f"{prefix}_bpSaved")
            bp_faced = _safe_float(row, f"{prefix}_bpFaced")

            svpt_won_pct = None
            hold = None
            if svpt and svpt > 0:
                total_won = (first_won or 0) + (second_won or 0)
                svpt_won_pct = total_won / svpt
            if sv_gms and sv_gms > 0 and bp_faced is not None:
                # hold = 1 - (break rate)
                # We approximate: a break occurs when bp_faced > 0 and a bp was NOT saved
                games_broken = (bp_faced or 0) - (bp_saved or 0)
                hold = max(0.0, 1.0 - games_broken / sv_gms) if sv_gms else None

            return {"svpt_won_pct": svpt_won_pct, "hold": hold}

        w_stat = _stat(winner, "w")
        l_stat = _stat(loser, "l")

        self._serve_stats[winner][surface].append(w_stat)
        self._serve_stats[loser][surface].append(l_stat)

        # Keep only the last STATS_WINDOW entries
        if len(self._serve_stats[winner][surface]) > STATS_WINDOW:
            self._serve_stats[winner][surface] = self._serve_stats[winner][surface][-STATS_WINDOW:]
        if len(self._serve_stats[loser][surface]) > STATS_WINDOW:
            self._serve_stats[loser][surface] = self._serve_stats[loser][surface][-STATS_WINDOW:]

    def _update_h2h(self, winner: str, loser: str, surface: str, match_date: Any) -> None:
        key = _h2h_key(winner, loser)
        if isinstance(match_date, pd.Timestamp):
            match_date = match_date.date()
        self._h2h[key].append({"winner": winner, "surface": surface, "date": match_date})


def _h2h_key(p1: str, p2: str) -> str:
    """Canonical sorted key for H2H lookup regardless of player order."""
    return "|".join(sorted([p1, p2]))


def _safe_float(row: Any, col: str) -> float | None:
    try:
        v = row[col]
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return float(v)
    except (KeyError, TypeError, ValueError):
        return None
