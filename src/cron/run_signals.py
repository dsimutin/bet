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

    # Tennis ATP scan (runs alongside football leagues)
    tennis_signals = _run_tennis(model_dir)
    all_signals.extend(tennis_signals)
    print(f"[signals] tennis_atp: {len(tennis_signals)} signals")

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


def _run_tennis(model_dir: Path) -> list[dict]:
    """Scan ATP tennis signals via ELO model."""
    from src.signals.tennis_signal_scan import scan_tennis_signals

    api_key = os.environ.get("THE_ODDS_API_KEY", "")
    if not api_key:
        return []

    model_path = model_dir / "tennis_elo_atp_latest.pkl"
    try:
        result = scan_tennis_signals(model_path=model_path, api_key=api_key)
        return result.get("all_signals", [])
    except Exception as e:
        print(f"[signals] tennis_atp: FAILED — {e}", file=sys.stderr)
        return []


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
            if sig.get("sport") == "tennis":
                text = _format_tennis_signal(sig)
                result = sender.send_message(text)
            else:
                result = sender.send_signal(sig)
            if result.get("ok"):
                sent += 1
        except Exception as e:
            print(f"[signals] Telegram send failed for {sig.get('signal_id')}: {e}",
                  file=sys.stderr)

    print(f"[signals] Telegram: {sent}/{len(signals)} signals sent")


def _format_tennis_signal(sig: dict) -> str:
    player   = sig.get("player", "?")
    opponent = sig.get("opponent", "?")
    odds     = sig.get("entry_odds", "?")
    edge     = sig.get("edge_pct", "?")
    mp       = sig.get("model_prob", 0)
    mp_str   = f"{mp:.1%}" if isinstance(mp, float) else str(mp)
    surface  = sig.get("surface", "hard").capitalize()
    tour     = sig.get("tour", "ATP")
    book     = sig.get("bookmaker", "?")
    best_of  = sig.get("best_of", 3)
    bo_str   = " | BO5" if best_of == 5 else ""

    rank     = sig.get("rank")
    opp_rank = sig.get("opp_rank")
    rank_str = f" (#{rank})" if rank else ""
    opp_rank_str = f" (#{opp_rank})" if opp_rank else ""

    serve    = sig.get("serve_win_pct")
    serve_str = f" | подача {serve:.1%}" if serve else ""
    days     = sig.get("days_since_last_match")
    rest_str = f" | отдых {days}д" if days is not None else ""
    form     = sig.get("recent_form")
    form_str = f" | форма {form:.0%}" if form is not None else ""
    cap      = sig.get("capper_support")
    cap_n    = sig.get("capper_tips", 0)
    cap_str  = f"\n👥 Каперы: {cap:.0%} за ({cap_n} прогнозов)" if cap is not None and cap_n > 0 else ""
    markov   = sig.get("markov_prob")
    markov_str = f" | Марков={markov:.1%}" if markov else ""
    src      = sig.get("model_source", "elo_only")
    src_str  = " 🧮" if "markov" in src else ""

    # Alt bookmakers — compact line showing other options
    alt_books = sig.get("alt_books", [])
    if alt_books:
        alts = "  ".join(f"{a['bookmaker']} {a['odds']}" for a in alt_books[:4])
        alt_str = f"\nДругие BK: {alts}"
    else:
        alt_str = ""

    return (
        f"🎾 {tour} Сигнал — {surface}{bo_str}\n"
        f"{player}{rank_str} vs {opponent}{opp_rank_str}\n"
        f"Ставка: победа <b>{player}</b>\n"
        f"💰 Лучшая линия: <b>{book} @ {odds}</b> | edge=<b>{edge}%</b>\n"
        f"Модель: {mp_str}{markov_str}{src_str}{alt_str}\n"
        f"{serve_str.lstrip(' | ')}{rest_str}{form_str}{cap_str}\n"
        f"📄 Paper trade"
    )


def _log_run(job: str, status: str, duration_s: float, message: str, meta: dict | None = None):
    try:
        from src.infrastructure.render_db import get_db
        get_db().log_cron_run(job, status, duration_s, message, meta)
    except Exception:
        pass


if __name__ == "__main__":
    main()
