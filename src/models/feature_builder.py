"""Leakage-aware feature building for model diagnostics and future ML layers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from src.models.dixon_coles import DixonColesModel


@dataclass(frozen=True)
class MatchInfo:
    home_team: str
    away_team: str
    match_date: date


@dataclass(frozen=True)
class MatchFeatures:
    home_attack: float
    away_attack: float
    home_defense: float
    away_defense: float
    home_form_5: float
    away_form_5: float
    days_since_last_match_home: int | None
    days_since_last_match_away: int | None
    is_home_game: bool = True


class FeatureBuilder:
    """Builds only features available before ``cutoff_ts``."""

    def __init__(self, model: DixonColesModel, matches: pd.DataFrame) -> None:
        self.model = model
        self.matches = DixonColesModel.prepare_matches(matches)

    def build(self, match: MatchInfo, cutoff_ts: datetime) -> MatchFeatures:
        cutoff_date = cutoff_ts.date()
        params = self.model.params
        if params is None:
            raise ValueError("Dixon-Coles model must be fitted before feature building")
        return MatchFeatures(
            home_attack=params.attack.get(match.home_team, 0.0),
            away_attack=params.attack.get(match.away_team, 0.0),
            home_defense=params.defense.get(match.home_team, 0.0),
            away_defense=params.defense.get(match.away_team, 0.0),
            home_form_5=self._rolling_points(match.home_team, n=5, cutoff=cutoff_date),
            away_form_5=self._rolling_points(match.away_team, n=5, cutoff=cutoff_date),
            days_since_last_match_home=self._days_since_last_match(match.home_team, cutoff_date),
            days_since_last_match_away=self._days_since_last_match(match.away_team, cutoff_date),
        )

    def _rolling_points(self, team: str, n: int, cutoff: date) -> float:
        team_matches = self._past_team_matches(team, cutoff).tail(n)
        if team_matches.empty:
            return 0.0
        points = 0
        for _, row in team_matches.iterrows():
            home = str(row["home_team"]) == team
            goals_for = int(row["home_goals"] if home else row["away_goals"])
            goals_against = int(row["away_goals"] if home else row["home_goals"])
            if goals_for > goals_against:
                points += 3
            elif goals_for == goals_against:
                points += 1
        return points / (3.0 * len(team_matches))

    def _days_since_last_match(self, team: str, cutoff: date) -> int | None:
        team_matches = self._past_team_matches(team, cutoff)
        if team_matches.empty:
            return None
        last_date = team_matches["match_date"].max()
        return int((cutoff - last_date).days)

    def _past_team_matches(self, team: str, cutoff: date) -> pd.DataFrame:
        df = self.matches[
            (self.matches["match_date"] < cutoff)
            & ((self.matches["home_team"] == team) | (self.matches["away_team"] == team))
        ]
        return df.sort_values("match_date")


def match_info_from_row(row: pd.Series) -> MatchInfo:
    raw_date: Any = row.get("match_date", row.get("Date"))
    parsed = pd.to_datetime(raw_date, dayfirst=True).date()
    return MatchInfo(
        home_team=str(row.get("home_team", row.get("HomeTeam"))),
        away_team=str(row.get("away_team", row.get("AwayTeam"))),
        match_date=parsed,
    )
