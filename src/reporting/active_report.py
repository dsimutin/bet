"""Active monitoring Telegram report formatter.

Produces the 3-hourly status message sent by run_active_report.
All sections are plain-text safe (no HTML parse_mode required).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from glob import glob
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
MODEL_DIR = Path(os.environ.get("MODEL_DIR", DATA_DIR / "models"))
LEDGER_PATH = Path(os.environ.get("LEDGER_PATH", DATA_DIR / "core" / "paper_signal_ledger.json"))
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", DATA_DIR / "reports"))

_LEAGUE_DISPLAY = {
    "EPL": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 EPL",
    "BUNDESLIGA": "🇩🇪 Bundesliga",
    "LALIGA": "🇪🇸 La Liga",
    "SERIEA": "🇮🇹 Serie A",
    "LIGUE1": "🇫🇷 Ligue 1",
}

# Odds API sport_key for each configured league
_LEAGUE_SPORT_KEY = {
    "EPL": "soccer_epl",
    "BUNDESLIGA": "soccer_germany_bundesliga",
    "LALIGA": "soccer_spain_la_liga",
    "SERIEA": "soccer_italy_serie_a",
    "LIGUE1": "soccer_france_ligue_one",
}

# World Cup 2026: group stage starts 2026-06-11
_WORLD_CUP_2026_START = datetime(2026, 6, 11, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def format_active_report(
    signals_result: dict[str, Any],
    settlement_result: dict[str, Any],
    training_result: dict[str, Any],
) -> str:
    """Return a ready-to-send Telegram text for the active status report."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts: list[str] = [f"🤖 Отчёт / Report — {now}", ""]

    parts += _section_football_by_league(signals_result)
    parts += _section_signals(signals_result)
    parts += _section_ledger_pnl()
    parts += _section_training(training_result)
    parts += _section_health(settlement_result)

    has_api = signals_result.get("has_odds_api_key", False)
    active_soccer = signals_result.get("active_soccer_leagues", [])
    total_signals = signals_result.get("signals_count", 0)

    if not has_api:
        parts += [
            "─────────────────────",
            "⚠️ Для сигналов нужен THE_ODDS_API_KEY",
            "Render Dashboard → Environment → добавь ключ",
            "Бесплатный план: https://the-odds-api.com (500 req/month)",
        ]
    elif has_api and active_soccer and total_signals == 0:
        # Key works, but configured leagues are in off-season
        # Show what IS active so user knows bot is healthy
        now_dt = datetime.now(timezone.utc)
        days_to_wc = (_WORLD_CUP_2026_START - now_dt).days
        wc_note = f"🏆 ЧМ 2026 через {days_to_wc} дн. (11 июня)!" if 0 < days_to_wc <= 30 else ""
        active_display = [s.replace("soccer_", "").replace("_", " ").title() for s in active_soccer[:6]]
        parts += ["─────────────────────", "ℹ️ Все настроенные лиги в межсезонье"]
        if wc_note:
            parts.append(wc_note)
        parts += [
            f"Активны сейчас ({len(active_soccer)}): {', '.join(active_display)}",
            "Сигналы вернутся в августе (EPL, Бундеслига, Ла Лига, Серия А, Лига 1)",
        ]

    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _section_football_by_league(signals_result: dict[str, Any]) -> list[str]:
    """Per-league signal breakdown — the main section users care about."""
    per_league: dict[str, dict] = signals_result.get("per_league", {})
    has_api = signals_result.get("has_odds_api_key", False)
    total_signals = signals_result.get("signals_count", 0)
    active_soccer = signals_result.get("active_soccer_leagues", [])
    providers_skip = signals_result.get("providers_skip", [])
    api_errored = any("Odds API" in p and "ошибка" in p for p in providers_skip)

    lines = ["⚽ Football — сигналы по лигам"]

    # World Cup 2026 countdown block
    now = datetime.now(timezone.utc)
    days_to_wc = (_WORLD_CUP_2026_START - now).days
    if 0 < days_to_wc <= 30:
        lines.append(f"  🏆 ЧМ 2026: старт через {days_to_wc} дн. (11 июня)")
    elif days_to_wc <= 0 and "soccer_fifa_world_cup" in active_soccer:
        lines.append("  🏆 ЧМ 2026: идёт! Сигналы по национальным командам не поддерживаются (нет модели)")

    if not per_league:
        leagues = signals_result.get("leagues", [])
        for lg in leagues:
            label = _LEAGUE_DISPLAY.get(lg, lg)
            lines.append(f"  {label}: — (нет данных)")
    else:
        for league, info in per_league.items():
            label = _LEAGUE_DISPLAY.get(league, league)
            status = info.get("status", "ok")
            n = info.get("signals", 0)
            sport_key = _LEAGUE_SPORT_KEY.get(league, "")

            if status == "no_model":
                lines.append(f"  {label}: ⚠️ нет модели")
            elif status == "error":
                err = info.get("error", "")
                lines.append(f"  {label}: ❌ {err[:60]}" if err else f"  {label}: ❌ ошибка")
            elif api_errored:
                lines.append(f"  {label}: ⚠️ Odds API — неверный ключ")
            elif status == "no_fixtures":
                if has_api and active_soccer and sport_key not in active_soccer:
                    lines.append(f"  {label}: 🔴 межсезонье (лига не активна)")
                elif has_api:
                    lines.append(f"  {label}: ✅ нет матчей сегодня")
                else:
                    lines.append(f"  {label}: — нет фикстур (нет Odds API)")
            elif n > 0:
                lines.append(f"  {label}: 🎯 {n} сигнал{'а' if 1 < n < 5 else 'ов' if n >= 5 else ''}")
            else:
                lines.append(f"  {label}: ✅ матчи есть, edge не найден")

    if total_signals > 0:
        top = signals_result.get("top_signals", [])
        if top:
            lines.append("")
            lines.append("Топ сигналы:")
            for s in top[:3]:
                home = s.get("home_team", s.get("HomeTeam", "?"))
                away = s.get("away_team", s.get("AwayTeam", "?"))
                edge = s.get("edge_pct", "?")
                odds_val = s.get("entry_odds", s.get("book_odds", "?"))
                sel = s.get("selection_ru", s.get("selection", "?"))
                lg = s.get("league", "")
                lg_label = _LEAGUE_DISPLAY.get(lg, lg)
                lines.append(f"  🎯 {home} vs {away}")
                lines.append(f"     {sel} @ {odds_val} | edge={edge}% | {lg_label}")

    return lines + [""]


