"""Detailed Telegram views for today's football and tennis paper signals."""

from __future__ import annotations
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
import os

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
LEDGER_PATH = Path(os.environ.get("LEDGER_PATH", DATA_DIR / "core" / "paper_signal_ledger.json"))


def build_today_text() -> str:
    entries = list(_load_entries())
    today = date.today()
    visible = [
        item
        for item in entries
        if item.get("ledger_status") == "open"
        and item.get("delivery_status") != "blocked"
        and item.get("recommendation_tier", "priority") in {"priority", "watchlist"}
        and _event_day(item) == today
    ]
    visible.sort(
        key=lambda item: (
            0 if item.get("recommendation_tier") == "priority" else 1,
            -_num(item.get("edge_pct", item.get("edge_vs_fair_pct"))),
        )
    )
    priority = [item for item in visible if item.get("recommendation_tier") == "priority"]
    watch = [item for item in visible if item.get("recommendation_tier") == "watchlist"]
    lines = [f"📅 <b>Ставки на сегодня — {today.strftime('%d.%m.%Y')}</b>", ""]
    if not visible:
        lines += [
            "Подходящих сигналов на сегодня пока нет.",
            "Бот не заполняет пустоту случайными ставками. Нажмите «Обновить», чтобы проверить свежую линию.",
        ]
        return "\n".join(lines)
    if priority:
        lines.append(f"✅ <b>Приоритетные сигналы ({len(priority)})</b>")
        for item in priority:
            lines.extend(_format_pick(item, priority=True))
    if watch:
        lines.append(f"\n👀 <b>Наблюдение ({len(watch)})</b>")
        lines.append(
            "Edge есть, но уверенность ниже. Эти варианты бот сохраняет для обучения и показывает отдельно."
        )
        for item in watch:
            lines.extend(_format_pick(item, priority=False))
    lines.append(
        "\n📄 Бумажные сигналы. Коэффициенты меняются: перед любым решением проверьте линию самостоятельно."
    )
    return "\n".join(lines)


def build_stats_text() -> str:
    entries = list(_load_entries())
    settled = [item for item in entries if item.get("ledger_status") == "settled"]
    opened = [
        item
        for item in entries
        if item.get("ledger_status") == "open" and item.get("delivery_status") != "blocked"
    ]
    wins = [item for item in settled if item.get("result") == "win"]
    pnl = sum(_num(item.get("pnl_units")) for item in settled)
    stake = sum(_num(item.get("stake_units"), 1.0) for item in settled)
    lines = [
        "📈 <b>Статистика бумажных сигналов</b>",
        "",
        f"Всего записей: <b>{len(entries)}</b>",
        f"Открыто: {len(opened)} | Закрыто: {len(settled)}",
        f"Победы: {len(wins)} | Точность: {(len(wins) / len(settled) * 100 if settled else 0):.1f}%",
        f"ROI: {(pnl / stake * 100 if stake else 0):.1f}% | P&L: {pnl:.2f}u",
    ]
    for sport, icon in (("football", "⚽"), ("tennis", "🎾")):
        rows = [item for item in settled if str(item.get("sport") or "football") == sport]
        sport_wins = sum(item.get("result") == "win" for item in rows)
        lines.append(
            f"{icon} {sport}: {sport_wins}/{len(rows)}"
            if rows
            else f"{icon} {sport}: пока нет закрытых ставок"
        )
    return "\n".join(lines)


def build_history_text(limit: int = 20) -> str:
    entries = list(_load_entries())
    entries.sort(
        key=lambda item: item.get("ledger_updated_at_utc")
        or item.get("ledger_created_at_utc")
        or "",
        reverse=True,
    )
    lines = ["🏆 <b>История ставок</b>", "", "Последние записи по футболу и теннису:"]
    for item in entries[:limit]:
        sport = "🎾" if item.get("sport") == "tennis" else "⚽"
        result = str(item.get("result", "")) if item.get("result") else ""
        status = {"win": "✅", "loss": "❌", "void": "↩️"}.get(result, "⏳")
        if item.get("sport") == "tennis":
            name = f"{item.get('player', '?')} vs {item.get('opponent', '?')}"
        else:
            name = f"{item.get('home_team', '?')} — {item.get('away_team', '?')} [{item.get('selection_ru', item.get('selection', '?'))}]"
        lines.append(
            f"{status}{sport} {escape(str(name))} @ {item.get('entry_odds', '?')} | {escape(str(item.get('recommendation_tier', 'legacy')))}"
        )
    if len(lines) == 3:
        lines.append("Пока нет записей.")
    return "\n".join(lines)


