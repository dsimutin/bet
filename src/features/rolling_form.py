"""Rolling form features — last-N match statistics per team.

Computes per-team rolling averages (goals, wins, points) from historical
match data, respecting a cutoff date for anti-leakage compliance.

Usage:
    builder = RollingFormBuilder(window=5)
    builder.build(history_df, cutoff_date=date.today())
    features = builder.features_for_match("Arsenal", "Chelsea")
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class TeamFormFeatures:
    team: str
    n_matches: int
    wins: int
    draws: int
    losses: int
    goals_scored_mean: float
    goals_conceded_mean: float
    points_per_game: float
    win_rate: float


@dataclass(frozen=True)
class MatchFormFeatures:
    home_team: str
    away_team: str
    home_wins: int
    home_goals_scored_mean: float
    home_goals_conceded_mean: float
    home_points_per_game: float
    home_win_rate: float
    away_wins: int
    away_goals_scored_mean: float
    away_goals_conceded_mean: float
    away_points_per_game: float
    away_win_rate: float
    goals_diff_mean: float  # home_scored_mean − away_scored_mean
    form_advantage: float  # home_ppg − away_ppg


class RollingFormBuilder:
    """Builds rolling form features from historical matches."""

    def __init__(self, window: int = 5) -> None:
        self.window = window
        self._team_records: dict[str, list[dict[str, Any]]] = {}

    def build(self, matches: pd.DataFrame, cutoff_date: date | None = None) -> RollingFormBuilder:
        """
        Ingest historical matches, optionally capped at ``cutoff_date``.
        Call before any ``features_for_*`` method.
        """
        df = _prepare(matches)
        if cutoff_date is not None:
            df = df[df["match_date"] < cutoff_date]
        df = df.sort_values("match_date")

        records: dict[str, list[dict[str, Any]]] = {}
        for _, row in df.iterrows():
            home = str(row["home_team"])
            away = str(row["away_team"])
            hg, ag = int(row["home_goals"]), int(row["away_goals"])

            _push(
                records,
                home,
                {
                    "goals_scored": hg,
                    "goals_conceded": ag,
                    "win": int(hg > ag),
                    "draw": int(hg == ag),
                    "loss": int(hg < ag),
                },
            )
            _push(
                records,
                away,
                {
                    "goals_scored": ag,
                    "goals_conceded": hg,
                    "win": int(ag > hg),
                    "draw": int(ag == hg),
                    "loss": int(ag < hg),
                },
            )

        self._team_records = records
        return self

    @property
    def form_history(self) -> dict[str, list[dict[str, Any]]]:
        """All team-match records (for debugging and inspection)."""
        return dict(self._team_records)

    def team_form(self, team: str) -> TeamFormFeatures:
        records = self._team_records.get(team, [])[-self.window :]
        n = len(records)
        if n == 0:
            return TeamFormFeatures(
                team=team,
                n_matches=0,
                wins=0,
                draws=0,
                losses=0,
                goals_scored_mean=0.0,
                goals_conceded_mean=0.0,
                points_per_game=0.0,
                win_rate=0.0,
            )
        wins = sum(r["win"] for r in records)
        draws = sum(r["draw"] for r in records)
        losses = sum(r["loss"] for r in records)
        gs_mean = sum(r["goals_scored"] for r in records) / n
        gc_mean = sum(r["goals_conceded"] for r in records) / n
        ppg = (wins * 3 + draws) / n
        return TeamFormFeatures(
            team=team,
            n_matches=n,
            wins=wins,
            draws=draws,
            losses=losses,
            goals_scored_mean=round(gs_mean, 4),
            goals_conceded_mean=round(gc_mean, 4),
            points_per_game=round(ppg, 4),
            win_rate=round(wins / n, 4),
        )

    def features_for_match(self, home_team: str, away_team: str) -> MatchFormFeatures:
        h = self.team_form(home_team)
        a = self.team_form(away_team)
        return MatchFormFeatures(
            home_team=home_team,
            away_team=away_team,
            home_wins=h.wins,
            home_goals_scored_mean=h.goals_scored_mean,
            home_goals_conceded_mean=h.goals_conceded_mean,
            home_points_per_game=h.points_per_game,
            home_win_rate=h.win_rate,
            away_wins=a.wins,
            away_goals_scored_mean=a.goals_scored_mean,
            away_goals_conceded_mean=a.goals_conceded_mean,
            away_points_per_game=a.points_per_game,
            away_win_rate=a.win_rate,
            goals_diff_mean=round(h.goals_scored_mean - a.goals_scored_mean, 4),
            form_advantage=round(h.points_per_game - a.points_per_game, 4),
        )

    def add_form_features(self, candidates: pd.DataFrame) -> pd.DataFrame:
        """Attach rolling form columns to a candidate match DataFrame."""
        df = candidates.copy()
        home_col = _find_col(df, ("home_team", "HomeTeam"))
        away_col = _find_col(df, ("away_team", "AwayTeam"))
        if home_col is None or away_col is None:
            return df

        rows = []
        for _, row in df.iterrows():
            feat = self.features_for_match(str(row[home_col]), str(row[away_col]))
            rows.append(
                {
                    "form_home_wins": feat.home_wins,
                    "form_home_gs_mean": feat.home_goals_scored_mean,
                    "form_home_gc_mean": feat.home_goals_conceded_mean,
                    "form_home_ppg": feat.home_points_per_game,
                    "form_home_win_rate": feat.home_win_rate,
                    "form_away_wins": feat.away_wins,
                    "form_away_gs_mean": feat.away_goals_scored_mean,
                    "form_away_gc_mean": feat.away_goals_conceded_mean,
                    "form_away_ppg": feat.away_points_per_game,
                    "form_away_win_rate": feat.away_win_rate,
                    "form_goals_diff_mean": feat.goals_diff_mean,
                    "form_advantage": feat.form_advantage,
                }
            )
        return pd.concat([df.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _prepare(matches: pd.DataFrame) -> pd.DataFrame:
    df = matches.copy()
    remap = {
        "Date": "match_date",
        "date": "match_date",
        "HomeTeam": "home_team",
        "AwayTeam": "away_team",
        "FTHG": "home_goals",
        "FTAG": "away_goals",
        "goals_home_ft": "home_goals",
        "goals_away_ft": "away_goals",
    }
    df = df.rename(columns={k: v for k, v in remap.items() if k in df.columns})
    required = {"match_date", "home_team", "away_team", "home_goals", "away_goals"}
    if not required.issubset(df.columns):
        raise KeyError(f"RollingFormBuilder: missing {required - set(df.columns)}")
    df["match_date"] = pd.to_datetime(df["match_date"], dayfirst=True, errors="coerce").dt.date
    for col in ("home_goals", "away_goals"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=list(required)).reset_index(drop=True)


def _push(records: dict[str, list[Any]], team: str, record: dict[str, Any]) -> None:
    records.setdefault(team, []).append(record)


def _find_col(df: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None
