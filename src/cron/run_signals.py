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
    reports_dir = Path(os.environ.get("REPORTS_DIR", "data/reports"))
    today = date.today()

    print(f"[signals] Starting at {datetime.now(timezone.utc).isoformat()}")
    print(f"[signals] Leagues: {leagues} | Date: {today}")

    all_signals: list[dict] = []
    for league in leagues:
        try:
            signals = _run_league(league, model_dir, today)
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


def _run_league(league: str, model_dir: Path, today: date) -> list[dict]:
    from src.models.model_registry import ModelRegistry
    registry = ModelRegistry(model_dir)
    try:
        model = registry.load_latest(league, production_only=True)
    except Exception:
        print(f"[signals] {league}: no production model — skipping")
        return []

    # Signal generation logic (delegates to existing signal engine)
    try:
        from src.signals.run_signal_scan import generate_signals_for_league
        return generate_signals_for_league(model=model, league=league, scan_date=today)
    except ImportError:
        # Minimal fallback if run_signal_scan doesn't expose the function yet
        print(f"[signals] {league}: run_signal_scan.generate_signals_for_league not found — stub")
        return []


def _notify_telegram(signals: list[dict], today: date) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("[signals] Telegram not configured — dry-run stdout only")
        for sig in signals[:5]:
            print(f"  📊 {sig.get('market')} | edge={sig.get('edge_pct')}% | "
                  f"odds={sig.get('book_odds')} | {sig.get('match_id','')}")
        return

    try:
        import urllib.request
        lines = [f"📊 *{len(signals)} signals — {today}*\n"]
        for sig in signals[:10]:
            lines.append(
                f"• {sig.get('market','?')} | edge={sig.get('edge_pct','?')}% | "
                f"odds={sig.get('book_odds','?')} | Kelly={sig.get('recommended_stake_kelly_fraction','?')}"
            )
        if len(signals) > 10:
            lines.append(f"_...and {len(signals)-10} more_")

        text = "\n".join(lines)
        payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload.encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10):
            print(f"[signals] Telegram sent: {len(signals)} signals")
    except Exception as e:
        print(f"[signals] Telegram failed (non-critical): {e}", file=sys.stderr)


def _log_run(job: str, status: str, duration_s: float, message: str, meta: dict | None = None):
    try:
        from src.infrastructure.render_db import get_db
        get_db().log_cron_run(job, status, duration_s, message, meta)
    except Exception:
        pass


if __name__ == "__main__":
    main()
