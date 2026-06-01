"""Cron entrypoint: settle ledger + drift check.

Invoked by Render Cron Job 'settle-ledger' at 23:00 UTC.
Replaces GitHub Actions settle-ledger.yml for production.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> None:
    t0 = time.perf_counter()
    leagues = os.environ.get("LEAGUES", "EPL,BUNDESLIGA,LALIGA,SERIEA").split(",")
    seasons = os.environ.get("OPENFOOTBALL_SEASONS", "2023-24,2024-25").split(",")
    staging_dir = Path(os.environ.get("STAGING_DIR", "data/staging"))
    ledger_path = Path(os.environ.get("LEDGER_PATH", "data/core/paper_signal_ledger.json"))
    reports_dir = Path(os.environ.get("REPORTS_DIR", "data/reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)

    print(f"[settle] Starting at {datetime.now(timezone.utc).isoformat()}")
    print(f"[settle] Leagues: {leagues} | Seasons: {seasons}")

    # Step 1: Download results
    try:
        from src.ingest.openfootball import OpenFootballLoader
        loader = OpenFootballLoader()
        result = loader.build(leagues=leagues, seasons=seasons, use_cache=False)
        if result.dataframe.empty:
            print("[settle] No results downloaded — nothing to settle.")
            _log_run("settle-ledger", "skip", time.perf_counter() - t0, "no results")
            return

        csv_path = loader.save_combined(result.dataframe, staging_dir, "latest_results.csv")
        print(f"[settle] Downloaded {len(result.dataframe)} matches → {csv_path}")
    except Exception as e:
        print(f"[settle] Download failed: {e}", file=sys.stderr)
        _log_run("settle-ledger", "failure", time.perf_counter() - t0, str(e))
        sys.exit(1)

    # Step 2: Settle ledger
    results_csv = staging_dir / "latest_results.csv"
    if not results_csv.exists():
        print("[settle] No results CSV — skipping.")
        return

    try:
        from src.models.settle_signal_ledger import settle_ledger_from_results
        from src.models.signal_ledger import SignalLedger
        import pandas as pd

        ledger = SignalLedger.load_or_create(ledger_path)
        results_df = pd.read_csv(results_csv, encoding="latin-1")
        report = settle_ledger_from_results(ledger, results_df)

        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger.save(ledger_path)

        report_path = reports_dir / f"settlement_{date.today().isoformat()}.json"
        import json
        report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[settle] Settled {report.get('settled_count', 0)} bets → {report_path}")
    except Exception as e:
        print(f"[settle] Settlement failed: {e}", file=sys.stderr)
        _log_run("settle-ledger", "failure", time.perf_counter() - t0, str(e))
        sys.exit(1)

    # Step 3: CUSUM drift check
    try:
        from src.monitoring.drift_detector import CUSUMDriftDetector
        import json

        detector = CUSUMDriftDetector(threshold=0.15, drift_window=14, min_window=10)
        drift_report = detector.evaluate_ledger_path(ledger_path)

        drift_path = reports_dir / "drift_report.json"
        drift_path.write_text(
            json.dumps(drift_report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        status_str = "DRIFT DETECTED" if drift_report.drift_detected else "no drift"
        print(f"[settle] Drift: {status_str} | kelly={drift_report.kelly_multiplier}")
        _log_run("settle-ledger", "drift_alert" if drift_report.drift_detected else "success",
                 time.perf_counter() - t0, status_str,
                 {"drift_detected": drift_report.drift_detected,
                  "kelly_multiplier": drift_report.kelly_multiplier})
    except Exception as e:
        print(f"[settle] Drift check failed (non-critical): {e}", file=sys.stderr)

    # Step 4: Settle tennis signals
    _settle_tennis(ledger_path, reports_dir)

    elapsed = round(time.perf_counter() - t0, 1)
    print(f"[settle] Done in {elapsed}s")
    _log_run("settle-ledger", "success", elapsed, "completed")


def _settle_tennis(ledger_path: Path, reports_dir: Path) -> None:
    """Settle open tennis paper signals and send Telegram summary."""
    try:
        import json
        from src.models.signal_ledger import SignalLedger
        from src.models.settle_tennis_signals import (
            settle_tennis_from_sackmann,
            format_settlement_telegram,
        )

        cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "raw" / "tennis_atp"
        ledger = SignalLedger.load_or_create(ledger_path)

        report = settle_tennis_from_sackmann(ledger, cache_dir)
        if report.get("no_data"):
            print("[settle] Tennis: no ATP data available")
            return

        settled = report.get("settled", 0)
        unmatched = report.get("unmatched", 0)
        print(f"[settle] Tennis: {settled} settled, {unmatched} still pending")

        if settled > 0:
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            ledger.save(ledger_path)

            report_path = reports_dir / f"tennis_settlement_{date.today().isoformat()}.json"
            report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            print(f"[settle] Tennis report → {report_path}")

            # Telegram notification
            _notify_tennis_results(report.get("results", []))
    except Exception as e:
        print(f"[settle] Tennis settlement failed (non-critical): {e}", file=sys.stderr)


def _notify_tennis_results(results: list[dict]) -> None:
    """Send settlement summary to Telegram."""
    if not results:
        return
    try:
        from src.models.settle_tennis_signals import format_settlement_telegram
        from src.integrations.telegram_sender import TelegramConfig, TelegramSender

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not (token and chat_id):
            text = format_settlement_telegram(results)
            print(f"[settle] Tennis Telegram dry-run:\n{text}")
            return

        text = format_settlement_telegram(results)
        if not text:
            return
        config = TelegramConfig(bot_token=token, chat_id=chat_id, dry_run=False)
        sender = TelegramSender(config)
        sender.send_message(text)
        print(f"[settle] Tennis: Telegram settlement message sent ({len(results)} results)")
    except Exception as e:
        print(f"[settle] Tennis Telegram notify failed: {e}", file=sys.stderr)


def _log_run(job: str, status: str, duration_s: float, message: str, meta: dict | None = None):
    try:
        from src.infrastructure.render_db import get_db
        get_db().log_cron_run(job, status, duration_s, message, meta)
    except Exception:
        pass  # DB logging is best-effort


if __name__ == "__main__":
    main()
