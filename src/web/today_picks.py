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
    roi = pnl / stake * 100 if stake else 0
    accuracy = len(wins) / len(settled) * 100 if settled else 0
    lines = [
        "📈 <b>Статистика бумажных сигналов</b>",
        "",
        f"Всего записей: <b>{len(entries)}</b>",
        f"Открыто: {len(opened)} | Закрыто: {len(settled)}",
        f"Победы: {len(wins)} | Точность: {accuracy:.1f}%",
        f"ROI: {roi:+.1f}% | P&L: {pnl:+.2f}u",
        "",
    ]
    for sport, icon, name_ru in (("football", "⚽", "Футбол"), ("tennis", "🎾", "Теннис")):
        rows = [item for item in settled if str(item.get("sport") or "football") == sport]
        sport_wins = sum(item.get("result") == "win" for item in rows)
        sport_pnl = sum(_num(r.get("pnl_units")) for r in rows)
        sport_stake = sum(_num(r.get("stake_units"), 1.0) for r in rows)
        sport_roi = sport_pnl / sport_stake * 100 if sport_stake else 0
        if rows:
            lines.append(
                f"{icon} {name_ru}: {sport_wins}/{len(rows)} ({sport_wins/len(rows)*100:.0f}%)"
                f" | ROI {sport_roi:+.1f}%"
            )
        else:
            lines.append(f"{icon} {name_ru}: пока нет закрытых ставок")
    lines += ["", "<b>Как модель учится</b>"]
    lines += _build_model_info()
    return "\n".join(lines)


