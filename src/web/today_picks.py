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
    tomorrow = today + __import__("datetime").timedelta(days=1)
    now = datetime.now(timezone.utc)

    def _in_window(item: dict) -> bool:
        d = _event_day(item)
        return d == today or d == tomorrow

    today_entries = [
        item
        for item in entries
        if item.get("ledger_status") == "open"
        and item.get("delivery_status") != "blocked"
        and item.get("recommendation_tier", "priority") in {"priority", "watchlist"}
        and _in_window(item)
    ]

    # Split into upcoming (not yet started) and already started
    upcoming = [item for item in today_entries if not _event_already_started(item, now)]
    started = [item for item in today_entries if _event_already_started(item, now)]

    upcoming.sort(
        key=lambda item: (
            0 if item.get("recommendation_tier") == "priority" else 1,
            -_num(item.get("edge_pct", item.get("edge_vs_fair_pct"))),
        )
    )
    priority = [item for item in upcoming if item.get("recommendation_tier") == "priority"]
    watch = [item for item in upcoming if item.get("recommendation_tier") == "watchlist"]
    lines = [f"📅 <b>Ставки — {today.strftime('%d.%m')} и {tomorrow.strftime('%d.%m.%Y')}</b>", ""]
    if not upcoming and not started:
        lines += [
            "Подходящих сигналов на сегодня пока нет.",
            "Бот не заполняет пустоту случайными ставками. Нажмите «Обновить», чтобы проверить свежую линию.",
        ]
        # Context: off-season for top leagues + WC countdown
        from datetime import date as _date

        wc_start = _date(2026, 6, 11)
        days_to_wc = (wc_start - today).days
        if 0 < days_to_wc <= 14:
            lines.append(
                f"\n🏆 <b>ЧМ 2026 начнётся через {days_to_wc} дн.</b> — бот начнёт сканировать матчи автоматически."
            )
        elif days_to_wc <= 0:
            lines.append("\n🏆 ЧМ 2026 идёт — бот сканирует матчи.")
        else:
            lines.append(
                "\n⚽ Топ-лиги (АПЛ, Бундеслига, и др.) в межсезонье. Сканируются: MLS, Бразилия, Аргентина, РПЛ, теннис."
            )
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
    if started:
        lines.append(f"\n⏳ <b>Уже начались ({len(started)}) — ожидаем результатов</b>")
        for item in started:
            sport = item.get("sport", "football")
            if sport == "tennis":
                match_label = escape(
                    str(item.get("player", "?")) + " vs " + str(item.get("opponent", "?"))
                )
            else:
                match_label = escape(f"{item.get('home_team', '?')} — {item.get('away_team', '?')}")
            lines.append(f"• {match_label} | {_event_time_text(item)}")
        lines.append("Результаты закроются автоматически после обновления данных.")
    if upcoming or started:
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
    expired = [item for item in entries if item.get("ledger_status") == "expired"]
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
    ]
    if expired:
        lines.append(
            f"⚠️ Истекло без результата: {len(expired)} "
            f"(данные не совпали с историей — не влияют на обучение)"
        )
    lines.append("")
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
        metas = sorted(
            model_dir.glob("dc_*.meta.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
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
        ledger_status = item.get("ledger_status", "")
        if ledger_status == "expired":
            status = "❓"
        else:
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


def _rest_line(
    home: str, rest_h: Any, fatigue_h: str, away: str, rest_a: Any, fatigue_a: str
) -> str:
    if rest_h is None and rest_a is None:
        return ""
    _fat = {"severe": "🔴 измотан", "mild": "🟡 устал", "none": "🟢 отдохнул"}
    h_txt = f"{_fat.get(fatigue_h, '')} {rest_h}д." if rest_h is not None else "?"
    a_txt = f"{_fat.get(fatigue_a, '')} {rest_a}д." if rest_a is not None else "?"
    return f"Отдых: {home} {h_txt} | {away} {a_txt}"


def _form_line(home: str, form_h: str, away: str, form_a: str) -> str:
    if not form_h and not form_a:
        return ""
    h = form_h or "?"
    a = form_a or "?"
    return f"Форма (посл. 5): {home} <b>{h}</b> | {away} <b>{a}</b>"


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
    elif item.get("model_source") == "bayesian_zero_shot":
        home = escape(str(item.get("home_team", "?")))
        away = escape(str(item.get("away_team", "?")))
        sel = item.get("selection", "")
        sel_ru = item.get("selection_ru", "")
        bet_label = _football_bet_label(sel, sel_ru, home, away)
        league_name = escape(str(item.get("league_name", item.get("league", "?"))))
        title = f"{icon} 🌍 <b>{home} — {away}</b>"
        facts = [
            f"Ставка: <b>{bet_label}</b>",
            f"Лига: {league_name} | Байесовская модель (нет истории)",
            f"Маржа БК: {item.get('margin_pct', '?')}% | Ставка: {item.get('stake_units', 0.5)}u (снижена)",
            "⚠️ Слабый сигнал: нет исторических данных. Только Watchlist.",
            "⚠️ Составы/травмы: проверьте вручную (Sofascore, Flashscore)",
        ]
    else:
        home = escape(str(item.get("home_team", "?")))
        away = escape(str(item.get("away_team", "?")))
        sel = item.get("selection", "")
        sel_ru = item.get("selection_ru", "")
        bet_label = _football_bet_label(sel, sel_ru, home, away)
        title = f"{icon} ⚽ <b>{home} — {away}</b>"
        league = escape(str(item.get("league", item.get("competition", "?"))))
        fair_odds = item.get("reference_fair_odds", "?")

        # Fatigue & form context
        rest_h = item.get("ctx_rest_days_home")
        rest_a = item.get("ctx_rest_days_away")
        form_h = item.get("ctx_form_str_home", "")
        form_a = item.get("ctx_form_str_away", "")
        fatigue_h = item.get("ctx_fatigue_home", "none")
        fatigue_a = item.get("ctx_fatigue_away", "none")
        ctx_adj = item.get("context_adj")

        rest_line = _rest_line(home, rest_h, fatigue_h, away, rest_a, fatigue_a)
        form_line = _form_line(home, form_h, away, form_a)
        adj_note = f" (скорр. контекстом {ctx_adj:+.1%})" if ctx_adj else ""

        facts = [
            f"Ставка: <b>{bet_label}</b>",
            f"Лига: {league} | Dixon–Coles (история голов){adj_note}",
            f"Справедливый кэф: {fair_odds} | Ставка: {item.get('stake_units', 1)}u",
        ]
        if rest_line:
            facts.append(rest_line)
        if form_line:
            facts.append(form_line)
        inj_text = item.get("injuries_text", "")
        if inj_text:
            facts.append(escape(inj_text))
        else:
            facts.append("⚠️ Травмы/состав: проверьте самостоятельно (Sofascore, Flashscore)")
    lines = [
        "",
        title,
        f"📅 {_event_time_text(item)}",
        f"Коэффициент: <b>{odds}</b> | Вероятность модели: <b>{prob_text}</b>",
        f"Рынок после снятия маржи: {market_text} | Edge: <b>{edge}%</b>",
    ]
    lines.extend(_trust_cockpit_lines(item))
    lines.extend(f"• {fact}" for fact in facts)
    if reason:
        lines.append(f"• Статус: {reason}")
    return lines


def _trust_cockpit_lines(item: dict[str, Any]) -> list[str]:
    score = _trust_score(item)
    if score >= 85:
        label = "A"
        icon = "🛡️"
    elif score >= 70:
        label = "B"
        icon = "🧭"
    elif score >= 50:
        label = "C"
        icon = "⚠️"
    else:
        label = "D"
        icon = "⛔"

    timestamp_status = str(item.get("timestamp_verification_status") or "unknown")
    freshness = str(item.get("odds_freshness_tier") or "unknown")
    age = item.get("odds_snapshot_age_seconds")
    age_text = f"{int(age)}с" if isinstance(age, (int, float)) else "?"
    market = str(item.get("market", item.get("market_key", "h2h")))
    tier = str(item.get("recommendation_tier", "legacy"))
    return [
        f"• {icon} Trust Cockpit: <b>{label}</b> ({score}/100)",
        f"• timestamp={escape(timestamp_status)} | freshness={escape(freshness)} ({age_text})",
        f"• market={escape(market)} | tier={escape(tier)}",
    ]


def _trust_score(item: dict[str, Any]) -> int:
    score = 100
    if item.get("timestamp_verification_status") != "verified_pre_match":
        score -= 30
    freshness = item.get("odds_freshness_tier")
    if freshness == "watchlist":
        score -= 12
    elif freshness not in {"priority", "unknown", None}:
        score -= 25
    if item.get("recommendation_tier") != "priority":
        score -= 12
    if str(item.get("market", item.get("market_key", "h2h"))) != "h2h":
        score -= 18
    if item.get("experimental") or item.get("experimental_market"):
        score -= 25
    edge = _num(item.get("edge_pct", item.get("edge_vs_fair_pct")))
    if edge <= 0:
        score -= 20
    elif edge > 55:
        score -= 20
    if _num(item.get("stake_units"), 1.0) <= 0:
        score -= 10
    return max(0, min(100, score))


def _load_entries() -> list[dict[str, Any]]:
    from src.infrastructure.persistent_ledger import load_ledger

    return list(load_ledger(LEDGER_PATH).entries().values())


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


def _event_already_started(item: dict[str, Any], now: datetime) -> bool:
    """Return True if the event start time has passed (match already underway or finished)."""
    raw = item.get("commence_time") or item.get("event_time_utc") or ""
    if not raw:
        return False
    try:
        event_dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return event_dt <= now
    except ValueError:
        return False


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


def build_weekly_report_text() -> str:
    """Weekly performance summary for the last 7 days."""
    from datetime import timedelta

    entries = list(_load_entries())
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    def _created_at(item: dict) -> datetime | None:
        raw = item.get("ledger_created_at_utc") or item.get("settled_at_utc") or ""
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    week_entries = [e for e in entries if (_created_at(e) or now) >= week_ago]
    settled = [e for e in week_entries if e.get("ledger_status") == "settled"]
    opened = [
        e for e in week_entries
        if e.get("ledger_status") == "open" and e.get("delivery_status") != "blocked"
    ]
    wins = [e for e in settled if e.get("result") == "win"]
    losses = [e for e in settled if e.get("result") == "loss"]
    pnl = sum(_num(e.get("pnl_units")) for e in settled)
    stake = sum(_num(e.get("stake_units"), 1.0) for e in settled if e.get("result") in {"win", "loss"})
    roi = pnl / stake * 100 if stake else 0.0
    accuracy = len(wins) / len(settled) * 100 if settled else 0.0
    avg_edge = (
        sum(_num(e.get("edge_pct")) for e in week_entries) / len(week_entries)
        if week_entries else 0.0
    )

    pnl_emoji = "📈" if pnl >= 0 else "📉"
    lines = [
        f"📊 <b>Недельный отчёт</b>",
        f"<i>{(now - timedelta(days=7)).strftime('%d.%m')} — {now.strftime('%d.%m.%Y')}</i>",
        "",
        f"Сигналов за неделю: <b>{len(week_entries)}</b>",
        f"Открыто: {len(opened)} | Закрыто: {len(settled)}",
    ]
    if settled:
        lines += [
            f"Победы / Поражения: <b>{len(wins)}W / {len(losses)}L</b>",
            f"Точность: <b>{accuracy:.1f}%</b>",
            f"{pnl_emoji} ROI: <b>{roi:+.1f}%</b> | P&amp;L: <b>{pnl:+.2f}u</b>",
            f"Средний edge: {avg_edge:.1f}%",
        ]
    else:
        lines += [
            "Закрытых ставок за неделю нет.",
            "Результаты появятся после завершения матчей.",
        ]

    # Per-sport breakdown
    lines.append("")
    for sport, icon, name_ru in (("football", "⚽", "Футбол"), ("tennis", "🎾", "Теннис")):
        rows = [e for e in settled if str(e.get("sport") or "football") == sport]
        if rows:
            sw = sum(e.get("result") == "win" for e in rows)
            sp = sum(_num(e.get("pnl_units")) for e in rows)
            ss = sum(_num(e.get("stake_units"), 1.0) for e in rows if e.get("result") in {"win", "loss"})
            sr = sp / ss * 100 if ss else 0.0
            lines.append(f"{icon} {name_ru}: {sw}/{len(rows)} | ROI {sr:+.1f}% | P&amp;L {sp:+.2f}u")
        else:
            lines.append(f"{icon} {name_ru}: нет ставок за неделю")

    # All-time totals for context
    all_settled = [e for e in entries if e.get("ledger_status") == "settled"]
    if all_settled:
        all_pnl = sum(_num(e.get("pnl_units")) for e in all_settled)
        all_stake = sum(_num(e.get("stake_units"), 1.0) for e in all_settled if e.get("result") in {"win", "loss"})
        all_roi = all_pnl / all_stake * 100 if all_stake else 0.0
        lines += [
            "",
            f"<b>Всего за всё время:</b> {len(all_settled)} ставок | ROI {all_roi:+.1f}% | P&amp;L {all_pnl:+.2f}u",
        ]

    lines += ["", "📄 Бумажные сигналы — реальные деньги не используются."]
    return "\n".join(lines)
