"""Cron entrypoint: daily signal pipeline.

Invoked by Render Cron Job 'signal-pipeline' at 08:15 UTC.
Replaces GitHub Actions signal-pipeline.yml for production.
"""

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
    leagues = os.environ.get("LEAGUES", "EPL,BUNDESLIGA,LALIGA,SERIEA").split(",")
    model_dir = Path(os.environ.get("MODEL_DIR", "data/models"))
    ledger_path = Path(os.environ.get("LEDGER_PATH", "data/core/paper_signal_ledger.json"))
    staging_dir = Path(os.environ.get("STAGING_DIR", "data/staging"))
    reports_dir = Path(os.environ.get("REPORTS_DIR", "data/reports"))
    today = date.today()

    print(f"[signals] Starting at {datetime.now(timezone.utc).isoformat()}")
    print(f"[signals] Leagues: {leagues} | Date: {today}")

    all_signals: list[dict] = []
    for league in leagues:
        try:
            signals = _run_league(league, model_dir, staging_dir, today)
            all_signals.extend(signals)
            print(f"[signals] {league}: {len(signals)} signals")
        except Exception as e:
            print(f"[signals] {league}: FAILED — {e}", file=sys.stderr)

    if not all_signals:
        print("[signals] No signals generated today.")
        _log_run("signal-pipeline", "no_signals", time.perf_counter() - t0, "0 signals")
        return

    # Save signals to ledger
    try:
        from src.models.signal_ledger import SignalLedger
        ledger = SignalLedger.load_or_create(ledger_path)
        result = ledger.add_signal_batch(all_signals) if hasattr(ledger, "add_signal_batch") else None
        if result is None:
            for sig in all_signals:
                try:
                    ledger.add_signal(sig)
                except Exception:
                    pass
        ledger.save(ledger_path)
        print(f"[signals] Saved {len(all_signals)} signals → {ledger_path}")
    except Exception as e:
        print(f"[signals] Ledger save failed: {e}", file=sys.stderr)

    # Write daily signals file
    out_path = reports_dir / f"{today.isoformat()}_signals.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"date": str(today), "signals": all_signals}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[signals] Written → {out_path}")

    # Telegram dry-run notification
    _notify_telegram(all_signals, today)

    elapsed = round(time.perf_counter() - t0, 1)
    print(f"[signals] Done in {elapsed}s | {len(all_signals)} total signals")
    _log_run("signal-pipeline", "success", elapsed, f"{len(all_signals)} signals",
             {"n_signals": len(all_signals), "leagues": leagues})


def _run_league(league: str, model_dir: Path, staging_dir: Path, today: date) -> list[dict]:
    from src.models.model_registry import ModelRegistry
    from src.signals.run_signal_scan import generate_signals_for_league

    registry = ModelRegistry(model_dir)
    try:
        model = registry.load_latest(league, production_only=True)
    except FileNotFoundError:
        print(f"[signals] {league}: no production model — skipping (train first)")
        return []
    except Exception as e:
        print(f"[signals] {league}: model load failed — {e}", file=sys.stderr)
        return []

    return generate_signals_for_league(
        model=model,
        league=league,
        scan_date=today,
        staging_dir=staging_dir,
        odds_api_key=os.environ.get("THE_ODDS_API_KEY", ""),
    )


def _notify_telegram(signals: list[dict], today: date) -> None:
    """Send signals via TelegramSender.

    Uses dry_run=False when TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are set.
    Falls back to stdout dry-run when either is missing.
    Each signal is sent individually using the standard TelegramSender template
    so field names always match ProductionDixonColesSignalEngine output.
    """
    from src.integrations.telegram_sender import TelegramConfig, TelegramSender

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    dry_run = not (token and chat_id)

    if dry_run:
        print("[signals] Telegram not configured — dry-run stdout only")
        for sig in signals[:5]:
            home = sig.get("home_team", "?")
            away = sig.get("away_team", "?")
            edge = sig.get("edge_pct", sig.get("edge_vs_fair_pct", "?"))
            odds = sig.get("entry_odds", "?")
            sel = sig.get("selection_ru", sig.get("selection", "?"))
            print(f"  [DRY-RUN] {home} vs {away} | {sel} @ {odds} | edge={edge}%")
        return

    config = TelegramConfig(bot_token=token, chat_id=chat_id, dry_run=False)
    sender = TelegramSender(config)

    sent = 0
    for sig in signals:
        try:
            result = sender.send_signal(sig)
            if result.get("ok"):
                sent += 1
        except Exception as e:
            print(f"[signals] Telegram send failed for {sig.get('signal_id')}: {e}",
                  file=sys.stderr)

    print(f"[signals] Telegram: {sent}/{len(signals)} signals sent")


def _log_run(job: str, status: str, duration_s: float, message: str, meta: dict | None = None):
    try:
        from src.infrastructure.render_db import get_db
        get_db().log_cron_run(job, status, duration_s, message, meta)
    except Exception:
        pass


if __name__ == "__main__":
    main()
