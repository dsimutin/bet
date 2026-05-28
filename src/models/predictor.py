"""Value prediction using production model probabilities versus market prices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from src.models.calibrator import ProbabilityCalibrator
from src.models.dixon_coles import DixonColesModel

Selection = Literal["home", "draw", "away"]
SELECTIONS: tuple[Selection, Selection, Selection] = ("home", "draw", "away")


@dataclass(frozen=True)
class ValueSignalCandidate:
    selection: Selection
    model_prob: float
    market_prob: float
    fair_market_prob: float
    edge_vs_market: float
    edge_vs_fair: float
    odds: float
    model_id: str | None


class ModelValuePredictor:
    """Compares independent model probabilities with offered bookmaker odds."""

    def __init__(
        self,
        model: DixonColesModel,
        calibrator: ProbabilityCalibrator | None = None,
    ) -> None:
        self.model = model
        self.calibrator = calibrator

    def predict_value(
        self,
        home: str,
        away: str,
        odds_1x2: tuple[float, float, float],
    ) -> list[ValueSignalCandidate]:
        raw = np.asarray([self.model.predict_1x2(home, away)], dtype=float)
        probs = self.calibrator.calibrate(raw)[0] if self.calibrator else raw[0]
        fair_market = _devig_power(odds_1x2)
        candidates: list[ValueSignalCandidate] = []
        for idx, selection in enumerate(SELECTIONS):
            market_prob = 1.0 / odds_1x2[idx]
            candidates.append(
                ValueSignalCandidate(
                    selection=selection,
                    model_prob=round(float(probs[idx]), 6),
                    market_prob=round(market_prob, 6),
                    fair_market_prob=round(fair_market[idx], 6),
                    edge_vs_market=round(float(probs[idx] - market_prob), 6),
                    edge_vs_fair=round(float(probs[idx] - fair_market[idx]), 6),
                    odds=round(float(odds_1x2[idx]), 4),
                    model_id=self.model.model_id,
                )
            )
        return candidates

    def signals(
        self,
        home: str,
        away: str,
        odds_1x2: tuple[float, float, float],
        min_edge_pct: float,
        min_model_prob: float,
    ) -> list[ValueSignalCandidate]:
        min_edge = min_edge_pct / 100.0
        return [
            item
            for item in self.predict_value(home, away, odds_1x2)
            if item.edge_vs_fair > min_edge and item.model_prob > min_model_prob
        ]


def _devig_power(odds: tuple[float, float, float]) -> tuple[float, float, float]:
    implied = np.asarray([1.0 / value for value in odds], dtype=float)
    lo, hi = 0.5, 5.0
    for _ in range(64):
        mid = (lo + hi) / 2.0
        if float(np.sum(implied**mid)) > 1.0:
            lo = mid
        else:
            hi = mid
    fair = implied ** ((lo + hi) / 2.0)
    fair = fair / fair.sum()
    return (float(fair[0]), float(fair[1]), float(fair[2]))
