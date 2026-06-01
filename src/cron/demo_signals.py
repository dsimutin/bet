"""Demo signal generator for testing the full pipeline without API key."""

from __future__ import annotations
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> None:
    """Generate and save demo signals for today (football + tennis)."""
    ledger_path = Path(os.environ.get("LEDGER_PATH", "data/core/paper_signal_ledger.json"))
    today = date.today()

    from src.infrastructure.persistent_ledger import load_ledger, save_ledger

    ledger = load_ledger(ledger_path)

    # Demo football signal (EPL)
    football_signal = {
        "signal_id": str(uuid4()),
        "strategy_id": "dixon_coles_runtime_v1",
        "sport": "football",
        "league": "EPL",
        "home_team": "Manchester United FC",
        "away_team": "Liverpool FC",
        "selection": "home",
        "selection_ru": "Манчестер Юнайтед",
        "commence_time": f"{today}T15:00:00Z",
        "event_time_utc": f"{today}T15:00:00Z",
        "entry_odds": 2.15,
        "model_probability": 0.68,
        "fair_market_probability": 0.58,
        "edge_pct": 10.0,
        "edge_vs_fair_pct": 10.0,
        "recommendation_tier": "priority",
        "recommendation_reason": "meets priority thresholds",
        "model_source": "dixon_coles",
        "reference_fair_odds": 1.72,
        "stake_units": 1.0,
        "dataset_hash": "demo_football_20260601",
        "sport": "football",
        "ledger_status": "open",
        "delivery_status": "pending",
    }

    # Demo tennis signal (ATP)
    tennis_signal = {
        "signal_id": str(uuid4()),
        "strategy_id": "tennis_elo_markov_v1",
        "sport": "tennis",
        "player": "Matteo Berrettini",
        "opponent": "Juan Manuel Cerundolo",
        "rank": 105,
        "opp_rank": 56,
        "surface": "clay",
        "entry_odds": 1.67,
        "model_probability": 0.771,
        "fair_market_probability": 0.584,
        "edge_pct": 28.71,
        "edge_vs_fair_pct": 28.71,
        "recommendation_tier": "priority",
        "recommendation_reason": "meets priority thresholds",
        "model_source": "markov+elo",
        "elo_prob": 0.682,
        "recent_form": 0.40,
        "serve_win_pct": 0.692,
        "h2h_adj": -0.0,
        "days_since_last_match": 26,
        "event_time_utc": f"{today}T14:20:00Z",
        "commence_time": f"{today}T14:20:00Z",
        "stake_units": 1.0,
        "dataset_hash": "demo_tennis_20260601",
        "ledger_status": "open",
        "delivery_status": "pending",
    }

    # Add signals to ledger
    result = ledger.add_signals([football_signal, tennis_signal])
    save_ledger(ledger, ledger_path)

    print(f"✅ Demo signals generated:")
    print(f"  Football: {football_signal['home_team']} vs {football_signal['away_team']}")
    print(f"    Odds: {football_signal['entry_odds']} | Edge: {football_signal['edge_pct']}%")
    print(f"  Tennis: {tennis_signal['player']} vs {tennis_signal['opponent']}")
    print(f"    Odds: {tennis_signal['entry_odds']} | Edge: {tennis_signal['edge_pct']}%")
    print(f"\n  Saved to: {ledger_path}")
    print(f"  Total signals in ledger: {len(ledger.entries())}")


if __name__ == "__main__":
    main()