def _section_signals(signals_result: dict[str, Any]) -> list[str]:
    new_signals = signals_result.get("signals_count", 0)
    sent = signals_result.get("sent_count", 0)
    dupes = signals_result.get("duplicates_skipped", 0)
    src_errors = signals_result.get("source_errors", [])
    duration = signals_result.get("duration_s", 0)
    no_signal_reason = signals_result.get("no_signal_reason", "")

    lines = ["📡 Сканирование / Signals"]
    lines.append(f"Новых сигналов: {new_signals}")
    if new_signals == 0 and no_signal_reason:
        lines.append(f"Причина: {no_signal_reason}")
    if sent > 0:
        lines.append(f"Отправлено алертов: {sent}")
    if dupes > 0:
        lines.append(f"Дублей пропущено: {dupes}")
    if src_errors:
        lines.append(f"Ошибки: {'; '.join(src_errors[:2])}")
    lines.append(f"Время: {duration:.1f}s")
    return lines + [""]


def _section_ledger_pnl() -> list[str]:
    """Show paper ledger P&L summary."""
    lines = ["💰 P&L / Results"]
    try:
        if not LEDGER_PATH.exists():
            lines.append("Леджер не найден")
            return lines + [""]
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        summary = data.get("summary", {})
        entries = list(data.get("entries", {}).values())
        total = len(entries)
        settled = [e for e in entries if e.get("ledger_status") == "settled"]
        open_ = [e for e in entries if e.get("ledger_status") == "open"]

        lines.append(f"Всего ставок: {total} (открытых: {len(open_)}, закрытых: {len(settled)})")

        if settled:
            wins = sum(1 for e in settled if e.get("result") == "win")
            losses = sum(1 for e in settled if e.get("result") == "loss")
            win_rate = wins / len(settled) * 100 if settled else 0
            lines.append(f"W/L: {wins}/{losses} ({win_rate:.0f}% winrate)")

        pnl = summary.get("pnl_units")
        roi = summary.get("roi_pct")
        if pnl is not None:
            sign = "+" if pnl >= 0 else ""
            lines.append(f"PnL: {sign}{pnl:.2f}u")
        if roi is not None:
            sign = "+" if roi >= 0 else ""
            lines.append(f"ROI: {sign}{roi:.1f}%")
    except Exception as exc:
        lines.append(f"Ошибка чтения леджера: {exc}")
    return lines + [""]


