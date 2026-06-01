"""
Модуль нормализации данных о коэффициентах в единый стандартный формат.

Поддерживает данные из The Odds API и football-data.co.uk.
Включает методы удаления маржи (девиггинга) и усреднения по букмекерам.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, Field, field_validator

from src.normalize.team_names import normalize_team_name

# Настройка логгера модуля
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic-модель нормализованных коэффициентов
# ---------------------------------------------------------------------------


class NormalizedOdds(BaseModel):
    """
    Нормализованная запись коэффициента для одного исхода.

    Все временные метки хранятся в UTC.
    Поле fair_odds_devigged содержит справедливые коэффициенты после удаления маржи.
    """

    normalized_event_id: str = Field(
        ..., description="Уникальный идентификатор события (составной)"
    )
    sport: str = Field(..., description="Вид спорта (например, soccer)")
    league: str = Field(..., description="Код лиги (например, E0, soccer_epl)")
    bookmaker: str = Field(..., description="Идентификатор букмекера")
    market_key: str = Field(..., description="Ключ рынка: h2h, spreads, totals")
    selection: str = Field(..., description="Название исхода (команда или тип)")
    odds_decimal: float = Field(..., description="Коэффициент в десятичном формате", gt=1.0)
    snapshot_ts_utc: datetime = Field(..., description="Время снятия снимка (UTC)")
    event_time_utc: datetime = Field(..., description="Время начала события (UTC)")
    is_live: bool = Field(False, description="Признак live-события")
    raw_source: str = Field(..., description="Источник данных: odds_api | football_data")
    margin_pct: float | None = Field(None, description="Маржа букмекера в процентах (overround)")
    fair_odds_devigged: float | None = Field(
        None, description="Справедливый коэффициент после удаления маржи"
    )

    @field_validator("snapshot_ts_utc", "event_time_utc", mode="before")
    @classmethod
    def ensure_utc(cls, v: Any) -> datetime:
        """Гарантирует, что временная метка содержит UTC-тайм-зону."""
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if isinstance(v, datetime):
            if v.tzinfo is None:
                # Наивные datetime считаем UTC
                return v.replace(tzinfo=timezone.utc)
            return v.astimezone(timezone.utc)
        raise ValueError(f"Неподдерживаемый тип временной метки: {type(v)}")


# ---------------------------------------------------------------------------
# Вспомогательный класс нормализации названий команд
# ---------------------------------------------------------------------------


class TeamNameNormalizer:
    """
    Нормализатор названий команд с использованием словаря соответствий.

    Позволяет сопоставлять различные написания названий команд
    (русские, сокращённые, альтернативные) с каноническим названием.
    """

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        # Используем внешний маппинг из team_names.py или пользовательский
        if mapping is not None:
            self._map = {k.lower(): v for k, v in mapping.items()}
        else:
            # Импортируем маппинг из модуля team_names
            from src.normalize.team_names import TEAM_NAME_MAP

            self._map = {k.lower(): v for k, v in TEAM_NAME_MAP.items()}

    def normalize(self, name: str) -> str:
        """
        Возвращает нормализованное название команды.

        Параметры
        ----------
        name : str
            Исходное название команды.

        Возвращает
        ----------
        str
            Нормализованное название или исходное, если совпадение не найдено.
        """
        return self._map.get(name.strip().lower(), name.strip())


# ---------------------------------------------------------------------------
# Основной класс нормализации
# ---------------------------------------------------------------------------


class OddsNormalizer:
    """
    Нормализатор данных о коэффициентах из различных источников.

    Преобразует сырые данные из The Odds API и football-data.co.uk
    в единый формат NormalizedOdds. Включает методы девиггинга.
    """

    # Маппинг полей букмекеров football-data.co.uk на нормализованные названия
    _FDUK_BOOKMAKER_MAP: dict[str, str] = {
        "b365": "bet365",
        "bf": "betfair",
        "wh": "williamhill",
        "ps": "pinnacle",
        "vc": "vcbet",
        "bw": "bwin",
        "gb": "gamebookers",
        "iw": "interwetten",
        "lb": "ladbrokes",
        "sb": "sportingbet",
    }

    # Маппинг суффиксов исходов football-data.co.uk
    _FDUK_OUTCOME_MAP: dict[str, str] = {
        "h": "home",
        "d": "draw",
        "a": "away",
    }

    def __init__(self) -> None:
        self._team_normalizer = TeamNameNormalizer()
        logger.info("OddsNormalizer инициализирован")

    # ------------------------------------------------------------------
    # Методы девиггинга
    # ------------------------------------------------------------------

    def devig_multiplicative(self, odds: list[float]) -> list[float]:
        """
        Удаляет маржу мультипликативным методом (пропорциональное масштабирование).

        Каждая вероятность делится на общую сумму вероятностей (overround).
        Это наиболее распространённый и простой метод девиггинга.

        Параметры
        ----------
        odds : list[float]
            Список коэффициентов в десятичном формате.

        Возвращает
        ----------
        list[float]
            Список справедливых коэффициентов без маржи.
        """
        if not odds or any(o <= 1.0 for o in odds):
            logger.warning("Некорректные коэффициенты для девиггинга: %s", odds)
            return odds

        # Вычисляем implied probability для каждого исхода
        implied_probs = [1.0 / o for o in odds]
        overround = sum(implied_probs)

        # Нормализуем вероятности (сумма = 1.0)
        fair_probs = [p / overround for p in implied_probs]

        # Конвертируем обратно в коэффициенты
        fair_odds = [1.0 / p for p in fair_probs]
        logger.debug(
            "Мультипликативный девиггинг: overround=%.4f, исходов=%d",
            overround,
            len(odds),
        )
        return fair_odds

    def devig_power(self, odds: list[float]) -> list[float]:
        """
        Удаляет маржу степенным методом (Power/Shin method).

        Находит показатель степени k, при котором сумма скорректированных
        вероятностей равна 1.0. Более точен при неравномерном распределении маржи.

        Параметры
        ----------
        odds : list[float]
            Список коэффициентов в десятичном формате.

        Возвращает
        ----------
        list[float]
            Список справедливых коэффициентов без маржи.
        """
        if not odds or any(o <= 1.0 for o in odds):
            logger.warning("Некорректные коэффициенты для степенного девиггинга: %s", odds)
            return odds

        implied_probs = [1.0 / o for o in odds]

        # Бинарный поиск показателя степени k
        # При k=1: сумма = overround; при k -> inf: сумма -> 1
        lo, hi = 0.5, 5.0
        for _ in range(64):  # 64 итерации — достаточная точность
            mid = (lo + hi) / 2.0
            adj_sum = sum(p**mid for p in implied_probs)
            if adj_sum > 1.0:
                lo = mid
            else:
                hi = mid

        k = (lo + hi) / 2.0
        fair_probs = [p**k for p in implied_probs]

        # Нормализуем для устранения численных погрешностей
        total = sum(fair_probs)
        fair_probs = [p / total for p in fair_probs]

        fair_odds = [1.0 / p for p in fair_probs]
        logger.debug(
            "Степенной девиггинг: k=%.4f, исходов=%d",
            k,
            len(odds),
        )
        return fair_odds

    def market_average_devigged(
        self,
        book_odds: dict[str, list[float]],
    ) -> list[float]:
        """
        Вычисляет средние справедливые вероятности по нескольким букмекерам.

        Для каждого букмекера применяется мультипликативный девиггинг,
        затем вероятности усредняются и конвертируются обратно в коэффициенты.

        Параметры
        ----------
        book_odds : dict[str, list[float]]
            Словарь {букмекер: список коэффициентов по исходам}.

        Возвращает
        ----------
        list[float]
            Список усреднённых справедливых коэффициентов.
        """
        if not book_odds:
            logger.warning("Пустой словарь книг для усреднения")
            return []

        n_outcomes = len(next(iter(book_odds.values())))
        all_fair_probs: list[list[float]] = []

        for bookmaker, odds in book_odds.items():
            if len(odds) != n_outcomes:
                logger.warning(
                    "Букмекер %s: ожидалось %d исходов, получено %d — пропускаем",
                    bookmaker,
                    n_outcomes,
                    len(odds),
                )
                continue
            fair = self.devig_multiplicative(odds)
            # Конвертируем обратно в вероятности
            all_fair_probs.append([1.0 / o for o in fair])

        if not all_fair_probs:
            logger.warning("Нет валидных данных для усреднения")
            return []

        # Усредняем вероятности по букмекерам
        avg_probs = [
            sum(row[i] for row in all_fair_probs) / len(all_fair_probs) for i in range(n_outcomes)
        ]

        # Нормализуем и конвертируем в коэффициенты
        total = sum(avg_probs)
        avg_odds = [1.0 / (p / total) for p in avg_probs]

        logger.debug(
            "Усреднение по %d букмекерам, %d исходам",
            len(all_fair_probs),
            n_outcomes,
        )
        return avg_odds

    # ------------------------------------------------------------------
    # Нормализация данных The Odds API
    # ------------------------------------------------------------------

    def normalize_odds_api_response(
        self,
        raw: list[dict],
        snapshot_ts_utc: datetime,
    ) -> list[NormalizedOdds]:
        """
        Преобразует сырой ответ The Odds API в список NormalizedOdds.

        Параметры
        ----------
        raw : list[dict]
            Список событий из ответа The Odds API.
        snapshot_ts_utc : datetime
            Время снятия снимка (UTC).

        Возвращает
        ----------
        list[NormalizedOdds]
            Список нормализованных записей коэффициентов.
        """
        results: list[NormalizedOdds] = []

        for event in raw:
            event_id: str = event.get("id", "")
            sport: str = event.get("sport_key", "")
            # Используем sport_key как league (уточняется на этапе нормализации лиги)
            league: str = event.get("sport_key", "")
            home_team: str = self._team_normalizer.normalize(event.get("home_team", ""))
            away_team: str = self._team_normalizer.normalize(event.get("away_team", ""))

            # Парсим время начала события
            try:
                event_time_raw = event.get("commence_time", "")
                if isinstance(event_time_raw, str):
                    event_time_utc = datetime.fromisoformat(event_time_raw.replace("Z", "+00:00"))
                else:
                    event_time_utc = event_time_raw
                if event_time_utc.tzinfo is None:
                    event_time_utc = event_time_utc.replace(tzinfo=timezone.utc)
                event_time_utc = event_time_utc.astimezone(timezone.utc)
            except (ValueError, AttributeError) as exc:
                logger.warning("Ошибка парсинга времени события %s: %s", event_id, exc)
                continue

            bookmakers: list[dict] = event.get("bookmakers", [])

            for bm in bookmakers:
                bookmaker_key: str = bm.get("key", "")
                markets: list[dict] = bm.get("markets", [])

                for market in markets:
                    market_key: str = market.get("key", "")
                    outcomes: list[dict] = market.get("outcomes", [])

                    # Собираем все коэффициенты рынка для расчёта маржи
                    all_odds = [float(o["price"]) for o in outcomes if "price" in o]
                    margin_pct: float | None = None
                    fair_odds_map: dict[str, float] = {}

                    if len(all_odds) >= 2:
                        overround = sum(1.0 / o for o in all_odds)
                        margin_pct = round((overround - 1.0) * 100, 4)
                        devigged = self.devig_multiplicative(all_odds)
                        fair_odds_map = {
                            o["name"]: dv for o, dv in zip(outcomes, devigged) if "name" in o
                        }

                    for outcome in outcomes:
                        selection: str = self._team_normalizer.normalize(outcome.get("name", ""))
                        try:
                            odds_decimal = float(outcome["price"])
                        except (KeyError, ValueError, TypeError):
                            logger.warning(
                                "Пропуск исхода без корректного коэффициента: %s",
                                outcome,
                            )
                            continue

                        # Формируем составной идентификатор события
                        normalized_event_id = f"{sport}__{home_team}__{away_team}__{event_id}"

                        record = NormalizedOdds(
                            normalized_event_id=normalized_event_id,
                            sport=sport,
                            league=league,
                            bookmaker=bookmaker_key,
                            market_key=market_key,
                            selection=selection,
                            odds_decimal=odds_decimal,
                            snapshot_ts_utc=snapshot_ts_utc,
                            event_time_utc=event_time_utc,
                            is_live=False,  # The Odds API v4 pre-match эндпоинт
                            raw_source="odds_api",
                            margin_pct=margin_pct,
                            fair_odds_devigged=fair_odds_map.get(outcome.get("name", "")),
                        )
                        results.append(record)

        logger.info(
            "Нормализация Odds API: входящих событий=%d, исходящих записей=%d",
            len(raw),
            len(results),
        )
        return results

    # ------------------------------------------------------------------
    # Нормализация строки football-data.co.uk
    # ------------------------------------------------------------------

    def normalize_football_data_row(self, row: pd.Series) -> list[NormalizedOdds]:
        """
        Преобразует строку датафрейма football-data.co.uk в список NormalizedOdds.

        Обрабатывает коэффициенты для всех доступных букмекеров в строке.

        Параметры
        ----------
        row : pd.Series
            Строка датафрейма (после применения COLUMN_MAPPING).

        Возвращает
        ----------
        list[NormalizedOdds]
            Список нормализованных записей (по одной на каждый исход каждого букмекера).
        """
        results: list[NormalizedOdds] = []

        # Базовые поля строки
        home_team: str = self._team_normalizer.normalize(str(row.get("home_team", "")))
        away_team: str = self._team_normalizer.normalize(str(row.get("away_team", "")))
        league: str = str(row.get("league_code", "unknown"))

        # Парсим дату матча и конвертируем в UTC datetime
        match_date = row.get("match_date")
        if pd.isna(match_date) or match_date is None:
            logger.warning("Строка без даты матча: %s vs %s — пропускаем", home_team, away_team)
            return []

        if isinstance(match_date, pd.Timestamp):
            event_time_utc = match_date.to_pydatetime().replace(tzinfo=timezone.utc)
        elif isinstance(match_date, datetime):
            event_time_utc = (
                match_date.replace(tzinfo=timezone.utc)
                if match_date.tzinfo is None
                else match_date.astimezone(timezone.utc)
            )
        else:
            try:
                event_time_utc = datetime.fromisoformat(str(match_date)).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                logger.warning("Не удалось распарсить дату: %s", match_date)
                return []

        # Snapshot timestamp = время события (исторические данные)
        snapshot_ts_utc = event_time_utc

        # Формируем составной идентификатор
        date_str = event_time_utc.strftime("%Y%m%d")
        normalized_event_id = f"soccer__{league}__{home_team}__{away_team}__{date_str}"

        # Перебираем все известные букмекеры (проверяем наличие в строке)
        for bm_prefix, bm_name in self._FDUK_BOOKMAKER_MAP.items():
            # Ожидаемые столбцы: {bm_prefix}_odds_home, _draw, _away
            odds_cols = {
                suffix: f"{bm_prefix}_odds_{self._FDUK_OUTCOME_MAP[suffix]}"
                for suffix in ["h", "d", "a"]
            }

            # Проверяем наличие хотя бы одного столбца
            available = {
                sfx: col
                for sfx, col in odds_cols.items()
                if col in row.index and pd.notna(row.get(col))
            }
            if len(available) < 2:
                # Недостаточно данных для расчёта маржи
                continue

            # Собираем коэффициенты
            selection_map = {
                "home": home_team,
                "draw": "Draw",
                "away": away_team,
            }
            sfx_to_label = {"h": "home", "d": "draw", "a": "away"}

            valid_odds: list[tuple[str, float]] = []
            for sfx, col in available.items():
                try:
                    val = float(row[col])
                    if val > 1.0:
                        valid_odds.append((sfx_to_label[sfx], val))
                except (ValueError, TypeError):
                    continue

            if len(valid_odds) < 2:
                continue

            all_decimal = [v for _, v in valid_odds]
            overround = sum(1.0 / o for o in all_decimal)
            margin_pct = round((overround - 1.0) * 100, 4)
            devigged = self.devig_multiplicative(all_decimal)
            fair_map = {lbl: dv for (lbl, _), dv in zip(valid_odds, devigged)}

            for label, odds_val in valid_odds:
                record = NormalizedOdds(
                    normalized_event_id=normalized_event_id,
                    sport="soccer",
                    league=league,
                    bookmaker=bm_name,
                    market_key="h2h",
                    selection=selection_map.get(label, label),
                    odds_decimal=odds_val,
                    snapshot_ts_utc=snapshot_ts_utc,
                    event_time_utc=event_time_utc,
                    is_live=False,
                    raw_source="football_data",
                    margin_pct=margin_pct,
                    fair_odds_devigged=fair_map.get(label),
                )
                results.append(record)

        return results


# ---------------------------------------------------------------------------
# Utility function for devigging pairs of odds
# ---------------------------------------------------------------------------


def devig_pair(odds1: float, odds2: float) -> tuple[float, float]:
    """
    Devig a pair of odds and return fair probabilities.

    Applies multiplicative devigging to remove bookmaker margin.

    Parameters
    ----------
    odds1 : float
        Decimal odds for outcome 1.
    odds2 : float
        Decimal odds for outcome 2.

    Returns
    -------
    tuple[float, float]
        Fair probabilities for outcomes 1 and 2 (sum = 1.0).
    """
    if odds1 <= 1.0 or odds2 <= 1.0:
        raise ValueError(f"Invalid odds: {odds1}, {odds2}")

    # Compute implied probabilities
    p1 = 1.0 / odds1
    p2 = 1.0 / odds2
    overround = p1 + p2

    # Normalize
    fair_p1 = p1 / overround
    fair_p2 = p2 / overround

    return fair_p1, fair_p2
