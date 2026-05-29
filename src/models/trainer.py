"""Daily training orchestration for production Dixon-Coles models."""

from __future__ import annotations

from dataclasses import dataclass, replace as dc_replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from src.models.calibrator import ProbabilityCalibrator
from src.models.dixon_coles import DixonColesConfig, DixonColesModel, dataset_hash
from src.models.model_registry import ModelRegistry


@dataclass(frozen=True)
class TrainingResult:
    model_id: str
    n_new_matches: int
    brier_score: float
    log_loss: float
    promoted: bool
    dataset_hash: str
    promotion_reason: str


class DailyTrainer:
    """Train, validate, save, and optionally promote a Dixon-Coles model."""

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        staging_dir: Path = Path("data/staging"),
        config: DixonColesConfig | None = None,
        max_brier_score: float | None = 0.60,
        max_log_loss: float | None = 1.20,
    ) -> None:
        self.registry = registry or ModelRegistry()
        self.staging_dir = staging_dir
        self.config = config or DixonColesConfig()
        self.max_brier_score = max_brier_score
        self.max_log_loss = max_log_loss

    def run(self, league: str, cutoff_date: date) -> TrainingResult:
        matches = self._load_matches(league)
        return self.run_on_dataframe(league, cutoff_date, matches)

    def run_on_dataframe(
        self, league: str, cutoff_date: date, matches: pd.DataFrame
    ) -> TrainingResult:
        matches = DixonColesModel.prepare_matches(_filter_league(matches, league))
        train = matches[matches["match_date"] < cutoff_date]
        if train.empty:
            raise ValueError(f"No training matches for {league} before {cutoff_date}")

        brier, log_loss, calibrator = self._validate_oos(train, league)
        previous = self._load_previous_or_none(league)
        previous_count = previous.params.n_matches if previous and previous.params else 0
        model = self._fit_production_model(league, train, previous)
        current_best = self._current_best_brier(league)
        converged = bool(model.params.converged) if model.params else False
        promoted, promotion_reason = self._promotion_decision(
            brier,
            log_loss,
            current_best,
            converged=converged,
        )
        model_id = self.registry.save(
            model,
            league=league,
            metrics={
                "brier_score": brier,
                "log_loss": log_loss,
                "converged": converged,
                "max_brier_score": self.max_brier_score,
                "max_log_loss": self.max_log_loss,
                "promotion_reason": promotion_reason,
            },
            status="production" if promoted else "candidate",
            calibrator=calibrator,
        )
        if promoted:
            self.registry.promote(model_id)

        return TrainingResult(
            model_id=model_id,
            n_new_matches=max(len(train) - previous_count, 0),
            brier_score=brier,
            log_loss=log_loss,
            promoted=promoted,
            dataset_hash=dataset_hash(train),
            promotion_reason=promotion_reason,
        )

    def _load_matches(self, league: str) -> pd.DataFrame:
        candidates = [
            self.staging_dir / f"{league}.csv",
            self.staging_dir / f"{league}_matches.csv",
            self.staging_dir / "football_data_combined.csv",
        ]
        for path in candidates:
            if path.exists():
                return pd.read_csv(path, encoding="latin-1")
        raise FileNotFoundError(f"No staged matches found for league={league}")

    def _load_previous_or_none(self, league: str) -> DixonColesModel | None:
        try:
            return self.registry.load_latest(league)
        except FileNotFoundError:
            return None

    def _current_best_brier(self, league: str) -> float | None:
        scores = [
            item.brier_score
            for item in self.registry.list_versions(league)
            if item.status == "production" and item.brier_score is not None
        ]
        return min(scores) if scores else None

    def _promotion_decision(
        self,
        brier_score: float,
        log_loss: float,
        current_best: float | None,
        converged: bool = True,
    ) -> tuple[bool, str]:
        if not converged:
            return False, "optimizer_did_not_converge"
        if self.max_brier_score is not None and brier_score > self.max_brier_score:
            return (
                False,
                f"brier_score {brier_score:.6f} > max_brier_score {self.max_brier_score:.6f}",
            )
        if self.max_log_loss is not None and log_loss > self.max_log_loss:
            return (
                False,
                f"log_loss {log_loss:.6f} > max_log_loss {self.max_log_loss:.6f}",
            )
        if current_best is None:
            return True, "first_model_passed_absolute_brier_gate"
        if brier_score < current_best:
            return True, f"brier_score {brier_score:.6f} improved current_best {current_best:.6f}"
        return (
            False,
            f"brier_score {brier_score:.6f} did not improve current_best {current_best:.6f}",
        )

    def _fit_production_model(
        self,
        league: str,
        train: pd.DataFrame,
        previous: DixonColesModel | None,
    ) -> DixonColesModel:
        model = previous or DixonColesModel(
            dc_replace(self.config, league=league)
        )
        if previous is None:
            model.fit(train, warm_start=False)
        else:
            model.partial_fit(train)
        return model

    def _validate_oos(
        self, matches: pd.DataFrame, league: str, weeks: int = 8
    ) -> tuple[float, float, ProbabilityCalibrator]:
        max_date = matches["match_date"].max()
        cutoff = max_date - pd.Timedelta(weeks=weeks).to_pytimedelta()
        validation = matches[matches["match_date"] > cutoff]
        validation_train = matches[matches["match_date"] <= cutoff]
        if len(validation_train) < self.config.min_matches or validation.empty:
            validation = matches.tail(min(len(matches), 30))
            validation_train = matches.iloc[: max(len(matches) - len(validation), 0)]
        if len(validation_train) < self.config.min_matches or validation.empty:
            raise ValueError("Not enough pre-holdout matches for leakage-free OOS validation")

        validation_model = DixonColesModel(
            dc_replace(self.config, league=league)
        )
        validation_model.fit(validation_train, warm_start=False)

        probs: list[tuple[float, float, float]] = []
        outcomes: list[int] = []
        for _, row in validation.iterrows():
            probs.append(validation_model.predict_1x2(str(row["home_team"]), str(row["away_team"])))
            home_goals = int(row["home_goals"])
            away_goals = int(row["away_goals"])
            outcomes.append(0 if home_goals > away_goals else 1 if home_goals == away_goals else 2)

        predicted = np.asarray(probs, dtype=float)
        actual = np.asarray(outcomes)

        cal_predicted, test_predicted, cal_actual, test_actual = _split_calibration_and_final(
            predicted,
            actual,
        )

        calibrator = ProbabilityCalibrator()
        calibrator.fit(cal_predicted, cal_actual)

        calibrated_test = calibrator.calibrate(test_predicted)
        brier = calibrator.brier_score(calibrated_test, test_actual)
        ll = calibrator.log_loss(calibrated_test, test_actual)

        return brier, ll, calibrator


