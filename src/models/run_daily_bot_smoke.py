"""Offline smoke run for the daily paper-signal bot loop."""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from src.integrations.telegram_sender import TelegramConfig, TelegramSender
from src.models.historical_value_model import HistoricalValueModel, HistoricalValueModelConfig
from src.models.settle_signal_ledger import settle_ledger_from_results, write_settlement_report
from src.models.signal_ledger import SignalLedger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an offline daily bot smoke test.")
    parser.add_argument("--output-dir", type=Path, default=Path("data/reports/smoke"))
    parser.add_argument("--ledger-path", type=Path)
    parser.add_argument("--bookmaker-prefix", default="B365")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_smoke(
        output_dir=args.output_dir,
        ledger_path=args.ledger_path,
        bookmaker_prefix=args.bookmaker_prefix,
    )
    print(
        "Daily bot smoke completed: "
        f"signals={result['signals_count']}, "
        f"telegram_payloads={len(result['telegram_payload_paths'])}, "
        f"ledger={result['ledger_path']}"
    )


def run_smoke(
    output_dir: Path,
    ledger_path: Path | None = None,
    bookmaker_prefix: str = "B365",
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_path or (output_dir / "paper_signal_ledger.json")

    history = _synthetic_history(bookmaker_prefix)
    upcoming = _synthetic_upcoming(bookmaker_prefix)
    history_path = output_dir / "smoke_history.csv"
    upcoming_path = output_dir / "smoke_upcoming.csv"
    history.to_csv(history_path, index=False)
    upcoming.to_csv(upcoming_path, index=False)

    model = HistoricalValueModel(
        HistoricalValueModelConfig(
            bookmaker_prefix=bookmaker_prefix,
            min_train_matches=10,
            min_edge_pct=1.0,
            min_signal_probability=0.45,
            require_recent_quality=False,
            max_signal_odds=2.5,
        )
    )
    report = model.build_report(history)
    model.write_report(report, output_dir)

    ledger = SignalLedger.load_or_create(ledger_path)
    previous_signal = _previous_open_signal(bookmaker_prefix)
    ledger.add_signal(previous_signal, stake_units=float(previous_signal["paper_stake_units"]))
    settlement_report = settle_ledger_from_results(ledger, history)
    settlement_path = write_settlement_report(
        settlement_report,
        output_dir / "smoke_signal_ledger_settlement_report.json",
    )
    ledger.save(ledger_path)

    signals = model.generate_signals(history, upcoming, max_signals=3)
    signals_path = model.save_signals(signals, output_dir)
    add_result = ledger.add_signals(signals)
    ledger.save(ledger_path)

    sender = TelegramSender(
        TelegramConfig(
            bot_token="dry-run-token",
            chat_id="dry-run",
            dry_run=True,
            max_message_length=4096,
        )
    )
    payload_paths: list[str] = []
    for signal in add_result.added:
        payload = {
            "signal": signal,
            "message": sender.format_signal_message(signal),
            "send_result": sender.send_signal(signal),
        }
        payload_paths.append(str(sender.save_payload(payload, output_dir)))
        ledger.mark_delivery(str(signal["signal_id"]), status="dry_run", delivery_result=payload)
    ledger.save(ledger_path)

    result = {
        "passed": bool(signals and payload_paths),
        "history_path": str(history_path),
        "upcoming_path": str(upcoming_path),
        "signals_path": str(signals_path),
        "signals_count": len(signals),
        "telegram_payload_paths": payload_paths,
        "ledger_path": str(ledger_path),
        "settlement_report_path": str(settlement_path),
        "settled_previous_signals": settlement_report["settled"],
        "added_to_ledger": len(add_result.added),
        "duplicate_signals": len(add_result.duplicates),
        "paper_stakes": [signal.get("paper_stake_units") for signal in signals],
    }
    report_path = output_dir / "daily_bot_smoke_report.json"
    report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    result["report_path"] = str(report_path)
    return result


def _synthetic_history(bookmaker_prefix: str) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows: list[dict[str, Any]] = []
    for idx in range(24):
        home_team = "Alpha FC" if idx % 2 == 0 else "Beta FC"
        away_team = "Beta FC" if idx % 2 == 0 else "Gamma FC"
        rows.append(
            {
                "Date": (start + timedelta(days=idx)).strftime("%d/%m/%Y"),
                "HomeTeam": home_team,
                "AwayTeam": away_team,
                "FTR": "H",
                f"{bookmaker_prefix}H": 2.20,
                f"{bookmaker_prefix}D": 3.40,
                f"{bookmaker_prefix}A": 3.10,
            }
        )
    return pd.DataFrame(rows)


def _previous_open_signal(bookmaker_prefix: str) -> dict[str, Any]:
    return {
        "signal_id": "smoke_previous_alpha_beta_home",
        "strategy_id": "historical_value_model_v1",
        "event_id": "smoke_previous_alpha_beta",
        "home_team": "Alpha FC",
        "away_team": "Beta FC",
        "event_date": "2026-01-01",
        "bookmaker": bookmaker_prefix,
        "bookmaker_title": f"{bookmaker_prefix} smoke",
        "market_key": "h2h",
        "selection": "home",
        "selection_ru": "П1",
        "entry_odds": 2.20,
        "reference_fair_odds": 1.70,
        "edge_pct": 10.0,
        "model_probability": 0.55,
        "paper_stake_units": 0.25,
        "confidence": "medium",
        "timestamp_utc": "2025-12-31T12:00:00+00:00",
        "explain_formatted": "Smoke previous signal for settlement.",
        "status": "paper",
        "dataset_hash": "sha256:smoke_synthetic_data",
    }


def _synthetic_upcoming(bookmaker_prefix: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Date": "01/02/2026",
                "HomeTeam": "Alpha FC",
                "AwayTeam": "Beta FC",
                f"{bookmaker_prefix}H": 2.20,
                f"{bookmaker_prefix}D": 3.40,
                f"{bookmaker_prefix}A": 3.10,
                "source_event_id": "smoke_alpha_beta",
                "source_bookmaker_key": bookmaker_prefix,
                "source_bookmaker_title": f"{bookmaker_prefix} smoke",
                "source_type": "offline_smoke",
            }
        ]
    )


if __name__ == "__main__":
    main()
