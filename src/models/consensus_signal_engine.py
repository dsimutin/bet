"""Consensus signal generation across independent football models."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.models.historical_value_model import HistoricalValueModel, HistoricalValueModelConfig
from src.models.poisson_team_model import PoissonTeamModel, PoissonTeamModelConfig


@dataclass(frozen=True)
class ConsensusConfig:
    min_edge_pct: float = 2.0
    min_probability: float = 0.45
    max_entry_odds: float | None = None
    require_quality_gates: bool = True
    max_signals: int = 10


class ConsensusSignalEngine:
    """Only emits signals where market-calibration and Poisson models agree."""

    def __init__(
        self,
        value_model: HistoricalValueModel | None = None,
        poisson_model: PoissonTeamModel | None = None,
        config: ConsensusConfig | None = None,
    ) -> None:
        self.value_model = value_model or HistoricalValueModel(
            HistoricalValueModelConfig(require_recent_quality=False)
        )
        self.poisson_model = poisson_model or PoissonTeamModel()
        self.config = config or ConsensusConfig()

    def quality_gate_report(self, history_matches: pd.DataFrame) -> dict[str, Any]:
        value_gate = self.value_model.quality_gate_report(history_matches)
        poisson_gate = self.poisson_model.recent_quality_report(history_matches)
        passed = bool(value_gate["passed"] and poisson_gate["passed"])
        reasons = []
        if not value_gate["passed"]:
            reasons.append(f"value_model: {value_gate['reason']}")
        if not poisson_gate["passed"]:
            reasons.append(f"poisson_model: {poisson_gate['reason']}")
        return {
            "passed": passed,
            "reason": "; ".join(reasons) if reasons else "passed",
            "value_model": value_gate,
            "poisson_model": poisson_gate,
        }

    def generate_signals(
        self,
        history_matches: pd.DataFrame,
        candidate_matches: pd.DataFrame,
    ) -> list[dict[str, Any]]:
        if (
            self.config.require_quality_gates
            and not self.quality_gate_report(history_matches)["passed"]
        ):
            return []

        history_for_value = self.value_model.prepare_matches(history_matches)
        history_for_poisson = self.poisson_model.prepare_matches(history_matches)
        candidates = self.value_model.prepare_prediction_matches(candidate_matches)
        if history_for_value.empty or history_for_poisson.empty or candidates.empty:
            return []

        self.value_model.fit(history_for_value)
        self.poisson_model.fit(history_for_poisson)
        generated_at = datetime.now(timezone.utc).isoformat()

        signals: list[dict[str, Any]] = []
        for _, row in candidates.iterrows():
            value_best = self._best_value_prediction(row)
            poisson_best = self._best_poisson_prediction(row)
            if value_best is None or poisson_best is None:
                continue
            if value_best.selection != poisson_best.selection:
                continue
            if (
                self.config.max_entry_odds is not None
                and value_best.odds > self.config.max_entry_odds
            ):
                continue

            combined_probability = min(
                value_best.calibrated_probability,
                poisson_best.probability,
            )
            combined_edge = (combined_probability * value_best.odds - 1.0) * 100
            if (
                combined_probability < self.config.min_probability
                or combined_edge < self.config.min_edge_pct
            ):
                continue

            signal = self.value_model._prediction_to_signal(value_best, generated_at)
            signal.update(
                {
                    "signal_id": f"consensus_{value_best.event_id}_{value_best.selection}",
                    "strategy_id": "consensus_value_poisson_v1",
                    "reference_fair_odds": round(1.0 / combined_probability, 4),
                    "edge_pct": round(combined_edge, 4),
                    "model_probability": round(combined_probability, 6),
                    "poisson_probability": poisson_best.probability,
                    "value_probability": value_best.calibrated_probability,
                    "expected_home_goals": poisson_best.expected_home_goals,
                    "expected_away_goals": poisson_best.expected_away_goals,
                    "confidence": self._confidence(combined_probability, combined_edge),
                    "explain_formatted": (
                        f"Consensus: value {value_best.calibrated_probability:.1%}, "
                        f"Poisson {poisson_best.probability:.1%}, "
                        f"xG {poisson_best.expected_home_goals:.2f}:"
                        f"{poisson_best.expected_away_goals:.2f}."
                    ),
                }
            )
            signals.append(signal)

        return sorted(signals, key=lambda item: item["edge_pct"], reverse=True)[
            : self.config.max_signals
        ]

    def save_signals(self, signals: list[dict[str, Any]], output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        output_path = output_dir / f"consensus_signals_{date_str}.json"
        output_path.write_text(
            json.dumps(signals, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return output_path

    def save_quality_gate(self, report: dict[str, Any], output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "consensus_quality_gate.json"
        output_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return output_path

    def _best_value_prediction(self, row: pd.Series):
        predictions = [
            item
            for item in self.value_model.predict_match(row)
            if item.edge_pct >= self.config.min_edge_pct
            and item.calibrated_probability >= self.config.min_probability
            and (self.config.max_entry_odds is None or item.odds <= self.config.max_entry_odds)
        ]
        return max(predictions, key=lambda item: item.edge_pct, default=None)

    def _best_poisson_prediction(self, row: pd.Series):
        predictions = [
            item
            for item in self.poisson_model.predict_match(row)
            if item.edge_pct >= self.config.min_edge_pct
            and item.probability >= self.config.min_probability
            and (self.config.max_entry_odds is None or item.odds <= self.config.max_entry_odds)
        ]
        return max(predictions, key=lambda item: item.edge_pct, default=None)

    def _confidence(self, probability: float, edge_pct: float) -> str:
        if probability >= 0.6 and edge_pct >= 5.0:
            return "high"
        if probability >= 0.5 and edge_pct >= 3.0:
            return "medium"
        return "low"

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": asdict(self.config),
            "value_model": asdict(self.value_model.config),
            "poisson_model": asdict(self.poisson_model.config),
        }
