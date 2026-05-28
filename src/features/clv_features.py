"""
Модуль вычисления признаков CLV (Closing Line Value — ценность относительно линии закрытия).

CLV является первичной метрикой качества беттинговых решений.
Если стратегия систематически обыгрывает линию закрытия, это означает наличие
реального преимущества (edge), а не удачи. Линия закрытия — консенсусная оценка
вероятности события, сформированная рынком с учётом максимального объёма информации.

Ссылки на концепцию:
    - Линия закрытия отражает «мудрость толпы» профессиональных беттеров.
    - Положительный CLV (entry_odds > closing_odds) указывает на то, что ставка
      была сделана по более выгодному курсу, чем итоговая рыночная оценка.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Pydantic-модель записи CLV
# ---------------------------------------------------------------------------


class CLVRecord(BaseModel):
    """Запись CLV для одной ставки/сигнала."""

    event_id: str = Field(..., description="Идентификатор спортивного события")
    selection: str = Field(..., description="Название исхода (например, 'home_win', 'over_2.5')")
    entry_odds: float = Field(..., gt=1.0, description="Курс в момент размещения ставки")
    entry_ts: datetime = Field(..., description="Время размещения ставки (UTC)")
    closing_odds: float = Field(..., gt=1.0, description="Курс закрытия (перед началом матча, UTC)")
    closing_ts: datetime = Field(..., description="Время фиксации линии закрытия (UTC)")
    clv_pct: float = Field(
        ...,
        description="CLV в процентах: ((entry_odds / closing_odds) - 1) * 100",
    )
    is_positive_clv: bool = Field(
        ...,
        description="True, если clv_pct > порогового значения (по умолчанию 0.0%)",
    )

    @field_validator("entry_ts", "closing_ts", mode="before")
    @classmethod
    def ensure_utc(cls, v: datetime | str) -> datetime:
        """Нормализует временную метку в UTC."""
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Анализатор CLV
# ---------------------------------------------------------------------------


class CLVAnalyzer:
    """
    Инструмент для вычисления и анализа Closing Line Value (CLV).

    CLV является первичной метрикой качества ставок:
        - Положительный CLV означает, что ставка сделана по курсу выше,
          чем итоговая рыночная оценка — признак реального преимущества.
        - Отрицательный CLV указывает на ставку по курсу хуже рынка.
        - Систематически положительный CLV подтверждает наличие edge,
          независимо от краткосрочных результатов.

    Формула: CLV% = ((entry_odds / closing_odds) - 1) * 100
    """

    def compute_clv(self, entry_odds: float, closing_odds: float) -> float:
        """
        Вычисляет CLV в процентах для одной ставки.

        Формула: ((entry_odds / closing_odds) - 1) * 100

        Параметры
        ----------
        entry_odds : float
            Курс в момент размещения ставки (десятичный формат, > 1.0).
        closing_odds : float
            Курс закрытия непосредственно перед началом матча (> 1.0).

        Возвращает
        ----------
        float
            CLV в процентах. Положительное значение — выгодная ставка.

        Примеры
        --------
        >>> analyzer = CLVAnalyzer()
        >>> analyzer.compute_clv(2.10, 1.95)
        7.69  # ставка по курсу выше линии закрытия — положительный CLV
        """
        if closing_odds <= 0:
            raise ValueError(f"closing_odds должен быть > 0, получено: {closing_odds}")
        return ((entry_odds / closing_odds) - 1) * 100

    def is_positive(self, clv_pct: float, threshold: float = 0.0) -> bool:
        """
        Проверяет, превышает ли CLV заданный порог.

        Параметры
        ----------
        clv_pct : float
            Значение CLV в процентах.
        threshold : float, optional
            Пороговое значение (по умолчанию 0.0 — любой положительный CLV).

        Возвращает
        ----------
        bool
            True, если clv_pct > threshold.
        """
        return clv_pct > threshold

    def batch_compute(self, trades_df: pd.DataFrame) -> pd.DataFrame:
        """
        Вычисляет CLV для батча ставок из DataFrame.

        Ожидаемые колонки входного DataFrame:
            - entry_odds (float): курс входа
            - closing_odds (float): курс закрытия

        Добавляемые колонки:
            - clv_pct (float): CLV в процентах
            - is_positive_clv (bool): True, если clv_pct > 0

        Параметры
        ----------
        trades_df : pd.DataFrame
            DataFrame со ставками. Должен содержать колонки entry_odds и closing_odds.

        Возвращает
        ----------
        pd.DataFrame
            Копия исходного DataFrame с добавленными колонками clv_pct и is_positive_clv.

        Исключения
        ----------
        KeyError
            Если отсутствуют обязательные колонки entry_odds или closing_odds.
        """
        required_cols = {"entry_odds", "closing_odds"}
        missing = required_cols - set(trades_df.columns)
        if missing:
            raise KeyError(f"Отсутствуют обязательные колонки: {missing}")

        result = trades_df.copy()

        # Вычисляем CLV поэлементно
        result["clv_pct"] = (result["entry_odds"] / result["closing_odds"] - 1) * 100

        # Флаг положительного CLV
        result["is_positive_clv"] = result["clv_pct"] > 0.0

        return result

    def summarize(self, clv_records: list[CLVRecord]) -> dict[str, Any]:
        """
        Строит сводную статистику по списку CLV-записей.

        Основные метрики:
            - mean_clv_pct: среднее значение CLV (ключевой KPI качества стратегии)
            - positive_clv_rate: доля ставок с положительным CLV (в %)
            - std_clv_pct: стандартное отклонение CLV
            - n_records: количество записей
            - min_clv_pct / max_clv_pct: диапазон значений

        Параметры
        ----------
        clv_records : list[CLVRecord]
            Список записей CLV для агрегации.

        Возвращает
        ----------
        dict
            Словарь со сводными статистиками CLV.
        """
        if not clv_records:
            return {
                "mean_clv_pct": 0.0,
                "positive_clv_rate": 0.0,
                "std_clv_pct": 0.0,
                "n_records": 0,
                "min_clv_pct": None,
                "max_clv_pct": None,
            }

        clv_values = [r.clv_pct for r in clv_records]
        n = len(clv_values)
        mean_clv = sum(clv_values) / n
        positive_count = sum(1 for v in clv_values if v > 0)

        # Стандартное отклонение
        if n > 1:
            variance = sum((v - mean_clv) ** 2 for v in clv_values) / (n - 1)
            std_clv = variance**0.5
        else:
            std_clv = 0.0

        return {
            "mean_clv_pct": round(mean_clv, 4),
            "positive_clv_rate": round(positive_count / n * 100, 2),
            "std_clv_pct": round(std_clv, 4),
            "n_records": n,
            "min_clv_pct": round(min(clv_values), 4),
            "max_clv_pct": round(max(clv_values), 4),
        }
