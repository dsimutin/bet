"""Settle open paper signals against finished football-data results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from src.models.signal_ledger import SignalLedger

RESULT_TO_SELECTION = {"H": "home", "D": "draw", "A": "away"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Settle paper signal ledger from results CSV.")
    parser.add_argument("--ledger-path", required=True, type=Path)
    parser.add_argument("--results-input", required=True, type=Path)
    parser.add_argument("--output-path", type=Path)
    parser.add_argument("--report-path", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    from src.infrastructure.persistent_ledger import load_ledger, save_ledger

    ledger = load_ledger(args.ledger_path)
    results = pd.read_csv(args.results_input, encoding="latin-1")
    report = settle_ledger_from_results(ledger, results)
    output_path = save_ledger(ledger, args.output_path or args.ledger_path)
    if args.report_path is not None:
        report_path = write_settlement_report(report, args.report_path)
        print(f"Wrote {report_path}")
    print(f"Wrote {output_path}")
    print(
        "Settled "
        f"{report['settled_count']} signal(s), unmatched={report['unmatched_open_signals']}"
    )


_CLOSING_ODDS_COLS: dict[str, tuple[str, str, str]] = {
    "BbCl": ("BbClH", "BbClD", "BbClA"),
    "B365C": ("B365CH", "B365CD", "B365CA"),
    "PSC": ("PSCH", "PSCD", "PSCA"),
}

_SELECTION_IDX = {"home": 0, "draw": 1, "away": 2}


def _pick_closing_odds(
    row: "pd.Series[Any]",
    selection: str,
) -> float | None:
    idx = _SELECTION_IDX.get(str(selection).lower())
    if idx is None:
        return None
    for _, (col_h, col_d, col_a) in _CLOSING_ODDS_COLS.items():
        cols = (col_h, col_d, col_a)
        if all(c in row.index for c in cols):
            val = row[cols[idx]]
            try:
                fval = float(val)
                if fval > 1.0:
                    return fval
            except (TypeError, ValueError):
                pass
    return None


def settle_ledger_from_results(
    ledger: SignalLedger,
    results: pd.DataFrame,
    api_key: str = "",
) -> dict[str, Any]:
    """Settle football h2h signals and return a backward-compatible report.

    Falls back to live API results if historical data doesn't have the match.
    """
    prepared_results = _prepare_results(results)
    result_by_match = {
        _match_key(row["match_date"], row["home_team"], row["away_team"]): row
        for _, row in prepared_results.iterrows()
    }

    settled_signals: list[dict[str, Any]] = []
    unmatched: list[str] = []
    for signal_id, entry in ledger.entries().items():
        if entry.get("ledger_status") != "open":
            continue
        if entry.get("market_key", "h2h") != "h2h":
            continue

        sport = entry.get("sport", "football")

        # Handle football
        if sport == "football":
            match_key = _match_key(
                entry.get("event_date"),
                entry.get("home_team"),
                entry.get("away_team"),
            )
            result_row = result_by_match.get(match_key)

            # Fallback to live API if historical data doesn't have it
            if result_row is None and api_key:
                result_row = _get_live_result_fallback(entry, api_key)

            if result_row is None:
                unmatched.append(signal_id)
                continue

            actual = result_row["actual_selection"]
            result: Literal["win", "loss"] = "win" if entry.get("selection") == actual else "loss"
            closing_odds = _pick_closing_odds(result_row, entry.get("selection", ""))
            ledger.update_result(signal_id, result=result, closing_odds=closing_odds)
            settled_signals.append(ledger.get(signal_id))

        # Handle tennis
        elif sport == "tennis":
            result_row = _get_live_tennis_result_fallback(entry, api_key)
            if result_row is None:
                unmatched.append(signal_id)
                continue

            actual = result_row["actual_selection"]
            # Tennis signals back the player by default, so selection is "H"
            signal_selection = entry.get("selection", "H")
            result: Literal["win", "loss"] = "win" if signal_selection == actual else "loss"
            ledger.update_result(signal_id, result=result, closing_odds=None)
            settled_signals.append(ledger.get(signal_id))

    settled_count = len(settled_signals)
    wins = sum(1 for item in settled_signals if item.get("result") == "win")
    losses = sum(1 for item in settled_signals if item.get("result") == "loss")
    voids = sum(1 for item in settled_signals if item.get("result") == "void")
    return {
        # Backward compatibility for older CLI/report consumers.
        "settled": settled_count,
        # Canonical fields for active monitoring and retrain accounting.
        "settled_count": settled_count,
        "settled_signals": settled_signals,
        "wins": wins,
        "losses": losses,
        "voids": voids,
        "unmatched_open_signals": len(unmatched),
        "unmatched_signal_ids": unmatched,
        "summary": ledger.summary(),
    }


def write_settlement_report(report: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _prepare_results(results: pd.DataFrame) -> pd.DataFrame:
    df = results.copy()
    df = df.rename(
        columns={
            "Date": "match_date",
            "HomeTeam": "home_team",
            "AwayTeam": "away_team",
            "FTR": "result_ft",
        }
    )
    required = {"match_date", "home_team", "away_team", "result_ft"}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"Missing required result columns: {sorted(missing)}")

    df["match_date"] = pd.to_datetime(df["match_date"], dayfirst=True, errors="coerce").dt.date
    df = df.dropna(subset=["match_date", "home_team", "away_team", "result_ft"])
    df = df[df["result_ft"].isin(RESULT_TO_SELECTION)]
    df["actual_selection"] = df["result_ft"].map(RESULT_TO_SELECTION)
    return df


def _match_key(match_date: Any, home_team: Any, away_team: Any) -> tuple[str, str, str]:
    raw_date = str(match_date or "").strip()
    dayfirst = not _looks_like_iso_date(raw_date)
    parsed_date = pd.to_datetime(raw_date, dayfirst=dayfirst, errors="coerce")
    date_text = parsed_date.date().isoformat() if not pd.isna(parsed_date) else str(match_date)
    return (
        date_text,
        _normalize_team(home_team),
        _normalize_team(away_team),
    )


def _looks_like_iso_date(value: str) -> bool:
    return len(value) >= 10 and value[4] == "-" and value[7] == "-"


def _normalize_team(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _get_live_result_fallback(entry: dict[str, Any], api_key: str) -> dict[str, Any] | None:
    """Try to get result from live API when historical data doesn't have it."""
    try:
        from src.ingest.live_results import get_live_match_result
        from datetime import datetime

        home_team = entry.get("home_team", "")
        away_team = entry.get("away_team", "")
        league = entry.get("league", "")
        event_date_str = entry.get("event_date") or entry.get("event_time_utc") or ""

        if not all([home_team, away_team, league, event_date_str]):
            return None

        # Parse event date
        try:
            event_date = datetime.fromisoformat(
                str(event_date_str).replace("Z", "+00:00")
            ).date()
        except ValueError:
            return None

        live_result = get_live_match_result(home_team, away_team, event_date, league, api_key)
        if live_result:
            # Convert to expected format
            return {
                "home_team": home_team,
                "away_team": away_team,
                "match_date": event_date,
                "actual_selection": RESULT_TO_SELECTION.get(live_result["result_ft"], ""),
                "_source": "live_api",
            }
    except Exception as exc:
        import logging

        logging.getLogger(__name__).debug("[settle] live_result fallback failed: %s", exc)

    return None