def _build_model_info() -> list[str]:
    """Short description of model training state."""
    import os
    from pathlib import Path

    model_dir = Path(os.environ.get("MODEL_DIR", "data/models"))
    lines: list[str] = []

    # Football models
    try:
        metas = sorted(model_dir.glob("dc_*.meta.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        leagues_seen: set[str] = set()
        for meta_path in metas:
            import json

            with open(meta_path) as f:
                meta = json.load(f)
            league = str(meta.get("league", "?"))
            if league in leagues_seen:
                continue
            leagues_seen.add(league)
            n_matches = meta.get("n_matches", "?")
            trained_on_raw = meta.get("trained_on", {})
            if isinstance(trained_on_raw, dict):
                trained_on = f"{str(trained_on_raw.get('start','?'))[:10]}–{str(trained_on_raw.get('end','?'))[:10]}"
            else:
                trained_on = str(trained_on_raw)[:10]
            lines.append(f"⚽ {league}: {n_matches} матчей, {trained_on}")
    except Exception:
        lines.append("⚽ Футбол: данные модели недоступны")

    # Tennis ELO model
    try:
        elo_path = model_dir / "tennis_elo_atp_latest.pkl"
        if elo_path.exists():
            import pickle

            with open(elo_path, "rb") as f:
                elo_model = pickle.load(f)
            params = getattr(elo_model, "params", None)
            n_players = getattr(params, "n_players", "?") if params else "?"
            n_matches = getattr(params, "n_matches", "?") if params else "?"
            lines.append(f"🎾 Теннис ELO: {n_players} игроков, {n_matches} матчей")
            lines.append("   Обновляется еженедельно по данным Jeff Sackmann ATP")
        else:
            lines.append("🎾 Теннис: модель не найдена")
    except Exception:
        lines.append("🎾 Теннис: данные модели недоступны")

    lines += [
        "",
        "Модели учатся на закрытых ставках: чем больше settled,",
        "тем точнее отделяются priority от watchlist сигналов.",
    ]
    return lines


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


_SURFACE_RU = {
    "clay": "грунт",
    "hard": "хард",
    "grass": "трава",
    "carpet": "ковёр",
    "indoor hard": "хард (крытый)",
    "indoor": "крытый",
}

_MARKET_ICON = {
    "h2h": "🏆",
    "spreads": "↔️",
    "totals": "🔢",
}


_SELECTION_RU_MAP = {
    "home": "победа хозяев (П1)",
    "draw": "ничья (Х)",
    "away": "победа гостей (П2)",
    "over": "тотал больше",
    "under": "тотал меньше",
    "btts_yes": "обе забьют — да",
    "btts_no": "обе забьют — нет",
}


def _football_bet_label(sel: str, sel_ru: str, home: str, away: str) -> str:
    sel_low = sel.lower().strip()
    if sel_low == "home":
        return f"П1 — победа {home}"
    if sel_low == "draw":
        return "Х — ничья"
    if sel_low == "away":
        return f"П2 — победа {away}"
    mapped = _SELECTION_RU_MAP.get(sel_low)
    if mapped:
        return mapped
    return sel_ru or sel or "?"


def _surface_ru(surface: Any) -> str:
    s = str(surface or "").lower().strip()
    return _SURFACE_RU.get(s, s or "?")


def _format_pick(item: dict[str, Any], priority: bool) -> list[str]:
    icon = "🟢" if priority else "🟡"
    odds = item.get("entry_odds", "?")
    prob = item.get("model_probability", item.get("model_prob"))
    market_prob = item.get("fair_market_probability", item.get("market_prob"))
    edge = item.get("edge_pct", item.get("edge_vs_fair_pct", "?"))
    prob_text = f"{float(prob):.1%}" if isinstance(prob, (int, float)) else "?"
    market_text = f"{float(market_prob):.1%}" if isinstance(market_prob, (int, float)) else "?"
    reason = escape(str(item.get("recommendation_reason", "")))
    market = item.get("market", "h2h")
    market_icon = _MARKET_ICON.get(str(market), "")
    if item.get("sport") == "tennis":
        selection_ru = escape(str(item.get("selection_ru", "")))
        if market == "h2h":
            title = f"{icon} 🎾{market_icon} <b>{escape(str(item.get('player', '?')))} победит {escape(str(item.get('opponent', '?')))}</b>"
        else:
            title = f"{icon} 🎾{market_icon} <b>{escape(str(item.get('player', '?')))} vs {escape(str(item.get('opponent', '?')))}</b> — {selection_ru or escape(str(item.get('selection', '?')))}"
        surface_ru = _surface_ru(item.get("surface"))
        recent_form = item.get("recent_form")
        form_text = f"{float(recent_form):.0%}" if isinstance(recent_form, (int, float)) else "?"
        facts = [
            f"Покрытие: {surface_ru} | Рейтинг: #{item.get('rank', '?')} vs #{item.get('opp_rank', '?')}",
            f"ELO: {_pct(item.get('elo_prob'))} | Форма (побед, посл. 10): {form_text}",
            f"Подача (выигрыш гейма): {_pct(item.get('serve_win_pct'))} | Отдых: {item.get('days_since_last_match', '?')} дн.",
            f"H2H-поправка: {_signed_pct(item.get('h2h_adj'))} | Модель: {escape(str(item.get('model_source', 'elo')))}",
        ]
    else:
        home = escape(str(item.get("home_team", "?")))
        away = escape(str(item.get("away_team", "?")))
        sel = item.get("selection", "")
        sel_ru = item.get("selection_ru", "")
        bet_label = _football_bet_label(sel, sel_ru, home, away)
        title = f"{icon} ⚽ <b>{home} — {away}</b>"
        league = escape(str(item.get("league", item.get("competition", "?"))))
        model_id = str(item.get("model_id", ""))
        model_label = model_id.split("_")[2] if model_id.count("_") >= 2 else league
        fair_odds = item.get("reference_fair_odds", "?")
        facts = [
            f"Ставка: <b>{bet_label}</b>",
            f"Лига: {league} | Dixon–Coles модель",
            f"Данные модели: {model_label} (только история голов)",
            f"Справедливый кэф модели: {fair_odds} | Ставка: {item.get('stake_units', 1)}u",
            "⚠️ Модель не учитывает травмы, состав, усталость — проверьте сами",
        ]
    lines = [
        "",
        title,
        f"📅 {_event_time_text(item)}",
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
