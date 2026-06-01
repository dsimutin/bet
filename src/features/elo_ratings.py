"""Elo rating system for football teams — leakage-aware feature for signal generation.

Usage:
    elo = EloRatingSystem()
    elo.build_from_matches(history_df, cutoff_date=date.today())
    diff = elo.elo_diff("Arsenal", "Chelsea")   # positive → Arsenal favoured
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class EloSnapshot:
    ratings: dict[str, float]
    built_from_date: str
    built_to_date: str
    n_matches: int
    k_factor: float
    home_advantage: float
    initial_rating: float
    created_at_utc: str


class EloRatingSystem:
    """Elo rating system with home-advantage correction and temporal anti-leakage."""

    def __init__(
        self,
        k_factor: float = 32.0,
        initial_rating: float = 1500.0,
        home_advantage: float = 100.0,
    ) -> None:
        self.k = k_factor
        self.initial = initial_rating
        self.home_advantage = home_advantage
        self.ratings: dict[str, float] = {}
        self._n_matches: int = 0
        self._date_range: tuple[date | None, date | None] = (None, None)

    # ------------------------------------------------------------------
    # Core Elo mechanics
    # ------------------------------------------------------------------

    def expected_score(self, rating_a: float, rating_b: float) -> float:
        return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))

    def update(
        self,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int,
    ) -> None:
        r_home_base = self.ratings.get(home_team, self.initial)
        r_away_base = self.ratings.get(away_team, self.initial)
        # Apply home-advantage bonus for expected score only
        exp_home = self.expected_score(r_home_base + self.home_advantage, r_away_base)
        exp_away = 1.0 - exp_home

        if home_goals > away_goals:
            actual_home, actual_away = 1.0, 0.0
        elif home_goals < away_goals:
            actual_home, actual_away = 0.0, 1.0
        else:
            actual_home, actual_away = 0.5, 0.5

        self.ratings[home_team] = r_home_base + self.k * (actual_home - exp_home)
        self.ratings[away_team] = r_away_base + self.k * (actual_away - exp_away)
        self._n_matches += 1

    def get(self, team: str) -> float:
        return self.ratings.get(team, self.initial)

    def elo_diff(self, home_team: str, away_team: str) -> float:
        """Home Elo minus Away Elo (positive → home stronger)."""
        return self.get(home_team) - self.get(away_team)

    def elo_win_prob(self, home_team: str, away_team: str) -> float:
        """Elo-implied win probability for home team (includes home advantage)."""
        return self.expected_score(self.get(home_team) + self.home_advantage, self.get(away_team))

    # ------------------------------------------------------------------
    # Build from history — LEAKAGE-SAFE
    # ------------------------------------------------------------------

    def build_from_matches(
        self,
        matches: pd.DataFrame,
        cutoff_date: date | None = None,
    ) -> EloRatingSystem:
        """
        Populate ratings from a historical match DataFrame.

        Only matches strictly before ``cutoff_date`` are used, preventing
        look-ahead leakage when computing features for a given trade date.
        """
        df = _prepare_matches(matches)
        if cutoff_date is not None:
            df = df[df["match_date"] < cutoff_date]
        if df.empty:
            return self

        self.ratings = {}
        self._n_matches = 0
        self._date_range = (df["match_date"].min(), df["match_date"].max())

        for _, row in df.sort_values("match_date").iterrows():
            self.update(
                str(row["home_team"]),
                str(row["away_team"]),
                int(row["home_goals"]),
                int(row["away_goals"]),
            )
        return self

    # ------------------------------------------------------------------
    # Feature generation for signal pipeline
    # ------------------------------------------------------------------

    def add_elo_features(self, candidates: pd.DataFrame) -> pd.DataFrame:
        """Attach elo_home, elo_away, elo_diff, elo_win_prob columns to candidate df."""
        df = candidates.copy()
        home_col = _find_col(df, ("home_team", "HomeTeam", "home"))
        away_col = _find_col(df, ("away_team", "AwayTeam", "away"))
        if home_col is None or away_col is None:
            return df

        df["elo_home"] = df[home_col].apply(lambda t: round(self.get(str(t)), 2))
        df["elo_away"] = df[away_col].apply(lambda t: round(self.get(str(t)), 2))
        df["elo_diff"] = df.apply(
            lambda row: round(self.elo_diff(str(row[home_col]), str(row[away_col])), 2),
            axis=1,
        )
        df["elo_win_prob_home"] = df.apply(
            lambda row: round(self.elo_win_prob(str(row[home_col]), str(row[away_col])), 4),
            axis=1,
        )
        return df

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_snapshot(self) -> EloSnapshot:
        lo, hi = self._date_range
        return EloSnapshot(
            ratings=dict(self.ratings),
            built_from_date=lo.isoformat() if lo else "",
            built_to_date=hi.isoformat() if hi else "",
            n_matches=self._n_matches,
            k_factor=self.k,
            home_advantage=self.home_advantage,
            initial_rating=self.initial,
            created_at_utc=datetime.now(timezone.utc).isoformat(),
        )

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self.to_snapshot()), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, path: Path | str) -> EloRatingSystem:
        raw: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        obj = cls(
            k_factor=float(raw.get("k_factor", 32.0)),
            initial_rating=float(raw.get("initial_rating", 1500.0)),
            home_advantage=float(raw.get("home_advantage", 100.0)),
        )
        obj.ratings = {str(k): float(v) for k, v in raw.get("ratings", {}).items()}
        obj._n_matches = int(raw.get("n_matches", 0))
        lo_str = raw.get("built_from_date", "")
        hi_str = raw.get("built_to_date", "")
        lo = date.fromisoformat(lo_str) if lo_str else None
        hi = date.fromisoformat(hi_str) if hi_str else None
        obj._date_range = (lo, hi)
        return obj


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _prepare_matches(matches: pd.DataFrame) -> pd.DataFrame:
    df = matches.copy()
    col_map = {
        "Date": "match_date",
        "date": "match_date",
        "HomeTeam": "home_team",
        "AwayTeam": "away_team",
        "FTHG": "home_goals",
        "FTAG": "away_goals",
        "goals_home_ft": "home_goals",
        "goals_away_ft": "away_goals",
        "score_home_ft": "home_goals",
        "score_away_ft": "away_goals",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    required = {"match_date", "home_team", "away_team", "home_goals", "away_goals"}
    if not required.issubset(df.columns):
        missing = required - set(df.columns)
        raise KeyError(f"EloRatingSystem: missing columns {sorted(missing)}")
    df["match_date"] = pd.to_datetime(df["match_date"], dayfirst=True, errors="coerce").dt.date
    for col in ("home_goals", "away_goals"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=list(required)).reset_index(drop=True)


def _find_col(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        if name in df.columns:
            return name
    return None