LEAGUE_ALIASES: dict[str, set[str]] = {
    "EPL": {"EPL", "E0", "EN1"},
    "E0": {"EPL", "E0", "EN1"},
    "BUNDESLIGA": {"BUNDESLIGA", "D1", "DE1"},
    "D1": {"BUNDESLIGA", "D1", "DE1"},
    "LALIGA": {"LALIGA", "SP1", "ES1"},
    "SP1": {"LALIGA", "SP1", "ES1"},
    "SERIEA": {"SERIEA", "I1", "IT1"},
    "I1": {"SERIEA", "I1", "IT1"},
    "LIGUE1": {"LIGUE1", "F1", "FR1"},
    "F1": {"LIGUE1", "F1", "FR1"},
}


def _filter_league(matches: pd.DataFrame, league: str) -> pd.DataFrame:
    league_cols = [col for col in ("source_league", "Div", "league") if col in matches.columns]
    if not league_cols:
        return matches
    allowed = LEAGUE_ALIASES.get(_league_key(league), {_league_key(league)})
    mask = pd.Series(False, index=matches.index)
    for col in league_cols:
        mask = mask | matches[col].map(lambda value: _league_key(value) in allowed)
    return matches[mask].copy()


def _league_key(value: object) -> str:
    return str(value).strip().replace(" ", "").replace("-", "").replace(".", "").upper()


def _split_calibration_and_final(
    predicted: np.ndarray,
    actual: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if len(predicted) < 2:
        raise ValueError("Need at least two validation rows for calibration/final split")
    mid = max(len(predicted) // 2, 1)
    test_predicted = predicted[mid:]
    test_actual = actual[mid:]
    if len(test_predicted) == 0:
        raise ValueError("Final validation split is empty")
    return predicted[:mid], test_predicted, actual[:mid], test_actual
