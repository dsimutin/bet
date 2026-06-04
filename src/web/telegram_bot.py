"""Telegram bot menu for unified football and tennis paper analytics."""

from __future__ import annotations
import json
import logging
import os
import time
import threading
import time
import urllib.request
from html import escape
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)
_seen_updates: set[int] = set()
_refresh_last_by_chat: dict[str, float] = {}


def handle_update(update: dict[str, Any]) -> None:
    update_id = update.get("update_id")
    if isinstance(update_id, int):
        if update_id in _seen_updates:
            return
        _seen_updates.add(update_id)
        if len(_seen_updates) > 1000:
            _seen_updates.clear()
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
    if not _chat_allowed(chat_id):
        _log.warning("[bot] ignoring non-allowlisted chat_id=%s", chat_id)
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
    if not _chat_allowed(chat_id):
        _log.warning("[bot] ignoring non-allowlisted callback chat_id=%s", chat_id)
        return
    action = callback.get("data")
    try:
        if action == "picks_today":
            _send_today(chat_id)
        elif action == "refresh":
            _refresh(chat_id)
        elif action == "stats":
            _send(chat_id, _stats_text(), _back_button())
        elif action == "history":
            _send(chat_id, _history_text(), _back_button())
        elif action == "weekly_report":
            _send(chat_id, _weekly_report_text(), _back_button())
        elif action == "how_it_works":
            _send(chat_id, _how_it_works(), _back_button())
        elif action == "debug_tennis":
            _debug_tennis(chat_id)
        else:
            _send_main_menu(chat_id, "")
    except Exception as exc:
        _log.exception("[bot] callback action=%s failed: %s", action, exc)
        _send(
            chat_id,
            "❌ Не удалось загрузить данные. Попробуйте через минуту.",
            _back_button(),
        )


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
            [
                {"text": "📋 Отчёт за неделю", "callback_data": "weekly_report"},
                {"text": "ℹ️ Как это работает", "callback_data": "how_it_works"},
            ],
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
    cooldown = int(os.environ.get("TELEGRAM_MANUAL_REFRESH_COOLDOWN_SECONDS", "300"))
    now = time.monotonic()
    last = _refresh_last_by_chat.get(chat_id, 0.0)
    if now - last < cooldown:
        remaining = int(cooldown - (now - last))
        _send(
            chat_id,
            f"⏳ Обновление недавно запускалось. Повторите через {remaining} сек.",
            _back_button(),
        )
        return
    _refresh_last_by_chat[chat_id] = now
    _send(chat_id, "🔄 Обновляю общий cached-скан футбола и тенниса. Это займёт до минуты.")

    def _run() -> None:
        from src.services.job_guard import job_guard

        with job_guard("signal_scan") as acquired:
            if not acquired:
                _send(
                    chat_id,
                    "⏳ Скан уже выполняется. Покажу свежие ставки после завершения.",
                    _back_button(),
                )
                return
            try:
                from src.cron.run_signals import main

                main()
                _send_today(chat_id)
            except Exception as exc:
                _log.exception("[bot] manual refresh failed: %s", exc)
                _send(
                    chat_id,
                    "❌ Обновление завершилось ошибкой. Подробности скрыты из соображений безопасности.",
                    _back_button(),
                )

    threading.Thread(target=_run, daemon=True).start()


def _stats_text() -> str:
    from src.web.today_picks import build_stats_text

    return build_stats_text()


def _history_text() -> str:
    from src.web.today_picks import build_history_text

    return build_history_text()


