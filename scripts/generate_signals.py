"""
Generate real value-bet signals using trained Dixon-Coles model.

Downloads current-season EPL data from football-data.co.uk, loads the
production model from the registry, and writes signals to the paper ledger.

Usage:
    python scripts/generate_signals.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import date, datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

warnings.filterwarnings("ignore", category=RuntimeWarning)


def main(dry_run: bool = True) -> None:
    import pandas as pd

    from src.ingest.football_data_co_uk import FootballDataLoader as FootballDataCoUkLoader
    from src.infrastructure.persistent_ledger import load_ledger, save_ledger
    from src.models.model_registry import ModelRegistry
    from src.models.predictor import ModelValuePredictor
    from src.models.settle_signal_ledger import settle_ledger_from_results

    registry = ModelRegistry(_ROOT / "data" / "models")
    ledger_path = _ROOT / "data" / "core" / "paper_signal_ledger.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    # Load production model
    try:
        model, calibrator = registry.load_latest_with_calibrator("EPL")
        print(f"[signals] Loaded model: {model.model_id}")
    except FileNotFoundError:
        print("[signals] No production model found. Run bootstrap_data.py first.", file=sys.stderr)
        sys.exit(1)

    # Download current season from football-data.co.uk (2425 = 2024/25)
    loader = FootballDataCoUkLoader()
    raw_dir = _ROOT / "data" / "raw" / "football_data"
    try:
        csv_path = loader.download_season("E0", "2425", raw_dir)
        print(f"[signals] Downloaded current season: {csv_path}")
        df = pd.read_csv(csv_path, encoding="latin-1")
    except Exception as exc:
        print(f"[signals] Could not download current season data: {exc}", file=sys.stderr)
        sys.exit(1)

    # Compute dataset hash for anti-leakage
    import hashlib

    df_hash = "sha256:" + hashlib.sha256(df.to_csv(index=False).encode("utf-8")).hexdigest()

    # Settle any open signals against completed results
    ledger = load_ledger(ledger_path)
    settlement = settle_ledger_from_results(ledger, df)
    save_ledger(ledger, ledger_path)
    print(f"[signals] Settled {settlement['settled']} signal(s)")

    # Find upcoming matches (no result yet)
    df_upcoming = _find_upcoming(df)
    if df_upcoming.empty:
        print("[signals] No upcoming matches found in dataset.")
    else:
        print(f"[signals] Scanning {len(df_upcoming)} upcoming matches...")

    generated_at = datetime.now(timezone.utc).isoformat()
    predictor = ModelValuePredictor(model, calibrator=calibrator)
    new_signals: list[dict] = []

    for _, row in df_upcoming.iterrows():
        home = str(row.get("HomeTeam", ""))
        away = str(row.get("AwayTeam", ""))
        if not home or not away:
            continue
        if home not in (model.params.attack if model.params else {}):
            continue  # unknown team
        odds_h = _safe_float(row.get("B365H") or row.get("PSH"))
        odds_d = _safe_float(row.get("B365D") or row.get("PSD"))
        odds_a = _safe_float(row.get("B365A") or row.get("PSA"))
        if not (odds_h and odds_d and odds_a):
            continue

        sigs = predictor.signals(
            home,
            away,
            odds_1x2=(odds_h, odds_d, odds_a),
            min_edge_pct=3.0,
            min_model_prob=0.40,
        )
        for sig in sigs:
            event_date = str(row.get("Date", ""))[:10]
            signal = {
                "signal_id": f"dc_{date.today().isoformat()}_{home[:4]}_{away[:4]}_{sig.selection}",
                "model_id": model.model_id,
                "strategy_id": "dixon_coles_v1",
                "home_team": home,
                "away_team": away,
                "event_date": event_date,
                "market": "1X2",
                "selection": sig.selection,
                "entry_odds": sig.odds,
                "model_prob": sig.model_prob,
                "market_prob": sig.market_prob,
                "edge_pct": round(sig.edge_vs_fair * 100, 2),
                "dataset_hash": df_hash,
                "generated_at_utc": generated_at,
                "status": "paper",
            }
            new_signals.append(signal)

    if new_signals:
        result = ledger.add_signals(new_signals)
        print(
            f"[signals] Added {len(result.added)} new signal(s), {len(result.duplicates)} duplicates"
        )
    else:
        print("[signals] No value signals found above threshold.")

    save_ledger(ledger, ledger_path)

    # Save JSON report
    report_path = _ROOT / "data" / "reports" / f"{date.today()}_signals.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {"date": date.today().isoformat(), "signals": new_signals, "summary": ledger.summary()},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"[signals] Report saved: {report_path}")

    summary = ledger.summary()
    print(f"[signals] Ledger summary: {summary}")

    if dry_run:
        print("[signals] DRY-RUN mode — Telegram not sent.")


def _find_upcoming(df: "pd.DataFrame") -> "pd.DataFrame":
    import pandas as pd

    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    today = pd.Timestamp.today().normalize()
    # Upcoming = no result (FTR is NaN or empty) OR match date >= today
    mask = df["FTR"].isna() | (df["FTR"] == "") | (df["Date"] >= today)
    return df[mask].reset_index(drop=True)


def _safe_float(val: object) -> float | None:
    try:
        f = float(val)  # type: ignore[arg-type]
        return f if f > 1.0 else None
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    main(dry_run=not args.send)