def _section_training(training_result: dict[str, Any]) -> list[str]:
    trained = training_result.get("trained", False)
    reason = training_result.get("training_reason", "")
    league = training_result.get("league", "")
    n_matches = training_result.get("n_matches", 0)
    old_brier = training_result.get("old_brier")
    new_brier = training_result.get("new_brier")
    promoted = training_result.get("promoted", False)
    model_age_h = training_result.get("model_age_hours")

    lines = ["🧠 Модели / Training"]

    # Per-league model status from meta files
    meta_files = sorted(glob(str(MODEL_DIR / "dc_*.meta.json")))
    prod_metas: list[dict] = []
    for mf in meta_files:
        try:
            m = json.loads(Path(mf).read_text())
            if m.get("status") == "production":
                prod_metas.append(m)
        except Exception:
            pass

    if prod_metas:
        for m in sorted(prod_metas, key=lambda x: x.get("league", "")):
            lg = m.get("league", "?")
            label = _LEAGUE_DISPLAY.get(lg, lg)
            brier = m.get("brier_score")
            brier_str = f" (Brier={brier:.3f})" if brier else ""
            lines.append(f"  {label}: production{brier_str}")
    else:
        lines.append("  Нет production моделей!")

    if trained:
        lines.append(f"Переобучение: да ({league}, {n_matches} матчей)")
        if old_brier is not None and new_brier is not None:
            direction = "↓ лучше" if new_brier < old_brier else "↑ хуже"
            lines.append(f"Brier: {old_brier:.4f} → {new_brier:.4f} ({direction})")
            lines.append("Решение: " + ("promoted ✅ продвинута" if promoted else "отклонена ❌"))
    else:
        skip_reason = reason.replace("training skipped: ", "").replace(
            "only ", "").replace("since last check, ", "")
        lines.append(f"Переобучение: пропущено ({skip_reason})")

    if model_age_h is not None:
        lines.append(f"Возраст модели: {model_age_h:.0f}ч")
    return lines + [""]


def _section_health(settlement_result: dict[str, Any]) -> list[str]:
    lines = ["🏥 Система / Health"]

    # Settlement
    settled = settlement_result.get("settled_count", 0)
    drift = settlement_result.get("drift_status", "no_data")
    kelly = settlement_result.get("kelly_multiplier", 1.0)
    wins = settlement_result.get("wins", 0)
    losses = settlement_result.get("losses", 0)
    lines.append(f"Закрыто ставок: {settled} (W={wins} L={losses})")
    if drift == "DRIFT":
        lines.append(f"⚠️ Дрейф модели! Kelly={kelly:.2f}")
    else:
        lines.append(f"Дрейф: ОК | Kelly={kelly:.1f}")

    # Last cron runs
    try:
        from src.models.run_history import read_last_run
        for job, label in [
            ("signal_scan", "Сигналы"),
            ("settlement", "Сеттлмент"),
            ("active_report", "Отчёт"),
        ]:
            rec = read_last_run(job)
            if rec:
                ts = rec.get("started_at", "?")[:16].replace("T", " ")
                status = rec.get("status", "?")
                icon = "✅" if status in ("success", "skip") else "⚠️"
                lines.append(f"{icon} {label}: {ts} UTC")
            else:
                lines.append(f"  {label}: —")
    except Exception:
        pass

    # Odds API status — show key presence AND validation result if available
    odds_key_raw = os.environ.get("THE_ODDS_API_KEY", "").strip()
    if odds_key_raw:
        key_len = len(odds_key_raw)
        key_hint = f"{odds_key_raw[:4]}...{odds_key_raw[-4:]}" if key_len > 8 else "***"
        lines.append(f"Odds API: ✅ ключ задан ({key_len} симв., {key_hint})")
    else:
        lines.append("Odds API: ❌ ключа нет — добавь THE_ODDS_API_KEY")

    return lines
