"""Walk-forward probability benchmarks for football 1X2 models."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from src.models.dixon_coles import DixonColesConfig, DixonColesModel
from src.models.historical_value_model import HistoricalValueModel, HistoricalValueModelConfig
from src.models.poisson_team_model import PoissonTeamModel, PoissonTeamModelConfig

SELECTIONS = ("home", "draw", "away")


@dataclass(frozen=True)
class BenchmarkConfig:
    bookmaker_prefix: str = "B365"
    min_train_matches: int = 120
    test_window_days: int = 30
    max_windows: int | None = None
    dixon_coles_max_iterations: int = 80


@dataclass(frozen=True)
class BenchmarkMetrics:
    model_name: str
    n_predictions: int
    brier_score: float
    log_loss: float
    top1_accuracy: float
    avg_confidence: float


@dataclass(frozen=True)
class BenchmarkWindow:
    window_name: str
    train_start: str | None
    train_end: str | None
    test_start: str | None
    test_end: str | None
    n_train_matches: int
    n_test_matches: int
    metrics: list[BenchmarkMetrics]


@dataclass(frozen=True)
class BenchmarkReport:
    generated_at_utc: str
    config: dict[str, Any]
    overall_metrics: list[BenchmarkMetrics]
    windows: list[BenchmarkWindow]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at_utc": self.generated_at_utc,
            "config": self.config,
            "overall_metrics": [asdict(item) for item in self.overall_metrics],
            "windows": [
                {
                    **asdict(window),
                    "metrics": [asdict(item) for item in window.metrics],
                }
                for window in self.windows
            ],
            "notes": self.notes,
        }


class ProbabilityBenchmark:
    """Compares market, calibrated-market, and Poisson 1X2 probabilities."""

    OUTCOME_TO_SELECTION = {"H": "home", "D": "draw", "A": "away"}

    def __init__(self, config: BenchmarkConfig | None = None) -> None:
        self.config = config or BenchmarkConfig()

    def run(self, matches: pd.DataFrame) -> BenchmarkReport:
        historical = HistoricalValueModel(
            HistoricalValueModelConfig(
                bookmaker_prefix=self.config.bookmaker_prefix,
                min_train_matches=self.config.min_train_matches,
                require_recent_quality=False,
            )
        )
        poisson = PoissonTeamModel(
            PoissonTeamModelConfig(
                bookmaker_prefix=self.config.bookmaker_prefix,
                min_train_matches=self.config.min_train_matches,
            )
        )
        dixon_coles = DixonColesModel(
            DixonColesConfig(
                min_matches=self.config.min_train_matches,
                max_iterations=self.config.dixon_coles_max_iterations,
            )
        )
        prepared = poisson.prepare_matches(matches)
        if prepared.empty:
            return self._empty_report()

        rows_by_model: dict[str, list[dict[str, Any]]] = {
            "market_implied": [],
            "historical_calibration": [],
            "poisson_team_strength": [],
            "dixon_coles_time_decay": [],
        }
        windows: list[BenchmarkWindow] = []
        min_date = prepared["match_date"].min()
        max_date = prepared["match_date"].max()
        test_start = min_date + timedelta(days=self.config.test_window_days)

        while test_start <= max_date:
            if self.config.max_windows is not None and len(windows) >= self.config.max_windows:
                break
            test_end = min(test_start + timedelta(days=self.config.test_window_days - 1), max_date)
            train_df = prepared[prepared["match_date"] < test_start]
            test_df = prepared[
                (prepared["match_date"] >= test_start) & (prepared["match_date"] <= test_end)
            ]

            window_rows = self._predict_window(historical, poisson, dixon_coles, train_df, test_df)
            for model_name, model_rows in window_rows.items():
                rows_by_model[model_name].extend(model_rows)

            windows.append(
                BenchmarkWindow(
                    window_name=f"fold_{len(windows) + 1}",
                    train_start=(
                        self._date_to_iso(train_df["match_date"].min())
                        if not train_df.empty
                        else None
                    ),
                    train_end=(
                        self._date_to_iso(train_df["match_date"].max())
                        if not train_df.empty
                        else None
                    ),
                    test_start=(
                        self._date_to_iso(test_df["match_date"].min())
                        if not test_df.empty
                        else None
                    ),
                    test_end=(
                        self._date_to_iso(test_df["match_date"].max())
                        if not test_df.empty
                        else None
                    ),
                    n_train_matches=int(len(train_df)),
                    n_test_matches=int(len(test_df)),
                    metrics=[
                        self._metrics(model_name, model_rows)
                        for model_name, model_rows in window_rows.items()
                    ],
                )
            )
            test_start = test_end + timedelta(days=1)

        return BenchmarkReport(
            generated_at_utc=datetime.now(timezone.utc).isoformat(),
            config=asdict(self.config),
            overall_metrics=[
                self._metrics(model_name, model_rows)
                for model_name, model_rows in rows_by_model.items()
            ],
            windows=windows,
            notes=[
                "Walk-forward only: each fold trains on matches before the test window.",
                "Brier score and log loss are lower-is-better probability metrics.",
                "Top-1 accuracy is not enough for betting; use it with ROI and CLV gates.",
            ],
        )

    def write_report(self, report: BenchmarkReport, output_dir: Path) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "model_probability_benchmark.json"
        md_path = output_dir / "model_probability_benchmark.md"
        json_path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        md_path.write_text(self._format_markdown(report), encoding="utf-8")
        return json_path, md_path

    def _predict_window(
        self,
        historical: HistoricalValueModel,
        poisson: PoissonTeamModel,
        dixon_coles: DixonColesModel,
        train_df: pd.DataFrame,
        test_df: pd.DataFrame,
    ) -> dict[str, list[dict[str, Any]]]:
        rows_by_model: dict[str, list[dict[str, Any]]] = {
            "market_implied": [],
            "historical_calibration": [],
            "poisson_team_strength": [],
            "dixon_coles_time_decay": [],
        }
        if len(train_df) < self.config.min_train_matches or test_df.empty:
            return rows_by_model

        historical.fit(train_df)
        poisson.fit(train_df)
        dixon_coles.fit(train_df)

        for _, row in test_df.iterrows():
            actual = self.OUTCOME_TO_SELECTION.get(str(row["result_ft"]))
            if actual is None:
                continue

            hist_predictions = historical.predict_match(row)
            poisson_predictions = poisson.predict_match(row)
            if len(hist_predictions) == 3:
                market_probs = {
                    item.selection: item.market_probability for item in hist_predictions
                }
                historical_probs = {
                    item.selection: item.calibrated_probability for item in hist_predictions
                }
                rows_by_model["market_implied"].append(self._prediction_row(market_probs, actual))
                rows_by_model["historical_calibration"].append(
                    self._prediction_row(historical_probs, actual)
                )
            if len(poisson_predictions) == 3:
                poisson_probs = {item.selection: item.probability for item in poisson_predictions}
                rows_by_model["poisson_team_strength"].append(
                    self._prediction_row(poisson_probs, actual)
                )
            try:
                dc_h, dc_d, dc_a = dixon_coles.predict_1x2(
                    str(row["home_team"]), str(row["away_team"])
                )
                rows_by_model["dixon_coles_time_decay"].append(
                    self._prediction_row({"home": dc_h, "draw": dc_d, "away": dc_a}, actual)
                )
            except Exception:
                pass

        return rows_by_model

    def _prediction_row(self, probabilities: Mapping[Any, float], actual: str) -> dict[str, Any]:
        normalized = self._normalize_probabilities(probabilities)
        top_selection = max(SELECTIONS, key=lambda selection: normalized[selection])
        return {
            "actual": actual,
            "probabilities": normalized,
            "top_selection": top_selection,
            "top_probability": normalized[top_selection],
        }

    def _normalize_probabilities(self, probabilities: Mapping[Any, float]) -> dict[str, float]:
        clipped = {
            selection: max(float(probabilities.get(selection, 0.0)), 1e-9)
            for selection in SELECTIONS
        }
        total = sum(clipped.values())
        return {selection: clipped[selection] / total for selection in SELECTIONS}

    def _metrics(self, model_name: str, rows: list[dict[str, Any]]) -> BenchmarkMetrics:
        if not rows:
            return BenchmarkMetrics(
                model_name=model_name,
                n_predictions=0,
                brier_score=0.0,
                log_loss=0.0,
                top1_accuracy=0.0,
                avg_confidence=0.0,
            )

        brier_total = 0.0
        log_loss_total = 0.0
        correct = 0
        confidence_total = 0.0
        for row in rows:
            actual = row["actual"]
            probabilities = row["probabilities"]
            for selection in SELECTIONS:
                target = 1.0 if selection == actual else 0.0
                brier_total += (probabilities[selection] - target) ** 2
            log_loss_total += -math.log(max(probabilities[actual], 1e-9))
            correct += int(row["top_selection"] == actual)
            confidence_total += float(row["top_probability"])

        n = len(rows)
        return BenchmarkMetrics(
            model_name=model_name,
            n_predictions=n,
            brier_score=round(brier_total / n, 6),
            log_loss=round(log_loss_total / n, 6),
            top1_accuracy=round(correct / n, 6),
            avg_confidence=round(confidence_total / n, 6),
        )

    def _empty_report(self) -> BenchmarkReport:
        return BenchmarkReport(
            generated_at_utc=datetime.now(timezone.utc).isoformat(),
            config=asdict(self.config),
            overall_metrics=[],
            windows=[],
            notes=["No usable matches for benchmark."],
        )

    def _format_markdown(self, report: BenchmarkReport) -> str:
        lines = [
            "# Model Probability Benchmark",
            "",
            f"Generated UTC: {report.generated_at_utc}",
            "",
            "## Overall",
            "",
            "| Model | Predictions | Brier | Log loss | Top-1 accuracy | Avg confidence |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for metric in report.overall_metrics:
            lines.append(
                "| "
                f"{metric.model_name} | {metric.n_predictions} | {metric.brier_score:.6f} | "
                f"{metric.log_loss:.6f} | {metric.top1_accuracy:.2%} | "
                f"{metric.avg_confidence:.2%} |"
            )
        lines.extend(["", "## Notes", ""])
        lines.extend(f"- {note}" for note in report.notes)
        return "\n".join(lines) + "\n"

    def _date_to_iso(self, value: Any) -> str:
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)