def _get_live_tennis_result_fallback(entry: dict[str, Any], api_key: str) -> dict[str, Any] | None:
    """Try to get tennis result from live API when historical data doesn't have it."""
    try:
        from src.ingest.live_results import get_live_tennis_result
        from datetime import datetime

        # Tennis signals use "player"/"opponent" instead of "home_team"/"away_team"
        player1 = entry.get("player") or entry.get("home_team", "")
        player2 = entry.get("opponent") or entry.get("away_team", "")

        # Tennis signals don't always have explicit tour, infer from rank/surface
        # For simplicity, default to ATP (can enhance later if needed)
        tour = entry.get("league", "ATP")
        event_date_str = entry.get("event_date") or entry.get("event_time_utc") or ""

        if not all([player1, player2, event_date_str]):
            return None

        # Parse event date
        try:
            event_date = datetime.fromisoformat(
                str(event_date_str).replace("Z", "+00:00")
            ).date()
        except ValueError:
            return None

        live_result = get_live_tennis_result(player1, player2, event_date, tour, api_key)
        if live_result:
            # Map result to home/away: player wins = "H", opponent wins = "A"
            result_ft = live_result.get("result_ft", "")
            return {
                "player": player1,
                "opponent": player2,
                "match_date": event_date,
                "actual_selection": result_ft,  # H or A, matches tennis signal selection
                "_source": "live_api",
            }
    except Exception as exc:
        import logging

        logging.getLogger(__name__).debug("[settle] live_tennis_result fallback failed: %s", exc)

    return None


if __name__ == "__main__":
    main()
