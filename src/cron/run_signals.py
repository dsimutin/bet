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

    calibrator = None
    try:
        calibrator = registry.load_calibrator(model.model_id)
    except Exception:
        pass

    return generate_signals_for_league(
        model=model,
        league=league,
        scan_date=today,
        staging_dir=staging_dir,
        odds_api_key=os.environ.get("THE_ODDS_API_KEY", ""),
        calibrator=calibrator,
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
                market = sig.get("market", "h2h")
                if market == "spreads":
                    text = _format_tennis_spread_signal(sig)
                elif market == "totals":
                    text = _format_tennis_total_signal(sig)
                else:
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
    edge_pct = sig.get("edge_pct", 0)
    mp       = sig.get("model_prob", 0)
    tour     = sig.get("tour", "ATP")
    book     = sig.get("bookmaker", "?")
    best_of  = sig.get("best_of", 3)

    # Surface in plain Russian
    surface_ru = {
        "clay": "грунт (Roland Garros)",
        "grass": "трава (Wimbledon)",
        "hard": "хард",
        "carpet": "ковёр",
    }.get(sig.get("surface", "hard"), sig.get("surface", "hard"))

    # Round label
    round_label = "Финальная стадия" if best_of == 5 else "Ранний раунд"

    # Rank context
    rank     = sig.get("rank")
    opp_rank = sig.get("opp_rank")
    rank_str = f"#{rank}" if rank else "?"
    opp_rank_str = f"#{opp_rank}" if opp_rank else "?"

    # Confidence in plain words
    prob_pct = int(mp * 100) if isinstance(mp, float) else 0
    if prob_pct >= 75:
        conf_label = "Очень высокая 🟢"
    elif prob_pct >= 65:
        conf_label = "Высокая 🟡"
    else:
        conf_label = "Умеренная 🟠"

    # Why we recommend — plain reasons
    reasons = []
    form = sig.get("recent_form")
    if form is not None and form >= 0.6:
        reasons.append(f"в хорошей форме ({form:.0%} побед)")
    serve = sig.get("serve_win_pct")
    if serve is not None and serve >= 0.65:
        reasons.append(f"сильная подача ({serve:.0%})")
    days = sig.get("days_since_last_match")
    if days is not None and days >= 1:
        reasons.append(f"отдохнул {days} дн.")
    if rank and opp_rank and rank < opp_rank:
        reasons.append(f"выше в рейтинге (#{rank} vs #{opp_rank})")
    cap = sig.get("capper_support")
    cap_n = sig.get("capper_tips", 0)
    if cap is not None and cap >= 0.6 and cap_n >= 2:
        reasons.append(f"{cap_n} каперов ({cap:.0%}) тоже ставят на него")
    reasons_str = "\n".join(f"  • {r}" for r in reasons) if reasons else "  • превосходит соперника по рейтингу"

    # Best bookmaker + payout example
    stake_example = 1000
    payout = round(stake_example * float(odds)) if isinstance(odds, (int, float)) else "?"
    book_display = _BOOK_NAMES.get(book, book)

    # Alt bookmakers
    alt_books = sig.get("alt_books", [])
    if alt_books:
        alt_lines = "\n".join(
            f"  • {_BOOK_NAMES.get(a['bookmaker'], a['bookmaker'])}: {a['odds']}"
            for a in alt_books[:3]
        )
        alt_str = f"\n\nДругие варианты:\n{alt_lines}"
    else:
        alt_str = ""

    return (
        f"🎾 <b>Рекомендация — {tour}</b>\n"
        f"📍 {surface_ru} · {round_label}\n"
        f"\n"
        f"<b>{player}</b> против {opponent}\n"
        f"(рейтинг {rank_str} vs {opp_rank_str})\n"
        f"\n"
        f"✅ Ставить на победу <b>{player}</b>\n"
        f"\n"
        f"Почему:\n{reasons_str}\n"
        f"\n"
        f"Уверенность модели: {conf_label} ({prob_pct}%)\n"
        f"\n"
        f"💰 <b>Где ставить:</b>\n"
        f"  🏆 {book_display}: коэффициент <b>{odds}</b>\n"
        f"     → поставил 1 000 ₽ = получишь <b>{payout} ₽</b> при победе{alt_str}\n"
        f"\n"
        f"📊 Преимущество нашей модели над букмекером: <b>{edge_pct}%</b>\n"
        f"📄 Бумажная ставка (реальные деньги не используются)"
    )


# Readable bookmaker names
_BOOK_NAMES = {
    "betfair_ex_uk":  "Betfair",
    "betfair_ex_eu":  "Betfair EU",
    "pinnacle":       "Pinnacle",
    "bet365":         "Bet365",
    "williamhill":    "William Hill",
    "betsson":        "Betsson",
    "smarkets":       "Smarkets",
    "nordicbet":      "NordicBet",
    "betway":         "Betway",
    "fanduel":        "FanDuel",
    "draftkings":     "DraftKings",
    "unibet_eu":      "Unibet",
    "gtbets":         "GTBets",
    "mybookieag":     "MyBookie",
    "betonlineag":    "BetOnline",
    "lowvig":         "LowVig",
    "bovada":         "Bovada",
    "betus":          "BetUS",
}


def _format_tennis_spread_signal(sig: dict) -> str:
    player   = sig.get("player", "?")
    opponent = sig.get("opponent", "?")
    odds     = sig.get("entry_odds", "?")
    edge_pct = sig.get("edge_pct", 0)
    mp       = sig.get("model_prob", 0)
    book     = sig.get("bookmaker", "?")
    handicap = sig.get("handicap", 0)
    tour     = sig.get("tour", "ATP")
    surf_ru  = {"clay": "грунт", "grass": "трава", "hard": "хард"}.get(
        sig.get("surface", "hard"), sig.get("surface", "hard"))
    book_display = _BOOK_NAMES.get(book, book)
    hcap_str = f"+{handicap}" if handicap > 0 else str(handicap)
    prob_pct = int(mp * 100) if isinstance(mp, float) else 0
    stake = 1000
    payout = round(stake * float(odds)) if isinstance(odds, (int, float)) else "?"
    return (
        f"🎾 <b>{tour} — Фора по сетам</b>\n"
        f"📍 {surf_ru}\n\n"
        f"<b>{player}</b> против {opponent}\n\n"
        f"✅ Ставить: фора <b>{hcap_str}</b> на {player}\n\n"
        f"Почему: модель даёт {prob_pct}% вероятности покрыть фору\n"
        f"Преимущество над букмекером: <b>{edge_pct}%</b>\n\n"
        f"💰 {book_display}: @ <b>{odds}</b>\n"
        f"   Поставил 1 000 ₽ → получишь <b>{payout} ₽</b>\n\n"
        f"📄 Бумажная ставка — реальных денег нет"
    )


def _format_tennis_total_signal(sig: dict) -> str:
    player   = sig.get("player", "?")
    opponent = sig.get("opponent", "?")
    odds     = sig.get("entry_odds", "?")
    edge_pct = sig.get("edge_pct", 0)
    mp       = sig.get("model_prob", 0)
    book     = sig.get("bookmaker", "?")
    threshold = sig.get("total_threshold", "?")
    sel_ru   = sig.get("selection_ru", sig.get("selection", "?"))
    tour     = sig.get("tour", "ATP")
    surf_ru  = {"clay": "грунт", "grass": "трава", "hard": "хард"}.get(
        sig.get("surface", "hard"), sig.get("surface", "hard"))
    book_display = _BOOK_NAMES.get(book, book)
    prob_pct = int(mp * 100) if isinstance(mp, float) else 0
    stake = 1000
    payout = round(stake * float(odds)) if isinstance(odds, (int, float)) else "?"
    direction_icon = "📈" if "Больше" in str(sel_ru) else "📉"
    return (
        f"🎾 <b>{tour} — Тотал геймов</b>\n"
        f"📍 {surf_ru}\n\n"
        f"<b>{player}</b> против {opponent}\n\n"
        f"✅ Ставить: {direction_icon} <b>{sel_ru}</b>\n\n"
        f"Почему: модель оценивает вероятность в {prob_pct}%\n"
        f"Преимущество над букмекером: <b>{edge_pct}%</b>\n\n"
        f"💰 {book_display}: @ <b>{odds}</b>\n"
        f"   Поставил 1 000 ₽ → получишь <b>{payout} ₽</b>\n\n"
        f"📄 Бумажная ставка — реальных денег нет"
    )


def _log_run(job: str, status: str, duration_s: float, message: str, meta: dict | None = None):
    try:
        from src.infrastructure.render_db import get_db
        get_db().log_cron_run(job, status, duration_s, message, meta)
    except Exception:
        pass


if __name__ == "__main__":
    main()
