"""Telegram bot handler — inline keyboard interface.

Handles incoming updates from Telegram webhook.
Set webhook once via:
    POST /webhook/telegram/setup

Available commands & buttons:
    /start       — main menu with inline buttons
    /today       — today's picks
    /stats       — win/loss statistics
    /help        — what the bot does

Buttons (callback_data):
    picks_today  — send morning digest now
    stats        — ledger stats
    how_it_works — explanation
    history      — last 10 settled bets
    refresh      — run fresh scan
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
LEDGER_PATH = Path(os.environ.get("LEDGER_PATH", DATA_DIR / "core" / "paper_signal_ledger.json"))
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", DATA_DIR / "reports"))


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

def handle_update(update: dict[str, Any]) -> None:
    """Dispatch an incoming Telegram Update to the right handler."""
    try:
        if "callback_query" in update:
            _handle_callback(update["callback_query"])
        elif "message" in update:
            _handle_message(update["message"])
    except Exception as exc:
        _log.exception("[bot] Unhandled error in handle_update: %s", exc)


# ---------------------------------------------------------------------------
# Message handler
# ---------------------------------------------------------------------------

def _handle_message(msg: dict) -> None:
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = str(msg.get("text", "")).strip()
    first_name = msg.get("from", {}).get("first_name", "")

    if not chat_id:
        return

    if text.startswith("/start"):
        _send_main_menu(chat_id, first_name)
    elif text.startswith("/today"):
        _action_picks_today(chat_id)
    elif text.startswith("/stats"):
        _action_stats(chat_id)
    elif text.startswith("/help"):
        _action_how_it_works(chat_id)
    elif text.startswith("/history"):
        _action_history(chat_id)
    else:
        _send_main_menu(chat_id, first_name)


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

def _handle_callback(cb: dict) -> None:
    chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
    cb_id = cb.get("id", "")
    data = cb.get("data", "")

    # Always answer the callback to remove loading spinner
    _answer_callback(cb_id)

    if not chat_id:
        return

    if data == "picks_today":
        _action_picks_today(chat_id)
    elif data == "stats":
        _action_stats(chat_id)
    elif data == "how_it_works":
        _action_how_it_works(chat_id)
    elif data == "history":
        _action_history(chat_id)
    elif data == "refresh":
        _action_refresh(chat_id)
    elif data == "calibration":
        _action_calibration(chat_id)
    elif data == "main_menu":
        _send_main_menu(chat_id, "")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _send_main_menu(chat_id: str, first_name: str) -> None:
    greeting = f"Привет, {first_name}! " if first_name else ""
    text = (
        f"{greeting}🤖 <b>Бот для анализа ставок</b>\n\n"
        "Я ищу матчи, где букмекер недооценил одну из команд или игрока. "
        "Когда нахожу — присылаю рекомендацию с объяснением почему.\n\n"
        "<b>Всё бумажно</b> — реальных денег не использую, "
        "только анализирую и веду статистику."
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📊 Ставки на сегодня", "callback_data": "picks_today"},
                {"text": "🔄 Обновить", "callback_data": "refresh"},
            ],
            [
                {"text": "📈 Статистика", "callback_data": "stats"},
                {"text": "🏆 История ставок", "callback_data": "history"},
            ],
            [
                {"text": "ℹ️ Как это работает", "callback_data": "how_it_works"},
            ],
            [{"text": "🎯 Калибровка модели", "callback_data": "calibration"}],
        ]
    }
    _send(chat_id, text, reply_markup=keyboard)


def _action_picks_today(chat_id: str) -> None:
    _send(chat_id, "⏳ Сканирую матчи, подожди 20–30 секунд...")

    import threading

    def _run():
        try:
            from src.cron.run_active_report import (
                _run_signal_scan, _run_tennis_scan, _format_morning_digest,
            )
            today = date.today()
            signals_result = _run_signal_scan()
            tennis_result = _run_tennis_scan()

            football = signals_result.get("top_signals", [])
            tennis = tennis_result.get("all_signals", [])
            all_sigs = football + tennis

            text = _format_morning_digest(all_sigs, today)
            _send(chat_id, text, reply_markup=_back_button())
        except Exception as exc:
            _log.exception("[bot] picks_today failed: %s", exc)
            _send(chat_id, "❌ Не удалось загрузить ставки — попробуй позже.")

    threading.Thread(target=_run, daemon=True).start()


def _action_stats(chat_id: str) -> None:
    try:
        text = _build_stats_text()
    except Exception as exc:
        _log.error("[bot] stats failed: %s", exc)
        text = "❌ Не удалось загрузить статистику."
    _send(chat_id, text, reply_markup=_back_button())


def _action_how_it_works(chat_id: str) -> None:
    text = (
        "ℹ️ <b>Как работает бот</b>\n\n"
        "1️⃣ <b>Каждый час</b> я запрашиваю коэффициенты у букмекеров\n\n"
        "2️⃣ <b>Моя математическая модель</b> рассчитывает реальную вероятность "
        "победы каждой команды или игрока. Для футбола — модель Диксон-Коулс. "
        "Для тенниса — ELO-рейтинг с учётом покрытия и подачи.\n\n"
        "3️⃣ <b>Сравниваю</b> мою вероятность с тем, что предлагает букмекер. "
        "Если моя вероятность выше — это называется «преимущество над рынком» (edge).\n\n"
        "4️⃣ <b>Присылаю рекомендацию</b> только если edge больше 1.5% — "
        "иначе ставка не имеет смысла.\n\n"
        "5️⃣ <b>Записываю результат</b> — выиграла ставка или нет. "
        "Веду статистику точности.\n\n"
        "📊 <b>Сейчас умею:</b>\n"
        "• Футбол: АПЛ, Бундеслига, Ла Лига, Серия А, Лига 1\n"
        "• Теннис: ATP (Roland Garros, Wimbledon и др.)\n\n"
        "📄 <i>Всё бумажное — реальных денег нет. "
        "Это аналитика и образование.</i>"
    )
    _send(chat_id, text, reply_markup=_back_button())


def _action_history(chat_id: str) -> None:
    try:
        text = _build_history_text()
    except Exception as exc:
        _log.error("[bot] history failed: %s", exc)
        text = "❌ Не удалось загрузить историю."
    _send(chat_id, text, reply_markup=_back_button())


def _action_refresh(chat_id: str) -> None:
    _send(chat_id, "🔄 Запускаю свежий скан...")
    _action_picks_today(chat_id)


def _action_calibration(chat_id: str) -> None:
    try:
        from src.models.calibration import calibration_stats, format_calibration_telegram
        stats = calibration_stats(LEDGER_PATH)
        text = format_calibration_telegram(stats)
    except Exception as exc:
        text = f"❌ Ошибка: {exc}"
    _send(chat_id, text, reply_markup=_back_button())


# ---------------------------------------------------------------------------
# Text builders
# ---------------------------------------------------------------------------

def _build_stats_text() -> str:
    if not LEDGER_PATH.exists():
        return "📈 <b>Статистика</b>\n\nПока нет данных — ни одной ставки ещё не записано."

    raw = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    entries = list(raw.get("entries", {}).values())

    settled = [e for e in entries if e.get("ledger_status") == "settled"]
    open_ = [e for e in entries if e.get("ledger_status") == "open"]
    wins = [e for e in settled if e.get("result") == "win"]
    losses = [e for e in settled if e.get("result") == "loss"]

    total_pnl = sum(e.get("pnl_units", 0) or 0 for e in settled)
    win_rate = round(len(wins) / len(settled) * 100, 1) if settled else 0

    # Tennis vs football breakdown
    tennis_settled = [e for e in settled if e.get("sport") == "tennis"]
    football_settled = [e for e in settled if e.get("sport") != "tennis"]
    tennis_wins = sum(1 for e in tennis_settled if e.get("result") == "win")
    football_wins = sum(1 for e in football_settled if e.get("result") == "win")

    pnl_rub = round(total_pnl * 1000)
    pnl_sign = "+" if pnl_rub >= 0 else ""

    lines = [
        "📈 <b>Статистика бумажных ставок</b>\n",
        f"Всего ставок: <b>{len(entries)}</b>",
        f"Завершено: {len(settled)} | В ожидании: {len(open_)}\n",
    ]

    if settled:
        lines += [
            f"✅ Выиграло: <b>{len(wins)}</b>  ❌ Проиграло: <b>{len(losses)}</b>",
            f"Точность: <b>{win_rate}%</b>",
            f"P&L: <b>{pnl_sign}{pnl_rub} ₽</b> (из расч. 1 000 ₽/ставка)\n",
        ]
        if football_settled:
            fr = round(football_wins / len(football_settled) * 100, 1)
            lines.append(f"⚽ Футбол: {football_wins}/{len(football_settled)} ({fr}%)")
        if tennis_settled:
            tr = round(tennis_wins / len(tennis_settled) * 100, 1)
            lines.append(f"🎾 Теннис: {tennis_wins}/{len(tennis_settled)} ({tr}%)")
    else:
        lines.append("Пока нет завершённых ставок.")

    lines.append("\n📄 <i>Бумажная статистика — реальных денег нет</i>")

    # Add calibration section if enough settled bets
    try:
        from src.models.calibration import calibration_stats, format_calibration_telegram
        cal = calibration_stats(LEDGER_PATH)
        if cal.get("total_settled", 0) >= 20:
            lines.append("\n" + format_calibration_telegram(cal))
    except Exception:
        pass

    return "\n".join(lines)


def _build_history_text() -> str:
    if not LEDGER_PATH.exists():
        return "🏆 <b>История</b>\n\nПока нет записей."

    raw = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    entries = list(raw.get("entries", {}).values())
    settled = sorted(
        [e for e in entries if e.get("ledger_status") == "settled"],
        key=lambda e: e.get("ledger_updated_at_utc", ""),
        reverse=True,
    )

    if not settled:
        return "🏆 <b>История ставок</b>\n\nПока нет завершённых ставок."

    lines = ["🏆 <b>Последние ставки</b>\n"]
    for e in settled[:10]:
        icon = "✅" if e.get("result") == "win" else "❌"
        sport = e.get("sport", "football")
        sport_icon = "🎾" if sport == "tennis" else "⚽"
        odds = e.get("entry_odds", "?")
        pnl = e.get("pnl_units", 0) or 0
        pnl_rub = round(pnl * 1000)
        pnl_str = f"+{pnl_rub} ₽" if pnl_rub >= 0 else f"{pnl_rub} ₽"

        if sport == "tennis":
            name = f"{e.get('player', '?')} vs {e.get('opponent', '?')}"
        else:
            sel = e.get("selection_ru", e.get("selection", "?"))
            name = f"{e.get('home_team', '?')}–{e.get('away_team', '?')} [{sel}]"

        updated = e.get("ledger_updated_at_utc", "")[:10]
        lines.append(f"{icon}{sport_icon} {name}\n   @ {odds} → {pnl_str}  <i>{updated}</i>")

    open_count = sum(1 for e in entries if e.get("ledger_status") == "open")
    if open_count:
        lines.append(f"\n⏳ Ещё {open_count} ставок ждут результата")

    lines.append("\n📄 <i>Бумажная статистика</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

def _back_button() -> dict:
    return {"inline_keyboard": [[{"text": "← Главное меню", "callback_data": "main_menu"}]]}


def _send(chat_id: str, text: str, reply_markup: dict | None = None) -> dict:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        _log.debug("[bot] No token — would send: %s", text[:80])
        return {}

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        _log.error("[bot] sendMessage failed: %s", exc)
        return {}


def _answer_callback(callback_query_id: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not callback_query_id:
        return
    try:
        data = json.dumps({"callback_query_id": callback_query_id}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception:
        pass


def setup_webhook(app_url: str) -> dict:
    """Register this app's webhook with Telegram. Call once after deploy."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return {"error": "TELEGRAM_BOT_TOKEN not set"}

    webhook_url = f"{app_url.rstrip('/')}/webhook/telegram"
    data = json.dumps({"url": webhook_url}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/setWebhook",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            _log.info("[bot] Webhook set: %s → %s", webhook_url, result)
            return result
    except Exception as exc:
        _log.error("[bot] setWebhook failed: %s", exc)
        return {"error": str(exc)}


def get_webhook_info() -> dict:
    """Check current webhook registration."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return {"error": "TELEGRAM_BOT_TOKEN not set"}
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getWebhookInfo",
            headers={"User-Agent": "bet-analytics/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        return {"error": str(exc)}
