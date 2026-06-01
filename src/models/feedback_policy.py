"""Conservative delivery filter learned from settled bot signals."""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    details: dict[str, Any]

class FeedbackPolicy:
    VERSION = "feedback_v1"
    def __init__(self, entries: dict[str, dict[str, Any]]) -> None:
        self.entries = entries

    def evaluate(self, signal: dict[str, Any]) -> Decision:
        sport = str(signal.get("sport") or "football").lower()
        odds = _num(signal.get("entry_odds"))
        edge = _num(signal.get("edge_pct", signal.get("edge_vs_fair_pct")))
        prob = _prob(signal)
        segment = _segment(odds)
        stats = self._stats(sport, segment)
        min_prob = self._min_prob(sport)
        max_odds = _env("TENNIS_MAX_DELIVERY_ODDS" if sport == "tennis" else "FOOTBALL_MAX_DELIVERY_ODDS", 4.0 if sport == "tennis" else 3.2)
        min_edge = _env("TENNIS_MIN_DELIVERY_EDGE_PCT" if sport == "tennis" else "FOOTBALL_MIN_DELIVERY_EDGE_PCT", 1.5 if sport == "tennis" else 2.5)
        max_edge = _env("MAX_SANE_DELIVERY_EDGE_PCT", 35.0)
        market = str(signal.get("market", signal.get("market_key", "h2h"))).lower()
        reason = "passed"
        allowed = True
        if market != "h2h": allowed, reason = False, "only h2h delivery is enabled"
        elif prob is None: allowed, reason = False, "missing model probability"
        elif prob < min_prob: allowed, reason = False, f"probability {prob:.1%} below {min_prob:.1%}"
        elif odds <= 1 or odds > max_odds: allowed, reason = False, f"odds {odds:.2f} outside delivery range"
        elif edge < min_edge: allowed, reason = False, f"edge {edge:.2f}% below {min_edge:.2f}%"
        elif edge > max_edge: allowed, reason = False, f"edge {edge:.2f}% above anomaly cap"
        elif stats["n"] >= 20 and stats["roi"] < -5: allowed, reason = False, f"segment {segment} paused by ROI"
        elif stats["n"] >= 20 and stats["win_rate"] < .40: allowed, reason = False, f"segment {segment} paused by hit rate"
        details = {"policy": self.VERSION, "sport": sport, "segment": segment, "segment_stats": stats, "min_prob": min_prob, "max_odds": max_odds, "min_edge": min_edge, "max_edge": max_edge}
        return Decision(allowed, reason, details)

    def _settled(self, sport: str) -> list[dict[str, Any]]:
        return [e for e in self.entries.values() if str(e.get("sport") or "football").lower() == sport and e.get("ledger_status") == "settled" and e.get("result") in {"win", "loss"}]

    def _stats(self, sport: str, segment: str) -> dict[str, Any]:
        rows = [e for e in self._settled(sport) if _segment(_num(e.get("entry_odds"))) == segment]
        wins = sum(e.get("result") == "win" for e in rows)
        stake = sum(_num(e.get("stake_units"), 1.0) for e in rows)
        pnl = sum(_num(e.get("pnl_units")) for e in rows)
        return {"n": len(rows), "wins": wins, "win_rate": round(wins / len(rows), 4) if rows else 0.0, "roi": round(pnl / stake * 100, 2) if stake else 0.0}

    def _min_prob(self, sport: str) -> float:
        base = _env("TENNIS_MIN_DELIVERY_PROB" if sport == "tennis" else "FOOTBALL_MIN_DELIVERY_PROB", .52 if sport == "tennis" else .50)
        rows = self._settled(sport)
        if len(rows) < 30: return base
        wins = sum(e.get("result") == "win" for e in rows)
        stake = sum(_num(e.get("stake_units"), 1.0) for e in rows)
        pnl = sum(_num(e.get("pnl_units")) for e in rows)
        return round(min(.72, base + (.03 if wins / len(rows) < .55 else 0) + (.02 if stake and pnl / stake < 0 else 0)), 4)

def _prob(signal: dict[str, Any]) -> float | None:
    for key in ("model_probability", "model_prob"):
        if isinstance(signal.get(key), (int, float)): return float(signal[key])
    return None

def _segment(odds: float) -> str:
    return "favorite" if odds < 1.7 else "balanced" if odds < 2.5 else "underdog" if odds < 4 else "longshot"

def _num(value: Any, default: float = 0.0) -> float:
    try: return float(value)
    except (TypeError, ValueError): return default

def _env(name: str, default: float) -> float:
    try: return float(os.environ.get(name, str(default)))
    except ValueError: return default
