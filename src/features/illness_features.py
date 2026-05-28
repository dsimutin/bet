"""
Модуль инженерии признаков на основе данных о травмах и дисквалификациях игроков.

КРИТИЧЕСКИ ВАЖНО (защита от заглядывания в будущее):
    Все события должны фильтроваться по параметру cutoff_ts.
    В модель передаются ТОЛЬКО события, опубликованные ДО cutoff_ts.
    cutoff_ts = время начала матча (или время генерации сигнала).
    Использование любых данных с report_ts >= cutoff_ts является утечкой данных (look-ahead bias)
    и приведёт к завышенным результатам бэктеста, нерабочим в проде.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Константы качества источников
# ---------------------------------------------------------------------------

# Веса качества источника: чем официальнее источник, тем выше доверие
SOURCE_QUALITY_SCORES: dict[str, float] = {
    "official_report": 1.0,  # Официальный пресс-релиз клуба/федерации
    "official_vendor": 0.8,  # Верифицированный официальный поставщик данных
    "paid_vendor": 0.6,  # Платный агрегатор (надёжный, но не официальный)
    "news_scrape": 0.4,  # Парсинг новостных источников
    "social_signal": 0.2,  # Социальные сети (низкое доверие, высокий шум)
}


# ---------------------------------------------------------------------------
# Pydantic-модель события о травме/дисквалификации
# ---------------------------------------------------------------------------


class IllnessEvent(BaseModel):
    """Событие о статусе готовности игрока (травма, болезнь, дисквалификация)."""

    player_id: str = Field(..., description="Уникальный идентификатор игрока")
    team_id: str = Field(..., description="Уникальный идентификатор команды")
    status: Literal["available", "doubtful", "out", "suspended"] = Field(
        ...,
        description=(
            "Статус готовности: available=доступен, doubtful=под вопросом, "
            "out=не сыграет, suspended=дисквалифицирован"
        ),
    )
    tag: str = Field(
        ...,
        description="Тег/категория причины (например, 'hamstring', 'illness', 'red_card')",
    )
    report_ts: datetime = Field(
        ...,
        description="Временная метка публикации информации (UTC). Используется для фильтрации по cutoff_ts.",
    )
    source_quality: Literal[
        "official_report", "official_vendor", "paid_vendor", "news_scrape", "social_signal"
    ] = Field(..., description="Категория качества источника информации")
    expected_minutes_proxy: float = Field(
        ...,
        ge=0.0,
        le=90.0,
        description="Ожидаемое количество минут, которые игрок провёл бы на поле (0–90)",
    )

    @field_validator("report_ts", mode="before")
    @classmethod
    def ensure_utc(cls, v: datetime | str) -> datetime:
        """Гарантирует, что временная метка хранится в UTC."""
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        # Если временная зона не указана — считаем UTC
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Построитель признаков травм
# ---------------------------------------------------------------------------


class IllnessFeatureBuilder:
    """
    Построитель признаков на основе данных о травмах и дисквалификациях.

    ВАЖНО О ЗАЩИТЕ ОТ УТЕЧКИ ДАННЫХ:
        Метод filter_by_cutoff является ключевым защитным барьером.
        Никогда не передавайте события без предварительной фильтрации.
        cutoff_ts должен быть установлен в момент начала матча или
        в момент генерации торгового сигнала — в зависимости от контекста.
    """

    # Статусы, при которых игрок считается отсутствующим
    ABSENT_STATUSES: frozenset[str] = frozenset({"doubtful", "out", "suspended"})

    # Порог для флага позднего изменения статуса (в секундах = 4 часа)
    LATE_CHANGE_THRESHOLD_SECONDS: int = 4 * 3600

    def load_events(self, path: Path) -> list[IllnessEvent]:
        """
        Загружает события о травмах из JSON-файла.

        Параметры
        ----------
        path : Path
            Путь к JSON-файлу. Ожидается список объектов, совместимых с IllnessEvent.

        Возвращает
        ----------
        list[IllnessEvent]
            Список десериализованных событий.
        """
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [IllnessEvent.model_validate(item) for item in raw]

    def filter_by_cutoff(
        self,
        events: list[IllnessEvent],
        cutoff_ts: datetime,
    ) -> list[IllnessEvent]:
        """
        Фильтрует события: оставляет только те, что опубликованы СТРОГО ДО cutoff_ts.

        КРИТИЧЕСКИ ВАЖНО — ЗАЩИТА ОТ LOOK-AHEAD BIAS:
            Этот метод является единственным барьером между «будущими» данными
            и моделью. Любое событие с report_ts >= cutoff_ts недопустимо,
            так как в реальном времени эти данные ещё не были бы известны.
            Пропуск этого шага делает бэктест недействительным.

        Параметры
        ----------
        events : list[IllnessEvent]
            Полный список событий (из хранилища данных).
        cutoff_ts : datetime
            Временная граница (время начала матча или генерации сигнала, UTC).
            Включаются только события с report_ts < cutoff_ts.

        Возвращает
        ----------
        list[IllnessEvent]
            Подмножество событий, опубликованных до cutoff_ts.
        """
        # Нормализуем cutoff_ts в UTC для корректного сравнения
        if cutoff_ts.tzinfo is None:
            cutoff_ts = cutoff_ts.replace(tzinfo=timezone.utc)
        else:
            cutoff_ts = cutoff_ts.astimezone(timezone.utc)

        # Включаем только события, известные ДО cutoff_ts
        return [e for e in events if e.report_ts < cutoff_ts]

    def compute_team_absence_score(
        self,
        events: list[IllnessEvent],
        team_id: str,
    ) -> dict:
        """
        Вычисляет агрегированные признаки отсутствия игроков для указанной команды.

        Формула взвешенного балла отсутствия:
            weighted_absence_score = sum(
                expected_minutes_proxy * SOURCE_QUALITY_SCORES[source_quality]
                для каждого отсутствующего игрока
            )

        Флаг позднего изменения статуса (late_status_change_flag):
            True, если хотя бы одно событие опубликовано менее чем за 4 часа до cutoff_ts.
            ВНИМАНИЕ: этот флаг можно рассчитать только при наличии cutoff_ts.
            Здесь используется максимальный report_ts как приближение.

        Параметры
        ----------
        events : list[IllnessEvent]
            Уже отфильтрованные по cutoff_ts события (только «прошлые» данные).
        team_id : str
            Идентификатор команды, для которой строятся признаки.

        Возвращает
        ----------
        dict
            {
                "number_of_absent_starters": int,   # кол-во отсутствующих игроков
                "weighted_absence_score": float,    # взвешенный балл отсутствия
                "late_status_change_flag": bool,    # было ли позднее изменение статуса
                "min_source_quality": float,        # минимальный балл качества источника
            }
        """
        # Фильтруем по команде и статусам отсутствия
        team_absent = [
            e for e in events if e.team_id == team_id and e.status in self.ABSENT_STATUSES
        ]

        if not team_absent:
            return {
                "number_of_absent_starters": 0,
                "weighted_absence_score": 0.0,
                "late_status_change_flag": False,
                "min_source_quality": 1.0,  # нет событий — максимальное доверие по умолчанию
            }

        # Взвешенный балл отсутствия: минуты * качество источника
        weighted_score = sum(
            e.expected_minutes_proxy * SOURCE_QUALITY_SCORES[e.source_quality] for e in team_absent
        )

        # Минимальное качество источника среди всех отсутствующих
        min_source_quality = min(SOURCE_QUALITY_SCORES[e.source_quality] for e in team_absent)

        # Флаг позднего изменения статуса: определяем по ближайшему к cutoff_ts событию.
        # Используем разницу между максимальным и минимальным report_ts как приближение.
        # Точный расчёт требует передачи cutoff_ts — см. compute_team_absence_score_with_cutoff.
        sorted_ts = sorted(e.report_ts for e in team_absent)
        latest_ts = sorted_ts[-1]
        earliest_ts = sorted_ts[0]
        time_window_seconds = (latest_ts - earliest_ts).total_seconds()
        late_status_change_flag = (
            time_window_seconds <= self.LATE_CHANGE_THRESHOLD_SECONDS and len(team_absent) > 1
        )

        # Если только одно событие — нет возможности определить «позднее изменение»
        # без cutoff_ts, устанавливаем False
        if len(team_absent) == 1:
            late_status_change_flag = False

        return {
            "number_of_absent_starters": len(team_absent),
            "weighted_absence_score": round(weighted_score, 4),
            "late_status_change_flag": late_status_change_flag,
            "min_source_quality": min_source_quality,
        }

    def compute_team_absence_score_with_cutoff(
        self,
        events: list[IllnessEvent],
        team_id: str,
        cutoff_ts: datetime,
    ) -> dict:
        """
        Расширенная версия compute_team_absence_score с точным расчётом флага позднего изменения.

        Флаг late_status_change_flag = True, если хотя бы одно событие
        опубликовано в промежутке [cutoff_ts - 4h, cutoff_ts).

        Параметры
        ----------
        events : list[IllnessEvent]
            Уже отфильтрованные по cutoff_ts события.
        team_id : str
            Идентификатор команды.
        cutoff_ts : datetime
            Временная граница (UTC).

        Возвращает
        ----------
        dict
            Те же поля, что и compute_team_absence_score, с точным late_status_change_flag.
        """
        # Нормализуем cutoff_ts
        if cutoff_ts.tzinfo is None:
            cutoff_ts = cutoff_ts.replace(tzinfo=timezone.utc)
        else:
            cutoff_ts = cutoff_ts.astimezone(timezone.utc)

        team_absent = [
            e for e in events if e.team_id == team_id and e.status in self.ABSENT_STATUSES
        ]

        if not team_absent:
            return {
                "number_of_absent_starters": 0,
                "weighted_absence_score": 0.0,
                "late_status_change_flag": False,
                "min_source_quality": 1.0,
            }

        # Взвешенный балл отсутствия
        weighted_score = sum(
            e.expected_minutes_proxy * SOURCE_QUALITY_SCORES[e.source_quality] for e in team_absent
        )

        min_source_quality = min(SOURCE_QUALITY_SCORES[e.source_quality] for e in team_absent)

        # Точный расчёт флага: событие в окне [cutoff_ts - 4h, cutoff_ts)
        from datetime import timedelta

        late_window_start = cutoff_ts - timedelta(seconds=self.LATE_CHANGE_THRESHOLD_SECONDS)
        late_status_change_flag = any(
            late_window_start <= e.report_ts < cutoff_ts for e in team_absent
        )

        return {
            "number_of_absent_starters": len(team_absent),
            "weighted_absence_score": round(weighted_score, 4),
            "late_status_change_flag": late_status_change_flag,
            "min_source_quality": min_source_quality,
        }
