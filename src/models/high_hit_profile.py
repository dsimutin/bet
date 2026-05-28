"""Search historical thresholds for fewer, higher-hit-rate signals."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.models.historical_value_model import HistoricalValueModel, HistoricalValueModelConfig


@dataclass(frozen=True)
class HighHitProfile:
    min_signal_probability: float
    max_entry_odds: float | None
    min_edge_pct: float
    n_bets: int
    win_rate: float
    roi_pct: float
    profit_units: float
    score: float
    passed: bool


@dataclass(frozen=True)
class HighHitProfileSearchConfig:
    target_win_rate: float = 0.55
    min_bets: int = 30
    probability_grid: tuple[float, ...] = (0.5, 0.55, 0.58, 0.6, 0.62, 0.65)
    max_odds_grid: tuple[float | None, ...] = (1.65, 1.75, 1.85, 2.0, None)
    min_edge_grid: tuple[float, ...] = (1.0, 2.0, 3.0, 5.0)


def find_high_hit_profile(
    matches: pd.DataFrame,
    base_config: HistoricalValueModelConfig,
    search_config: HighHitProfileSearchConfig | None = None,
) -> dict[str, Any]:
    search = search_config or HighHitProfileSearchConfig()
    candidates: list[HighHitProfile] = []

    for min_probability in search.probability_grid:
        for max_odds in search.max_odds_grid:
            for min_edge in search.min_edge_grid:
                candidate_config = HistoricalValueModelConfig(
                    **{
                        **asdict(base_config),
                        "min_signal_probability": min_probability,
                        "max_signal_odds": max_odds,
                        "min_edge_pct": min_edge,
                        "require_recent_quality": False,
                    }
                )
                model = HistoricalValueModel(candidate_config)
                bets, _ = model.walk_forward_backtest(matches)
                profile = _profile_from_bets(
                    bets=bets,
                    min_probability=min_probability,
                    max_odds=max_odds,
                    min_edge=min_edge,
                    target_win_rate=search.target_win_rate,
                    min_bets=search.min_bets,
                )
                candidates.append(profile)

    selected = _select_profile(candidates)
    return {
        "passed": selected.passed,
        "reason": "passed" if selected.passed else "no_profile_met_target",
        "target_win_rate": search.target_win_rate,
        "min_bets": search.min_bets,
        "selected": asdict(selected),
        "candidates": [asdict(item) for item in sorted(candidates, key=_sort_key, reverse=True)],
    }


def write_high_hit_profile_report(report: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "high_hit_profile_report.json"
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def _profile_from_bets(
    bets: list[Any],
    min_probability: float,
    max_odds: float | None,
    min_edge: float,
    target_win_rate: float,
    min_bets: int,
) -> HighHitProfile:
    n_bets = len(bets)
    wins = sum(1 for bet in bets if bet.result == "win")
    profit = round(sum(float(bet.profit_units) for bet in bets), 4)
    win_rate = round(wins / n_bets, 4) if n_bets else 0.0
    roi_pct = round(profit / n_bets * 100, 4) if n_bets else 0.0
    passed = n_bets >= min_bets and win_rate >= target_win_rate
    sample_score = min(n_bets / max(min_bets, 1), 3.0) / 100.0
    score = round(win_rate + sample_score + max(roi_pct, -100.0) / 10000.0, 8)
    return HighHitProfile(
        min_signal_probability=min_probability,
        max_entry_odds=max_odds,
        min_edge_pct=min_edge,
        n_bets=n_bets,
        win_rate=win_rate,
        roi_pct=roi_pct,
        profit_units=profit,
        score=score,
        passed=passed,
    )


def _select_profile(candidates: list[HighHitProfile]) -> HighHitProfile:
    passing = [item for item in candidates if item.passed]
    pool = passing or candidates
    if not pool:
        return HighHitProfile(
            min_signal_probability=1.0,
            max_entry_odds=1.0,
            min_edge_pct=100.0,
            n_bets=0,
            win_rate=0.0,
            roi_pct=0.0,
            profit_units=0.0,
            score=0.0,
            passed=False,
        )
    return max(pool, key=_sort_key)


def _sort_key(profile: HighHitProfile) -> tuple[bool, float, float, int, float]:
    return (
        profile.passed,
        profile.win_rate,
        profile.roi_pct,
        profile.n_bets,
        profile.score,
    )
