"""Rolling home-advantage feature — per-team and per-league.

Measures whether a team is performing above or below its historical
home advantage over a rolling window. Useful as a signal adjuster.

Usage:
    builder = HomeAdvantageBuilder(window=10)
    builder.build(history_df, cutoff_date=date.today())
    features = builder.features_for_match("Arsenal", "Chelsea")
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class HomeAdvantageFeatures:
    home_team: str
    away_team: str
    home_win_rate_home_games: float     # fraction of home wins in last N home games
    away_win_rate_away_games: float     # fraction of away wins in last N away games
    home_goals_at_home_mean: float      # avg goals scored at home
    away_goals_conceded_away_mean: float
    home_advantage_score: float         # composite (home_wr_home - away_wr_away)


class HomeAdvantageBuilder:
    """Rolling home-advantage features per team."""

    def __init__(self, window: int = 10) -> None:
        self.window = window
        self._home_records: dict[str, list[dict]] = {}
        self._away_records: dict[str, list[dict]] = {}

    def build(
        self, matches: pd.DataFrame, cutoff_date: date | None = None
    ) -> HomeAdvantageBuilder:
        df = _prepare(matches)
        if cutoff_date is not None:
            df = df[df["match_date"] < cutoff_date]
        df = df.sort_values("match_date")

        home_rec: dict[str, list[dict]] = {}
        away_rec: dict[str, list[dict]] = {}
        for _, row in df.iterrows():
            home = str(row["home_team"])
            away = str(row["away_team"])
            hg, ag = int(row["home_goals"]), int(row["away_goals"])
            home_rec.setdefault(home, []).append({
                "goals_scored": hg, "win": int(hg > ag),
            })
            away_rec.setdefault(away, []).append({
                "goals_conceded": hg, "win": int(ag > hg),
            })

        self._home_records = home_rec
        self._away_records = away_rec
        return self

    @property
    def advantage_history(self) -> dict[str, list[dict]]:
        """All home-game records per team (for debugging and inspection)."""
        return dict(self._home_records)

    def features_for_match(
        self, home_team: str, away_team: str
    ) -> HomeAdvantageFeatures:
        h_recs = self._home_records.get(home_team, [])[-self.window :]
        a_recs = self._away_records.get(away_team, [])[-self.window :]

        h_win_rate = _safe_mean([r["win"] for r in h_recs])
        a_win_rate = _safe_mean([r["win"] for r in a_recs])
        h_gs_mean = _safe_mean([r["goals_scored"] for r in h_recs])
        a_gc_mean = _safe_mean([r["goals_conceded"] for r in a_recs])

        return HomeAdvantageFeatures(
            home_team=home_team,
            away_team=away_team,
            home_win_rate_home_games=round(h_win_rate, 4),
            away_win_rate_away_games=round(a_win_rate, 4),
            home_goals_at_home_mean=round(h_gs_mean, 4),
            away_goals_conceded_away_mean=round(a_gc_mean, 4),
            home_advantage_score=round(h_win_rate - a_win_rate, 4),
        )

    def add_home_advantage_features(self, candidates: pd.DataFrame) -> pd.DataFrame:
        df = candidates.copy()
        home_col = _find_col(df, ("home_team", "HomeTeam"))
        away_col = _find_col(df, ("away_team", "AwayTeam"))
        if home_col is None or away_col is None:
            return df

        rows = []
        for _, row in df.iterrows():
            feat = self.features_for_match(str(row[home_col]), str(row[away_col]))
            rows.append({
                "ha_home_win_rate": feat.home_win_rate_home_games,
                "ha_away_win_rate": feat.away_win_rate_away_games,
                "ha_home_gs_mean": feat.home_goals_at_home_mean,
                "ha_away_gc_mean": feat.away_goals_conceded_away_mean,
                "ha_score": feat.home_advantage_score,
            })
        return pd.concat(
            [df.reset_index(drop=True), pd.DataFrame(rows)], axis=1
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _safe_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _prepare(matches: pd.DataFrame) -> pd.DataFrame:
    df = matches.copy()
    remap = {
        "Date": "match_date", "date": "match_date",
        "HomeTeam": "home_team", "AwayTeam": "away_team",
        "FTHG": "home_goals", "FTAG": "away_goals",
        "goals_home_ft": "home_goals", "goals_away_ft": "away_goals",
    }
    df = df.rename(columns={k: v for k, v in remap.items() if k in df.columns})
    required = {"match_date", "home_team", "away_team", "home_goals", "away_goals"}
    if not required.issubset(df.columns):
        raise KeyError(f"HomeAdvantageBuilder: missing {required - set(df.columns)}")
    df["match_date"] = pd.to_datetime(df["match_date"], dayfirst=True, errors="coerce").dt.date
    for col in ("home_goals", "away_goals"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=list(required)).reset_index(drop=True)


def _find_col(df: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None
