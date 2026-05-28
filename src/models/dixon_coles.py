"""Production Dixon-Coles model with versionable parameters.

This module is intentionally separate from ``dixon_coles_model.py``.  The older
module is used inside probability benchmarks; this one is the stable model API
for training, registry storage, calibration, and signal attribution.
"""

from __future__ import annotations

import hashlib
import logging
import math
import warnings
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DixonColesParams:
    attack: dict[str, float]
    defense: dict[str, float]
    home_advantage: float
    rho: float
    intercept: float
    league: str
    trained_on_dates: tuple[date, date]
    n_matches: int
    dataset_hash: str
    converged: bool = True


@dataclass(frozen=True)
class DixonColesConfig:
    league: str = "EPL"
    max_goals: int = 10
    xi: float = 0.0018
    l2_penalty: float = 0.001
    max_iterations: int = 200
    min_matches: int = 20


class DixonColesModel:
    """Dixon-Coles low-score-corrected Poisson football model."""

    def __init__(self, config: DixonColesConfig | None = None) -> None:
        self.config = config or DixonColesConfig()
        self.params: DixonColesParams | None = None
        self.model_id: str | None = None
        self._teams: list[str] = []
        self._training_matches = pd.DataFrame()

    def fit(self, matches: pd.DataFrame, warm_start: bool = True) -> None:
        prepared = self.prepare_matches(matches)
        if len(prepared) < self.config.min_matches:
            raise ValueError(
                f"Need at least {self.config.min_matches} matches, got {len(prepared)}"
            )

        teams = sorted(set(prepared["home_team"]) | set(prepared["away_team"]))
        self._teams = teams
        initial = self._initial_vector(teams, warm_start=warm_start)

        result = minimize(
            self._negative_log_likelihood,
            initial,
            args=(prepared, teams),
            method="L-BFGS-B",
            options={"maxiter": self.config.max_iterations, "ftol": 1e-8},
        )
        if not result.success:
            warnings.warn(
                f"DixonColes optimization did not converge for league={self.config.league}: "
                f"{result.message}",
                RuntimeWarning,
                stacklevel=2,
            )
            _log.warning(
                "DixonColes optimization did not converge (league=%s): %s",
                self.config.league,
                result.message,
            )
        vector = result.x  # always use best-found point, not the silent-zero initial
        attack, defense, home_advantage, rho, intercept = self._unpack(vector, teams)

        self.params = DixonColesParams(
            attack={team: round(float(attack[idx]), 8) for idx, team in enumerate(teams)},
            defense={team: round(float(defense[idx]), 8) for idx, team in enumerate(teams)},
            home_advantage=round(float(home_advantage), 8),
            rho=round(float(rho), 8),
            intercept=round(float(intercept), 8),
            league=self.config.league,
            trained_on_dates=(prepared["match_date"].min(), prepared["match_date"].max()),
            n_matches=int(len(prepared)),
            dataset_hash=dataset_hash(prepared),
            converged=bool(result.success),
        )
        self._training_matches = prepared

    def partial_fit(self, new_matches: pd.DataFrame) -> None:
        new_prepared = self.prepare_matches(new_matches)
        if new_prepared.empty:
            return
        if not self._training_matches.empty:
            last_date = self._training_matches["match_date"].max()
            new_prepared = new_prepared[new_prepared["match_date"] > last_date]
        if new_prepared.empty:
            return
        combined = pd.concat([self._training_matches, new_prepared], ignore_index=True)
        self.fit(combined, warm_start=True)

    def predict_1x2(self, home: str, away: str) -> tuple[float, float, float]:
        matrix = self._score_matrix(home, away)
        home_prob = float(np.tril(matrix, k=-1).sum())
        draw_prob = float(np.trace(matrix))
        away_prob = float(np.triu(matrix, k=1).sum())
        return _normalize_triplet((home_prob, draw_prob, away_prob))

    def predict_ou(self, home: str, away: str, total: float) -> tuple[float, float]:
        matrix = self._score_matrix(home, away)
        over = 0.0
        under = 0.0
        for home_goals in range(matrix.shape[0]):
            for away_goals in range(matrix.shape[1]):
                if home_goals + away_goals > total:
                    over += float(matrix[home_goals, away_goals])
                else:
                    under += float(matrix[home_goals, away_goals])
        total_prob = over + under
        return (over / total_prob, under / total_prob) if total_prob else (0.5, 0.5)

    def predict_btts(self, home: str, away: str) -> tuple[float, float]:
        matrix = self._score_matrix(home, away)
        yes = float(matrix[1:, 1:].sum())
        no = float(matrix.sum()) - yes
        total_prob = yes + no
        return (yes / total_prob, no / total_prob) if total_prob else (0.5, 0.5)

    def expected_goals(self, home: str, away: str) -> tuple[float, float]:
        params = self._require_params()
        home_lambda = math.exp(
            params.intercept
            + params.home_advantage
            + params.attack.get(home, 0.0)
            + params.defense.get(away, 0.0)
        )
        away_lambda = math.exp(
            params.intercept + params.attack.get(away, 0.0) + params.defense.get(home, 0.0)
        )
        return (min(max(home_lambda, 0.03), 8.0), min(max(away_lambda, 0.03), 8.0))

    def to_dict(self) -> dict[str, Any]:
        params = self._require_params()
        return asdict(params)

    @classmethod
    def prepare_matches(cls, matches: pd.DataFrame) -> pd.DataFrame:
        df = matches.copy()
        df = df.rename(
            columns={
                "Date": "match_date",
                "date": "match_date",
                "HomeTeam": "home_team",
                "AwayTeam": "away_team",
                "FTHG": "home_goals",
                "FTAG": "away_goals",
                "goals_home_ft": "home_goals",
                "goals_away_ft": "away_goals",
            }
        )
        required = {"match_date", "home_team", "away_team", "home_goals", "away_goals"}
        missing = required - set(df.columns)
        if missing:
            raise KeyError(f"Missing Dixon-Coles columns: {sorted(missing)}")
        df["match_date"] = pd.to_datetime(df["match_date"], dayfirst=True, errors="coerce").dt.date
        for col in ("home_goals", "away_goals"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["match_date", "home_team", "away_team", "home_goals", "away_goals"])
        df["home_goals"] = df["home_goals"].astype(int)
        df["away_goals"] = df["away_goals"].astype(int)
        return df.sort_values(["match_date", "home_team", "away_team"]).reset_index(drop=True)

    def temporal_weight(self, match_date: date, today: date) -> float:
        days_diff = max((today - match_date).days, 0)
        return math.exp(-self.config.xi * days_diff)

    def _initial_vector(self, teams: list[str], warm_start: bool) -> np.ndarray:
        vector = np.zeros(2 * len(teams) + 3, dtype=float)
        vector[-3] = 0.25
        vector[-2] = 0.0
        vector[-1] = math.log(1.25)
        if not warm_start or self.params is None:
            return vector
        for idx, team in enumerate(teams):
            vector[idx] = self.params.attack.get(team, 0.0)
            vector[len(teams) + idx] = self.params.defense.get(team, 0.0)
        vector[-3] = self.params.home_advantage
        vector[-2] = math.atanh(max(min(self.params.rho / 0.3, 0.999), -0.999))
        vector[-1] = self.params.intercept
        return vector

    def _negative_log_likelihood(
        self,
        vector: np.ndarray,
        matches: pd.DataFrame,
        teams: list[str],
    ) -> float:
        attack, defense, home_advantage, rho, intercept = self._unpack(vector, teams)
        team_index = {team: idx for idx, team in enumerate(teams)}
        max_date = matches["match_date"].max()
        nll = 0.0
        for _, row in matches.iterrows():
            home_idx = team_index[str(row["home_team"])]
            away_idx = team_index[str(row["away_team"])]
            home_lambda = math.exp(
                intercept + home_advantage + attack[home_idx] + defense[away_idx]
            )
            away_lambda = math.exp(intercept + attack[away_idx] + defense[home_idx])
            home_goals = int(row["home_goals"])
            away_goals = int(row["away_goals"])
            tau = _tau(home_goals, away_goals, home_lambda, away_lambda, rho)
            log_prob = (
                math.log(max(tau, 1e-12))
                + _log_poisson(home_goals, home_lambda)
                + _log_poisson(away_goals, away_lambda)
            )
            nll -= self.temporal_weight(row["match_date"], max_date) * log_prob
        penalty = self.config.l2_penalty * float(np.sum(vector[:-3] ** 2))
        return nll + penalty

    def _unpack(
        self, vector: np.ndarray, teams: list[str]
    ) -> tuple[np.ndarray, np.ndarray, float, float, float]:
        n_teams = len(teams)
        raw_attack = vector[:n_teams]
        raw_defense = vector[n_teams : 2 * n_teams]
        attack = raw_attack - float(np.mean(raw_attack))
        defense = raw_defense - float(np.mean(raw_defense))
        home_advantage = float(vector[-3])
        rho = float(0.3 * math.tanh(vector[-2]))
        intercept = float(vector[-1])
        return attack, defense, home_advantage, rho, intercept

    def _score_matrix(self, home: str, away: str) -> np.ndarray:
        params = self._require_params()
        home_lambda, away_lambda = self.expected_goals(home, away)
        matrix = np.zeros((self.config.max_goals + 1, self.config.max_goals + 1), dtype=float)
        for home_goals in range(self.config.max_goals + 1):
            for away_goals in range(self.config.max_goals + 1):
                matrix[home_goals, away_goals] = (
                    max(_tau(home_goals, away_goals, home_lambda, away_lambda, params.rho), 0.0)
                    * _poisson_pmf(home_goals, home_lambda)
                    * _poisson_pmf(away_goals, away_lambda)
                )
        total = float(matrix.sum())
        return matrix / total if total > 0 else matrix

    def _require_params(self) -> DixonColesParams:
        if self.params is None:
            raise ValueError("DixonColesModel is not fitted")
        return self.params


