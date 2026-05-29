"""Rest days feature — days since each team's last match.

Fatigue is a strong signal in football: teams with fewer rest days
underperform against their Elo/market expectations.

Usage:
    builder = RestDaysBuilder()
    builder.build(history_df, cutoff_date=date.today())
    features = builder.features_for_match("Arsenal", "Chelsea", match_date=date(2026, 6, 1))
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class RestDaysFeatures:
    home_team: str
    away_team: str
    home_rest_days: int | None      # None if first known match
    away_rest_days: int | None
    rest_days_diff: int | None      # home_rest - away_rest (positive → home rested)
    home_schedule_congestion: int   # matches in last 14 days
    away_schedule_congestion: int


class RestDaysBuilder:
    """Computes rest-days and schedule-congestion features from historical match data."""

    def __init__(self) -> None:
        self._last_match: dict[str, date] = {}
        self._all_dates: dict[str, list[date]] = {}

    def build(
        self, matches: pd.DataFrame, cutoff_date: date | None = None
    ) -> RestDaysBuilder:
        """
        Ingest historical matches, optionally capped at ``cutoff_date``.
        Must be called before any ``features_for_match`` call.
        """
        df = _prepare(matches)
        if cutoff_date is not None:
            df = df[df["match_date"] < cutoff_date]
        df = df.sort_values("match_date")

        last: dict[str, date] = {}
        all_dates: dict[str, list[date]] = {}

        for _, row in df.iterrows():
            match_date = row["match_date"]
            for team in (str(row["home_team"]), str(row["away_team"])):
                last[team] = match_date
                all_dates.setdefault(team, []).append(match_date)

        self._last_match = last
        self._all_dates = all_dates
        return self

    def features_for_match(
        self,
        home_team: str,
        away_team: str,
        match_date: date | None = None,
        congestion_window_days: int = 14,
    ) -> RestDaysFeatures:
        effective_date = match_date or date.today()

        home_last = self._last_match.get(home_team)
        away_last = self._last_match.get(away_team)

        home_rest = (effective_date - home_last).days if home_last else None
        away_rest = (effective_date - away_last).days if away_last else None
        diff = (home_rest - away_rest) if (home_rest is not None and away_rest is not None) else None

        home_congestion = self._congestion(home_team, effective_date, congestion_window_days)
        away_congestion = self._congestion(away_team, effective_date, congestion_window_days)

        return RestDaysFeatures(
            home_team=home_team,
            away_team=away_team,
            home_rest_days=home_rest,
            away_rest_days=away_rest,
            rest_days_diff=diff,
            home_schedule_congestion=home_congestion,
            away_schedule_congestion=away_congestion,
        )

    def add_rest_features(
        self, candidates: pd.DataFrame, congestion_window_days: int = 14
    ) -> pd.DataFrame:
        """Attach rest-days columns to a candidate match DataFrame."""
        df = candidates.copy()
        home_col = _find_col(df, ("home_team", "HomeTeam"))
        away_col = _find_col(df, ("away_team", "AwayTeam"))
        date_col = _find_col(df, ("match_date", "Date", "event_date"))

        if home_col is None or away_col is None:
            return df

        rows = []
        for _, row in df.iterrows():
            match_date = None
            if date_col and not pd.isna(row.get(date_col)):
                try:
                    raw = row[date_col]
                    if isinstance(raw, date):
                        match_date = raw
                    else:
                        parsed = pd.to_datetime(raw, dayfirst=True, errors="coerce")
                        match_date = parsed.date() if not pd.isna(parsed) else None
                except Exception:
                    pass
            feat = self.features_for_match(
                str(row[home_col]),
                str(row[away_col]),
                match_date=match_date,
                congestion_window_days=congestion_window_days,
            )
            rows.append({
                "rest_home_days": feat.home_rest_days,
                "rest_away_days": feat.away_rest_days,
                "rest_days_diff": feat.rest_days_diff,
                "schedule_congestion_home": feat.home_schedule_congestion,
                "schedule_congestion_away": feat.away_schedule_congestion,
            })
        return pd.concat(
            [df.reset_index(drop=True), pd.DataFrame(rows)], axis=1
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _congestion(self, team: str, before_date: date, window_days: int) -> int:
        dates = self._all_dates.get(team, [])
        cutoff = before_date - pd.Timedelta(days=window_days).to_pytimedelta()
        return sum(1 for d in dates if cutoff <= d < before_date)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _prepare(matches: pd.DataFrame) -> pd.DataFrame:
    df = matches.copy()
    remap = {
        "Date": "match_date", "date": "match_date",
        "HomeTeam": "home_team", "AwayTeam": "away_team",
    }
    df = df.rename(columns={k: v for k, v in remap.items() if k in df.columns})
    required = {"match_date", "home_team", "away_team"}
    if not required.issubset(df.columns):
        raise KeyError(f"RestDaysBuilder: missing {required - set(df.columns)}")
    df["match_date"] = pd.to_datetime(df["match_date"], dayfirst=True, errors="coerce").dt.date
    return df.dropna(subset=["match_date", "home_team", "away_team"]).reset_index(drop=True)


def _find_col(df: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None
