"""Conservative self-learning presentation policy for settled bot signals.

The base sports models learn from full match histories. This second layer learns
from the bot's own settled paper signals and classifies candidates into:
priority, watchlist, or blocked. Watchlist signals stay visible in the menu so
the bot does not become silent while it learns.
"""

from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Decision:
    tier: str
    reason: str
    details: dict[str, Any]

    @property
    def deliver_now(self) -> bool:
        return self.tier == "priority"

    @property
    def keep_visible(self) -> bool:
        return self.tier in {"priority", "watchlist"}


class FeedbackPolicy:
    VERSION = "feedback_v2"

    def __init__(self, entries: dict[str, dict[str, Any]]) -> None:
        self.entries = entries

    def evaluate(self, signal: dict[str, Any]) -> Decision:
        sport = str(signal.get("sport") or "football").lower()
        odds = _num(signal.get("entry_odds"))
        edge = _num(signal.get("edge_pct", signal.get("edge_vs_fair_pct")))
        prob = _prob(signal)
        segment = _segment(odds)
        stats = self._stats(sport, segment)
        priority_prob = self._priority_prob(sport)
        watch_prob = _env(
            "TENNIS_WATCHLIST_MIN_PROB" if sport == "tennis" else "FOOTBALL_WATCHLIST_MIN_PROB",
            0.35 if sport == "tennis" else 0.42,
        )
        priority_edge = _env(
            (
                "TENNIS_PRIORITY_MIN_EDGE_PCT"
                if sport == "tennis"
                else "FOOTBALL_PRIORITY_MIN_EDGE_PCT"
            ),
            2.0 if sport == "tennis" else 3.0,
        )
        watch_edge = _env("WATCHLIST_MIN_EDGE_PCT", 0.8)
        max_odds = _env(
            "TENNIS_MAX_VISIBLE_ODDS" if sport == "tennis" else "FOOTBALL_MAX_VISIBLE_ODDS",
            6.0 if sport == "tennis" else 4.5,
        )
        anomaly_edge = _env("MAX_SANE_VISIBLE_EDGE_PCT", 55.0)
        market = str(signal.get("market", signal.get("market_key", "h2h"))).lower()
        tier, reason = "priority", "meets priority thresholds"
        if market != "h2h":
            tier, reason = "blocked", "only h2h is supported for reliable settlement"
        elif prob is None:
            tier, reason = "blocked", "model probability missing"
        elif odds <= 1.0:
            tier, reason = "blocked", "invalid decimal odds"
        elif edge > anomaly_edge:
            tier, reason = "blocked", f"edge {edge:.1f}% exceeds anomaly cap"
        elif odds > max_odds:
            tier, reason = "watchlist", f"high odds {odds:.2f}: observation only"
        elif prob < watch_prob or edge < watch_edge:
            tier, reason = "blocked", "too weak even for watchlist"
        elif prob < priority_prob or edge < priority_edge:
            tier, reason = "watchlist", "edge exists, but confidence is below priority level"
        elif stats["n"] >= 20 and stats["roi"] < -5:
            tier, reason = "watchlist", f"segment {segment} is under review by ROI"
        elif stats["n"] >= 20 and stats["win_rate"] < 0.40:
            tier, reason = "watchlist", f"segment {segment} is under review by hit rate"
        return Decision(
            tier,
            reason,
            {
                "policy": self.VERSION,
                "sport": sport,
                "segment": segment,
                "stats": stats,
                "priority_prob": priority_prob,
                "watch_prob": watch_prob,
                "priority_edge": priority_edge,
                "watch_edge": watch_edge,
                "max_odds": max_odds,
                "model_probability": prob,
                "edge_pct": edge,
            },
        )

    def _settled(self, sport: str) -> list[dict[str, Any]]:
        return [
            e
            for e in self.entries.values()
            if str(e.get("sport") or "football").lower() == sport
            and e.get("ledger_status") == "settled"
            and e.get("result") in {"win", "loss"}
        ]

    def _stats(self, sport: str, segment: str) -> dict[str, Any]:
        rows = [e for e in self._settled(sport) if _segment(_num(e.get("entry_odds"))) == segment]
        wins = sum(e.get("result") == "win" for e in rows)
        stake = sum(_num(e.get("stake_units"), 1.0) for e in rows)
        pnl = sum(_num(e.get("pnl_units")) for e in rows)
        return {
            "n": len(rows),
            "wins": wins,
            "win_rate": round(wins / len(rows), 4) if rows else 0.0,
            "roi": round(pnl / stake * 100, 2) if stake else 0.0,
        }

    def _priority_prob(self, sport: str) -> float:
        base = _env(
            "TENNIS_PRIORITY_MIN_PROB" if sport == "tennis" else "FOOTBALL_PRIORITY_MIN_PROB",
            0.52 if sport == "tennis" else 0.50,
        )
        rows = self._settled(sport)
        if len(rows) < 30:
            return base
        wins = sum(e.get("result") == "win" for e in rows)
        stake = sum(_num(e.get("stake_units"), 1.0) for e in rows)
        pnl = sum(_num(e.get("pnl_units")) for e in rows)
        return round(
            min(
                0.70,
                base
                + (0.03 if wins / len(rows) < 0.55 else 0)
                + (0.02 if stake and pnl / stake < 0 else 0),
            ),
            4,
        )


def _prob(signal: dict[str, Any]) -> float | None:
    for key in ("model_probability", "model_prob"):
        if isinstance(signal.get(key), (int, float)):
            return float(signal[key])
    return None


def _segment(odds: float) -> str:
    return (
        "favorite"
        if odds < 1.7
        else "balanced" if odds < 2.5 else "underdog" if odds < 4 else "longshot"
    )


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default
