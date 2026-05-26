#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
demo_pipeline.py — Демонстрационный пайплайн Sports Betting Analytics MVP.

Запускается БЕЗ API-ключей на полностью синтетических данных.
Показывает весь поток обработки: генерация → нормализация → бэктест →
сигналы → отчёт → Telegram-сообщение.

Запуск:
    python demo_pipeline.py

Результаты сохраняются в data/reports/:
    demo_signals.json          — сигналы для «предстоящих» матчей
    demo_report.md             — Markdown-отчёт
    demo_telegram_payload.json — payload Telegram-сообщения (dry-run)
"""

from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Настройка пути для импорта из src/
# ---------------------------------------------------------------------------
# Добавляем корень проекта в sys.path, чтобы можно было делать `from src.xxx import ...`
_PROJECT_ROOT = Path(__file__).parent.resolve()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Импорты из стандартной библиотеки и pandas/pydantic (только они обязательны)
# ---------------------------------------------------------------------------
import pandas as pd
from pydantic import BaseModel, Field

# Фиксируем зерно генератора случайных чисел для воспроизводимости
random.seed(42)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

# Папка для сохранения результатов
REPORTS_DIR = _PROJECT_ROOT / "data" / "reports"

# Букмекеры в демо: три «целевых» российских + одна «справочная» линия
TARGET_BOOKMAKERS = ["fonbet", "winline", "betboom"]
REFERENCE_BOOKMAKER = "market_average"

# Пороговое значение edge для генерации сигнала (1.5%)
EDGE_THRESHOLD = 0.015

# Команды АПЛ 2025-26 для генерации синтетических матчей
EPL_TEAMS = [
    "Arsenal", "Chelsea", "Liverpool", "Manchester City", "Manchester United",
    "Tottenham", "Aston Villa", "Newcastle", "West Ham", "Brighton",
]

# Нормализация названий команд (пример маппинга)
TEAM_NAME_MAP: dict[str, str] = {
    "man city":       "Manchester City",
    "man utd":        "Manchester United",
    "man united":     "Manchester United",
    "spurs":          "Tottenham",
    "arsenal fc":     "Arsenal",
    "chelsea fc":     "Chelsea",
    "liverpool fc":   "Liverpool",
    "newcastle utd":  "Newcastle",
    "west ham utd":   "West Ham",
    "brighton & hove albion": "Brighton",
}


# ===========================================================================
# Pydantic-модели
# ===========================================================================

class OddsSnapshot(BaseModel):
    """Снимок коэффициентов одного букмекера для одного матча."""

    event_id: str = Field(..., description="Идентификатор события")
    home_team: str
    away_team: str
    bookmaker: str
    home_odds: float = Field(..., gt=1.0)
    draw_odds: float = Field(..., gt=1.0)
    away_odds: float = Field(..., gt=1.0)
    snapshot_ts: datetime
    event_ts: datetime
    is_reference: bool = Field(False, description="True для справочного источника")


class IllnessEvent(BaseModel):
    """Событие о статусе игрока (травма/дисквалификация)."""

    player_id: str
    team_id: str
    status: str  # out | doubtful | available
    report_ts: datetime
    description: str


class BacktestResult(BaseModel):
    """Результат одной бумажной ставки в бэктесте."""

    event_id: str
    bookmaker: str
    selection: str       # home | draw | away
    odds: float
    ref_fair_odds: float
    edge_pct: float
    stake: float = 1.0   # единичная ставка для расчёта ROI
    # Примечание: результат случайный для демо, т.к. реальных исходов нет
    won: bool
    pnl: float           # прибыль/убыток


class SignalRecord(BaseModel):
    """Сигнал для предстоящего матча."""

    signal_id: str
    generated_at: datetime
    event_id: str
    home_team: str
    away_team: str
    event_ts: datetime
    bookmaker: str
    selection: str
    target_odds: float
    ref_fair_odds: float
    edge_pct: float
    strategy: str
    paper_only: bool = True
    note: str = ""


# ===========================================================================
# ШАГ 1: Генерация синтетических коэффициентов АПЛ 2025-26
# ===========================================================================

def generate_synthetic_odds() -> list[OddsSnapshot]:
    """
    Генерирует синтетические коэффициенты 1X2 для 5 матчей АПЛ.

    Каждый матч представлен тремя российскими букмекерами (с небольшими
    отличиями для реализма) и одной справочной линией (market_average).
    Все коэффициенты лежат в реалистичных диапазонах.

    Возвращает список объектов OddsSnapshot.
    """
    print("\n[1/8] Генерация синтетических котировок для АПЛ 2025-26...")

    # Базовая дата снимка — вчера в 20:00 UTC
    base_snapshot = datetime.now(timezone.utc).replace(
        hour=20, minute=0, second=0, microsecond=0
    ) - timedelta(days=1)

    # Пары команд для 5 матчей (выбираем без повторений)
    team_pairs = [
        ("Arsenal", "Chelsea"),
        ("Liverpool", "Manchester City"),
        ("Manchester United", "Tottenham"),
        ("Aston Villa", "Newcastle"),
        ("West Ham", "Brighton"),
    ]

    snapshots: list[OddsSnapshot] = []

    for i, (home, away) in enumerate(team_pairs):
        event_id = f"epl_2526_{i+1:03d}"
        # Матчи проходили вчера с шагом в 2 часа
        event_ts = base_snapshot - timedelta(hours=2 * (5 - i))

        # «Справедливые» базовые вероятности (сумма = 1.0)
        # Для разнообразия варьируем силу команд
        if i == 0:
            # Примерно равные команды
            fair_probs = [0.42, 0.27, 0.31]
        elif i == 1:
            # Лёгкий фаворит дома
            fair_probs = [0.50, 0.25, 0.25]
        elif i == 2:
            # Гость фаворит
            fair_probs = [0.30, 0.27, 0.43]
        elif i == 3:
            # Заметный фаворит дома
            fair_probs = [0.55, 0.22, 0.23]
        else:
            # Почти ничья
            fair_probs = [0.35, 0.33, 0.32]

        # Справочная линия (market_average) — девиггированные вероятности → коэф.
        ref_odds = [round(1.0 / p, 3) for p in fair_probs]
        snapshots.append(OddsSnapshot(
            event_id=event_id,
            home_team=home,
            away_team=away,
            bookmaker=REFERENCE_BOOKMAKER,
            home_odds=ref_odds[0],
            draw_odds=ref_odds[1],
            away_odds=ref_odds[2],
            snapshot_ts=base_snapshot,
            event_ts=event_ts,
            is_reference=True,
        ))

        # Три российских букмекера — добавляем маржу (5–8%) и небольшой шум
        for bm in TARGET_BOOKMAKERS:
            # Маржа каждого букмекера: от 5% до 8%
            margin = random.uniform(0.05, 0.08)
            # Маленький шум в вероятностях (имитирует разные оценки)
            noisy_probs = [
                p * (1.0 + random.uniform(-0.02, 0.02))
                for p in fair_probs
            ]
            total = sum(noisy_probs)
            # Нормализуем + добавляем маржу (overround)
            overround = 1.0 + margin
            bm_odds = [
                round((total / p) / overround, 3)
                for p in noisy_probs
            ]
            # Гарантируем минимальный коэффициент > 1.01
            bm_odds = [max(o, 1.01) for o in bm_odds]

            snapshots.append(OddsSnapshot(
                event_id=event_id,
                home_team=home,
                away_team=away,
                bookmaker=bm,
                home_odds=bm_odds[0],
                draw_odds=bm_odds[1],
                away_odds=bm_odds[2],
                snapshot_ts=base_snapshot + timedelta(seconds=random.randint(0, 120)),
                event_ts=event_ts,
                is_reference=False,
            ))

    print(f"    Сгенерировано снимков: {len(snapshots)} "
          f"({len(team_pairs)} матчей × {len(TARGET_BOOKMAKERS) + 1} источника)")
    return snapshots


# ===========================================================================
# ШАГ 2: Генерация синтетических событий о травмах
# ===========================================================================

def generate_synthetic_illness_events(snapshots: list[OddsSnapshot]) -> list[IllnessEvent]:
    """
    Генерирует синтетические события о травмах/дисквалификациях.

    Часть событий опубликована ДО начала матча (должны включаться в анализ),
    часть — ПОСЛЕ начала матча (должны исключаться как look-ahead bias).

    Возвращает список IllnessEvent.
    """
    print("\n[2/8] Генерация синтетических событий о травмах (тест look-ahead bias)...")

    # Берём первый матч для демонстрации
    first_event = next(s for s in snapshots if s.event_id == "epl_2526_001")
    event_ts = first_event.event_ts

    events = [
        # ✅ Опубликованы ДО матча — должны включаться
        IllnessEvent(
            player_id="player_bukayo_saka",
            team_id="Arsenal",
            status="doubtful",
            report_ts=event_ts - timedelta(hours=5),
            description="Букайо Сака под вопросом: лёгкая травма бедра на тренировке",
        ),
        IllnessEvent(
            player_id="player_reece_james",
            team_id="Chelsea",
            status="out",
            report_ts=event_ts - timedelta(hours=2),
            description="Рис Джеймс не сыграет: повторное повреждение колена",
        ),
        # ❌ Опубликованы ПОСЛЕ матча — look-ahead bias, должны исключаться!
        IllnessEvent(
            player_id="player_declan_rice",
            team_id="Arsenal",
            status="out",
            report_ts=event_ts + timedelta(hours=1),
            description="[LOOK-AHEAD] Деклан Райс выбыл — но это стало известно ПОСЛЕ матча",
        ),
        IllnessEvent(
            player_id="player_enzo_fernandez",
            team_id="Chelsea",
            status="doubtful",
            report_ts=event_ts + timedelta(minutes=30),
            description="[LOOK-AHEAD] Энцо Фернандес — событие после стартового свистка",
        ),
    ]

    # Фильтрация по cutoff_ts = event_ts (защита от look-ahead bias)
    cutoff_ts = event_ts
    valid_events = [e for e in events if e.report_ts < cutoff_ts]
    excluded_events = [e for e in events if e.report_ts >= cutoff_ts]

    print(f"    Всего событий: {len(events)}")
    print(f"    ✅ Включено (до матча): {len(valid_events)}")
    print(f"    ❌ Исключено look-ahead bias: {len(excluded_events)}")
    for exc in excluded_events:
        print(f"       — {exc.player_id}: report_ts={exc.report_ts.isoformat()}")

    return valid_events


# ===========================================================================
# ШАГ 3: Нормализация коэффициентов (девиггирование и усреднение)
# ===========================================================================

def devig_multiplicative(odds: list[float]) -> list[float]:
    """
    Удаляет маржу из коэффициентов мультипликативным методом.

    Каждая подразумеваемая вероятность делится на overround (сумму вероятностей).
    Возвращает «справедливые» коэффициенты без маржи.
    """
    if not odds or any(o <= 1.0 for o in odds):
        return odds
    implied = [1.0 / o for o in odds]
    overround = sum(implied)
    fair_probs = [p / overround for p in implied]
    return [round(1.0 / p, 4) for p in fair_probs]


def normalize_team_name(name: str) -> str:
    """Возвращает каноническое название команды через TEAM_NAME_MAP."""
    return TEAM_NAME_MAP.get(name.strip().lower(), name.strip())


def normalize_snapshots(snapshots: list[OddsSnapshot]) -> pd.DataFrame:
    """
    Нормализует снимки коэффициентов в единый DataFrame.

    Для каждого снимка:
    - Нормализует названия команд
    - Девиггирует коэффициенты
    - Вычисляет маржу букмекера
    - Строит плоскую таблицу (одна строка = один исход)

    Возвращает DataFrame с колонками:
        event_id, home_team, away_team, bookmaker, selection,
        raw_odds, fair_odds, margin_pct, event_ts, snapshot_ts, is_reference
    """
    print("\n[3/8] Нормализация и девиггирование коэффициентов...")

    rows: list[dict] = []
    for snap in snapshots:
        home = normalize_team_name(snap.home_team)
        away = normalize_team_name(snap.away_team)

        raw = [snap.home_odds, snap.draw_odds, snap.away_odds]
        fair = devig_multiplicative(raw)

        # Вычисляем маржу (overround - 1) × 100%
        overround = sum(1.0 / o for o in raw)
        margin_pct = round((overround - 1.0) * 100, 2)

        for selection, raw_o, fair_o in zip(
            [f"{home} (Хозяева)", "Ничья", f"{away} (Гости)"],
            raw,
            fair,
        ):
            rows.append({
                "event_id":     snap.event_id,
                "home_team":    home,
                "away_team":    away,
                "bookmaker":    snap.bookmaker,
                "selection":    selection,
                "raw_odds":     raw_o,
                "fair_odds":    fair_o,
                "margin_pct":   margin_pct,
                "event_ts":     snap.event_ts,
                "snapshot_ts":  snap.snapshot_ts,
                "is_reference": snap.is_reference,
            })

    df = pd.DataFrame(rows)
    print(f"    Записей после нормализации: {len(df)}")
    print(f"    Уникальных событий: {df['event_id'].nunique()}")
    print(f"    Уникальных букмекеров: {df['bookmaker'].nunique()} "
          f"({', '.join(df['bookmaker'].unique())})")
    print(f"    Средняя маржа (без справочного): "
          f"{df[~df['is_reference']]['margin_pct'].mean():.2f}%")
    return df


# ===========================================================================
# ШАГ 4: Простой бэктест
# ===========================================================================

def run_backtest(df: pd.DataFrame) -> tuple[list[BacktestResult], dict]:
    """
    Запускает простой бэктест стратегии поиска ценности.

    Логика:
        Для каждого исхода у каждого целевого букмекера:
        если target_fair_odds > ref_fair_odds * (1 + EDGE_THRESHOLD):
            → размещаем бумажную ставку

    Результаты: случайные (для демо), но метки edge реальные.
    Примечание: в реальном пайплайне результат берётся из исторических данных.

    Возвращает список BacktestResult и словарь метрик.
    """
    print(f"\n[4/8] Бэктест (порог edge={EDGE_THRESHOLD*100:.1f}%, бумажные ставки)...")

    # Справочная линия (девиггированная market_average)
    ref_df = df[df["is_reference"]].copy()
    ref_df = ref_df[["event_id", "selection_key", "fair_odds"]].copy() \
        if "selection_key" in ref_df.columns \
        else ref_df.assign(
            selection_key=ref_df.apply(
                lambda r: _selection_key(r["event_id"], r["selection"]),
                axis=1,
            )
        )[["event_id", "selection_key", "fair_odds"]]

    # Целевые букмекеры
    target_df = df[~df["is_reference"]].copy()
    target_df["selection_key"] = target_df.apply(
        lambda r: _selection_key(r["event_id"], r["selection"]),
        axis=1,
    )

    # Добавляем справочный fair_odds
    ref_map: dict[str, float] = {}
    for _, row in df[df["is_reference"]].iterrows():
        key = _selection_key(row["event_id"], row["selection"])
        ref_map[key] = row["fair_odds"]

    results: list[BacktestResult] = []
    bets_placed = 0

    for _, row in target_df.iterrows():
        sel_key = _selection_key(row["event_id"], row["selection"])
        ref_fair = ref_map.get(sel_key)
        if ref_fair is None:
            continue

        # Вычисляем edge
        edge = row["fair_odds"] / ref_fair - 1.0

        if edge > EDGE_THRESHOLD:
            bets_placed += 1
            # Для демо результат случайный (нет реальных итогов матчей)
            # В реальном пайплайне: won = (actual_result == selection)
            won = random.random() < (1.0 / row["raw_odds"])  # случайный результат
            pnl = (row["raw_odds"] - 1.0) if won else -1.0

            results.append(BacktestResult(
                event_id=row["event_id"],
                bookmaker=row["bookmaker"],
                selection=row["selection"],
                odds=row["raw_odds"],
                ref_fair_odds=ref_fair,
                edge_pct=round(edge * 100, 2),
                stake=1.0,
                won=won,
                pnl=round(pnl, 4),
            ))

    # Метрики бэктеста
    n_bets = len(results)
    if n_bets > 0:
        total_pnl = sum(r.pnl for r in results)
        roi = total_pnl / n_bets * 100
        win_rate = sum(1 for r in results if r.won) / n_bets * 100
    else:
        total_pnl = 0.0
        roi = 0.0
        win_rate = 0.0

    metrics = {
        "n_bets":     n_bets,
        "total_pnl":  round(total_pnl, 4),
        "roi_pct":    round(roi, 2),
        "win_rate_pct": round(win_rate, 2),
    }

    print(f"    Ставок найдено: {n_bets} (edge > {EDGE_THRESHOLD*100:.1f}%)")
    print(f"    ROI (демо, случайные исходы): {roi:.2f}%")
    print(f"    Win Rate (демо): {win_rate:.1f}%")
    print(f"    Суммарный PnL (демо): {total_pnl:+.4f} ед. ставки")
    print("    ⚠️  Примечание: результаты демо случайны — реальные исходы матчей отсутствуют")
    return results, metrics


def _selection_key(event_id: str, selection: str) -> str:
    """Формирует ключ «событие + исход» для объединения таблиц."""
    # Используем первое слово выбора (название команды или «Ничья»)
    token = selection.split("(")[0].strip().split()[0].lower()
    return f"{event_id}__{token}"


# ===========================================================================
# ШАГ 5: Генерация сигналов для «предстоящих» матчей
# ===========================================================================

def generate_signals(df: pd.DataFrame) -> list[SignalRecord]:
    """
    Генерирует сигналы для 2 синтетических предстоящих матчей.

    «Предстоящие» матчи имеют временны́е метки в будущем.
    Для каждого матча генерируем 1–2 записи SignalRecord там, где edge > порога.
    Результаты сохраняются в data/reports/demo_signals.json.
    """
    print("\n[5/8] Генерация сигналов для предстоящих матчей...")

    now_utc = datetime.now(timezone.utc)

    # Синтетические «предстоящие» матчи
    upcoming = [
        {
            "event_id": "epl_2526_future_001",
            "home_team": "Arsenal",
            "away_team": "Liverpool",
            "event_ts": now_utc + timedelta(hours=26),
            # Коэффициенты специально выставлены с edge у fonbet на хозяина
            "ref_fair": {"home": 2.45, "draw": 3.80, "away": 2.90},
            "fonbet":   {"home": 2.58, "draw": 3.55, "away": 2.70},
            "winline":  {"home": 2.40, "draw": 3.75, "away": 2.85},
        },
        {
            "event_id": "epl_2526_future_002",
            "home_team": "Manchester City",
            "away_team": "Chelsea",
            "event_ts": now_utc + timedelta(hours=50),
            "ref_fair": {"home": 1.85, "draw": 3.70, "away": 4.10},
            "fonbet":   {"home": 1.82, "draw": 3.65, "away": 4.00},
            "winline":  {"home": 1.80, "draw": 3.60, "away": 4.20},
        },
    ]

    signals: list[SignalRecord] = []
    signal_counter = 1

    for match in upcoming:
        ref = match["ref_fair"]
        home = match["home_team"]
        away = match["away_team"]

        for bm in ["fonbet", "winline"]:
            if bm not in match:
                continue
            bm_odds: dict[str, float] = match[bm]

            for sel_key, sel_label, raw_o in [
                ("home", f"{home} (Хозяева)", bm_odds["home"]),
                ("draw", "Ничья",             bm_odds["draw"]),
                ("away", f"{away} (Гости)",   bm_odds["away"]),
            ]:
                ref_fair = ref[sel_key]
                # Девиггируем коэффициент целевого букмекера (одиночный исход)
                # Приближение: fair ≈ raw * (1 - margin_approx)
                # Для демо используем dev. на уровне трёх исходов
                bm_all = list(bm_odds.values())
                ref_all = list(ref.values())
                fair_bm = devig_multiplicative(bm_all)
                fair_ref = devig_multiplicative(ref_all)
                fair_bm_sel = fair_bm[["home", "draw", "away"].index(sel_key)]
                fair_ref_sel = fair_ref[["home", "draw", "away"].index(sel_key)]

                edge = fair_bm_sel / fair_ref_sel - 1.0

                if edge > EDGE_THRESHOLD:
                    sig = SignalRecord(
                        signal_id=f"SIG_{signal_counter:04d}",
                        generated_at=now_utc,
                        event_id=match["event_id"],
                        home_team=home,
                        away_team=away,
                        event_ts=match["event_ts"],
                        bookmaker=bm,
                        selection=sel_label,
                        target_odds=raw_o,
                        ref_fair_odds=round(fair_ref_sel, 4),
                        edge_pct=round(edge * 100, 2),
                        strategy="ref_value_soccer_1x2",
                        paper_only=True,
                        note="Синтетический демо-сигнал. Не является торговой рекомендацией.",
                    )
                    signals.append(sig)
                    signal_counter += 1

    # Сохраняем в JSON
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / "demo_signals.json"
    payload = [json.loads(s.model_dump_json()) for s in signals]
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"    Сигналов сгенерировано: {len(signals)}")
    for s in signals:
        print(f"    • {s.signal_id}: {s.home_team} vs {s.away_team} | "
              f"{s.bookmaker} | {s.selection} | {s.target_odds:.3f} | "
              f"edge={s.edge_pct:.2f}%")
    print(f"    Сохранено: {out_path}")
    return signals


# ===========================================================================
# ШАГ 6: Построение Markdown-отчёта
# ===========================================================================

def build_markdown_report(
    snapshots: list[OddsSnapshot],
    backtest_results: list[BacktestResult],
    metrics: dict,
    signals: list[SignalRecord],
    valid_illness_events: list[IllnessEvent],
) -> str:
    """
    Строит Markdown-отчёт с результатами демо-пайплайна.

    Сохраняет файл в data/reports/demo_report.md.
    Возвращает строку с содержимым отчёта.
    """
    print("\n[6/8] Построение Markdown-отчёта...")

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    def h(level: int, text: str) -> None:
        lines.append("#" * level + " " + text)
        lines.append("")

    def p(text: str) -> None:
        lines.append(text)
        lines.append("")

    def rule() -> None:
        lines.append("---")
        lines.append("")

    # Заголовок
    h(1, "Sports Betting Analytics MVP — Демо-отчёт")
    p(f"*Сгенерирован: {now_str}*")
    p("> ⚠️ **Только бумажная торговля. Не является инвестиционным советом.**  ")
    p("> Все данные синтетические. Результаты случайны и не отражают реальную доходность.")
    rule()

    # Сводка по данным
    h(2, "1. Сводка по данным")
    unique_events = len({s.event_id for s in snapshots})
    p(f"- Снимков коэффициентов: **{len(snapshots)}**")
    p(f"- Уникальных матчей: **{unique_events}**")
    p(f"- Целевых букмекеров: **{', '.join(TARGET_BOOKMAKERS)}**")
    p(f"- Справочный источник: **{REFERENCE_BOOKMAKER}**")
    p(f"- Событий о травмах (после фильтрации look-ahead): **{len(valid_illness_events)}**")
    rule()

    # Результаты бэктеста
    h(2, "2. Результаты бэктеста (бумажная торговля)")
    p(f"- Порог edge: **{EDGE_THRESHOLD*100:.1f}%**")
    p(f"- Количество ставок: **{metrics['n_bets']}**")
    p(f"- ROI (демо): **{metrics['roi_pct']:.2f}%** *(случайные исходы)*")
    p(f"- Win Rate (демо): **{metrics['win_rate_pct']:.1f}%** *(случайные исходы)*")
    p(f"- Суммарный PnL (демо): **{metrics['total_pnl']:+.4f}** ед. ставки")
    p("*Примечание: исходы матчей случайны — демо не содержит реальных результатов.*")

    if backtest_results:
        h(3, "Детали ставок")
        lines.append(
            "| Матч | Букмекер | Исход | Кеф. | Ref Fair | Edge% | Выигрыш | PnL |"
        )
        lines.append("|------|----------|-------|------|----------|-------|---------|-----|")
        for r in backtest_results[:10]:  # показываем не более 10
            won_str = "✅" if r.won else "❌"
            lines.append(
                f"| {r.event_id} | {r.bookmaker} | {r.selection[:20]} | "
                f"{r.odds:.3f} | {r.ref_fair_odds:.3f} | "
                f"{r.edge_pct:.2f}% | {won_str} | {r.pnl:+.4f} |"
            )
        if len(backtest_results) > 10:
            lines.append(f"*... и ещё {len(backtest_results) - 10} ставок*")
        lines.append("")
    rule()

    # Сигналы для предстоящих матчей
    h(2, "3. Сигналы для предстоящих матчей")
    if signals:
        lines.append("| ID | Матч | Букмекер | Исход | Кеф. | Ref Fair | Edge% |")
        lines.append("|----|------|----------|-------|------|----------|-------|")
        for s in signals:
            match_str = f"{s.home_team} vs {s.away_team}"
            lines.append(
                f"| {s.signal_id} | {match_str} | {s.bookmaker} | "
                f"{s.selection[:20]} | {s.target_odds:.3f} | "
                f"{s.ref_fair_odds:.3f} | {s.edge_pct:.2f}% |"
            )
        lines.append("")
    else:
        p("*Сигналов не обнаружено (edge не превысил порог).*")
    p("Файл: `data/reports/demo_signals.json`")
    rule()

    # Ключевые концепции
    h(2, "4. Ключевые концепции")
    h(3, "Девиггирование")
    p(
        "Букмекеры закладывают маржу в коэффициенты (overround > 100%). "
        "Девиггирование возвращает «справедливые» вероятности с суммой 1.0. "
        "Метод: каждая подразумеваемая вероятность делится на сумму всех вероятностей."
    )
    h(3, "Справочная линия (Reference Line)")
    p(
        "Среднерыночная справедливая линия — консенсусная оценка вероятности, "
        "формируемая эффективными рынками (Pinnacle, Betfair, агрегаторы). "
        "В демо моделируется как `market_average`."
    )
    h(3, "CLV (Closing Line Value)")
    p(
        "Метрика качества ставки: насколько курс при входе был лучше закрывающей линии. "
        "Формула: `CLV% = (entry_odds / closing_odds - 1) × 100`. "
        "Систематически положительный CLV — признак реального преимущества (edge)."
    )
    h(3, "Защита от look-ahead bias")
    p(
        "Все данные (травмы, новости) фильтруются по `cutoff_ts = время начала матча`. "
        "Использование данных с `report_ts >= cutoff_ts` делает бэктест недействительным. "
        "В демо: 2 из 4 событий о травмах отфильтрованы как look-ahead."
    )
    rule()

    # Дисклеймер
    h(2, "⚠️ Дисклеймер")
    p(
        "Данный отчёт сгенерирован в аналитических целях. "
        "Проект работает в режиме **только бумажной торговли** и **не размещает ставки автоматически**. "
        "Все целевые букмекеры — лицензированные операторы РФ (лицензия ФНС, Федеральный закон № 244-ФЗ). "
        "Ставки на спорт связаны с риском потери средств. "
        "Горячая линия помощи: **8-800-700-44-51** (бесплатно по России)."
    )

    content = "\n".join(lines)
    out_path = REPORTS_DIR / "demo_report.md"
    out_path.write_text(content, encoding="utf-8")
    print(f"    Отчёт сохранён: {out_path}")
    return content


# ===========================================================================
# ШАГ 7: Dry-run Telegram-сообщение
# ===========================================================================

def dry_run_telegram(signals: list[SignalRecord], metrics: dict) -> None:
    """
    Формирует и выводит Telegram-сообщение (dry-run, без отправки).

    Сохраняет payload в data/reports/demo_telegram_payload.json.
    Реальная отправка не производится — только печать в stdout.
    """
    print("\n[7/8] Dry-run Telegram-сообщения...")

    now_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    # Формируем текст сообщения в формате Telegram Markdown v2
    lines: list[str] = [
        "📊 *Sports Betting Analytics MVP*",
        f"🗓 {now_str} \\| Демо\\-отчёт",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "📈 *Бэктест \\(бумажная торговля\\)*",
        f"  Ставок: `{metrics['n_bets']}`",
        f"  ROI\\*: `{metrics['roi_pct']:.2f}%`",
        f"  Win Rate\\*: `{metrics['win_rate_pct']:.1f}%`",
        "  \\*случайные исходы, только демо",
        "",
    ]

    if signals:
        lines.append("🔔 *Сигналы \\(предстоящие матчи\\)*")
        for s in signals:
            event_str = f"{s.home_team} vs {s.away_team}"
            # Экранируем спецсимволы Telegram MD v2
            event_esc = event_str.replace("-", "\\-").replace(".", "\\.")
            sel_esc = s.selection.replace("(", "\\(").replace(")", "\\)").replace("-", "\\-")
            lines.append(
                f"  • *{event_esc}* \\| {s.bookmaker} \\| {sel_esc}"
            )
            lines.append(
                f"    Кеф\\. `{s.target_odds:.3f}` \\| Edge `{s.edge_pct:.2f}%`"
            )
        lines.append("")
    else:
        lines.append("🔔 *Сигналов нет* \\(edge не превысил порог\\)")
        lines.append("")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "⚠️ _Только бумажная торговля\\. Не является торговой рекомендацией\\._",
    ]

    message_text = "\n".join(lines)

    # Payload для Bot API (метод sendMessage)
    payload = {
        "method":     "sendMessage",
        "chat_id":    "${TELEGRAM_CHAT_ID}",
        "text":       message_text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }

    # Сохраняем payload
    out_path = REPORTS_DIR / "demo_telegram_payload.json"
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Выводим в stdout
    print("\n" + "=" * 60)
    print("Telegram dry-run (сообщение НЕ отправляется, только вывод):")
    print("=" * 60)
    print(message_text.replace("\\", ""))  # убираем экранирование для читаемости
    print("=" * 60)
    print(f"\n    Payload сохранён: {out_path}")


# ===========================================================================
# ШАГ 8: Итоговая сводная таблица
# ===========================================================================

def print_summary_table(
    snapshots: list[OddsSnapshot],
    backtest_results: list[BacktestResult],
    metrics: dict,
    signals: list[SignalRecord],
    valid_illness_events: list[IllnessEvent],
) -> None:
    """Печатает итоговую сводку в виде форматированной таблицы."""
    print("\n[8/8] Итоговая сводка пайплайна")
    print("=" * 60)
    print(f"{'Параметр':<35} {'Значение':>20}")
    print("-" * 60)
    rows = [
        ("Снимков коэффициентов",               str(len(snapshots))),
        ("Уникальных матчей",                   str(len({s.event_id for s in snapshots}))),
        ("Целевых букмекеров",                  str(len(TARGET_BOOKMAKERS))),
        ("Событий травм (после фильтрации)",    str(len(valid_illness_events))),
        ("Порог edge",                          f"{EDGE_THRESHOLD*100:.1f}%"),
        ("Ставок в бэктесте",                   str(metrics["n_bets"])),
        ("ROI бэктеста (демо)",                 f"{metrics['roi_pct']:.2f}%"),
        ("Win Rate (демо)",                     f"{metrics['win_rate_pct']:.1f}%"),
        ("PnL суммарный (демо)",                f"{metrics['total_pnl']:+.4f} ед."),
        ("Сигналов для предстоящих матчей",     str(len(signals))),
        ("Отчёт",                               "data/reports/demo_report.md"),
        ("Сигналы JSON",                        "data/reports/demo_signals.json"),
        ("Telegram payload",                    "data/reports/demo_telegram_payload.json"),
    ]
    for label, value in rows:
        print(f"  {label:<33} {value:>20}")
    print("=" * 60)
    print()
    print("⚠️  Напоминание: проект работает ТОЛЬКО в режиме бумажной торговли.")
    print("   Автоматического размещения ставок нет и не предусмотрено.")
    print()


# ===========================================================================
# Точка входа
# ===========================================================================

def main() -> None:
    """Запускает полный демо-пайплайн."""
    print("=" * 60)
    print("  Sports Betting Analytics MVP — Demo Pipeline")
    print("  Данные синтетические. API-ключи не требуются.")
    print("=" * 60)

    # Убеждаемся, что папка для отчётов существует
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Генерация синтетических коэффициентов
    snapshots = generate_synthetic_odds()

    # 2. Генерация событий о травмах с тестом look-ahead bias
    valid_illness_events = generate_synthetic_illness_events(snapshots)

    # 3. Нормализация и девиггирование
    df = normalize_snapshots(snapshots)

    # 4. Бэктест стратегии поиска ценности
    backtest_results, metrics = run_backtest(df)

    # 5. Генерация сигналов для предстоящих матчей
    signals = generate_signals(df)

    # 6. Markdown-отчёт
    build_markdown_report(snapshots, backtest_results, metrics, signals, valid_illness_events)

    # 7. Dry-run Telegram
    dry_run_telegram(signals, metrics)

    # 8. Итоговая таблица
    print_summary_table(snapshots, backtest_results, metrics, signals, valid_illness_events)

    print("✅ Demo pipeline завершён успешно")


if __name__ == "__main__":
    main()
