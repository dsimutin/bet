"""Cron entrypoint: settle paper ledger + drift check."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> None:
    t0 = time.perf_counter()
    leagues = [
        x.strip()
        for x in os.environ.get("LEAGUES", "EPL,BUNDESLIGA,LALIGA,SERIEA").split(",")
        if x.strip()
    ]
    seasons = [
        x.strip()
        for x in os.environ.get("OPENFOOTBALL_SEASONS", "2023-24,2024-25").split(",")
        if x.strip()
    ]
    staging_dir = Path(os.environ.get("STAGING_DIR", "data/staging"))
    ledger_path = Path(os.environ.get("LEDGER_PATH", "data/core/paper_signal_ledger.json"))
    reports_dir = Path(os.environ.get("REPORTS_DIR", "data/reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)

    print(f"[settle] Starting at {datetime.now(timezone.utc).isoformat()}")
    print(f"[settle] Leagues: {leagues} | Seasons: {seasons}")

    try:
        from src.ingest.openfootball import OpenFootballLoader

        loader = OpenFootballLoader()
        result = loader.build(leagues=leagues, seasons=seasons, use_cache=False)
        if result.dataframe.empty:
            print("[settle] No football results downloaded — continuing with tennis settlement.")
            football_df = None
        else:
            csv_path = loader.save_combined(result.dataframe, staging_dir, "latest_results.csv")
            print(f"[settle] Downloaded {len(result.dataframe)} matches → {csv_path}")
            import pandas as pd

            football_df = pd.read_csv(csv_path, encoding="latin-1")
    except Exception as exc:
        print(f"[settle] Football result download failed: {exc}", file=sys.stderr)
        football_df = None

    from src.infrastructure.persistent_ledger import load_ledger, save_ledger

    api_key = os.environ.get("THE_ODDS_API_KEY", "").strip()
    ledger = load_ledger(ledger_path)
    football_report: dict = {"settled_count": 0, "settled_signals": [], "summary": ledger.summary()}
    if football_df is not None:
        try:
            from src.models.settle_signal_ledger import settle_ledger_from_results

            football_report = settle_ledger_from_results(ledger, football_df, api_key=api_key)
            print(f"[settle] Football: {football_report.get('settled_count', 0)} settled")
        except Exception as exc:
            print(f"[settle] Football settlement failed: {exc}", file=sys.stderr)

    tennis_report = _settle_tennis(ledger, api_key)

    # Auto-expire open signals whose event passed >24h ago without a matching result.
    # This prevents stale signals from accumulating as permanent "open" entries.
    expired_ids = ledger.expire_stale_signals(hours_past_event=24.0)
    if expired_ids:
        print(f"[settle] Expired {len(expired_ids)} stale unresolved signal(s): {expired_ids}")

    save_ledger(ledger, ledger_path)

    report = {
        "date": date.today().isoformat(),
        "football": football_report,
        "tennis": tennis_report,
        "summary": ledger.summary(),
    }
    report_path = reports_dir / f"settlement_{date.today().isoformat()}.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(f"[settle] Report → {report_path}")

    try:
        from src.monitoring.drift_detector import CUSUMDriftDetector

        detector = CUSUMDriftDetector(threshold=0.15, drift_window=14, min_window=10)
        drift_report = detector.evaluate_ledger_path(ledger_path)
        drift_path = reports_dir / "drift_report.json"
        drift_path.write_text(
            json.dumps(drift_report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(
            f"[settle] Drift: {'DRIFT DETECTED' if drift_report.drift_detected else 'no drift'} "
            f"| kelly={drift_report.kelly_multiplier}"
        )
    except Exception as exc:
        print(f"[settle] Drift check failed (non-critical): {exc}", file=sys.stderr)

    elapsed = round(time.perf_counter() - t0, 1)
    total_settled = int(football_report.get("settled_count", 0)) + int(
        tennis_report.get("settled", 0)
    )
    print(f"[settle] Done in {elapsed}s | total settled={total_settled}")
    _log_run(
        "settle-ledger",
        "success",
        elapsed,
        f"settled={total_settled}",
        {"total_settled": total_settled},
    )


def _settle_tennis(ledger, api_key: str = "") -> dict:
    """Settle only supported tennis h2h signals."""
    try:
        from src.models.settle_tennis_signals import settle_tennis_from_sackmann

        cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "raw" / "tennis_atp"
        report = settle_tennis_from_sackmann(ledger, cache_dir, api_key=api_key)
        print(
            f"[settle] Tennis: {report.get('settled', 0)} settled, "
            f"{report.get('unmatched', 0)} pending"
        )
        return report
    except Exception as exc:
        print(f"[settle] Tennis settlement failed (non-critical): {exc}", file=sys.stderr)
        return {"settled": 0, "unmatched": 0, "error": str(exc)}


def _log_run(
    job: str, status: str, duration_s: float, message: str, meta: dict | None = None
) -> None:
    try:
        from src.infrastructure.render_db import get_db

        get_db().log_cron_run(job, status, duration_s, message, meta)
    except Exception:
        pass


if __name__ == "__main__":
    main()