def split_message(text: str, limit: int = 3900) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = block[:limit]
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


def _format_pick(item: dict[str, Any], priority: bool) -> list[str]:
    icon = "🟢" if priority else "🟡"
    odds = item.get("entry_odds", "?")
    prob = item.get("model_probability", item.get("model_prob"))
    market_prob = item.get("fair_market_probability", item.get("market_prob"))
    edge = item.get("edge_pct", item.get("edge_vs_fair_pct", "?"))
    prob_text = f"{float(prob):.1%}" if isinstance(prob, (int, float)) else "?"
    market_text = f"{float(market_prob):.1%}" if isinstance(market_prob, (int, float)) else "?"
    reason = escape(str(item.get("recommendation_reason", "")))
    if item.get("sport") == "tennis":
        title = f"{icon} 🎾 <b>{escape(str(item.get('player', '?')))} победит {escape(str(item.get('opponent', '?')))}</b>"
        facts = [
            f"Покрытие: {escape(str(item.get('surface', '?')))} | Рейтинг: #{item.get('rank', '?')} vs #{item.get('opp_rank', '?')}",
            f"ELO: {_pct(item.get('elo_prob'))} | Форма: {_pct(item.get('recent_form'))}",
            f"Подача: {_pct(item.get('serve_win_pct'))} | Отдых: {item.get('days_since_last_match', '?')} дн.",
            f"H2H-поправка: {_signed_pct(item.get('h2h_adj'))} | Модель: {escape(str(item.get('model_source', 'elo')))}",
        ]
    else:
        title = f"{icon} ⚽ <b>{escape(str(item.get('home_team', '?')))} — {escape(str(item.get('away_team', '?')))}</b> | {escape(str(item.get('selection_ru', item.get('selection', '?'))))}"
        facts = [
            f"Модель Dixon–Coles: {escape(str(item.get('model_id', '?')))}",
            f"Справедливый коэффициент модели: {item.get('reference_fair_odds', '?')}",
            f"Размер paper-ставки: {item.get('stake_units', 1)}u",
        ]
    lines = [
        "",
        title,
        f"Время: {_event_time_text(item)}",
        f"Коэффициент: <b>{odds}</b> | Вероятность модели: <b>{prob_text}</b>",
        f"Рынок после снятия маржи: {market_text} | Edge: <b>{edge}%</b>",
    ]
    lines.extend(f"• {fact}" for fact in facts)
    if reason:
        lines.append(f"• Статус: {reason}")
    return lines


def _load_entries() -> list[dict[str, Any]]:
    from src.models.signal_ledger import SignalLedger

    return list(SignalLedger.load_or_create(LEDGER_PATH).entries().values())


def _event_day(item: dict[str, Any]) -> date | None:
    raw = item.get("commence_time") or item.get("event_time_utc") or item.get("event_date")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            return None


def _event_time_text(item: dict[str, Any]) -> str:
    raw = item.get("commence_time") or item.get("event_time_utc") or item.get("event_date")
    if not raw:
        return "?"
    try:
        return (
            datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            .astimezone(timezone.utc)
            .strftime("%d.%m %H:%M UTC")
        )
    except ValueError:
        return escape(str(raw))


def _pct(value: Any) -> str:
    return f"{float(value):.1%}" if isinstance(value, (int, float)) else "?"


def _signed_pct(value: Any) -> str:
    return f"{float(value):+.1%}" if isinstance(value, (int, float)) else "?"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
