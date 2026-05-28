"""Dixon-Coles style time-decayed football score model."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy.optimize import minimize

Selection = Literal["home", "draw", "away"]
SELECTIONS: tuple[Selection, Selection, Selection] = ("home", "draw", "away")


@dataclass(frozen=True)
class DixonColesConfig:
    bookmaker_prefix: str = "B365"
    min_train_matches: int = 120
    max_goals: int = 8
    time_decay: float = 0.002
    l2_penalty: float = 0.001
    max_iterations: int = 200


@dataclass(frozen=True)
class DixonColesPrediction:
    event_id: str
    match_date: str
    home_team: str
    away_team: str
    selection: Selection
    probability: float
    expected_home_goals: float
    expected_away_goals: float


class DixonColesModel:
    """Fits attack/defense strengths with low-score Dixon-Coles adjustment."""

    OUTCOME_TO_SELECTION: dict[str, Selection] = {"H": "home", "D": "draw", "A": "away"}

    def __init__(self, config: DixonColesConfig | None = None) -> None:
        self.config = config or DixonColesConfig()
        self.teams_: list[str] = []
        self.attack_: dict[str, float] = {}
        self.defense_: dict[str, float] = {}
        self.home_advantage_: float = 0.0
        self.rho_: float = 0.0
        self.global_home_goals_: float = 1.35
        self.global_away_goals_: float = 1.10

    def fit(self, matches: pd.DataFrame) -> DixonColesModel:
        prepared = self.prepare_matches(matches)
        if len(prepared) < self.config.min_train_matches:
            return self

        self.teams_ = sorted(set(prepared["home_team"]) | set(prepared["away_team"]))
        self.global_home_goals_ = max(float(prepared["goals_home_ft"].mean()), 0.1)
        self.global_away_goals_ = max(float(prepared["goals_away_ft"].mean()), 0.1)
        initial = np.zeros(2 * len(self.teams_) + 2, dtype=float)
        initial[-2] = math.log(self.global_home_goals_ / self.global_away_goals_)

        result = minimize(
            self._negative_log_likelihood,
            initial,
            args=(prepared,),
            method="L-BFGS-B",
            options={"maxiter": self.config.max_iterations, "ftol": 1e-8},
        )
        params = result.x if result.success else initial
        attacks, defenses, home_advantage, rho = self._unpack_params(params)
        self.attack_ = dict(zip(self.teams_, attacks, strict=True))
        self.defense_ = dict(zip(self.teams_, defenses, strict=True))
        self.home_advantage_ = home_advantage
        self.rho_ = rho
        return self

    def predict_match(self, row: pd.Series) -> list[DixonColesPrediction]:
        home_team = str(row["home_team"])
        away_team = str(row["away_team"])
        home_lambda, away_lambda = self._expected_goals(home_team, away_team)
        probabilities = self._match_probabilities(home_lambda, away_lambda, self.rho_)
        event_id = self._event_id(row)
        match_date = self._date_to_iso(row["match_date"])
        return [
            DixonColesPrediction(
                event_id=event_id,
                match_date=match_date,
                home_team=home_team,
                away_team=away_team,
                selection=selection,
                probability=round(probabilities[selection], 6),
                expected_home_goals=round(home_lambda, 4),
                expected_away_goals=round(away_lambda, 4),
            )
            for selection in SELECTIONS
        ]

    def prepare_matches(self, matches: pd.DataFrame) -> pd.DataFrame:
        if matches.empty:
            return matches.copy()
        df = matches.copy()
        df = df.rename(
            columns={
                "Date": "match_date",
                "HomeTeam": "home_team",
                "AwayTeam": "away_team",
                "FTHG": "goals_home_ft",
                "FTAG": "goals_away_ft",
                "FTR": "result_ft",
            }
        )
        required = {
            "match_date",
            "home_team",
            "away_team",
            "goals_home_ft",
            "goals_away_ft",
            "result_ft",
        }
        missing = required - set(df.columns)
        if missing:
            raise KeyError(f"Missing required Dixon-Coles columns: {sorted(missing)}")

        df["match_date"] = pd.to_datetime(df["match_date"], dayfirst=True, errors="coerce").dt.date
        for col in ("goals_home_ft", "goals_away_ft"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(
            subset=[
                "match_date",
                "home_team",
                "away_team",
                "goals_home_ft",
                "goals_away_ft",
                "result_ft",
            ]
        )
        df = df[df["result_ft"].isin(["H", "D", "A"])]
        return df.sort_values(["match_date", "home_team", "away_team"]).reset_index(drop=True)

    def _negative_log_likelihood(self, params: np.ndarray, matches: pd.DataFrame) -> float:
        attacks, defenses, home_advantage, rho = self._unpack_params(params)
        team_index = {team: idx for idx, team in enumerate(self.teams_)}
        max_date = matches["match_date"].max()
        nll = 0.0

        for _, row in matches.iterrows():
            home_idx = team_index[str(row["home_team"])]
            away_idx = team_index[str(row["away_team"])]
            home_goals = int(row["goals_home_ft"])
            away_goals = int(row["goals_away_ft"])
            home_lambda = math.exp(home_advantage + attacks[home_idx] + defenses[away_idx])
            away_lambda = math.exp(attacks[away_idx] + defenses[home_idx])
            tau = self._tau(home_goals, away_goals, home_lambda, away_lambda, rho)
            log_prob = (
                math.log(max(tau, 1e-9))
                + self._log_poisson(home_goals, home_lambda)
                + self._log_poisson(away_goals, away_lambda)
            )
            age_days = max((max_date - row["match_date"]).days, 0)
            weight = math.exp(-self.config.time_decay * age_days)
            nll -= weight * log_prob

        penalty = self.config.l2_penalty * float(np.sum(params[:-2] ** 2))
        return nll + penalty

    def _unpack_params(self, params: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
        n_teams = len(self.teams_)
        raw_attacks = params[:n_teams]
        raw_defenses = params[n_teams : 2 * n_teams]
        attacks = raw_attacks - float(np.mean(raw_attacks))
        defenses = raw_defenses - float(np.mean(raw_defenses))
        home_advantage = float(params[-2])
        rho = float(0.2 * math.tanh(params[-1]))
        return attacks, defenses, home_advantage, rho

    def _expected_goals(self, home_team: str, away_team: str) -> tuple[float, float]:
        home_attack = self.attack_.get(home_team, 0.0)
        away_attack = self.attack_.get(away_team, 0.0)
        home_defense = self.defense_.get(home_team, 0.0)
        away_defense = self.defense_.get(away_team, 0.0)
        home_lambda = math.exp(self.home_advantage_ + home_attack + away_defense)
        away_lambda = math.exp(away_attack + home_defense)
        return (min(max(home_lambda, 0.05), 6.0), min(max(away_lambda, 0.05), 6.0))

    def _match_probabilities(
        self,
        home_lambda: float,
        away_lambda: float,
        rho: float,
    ) -> dict[Selection, float]:
        totals: dict[Selection, float] = {"home": 0.0, "draw": 0.0, "away": 0.0}
        for home_goals in range(self.config.max_goals + 1):
            home_prob = self._poisson_pmf(home_goals, home_lambda)
            for away_goals in range(self.config.max_goals + 1):
                away_prob = self._poisson_pmf(away_goals, away_lambda)
                tau = self._tau(home_goals, away_goals, home_lambda, away_lambda, rho)
                probability = max(tau, 0.0) * home_prob * away_prob
                if home_goals > away_goals:
                    totals["home"] += probability
                elif home_goals == away_goals:
                    totals["draw"] += probability
                else:
                    totals["away"] += probability
        total = sum(totals.values())
        if total <= 0:
            return {"home": 1.0 / 3.0, "draw": 1.0 / 3.0, "away": 1.0 / 3.0}
        return {selection: totals[selection] / total for selection in SELECTIONS}

    def _tau(
        self,
        home_goals: int,
        away_goals: int,
        home_lambda: float,
        away_lambda: float,
        rho: float,
    ) -> float:
        if home_goals == 0 and away_goals == 0:
            return 1.0 - home_lambda * away_lambda * rho
        if home_goals == 0 and away_goals == 1:
            return 1.0 + home_lambda * rho
        if home_goals == 1 and away_goals == 0:
            return 1.0 + away_lambda * rho
        if home_goals == 1 and away_goals == 1:
            return 1.0 - rho
        return 1.0

    def _poisson_pmf(self, goals: int, expected_goals: float) -> float:
        return math.exp(-expected_goals) * expected_goals**goals / math.factorial(goals)

    def _log_poisson(self, goals: int, expected_goals: float) -> float:
        return -expected_goals + goals * math.log(expected_goals) - math.lgamma(goals + 1)

    def _event_id(self, row: pd.Series) -> str:
        source_event_id = self._optional_row_str(row, "source_event_id")
        if source_event_id:
            return source_event_id
        match_date = self._date_to_iso(row["match_date"])
        home = str(row["home_team"]).strip().lower().replace(" ", "_")
        away = str(row["away_team"]).strip().lower().replace(" ", "_")
        return f"soccer__{home}__{away}__{match_date}"

    def _optional_row_str(self, row: pd.Series, key: str) -> str | None:
        value = row.get(key)
        if value is None or pd.isna(value):
            return None
        text = str(value).strip()
        return text or None

    def _date_to_iso(self, value: Any) -> str:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)
