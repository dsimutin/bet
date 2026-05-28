"""Model admission gate based on probability benchmark reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

BenchmarkScope = Literal["overall", "latest_window"]
GateMode = Literal["all", "any"]


class ModelQualityGate:
    """Decides whether model-driven Telegram signals are allowed."""

    def __init__(
        self,
        benchmark_path: Path,
        baseline_model: str = "market_implied",
        candidate_models: list[str] | None = None,
        min_predictions: int = 100,
        min_brier_improvement: float = 0.0,
        min_log_loss_improvement: float = 0.0,
        scope: BenchmarkScope = "latest_window",
        mode: GateMode = "all",
    ) -> None:
        self.benchmark_path = benchmark_path
        self.baseline_model = baseline_model
        self.candidate_models = candidate_models or [
            "historical_calibration",
            "poisson_team_strength",
            "dixon_coles_time_decay",
        ]
        self.min_predictions = min_predictions
        self.min_brier_improvement = min_brier_improvement
        self.min_log_loss_improvement = min_log_loss_improvement
        self.scope = scope
        self.mode = mode

    def evaluate(self) -> dict[str, Any]:
        if not self.benchmark_path.exists():
            return {
                "passed": False,
                "reason": f"benchmark_not_found: {self.benchmark_path}",
                "allowed_models": [],
                "baseline": None,
                "candidates": [],
            }

        data = json.loads(self.benchmark_path.read_text(encoding="utf-8"))
        metrics, scope_reason = self._metrics_for_scope(data)
        if not metrics:
            return {
                "passed": False,
                "reason": scope_reason,
                "allowed_models": [],
                "baseline": None,
                "candidates": [],
                "scope": self.scope,
            }

        metrics_by_name = {str(item["model_name"]): item for item in metrics}
        baseline = metrics_by_name.get(self.baseline_model)
        if baseline is None:
            return {
                "passed": False,
                "reason": f"baseline_not_found: {self.baseline_model}",
                "allowed_models": [],
                "baseline": None,
                "candidates": [],
                "scope": self.scope,
            }

        candidates: list[dict[str, Any]] = []
        allowed_models: list[str] = []
        for model_name in self.candidate_models:
            metric = metrics_by_name.get(model_name)
            if metric is None:
                candidates.append(
                    {
                        "model_name": model_name,
                        "passed": False,
                        "reason": "model_not_found",
                    }
                )
                continue

            evaluation = self._evaluate_candidate(metric, baseline)
            candidates.append(evaluation)
            if evaluation["passed"]:
                allowed_models.append(model_name)

        if self.mode == "all":
            passed = bool(candidates) and len(allowed_models) == len(candidates)
            reason = "passed" if passed else "not_all_required_models_beat_market_baseline"
        else:
            passed = bool(allowed_models)
            reason = "passed" if passed else "no_model_beats_market_baseline"

        return {
            "passed": passed,
            "reason": reason,
            "allowed_models": allowed_models,
            "baseline": baseline,
            "candidates": candidates,
            "scope": self.scope,
            "mode": self.mode,
        }

    def _metrics_for_scope(self, data: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        if self.scope == "overall":
            return list(data.get("overall_metrics", [])), "no_overall_metrics"
        if self.scope == "latest_window":
            windows = list(data.get("windows", []))
            if not windows:
                return [], "no_benchmark_windows"
            latest = windows[-1]
            return list(latest.get("metrics", [])), "no_latest_window_metrics"
        return [], f"unknown_scope: {self.scope}"

    def _evaluate_candidate(
        self,
        candidate: dict[str, Any],
        baseline: dict[str, Any],
    ) -> dict[str, Any]:
        failures: list[str] = []
        n_predictions = int(candidate.get("n_predictions", 0))
        brier_improvement = float(baseline["brier_score"]) - float(candidate["brier_score"])
        log_loss_improvement = float(baseline["log_loss"]) - float(candidate["log_loss"])

        if n_predictions < self.min_predictions:
            failures.append(f"n_predictions {n_predictions} < {self.min_predictions}")
        if brier_improvement < self.min_brier_improvement:
            failures.append(
                f"brier_improvement {brier_improvement:.6f} < {self.min_brier_improvement:.6f}"
            )
        if log_loss_improvement < self.min_log_loss_improvement:
            failures.append(
                f"log_loss_improvement {log_loss_improvement:.6f} < "
                f"{self.min_log_loss_improvement:.6f}"
            )

        return {
            "model_name": candidate["model_name"],
            "passed": not failures,
            "reason": "; ".join(failures) if failures else "passed",
            "n_predictions": n_predictions,
            "brier_score": candidate["brier_score"],
            "log_loss": candidate["log_loss"],
            "brier_improvement": round(brier_improvement, 6),
            "log_loss_improvement": round(log_loss_improvement, 6),
        }