def dataset_hash(matches: pd.DataFrame) -> str:
    prepared = DixonColesModel.prepare_matches(matches)
    stable = prepared[["match_date", "home_team", "away_team", "home_goals", "away_goals"]].copy()
    stable["match_date"] = stable["match_date"].astype(str)
    payload = stable.to_csv(index=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _tau(
    home_goals: int,
    away_goals: int,
    home_lambda: float,
    away_lambda: float,
    rho: float,
) -> float:
    if home_goals == 0 and away_goals == 0:
        return 1.0 - home_lambda * away_lambda * rho
    if home_goals == 0 and away_goals == 1:
        return 1.0 - home_lambda * rho
    if home_goals == 1 and away_goals == 0:
        return 1.0 - away_lambda * rho
    if home_goals == 1 and away_goals == 1:
        return 1.0 - rho
    return 1.0


def _poisson_pmf(goals: int, expected_goals: float) -> float:
    return math.exp(-expected_goals) * expected_goals**goals / math.factorial(goals)


def _log_poisson(goals: int, expected_goals: float) -> float:
    return -expected_goals + goals * math.log(max(expected_goals, 1e-12)) - math.lgamma(goals + 1)


def _normalize_triplet(values: tuple[float, float, float]) -> tuple[float, float, float]:
    total = sum(values)
    if total <= 0:
        return (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)
    return (values[0] / total, values[1] / total, values[2] / total)