def _weekly_report_text() -> str:
    from src.web.today_picks import build_weekly_report_text

    return build_weekly_report_text()


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
            from src.models.timestamp_policy import verify_pre_match_timestamps
            from src.models.feedback_policy import FeedbackPolicy

            model_dir = Path(os.environ.get("MODEL_DIR", "data/models"))
            result = scan_tennis_h2h_runtime(
                model_dir / "tennis_elo_atp_latest.pkl", os.environ.get("THE_ODDS_API_KEY", "")
            )
            events = result.get("events_checked", 0)
            signals = result.get("signals_count", 0)
            skipped = result.get("skipped_no_data", 0)
            api_st = escape(str(result.get("api_status", "?")))
            skipped_note = (
                f"\n⚠️ Пропущено {skipped}: у этих игроков нет истории"
                " в ELO-модели (меньше 10 матчей в базе)"
                if skipped
                else ""
            )
            h2h_n = result.get("h2h_count", signals)
            spread_n = result.get("spread_count", 0)
            total_n = result.get("total_count", 0)
            modes = {
                "multimarket_cached": "мультирынок (кэш)",
                "h2h_low_quota_cached": "эконом (h2h, кэш)",
                "h2h_low_quota": "эконом (h2h)",
            }
            mode_text = modes.get(
                str(result.get("runtime_mode", "")), str(result.get("runtime_mode", "?"))
            )
            top = result.get("top_signals", [])
            top_lines = ""
            if top:
                mkt_icons = {"h2h": "🏆", "spreads": "↔️", "totals": "🔢"}
                policy = FeedbackPolicy({})
                signal_details = []
                for s in top[:5]:
                    mkt = s.get("market", "h2h")
                    icon = mkt_icons.get(mkt, "")
                    player = escape(str(s.get("player", "?")))
                    edge = s.get("edge_pct", "?")
                    ts_status = verify_pre_match_timestamps(s)
                    freshness = s.get("odds_freshness_tier", "?")
                    if ts_status != "verified_pre_match":
                        block_reason = f"⛔ {ts_status}"
                    elif freshness == "stale_blocked":
                        block_reason = "⛔ котировки устарели (>1ч)"
                    else:
                        decision = policy.evaluate(s)
                        block_reason = (
                            f"✅ {decision.tier}" if decision.tier != "blocked"
                            else f"⛔ {decision.reason[:40]}"
                        )
                    signal_details.append(
                        f"• {icon}{player} edge {edge}% [{mkt}] → {block_reason}"
                    )
                top_lines = "\n\n<b>Топ сигналы (с причиной блокировки):</b>\n" + "\n".join(
                    signal_details
                )
            text = (
                "🔍 <b>Диагностика тенниса</b>\n"
                f"Событий проверено: <b>{events}</b>\n"
                f"Сигналов: <b>{signals}</b>"
                f" (🏆 {h2h_n} h2h | ↔️ {spread_n} фора | 🔢 {total_n} тотал)\n"
                f"Пропущено: {skipped}{skipped_note}\n"
                f"Статус API: {api_st}\n"
                f"Режим: {escape(mode_text)}"
                f"{top_lines}"
            )
            _send(chat_id, text, _back_button())
        except Exception as exc:
            _log.exception("[bot] tennis diagnostics failed: %s", _sanitize_error(exc))
            _send(
                chat_id,
                "❌ Диагностика завершилась ошибкой. Подробности скрыты из соображений безопасности.",
                _back_button(),
            )

    threading.Thread(target=_run, daemon=True).start()


def _back_button() -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": "← Главное меню", "callback_data": "main_menu"}]]}


def _sanitize_error(exc: Exception) -> str:
    text = str(exc)
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_WEBHOOK_SECRET",
        "ADMIN_API_TOKEN",
        "THE_ODDS_API_KEY",
        "ODDS_API_IO_KEY",
        "DATABASE_URL",
    ):
        value = os.environ.get(key, "")
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


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
        payload = {"url": f"{app_url.rstrip('/')}/webhook/telegram"}
        secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
        if secret:
            payload["secret_token"] = secret
        data = json.dumps(payload).encode("utf-8")
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


def _chat_allowed(chat_id: str) -> bool:
    allowed_raw = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if not allowed_raw:
        return os.environ.get("APP_ENV", "development").lower() != "production"
    allowed = {item.strip() for item in allowed_raw.split(",") if item.strip()}
    return chat_id in allowed


def notify_settlement_results(
    settled_signals: list[dict],
    ledger_summary: dict | None = None,
) -> None:
    """Send Telegram notification with newly settled signal results."""
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id or not settled_signals:
        return

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%d.%m %H:%M UTC")
    wins = [s for s in settled_signals if s.get("result") == "win"]
    losses = [s for s in settled_signals if s.get("result") == "loss"]
    voids = [s for s in settled_signals if s.get("result") == "void"]
    pnl = sum(float(s.get("pnl_units", 0)) for s in settled_signals)

    lines = [f"📊 <b>Результаты — {now}</b>", ""]

    for s in settled_signals[:10]:
        result = s.get("result", "")
        icon = {"win": "✅", "loss": "❌", "void": "↩️"}.get(result, "❓")
        sport = s.get("sport", "football")
        if sport == "tennis":
            match = escape(f"{s.get('player', '?')} vs {s.get('opponent', '?')}")
        else:
            match = escape(f"{s.get('home_team', '?')} — {s.get('away_team', '?')}")
        sel = escape(str(s.get("selection_ru") or s.get("selection") or "?"))
        odds = s.get("entry_odds", "?")
        pu = float(s.get("pnl_units", 0))
        lines.append(f"{icon} {match} | {sel} @ {odds} → {pu:+.2f}u")

    lines.append("")
    lines.append(
        f"Итог: {len(wins)}✅ {len(losses)}❌ {len(voids)}↩️ | P&L: {pnl:+.2f}u"
    )

    if ledger_summary:
        total_settled = ledger_summary.get("settled", 0)
        total_wins = ledger_summary.get("wins", 0)
        total_pnl = float(ledger_summary.get("pnl_units", 0))
        winrate = round(total_wins / total_settled * 100, 1) if total_settled else 0
        lines.append(
            f"Всего: {total_wins}/{total_settled} побед ({winrate}%) | ROI {total_pnl:+.2f}u"
        )

    _send(chat_id, "\n".join(lines))
