"""Telegram bot menu for unified football and tennis paper analytics."""

from __future__ import annotations
import json
import logging
import os
import threading
import urllib.request
from html import escape
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


def handle_update(update: dict[str, Any]) -> None:
    try:
        if "callback_query" in update:
            _handle_callback(update["callback_query"])
        elif "message" in update:
            _handle_message(update["message"])
    except Exception as exc:
        _log.exception("[bot] update failed: %s", exc)


def _handle_message(message: dict[str, Any]) -> None:
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = str(message.get("text", "")).strip()
    first_name = str(message.get("from", {}).get("first_name", ""))
    if not chat_id:
        return
    if text.startswith("/today"):
        _send_today(chat_id)
    elif text.startswith("/stats"):
        _send(chat_id, _stats_text(), _back_button())
    elif text.startswith("/history"):
        _send(chat_id, _history_text(), _back_button())
    elif text.startswith("/help"):
        _send(chat_id, _how_it_works(), _back_button())
    else:
        _send_main_menu(chat_id, first_name)


def _handle_callback(callback: dict[str, Any]) -> None:
    chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
    _answer_callback(str(callback.get("id", "")))
    if not chat_id:
        return
    action = callback.get("data")
    if action == "picks_today":
        _send_today(chat_id)
    elif action == "refresh":
        _refresh(chat_id)
    elif action == "stats":
        _send(chat_id, _stats_text(), _back_button())
    elif action == "history":
        _send(chat_id, _history_text(), _back_button())
    elif action == "how_it_works":
        _send(chat_id, _how_it_works(), _back_button())
    elif action == "debug_tennis":
        _debug_tennis(chat_id)
    else:
        _send_main_menu(chat_id, "")


def _send_main_menu(chat_id: str, first_name: str = "") -> None:
    greeting = f"Привет, {escape(first_name)}! " if first_name else ""
    text = (
        f"{greeting}🤖 <b>Бот спортивной аналитики</b>\n\n"
        "Футбол и теннис работают в одном контуре. Бот сохраняет сигналы, "
        "закрывает результаты и корректирует уровни доверия по накопленной истории.\n\n"
        "📄 Режим paper trading. Коэффициенты меняются: перед любым самостоятельным решением проверьте линию."
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
            [{"text": "ℹ️ Как это работает", "callback_data": "how_it_works"}],
            [{"text": "🔍 Диагностика тенниса", "callback_data": "debug_tennis"}],
        ]
    }
    _send(chat_id, text, keyboard)


def _send_today(chat_id: str) -> None:
    from src.web.today_picks import build_today_text, split_message

    for chunk in split_message(build_today_text()):
        _send(chat_id, chunk, _back_button())


def send_today_digest_default_chat() -> str:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        return "skip_no_chat"
    _send_today(chat_id)
    return "sent"


def _refresh(chat_id: str) -> None:
    _send(chat_id, "🔄 Обновляю общий cached-скан футбола и тенниса. Это займёт до минуты.")

    def _run() -> None:
        try:
            from src.cron.run_signals import main

            main()
            _send_today(chat_id)
        except Exception as exc:
            _log.exception("[bot] manual refresh failed: %s", exc)
            _send(chat_id, f"❌ Обновление завершилось ошибкой: {escape(str(exc))}", _back_button())

    threading.Thread(target=_run, daemon=True).start()


def _stats_text() -> str:
    from src.web.today_picks import build_stats_text

    return build_stats_text()


def _history_text() -> str:
    from src.web.today_picks import build_history_text

    return build_history_text()


def _how_it_works() -> str:
    return (
        "ℹ️ <b>Как работает бот</b>\n\n"
        "1. Получает cached-линии букмекеров по футболу и теннису.\n"
        "2. Футбол считает Dixon–Coles, теннис — ELO/Markov с покрытием, подачей, формой и усталостью.\n"
        "3. Каждый сигнал и его результат сохраняются в Supabase.\n"
        "4. На закрытых ставках бот корректирует уровни доверия: приоритетные варианты отделяются от наблюдения.\n"
        "5. Бот не выполняет ставки автоматически."
    )


def _debug_tennis(chat_id: str) -> None:
    _send(chat_id, "🔍 Запускаю экономную диагностику тенниса...")

    def _run() -> None:
        try:
            from src.signals.tennis_runtime_scan import scan_tennis_h2h_runtime

            model_dir = Path(os.environ.get("MODEL_DIR", "data/models"))
            result = scan_tennis_h2h_runtime(
                model_dir / "tennis_elo_atp_latest.pkl", os.environ.get("THE_ODDS_API_KEY", "")
            )
            text = (
                "🔍 <b>Диагностика тенниса</b>\n"
                f"Событий проверено: {result.get('events_checked', 0)}\n"
                f"Сигналов h2h: {result.get('signals_count', 0)}\n"
                f"Пропущено без данных: {result.get('skipped_no_data', 0)}\n"
                f"Статус API: {escape(str(result.get('api_status', '?')))}\n"
                f"Режим: {escape(str(result.get('runtime_mode', '?')))}"
            )
            _send(chat_id, text, _back_button())
        except Exception as exc:
            _send(chat_id, f"❌ Диагностика: {escape(str(exc))}", _back_button())

    threading.Thread(target=_run, daemon=True).start()


def _back_button() -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": "← Главное меню", "callback_data": "main_menu"}]]}


def _send(chat_id: str, text: str, reply_markup: dict[str, Any] | None = None) -> dict[str, Any]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return {}
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            _telegram_url(token, "sendMessage"),
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        _log.error("[bot] send failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _answer_callback(callback_id: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not callback_id:
        return
    try:
        req = urllib.request.Request(
            _telegram_url(token, "answerCallbackQuery"),
            data=json.dumps({"callback_query_id": callback_id}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception:
        pass


def setup_webhook(app_url: str) -> dict[str, Any]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return {"error": "TELEGRAM_BOT_TOKEN not set"}
    try:
        data = json.dumps({"url": f"{app_url.rstrip('/')}/webhook/telegram"}).encode("utf-8")
        req = urllib.request.Request(
            _telegram_url(token, "setWebhook"),
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        return {"error": str(exc)}


def get_webhook_info() -> dict[str, Any]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return {"error": "TELEGRAM_BOT_TOKEN not set"}
    try:
        with urllib.request.urlopen(_telegram_url(token, "getWebhookInfo"), timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        return {"error": str(exc)}


def _telegram_url(token: str, method: str) -> str:
    return "https://api.telegram.org/bot" + token + "/" + method
