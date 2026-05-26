"""
Модуль контроля качества данных о коэффициентах.

Предоставляет инструменты для проверки актуальности, дедупликации,
валидации значений и формирования сводного отчёта о качестве данных.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd

# Настройка логгера модуля
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Датакласс отчёта о качестве данных
# ---------------------------------------------------------------------------


@dataclass
class DataQualityReport:
    """
    Сводный отчёт о качестве набора данных о коэффициентах.

    Атрибуты
    ----------
    total_records : int
        Общее количество записей в наборе данных.
    valid_records : int
        Количество записей, прошедших все проверки.
    stale_odds_count : int
        Количество записей с устаревшими коэффициентами (превышен порог возраста).
    missing_results : int
        Количество записей с отсутствующими результатами матчей.
    broken_joins : int
        Количество записей с нарушенными связями между таблицами.
    duplicate_snapshots : int
        Количество дублирующихся снимков коэффициентов.
    quality_score : float
        Итоговая оценка качества данных от 0.0 до 1.0.
    details : dict
        Дополнительные сведения и метрики по каждой проверке.
    generated_at_utc : datetime
        Время формирования отчёта (UTC).
    """

    total_records: int = 0
    valid_records: int = 0
    stale_odds_count: int = 0
    missing_results: int = 0
    broken_joins: int = 0
    duplicate_snapshots: int = 0
    quality_score: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    generated_at_utc: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        """Проверяет корректность оценки качества после инициализации."""
        if not (0.0 <= self.quality_score <= 1.0):
            raise ValueError(
                f"quality_score должен быть в диапазоне [0.0, 1.0], "
                f"получено: {self.quality_score}"
            )

    def summary(self) -> str:
        """
        Возвращает краткое текстовое описание отчёта.

        Возвращает
        ----------
        str
            Многострочная строка с основными показателями качества.
        """
        return (
            f"=== Отчёт о качестве данных ===\n"
            f"Всего записей:            {self.total_records}\n"
            f"Валидных записей:         {self.valid_records}\n"
            f"Устаревших снимков:       {self.stale_odds_count}\n"
            f"Отсутствуют результаты:   {self.missing_results}\n"
            f"Нарушенных связей:        {self.broken_joins}\n"
            f"Дублирующихся снимков:    {self.duplicate_snapshots}\n"
            f"Оценка качества:          {self.quality_score:.2%}\n"
            f"Сформирован (UTC):        {self.generated_at_utc.isoformat()}"
        )


# ---------------------------------------------------------------------------
# Основной класс контроля качества
# ---------------------------------------------------------------------------


class QCNormalizer:
    """
    Инспектор качества данных о коэффициентах.

    Выполняет набор проверок над датафреймами нормализованных данных
    и формирует сводный отчёт DataQualityReport.

    Ожидаемые столбцы датафрейма (соответствуют NormalizedOdds):
        - snapshot_ts_utc : datetime (UTC)
        - event_time_utc  : datetime (UTC)
        - odds_decimal    : float
        - normalized_event_id : str
        - bookmaker       : str
        - market_key      : str
        - selection       : str
    """

    # Разумные пределы для десятичных коэффициентов
    _MIN_ODDS: float = 1.01
    _MAX_ODDS: float = 1000.0

    def check_stale_odds(
        self,
        df: pd.DataFrame,
        max_age_hours: float = 2.0,
    ) -> pd.Series:
        """
        Выявляет снимки коэффициентов, устаревших свыше заданного порога.

        Снимок считается устаревшим, если разница между временем события
        и временем снимка превышает max_age_hours часов (или если снимок
        был сделан намного позже начала события).

        Параметры
        ----------
        df : pd.DataFrame
            Датафрейм с колонками snapshot_ts_utc и event_time_utc.
        max_age_hours : float
            Максимально допустимый возраст снимка в часах.

        Возвращает
        ----------
        pd.Series
            Булева серия: True — запись устаревшая, False — актуальная.
        """
        if "snapshot_ts_utc" not in df.columns or "event_time_utc" not in df.columns:
            logger.warning(
                "Отсутствуют колонки snapshot_ts_utc или event_time_utc — "
                "проверка устаревания невозможна"
            )
            return pd.Series(False, index=df.index)

        # Вычисляем разницу между временем снимка и временем события (в часах)
        snap_ts = pd.to_datetime(df["snapshot_ts_utc"], utc=True, errors="coerce")
        event_ts = pd.to_datetime(df["event_time_utc"], utc=True, errors="coerce")

        # Снимок "устарел", если он был сделан после начала события более чем
        # на max_age_hours или если предматчевый снимок слишком старый
        age_hours = (snap_ts - event_ts).dt.total_seconds() / 3600.0

        # Для предматчевых данных: устаревший = снят раньше события на > max_age_hours
        # Для постматчевых: любой снимок после старта считается устаревшим
        stale_mask = age_hours.abs() > max_age_hours

        stale_count = stale_mask.sum()
        logger.info(
            "Проверка актуальности (порог=%.1f ч): устаревших=%d из %d",
            max_age_hours, stale_count, len(df),
        )
        return stale_mask

    def check_duplicates(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Обнаруживает дублирующиеся снимки одного рынка.

        Дубликатом считается запись с одинаковым сочетанием:
        (normalized_event_id, bookmaker, market_key, selection, snapshot_ts_utc).

        Параметры
        ----------
        df : pd.DataFrame
            Датафрейм нормализованных коэффициентов.

        Возвращает
        ----------
        pd.DataFrame
            Подмножество датафрейма, содержащее только дублирующиеся строки
            (все копии, кроме первой).
        """
        key_cols = [
            "normalized_event_id",
            "bookmaker",
            "market_key",
            "selection",
            "snapshot_ts_utc",
        ]
        # Используем только те столбцы, которые присутствуют в датафрейме
        available_keys = [c for c in key_cols if c in df.columns]

        if not available_keys:
            logger.warning(
                "Ни один из ключевых столбцов для дедупликации не найден в датафрейме"
            )
            return df.iloc[0:0]  # Пустой датафрейм с той же структурой

        duplicates = df[df.duplicated(subset=available_keys, keep="first")]
        logger.info(
            "Проверка дубликатов: найдено %d дублирующихся записей из %d",
            len(duplicates), len(df),
        )
        return duplicates

    def validate_decimal_odds(self, odds: float) -> bool:
        """
        Проверяет, что значение коэффициента находится в допустимом диапазоне.

        Допустимый диапазон: [1.01, 1000.0].
        Коэффициент 1.0 и ниже физически невозможен.
        Коэффициент выше 1000 — скорее всего ошибка данных.

        Параметры
        ----------
        odds : float
            Коэффициент в десятичном формате.

        Возвращает
        ----------
        bool
            True — коэффициент корректен, False — выходит за допустимые пределы.
        """
        try:
            val = float(odds)
        except (ValueError, TypeError):
            return False
        return self._MIN_ODDS <= val <= self._MAX_ODDS

    def validate_timezone(self, ts: str) -> bool:
        """
        Проверяет, что временная метка содержит информацию о часовом поясе UTC.

        Допустимые форматы: ISO 8601 с суффиксом 'Z' или '+00:00'.

        Параметры
        ----------
        ts : str
            Временная метка в виде строки.

        Возвращает
        ----------
        bool
            True — временная метка корректна и содержит UTC, False — иначе.
        """
        if not isinstance(ts, str):
            return False
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return False
            # Проверяем, что смещение равно 0 (UTC)
            offset = dt.utcoffset()
            return offset is not None and offset.total_seconds() == 0
        except (ValueError, AttributeError):
            return False

    def compute_quality_report(self, df: pd.DataFrame) -> DataQualityReport:
        """
        Формирует полный отчёт о качестве датафрейма коэффициентов.

        Выполняет все доступные проверки и вычисляет итоговую оценку качества
        как взвешенное среднее результатов отдельных проверок.

        Параметры
        ----------
        df : pd.DataFrame
            Датафрейм нормализованных коэффициентов.

        Возвращает
        ----------
        DataQualityReport
            Заполненный объект отчёта со всеми метриками.
        """
        total = len(df)
        logger.info("Формирование отчёта о качестве данных: всего записей=%d", total)

        if total == 0:
            logger.warning("Датафрейм пуст — возвращается нулевой отчёт")
            return DataQualityReport(
                total_records=0,
                valid_records=0,
                quality_score=0.0,
                details={"предупреждение": "Датафрейм не содержит записей"},
            )

        details: dict[str, Any] = {}

        # ------------------------------------------------------------------
        # 1. Проверка коэффициентов на корректность значений
        # ------------------------------------------------------------------
        invalid_odds_count = 0
        if "odds_decimal" in df.columns:
            invalid_mask = ~df["odds_decimal"].apply(self.validate_decimal_odds)
            invalid_odds_count = int(invalid_mask.sum())
            details["некорректных_коэффициентов"] = invalid_odds_count
            details["доля_корректных_коэффициентов"] = round(
                1.0 - invalid_odds_count / total, 4
            )
        else:
            details["некорректных_коэффициентов"] = None
            logger.warning("Столбец odds_decimal отсутствует в датафрейме")

        # ------------------------------------------------------------------
        # 2. Проверка временных зон
        # ------------------------------------------------------------------
        invalid_tz_count = 0
        if "snapshot_ts_utc" in df.columns:
            tz_mask = df["snapshot_ts_utc"].astype(str).apply(
                lambda x: not self.validate_timezone(x)
            )
            invalid_tz_count = int(tz_mask.sum())
            details["некорректных_временных_зон"] = invalid_tz_count
        else:
            details["некорректных_временных_зон"] = None

        # ------------------------------------------------------------------
        # 3. Проверка устаревших снимков (порог 2 часа)
        # ------------------------------------------------------------------
        stale_mask = self.check_stale_odds(df, max_age_hours=2.0)
        stale_count = int(stale_mask.sum())
        details["устаревших_снимков_2ч"] = stale_count

        # ------------------------------------------------------------------
        # 4. Дедупликация
        # ------------------------------------------------------------------
        duplicates_df = self.check_duplicates(df)
        dup_count = len(duplicates_df)
        details["дублирующихся_записей"] = dup_count

        # ------------------------------------------------------------------
        # 5. Отсутствующие результаты (если есть колонка result_ft)
        # ------------------------------------------------------------------
        missing_results = 0
        if "result_ft" in df.columns:
            missing_results = int(df["result_ft"].isna().sum())
            details["отсутствующих_результатов"] = missing_results
        else:
            details["отсутствующих_результатов"] = "н/д (столбец не найден)"

        # ------------------------------------------------------------------
        # 6. Нарушенные связи (normalized_event_id без пары home/away)
        # ------------------------------------------------------------------
        broken_joins = 0
        if "normalized_event_id" in df.columns:
            # Проверяем наличие двойного подчёркивания в ID (формат: sport__home__away__)
            broken_mask = ~df["normalized_event_id"].str.contains("__", na=False)
            broken_joins = int(broken_mask.sum())
            details["нарушенных_event_id"] = broken_joins

        # ------------------------------------------------------------------
        # 7. Итоговое количество валидных записей
        # ------------------------------------------------------------------
        # Запись считается невалидной, если она дублирующаяся, устаревшая
        # или содержит некорректные коэффициенты
        invalid_total = max(invalid_odds_count, 0) + dup_count
        valid_records = max(0, total - invalid_total)

        # ------------------------------------------------------------------
        # 8. Расчёт итоговой оценки качества
        # ------------------------------------------------------------------
        # Взвешенная оценка по четырём компонентам:
        #   - доля корректных коэффициентов  (вес 40%)
        #   - доля недублирующихся записей   (вес 25%)
        #   - доля актуальных снимков        (вес 25%)
        #   - доля корректных временных зон  (вес 10%)
        score_odds = 1.0 - (invalid_odds_count / total) if total > 0 else 0.0
        score_dedup = 1.0 - (dup_count / total) if total > 0 else 0.0
        score_stale = 1.0 - (stale_count / total) if total > 0 else 0.0
        score_tz = 1.0 - (invalid_tz_count / total) if total > 0 else 0.0

        quality_score = (
            0.40 * score_odds
            + 0.25 * score_dedup
            + 0.25 * score_stale
            + 0.10 * score_tz
        )
        quality_score = round(max(0.0, min(1.0, quality_score)), 4)

        details["компоненты_оценки"] = {
            "корректность_коэффициентов": round(score_odds, 4),
            "уникальность":               round(score_dedup, 4),
            "актуальность":               round(score_stale, 4),
            "временные_зоны":             round(score_tz, 4),
        }

        report = DataQualityReport(
            total_records=total,
            valid_records=valid_records,
            stale_odds_count=stale_count,
            missing_results=missing_results,
            broken_joins=broken_joins,
            duplicate_snapshots=dup_count,
            quality_score=quality_score,
            details=details,
        )

        logger.info(
            "Отчёт сформирован: оценка качества=%.2f%%, валидных=%d/%d",
            quality_score * 100, valid_records, total,
        )
        return report
