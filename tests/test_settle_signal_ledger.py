from __future__ import annotations

import pandas as pd

from src.models.settle_signal_ledger import settle_ledger_from_results, write_settlement_report
from src.models.signal_ledger import SignalLedger


def _signal(signal_id: str, selection: str = "home") -> dict:
    return {
        "signal_id": signal_id,
        "strategy_id": "consensus_value_poisson_v1",
        "event_date": "2026-06-01",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "bookmaker": "bet365",
        "market_key": "h2h",
        "selection": selection,
        "entry_odds": 1.8,
        "reference_fair_odds": 1.7,
        "edge_pct": 5.88,
        "timestamp_utc": "2026-05-27T12:00:00+00:00",
        "status": "paper",
    }


def test_settle_ledger_from_results_marks_wins_and_losses() -> None:
    ledger = SignalLedger()
    ledger.add_signal(_signal("sig_home", selection="home"))
    ledger.add_signal(_signal("sig_away", selection="away"))
    results = pd.DataFrame(
        [
            {
                "Date": "01/06/2026",
                "HomeTeam": "Arsenal",
                "AwayTeam": "Chelsea",
                "FTR": "H",
            }
        ]
    )

    report = settle_ledger_from_results(ledger, results)

    assert report["settled"] == 2
    assert ledger.get("sig_home")["result"] == "win"
    assert ledger.get("sig_away")["result"] == "loss"
    assert ledger.summary()["settled_signals"] == 2
    assert ledger.summary()["win_rate"] == 0.5


def test_settle_ledger_keeps_unmatched_signals_open() -> None:
    ledger = SignalLedger()
    ledger.add_signal(_signal("sig_open"))
    results = pd.DataFrame(
        [
            {
                "Date": "02/06/2026",
                "HomeTeam": "Arsenal",
                "AwayTeam": "Chelsea",
                "FTR": "H",
            }
        ]
    )

    report = settle_ledger_from_results(ledger, results)

    assert report["settled"] == 0
    assert report["unmatched_open_signals"] == 1
    assert ledger.get("sig_open")["ledger_status"] == "open"


def test_write_settlement_report(tmp_path) -> None:
    path = write_settlement_report(
        {"settled": 1, "unmatched_open_signals": 0},
        tmp_path / "nested" / "settlement.json",
    )

    assert path.exists()
    assert '"settled": 1' in path.read_text(encoding="utf-8")
