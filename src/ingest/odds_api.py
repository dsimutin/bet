"""
Модуль для получения коэффициентов из The Odds API (https://the-odds-api.com).
Поддерживает повторные попытки, обработку ограничений частоты запросов и
сохранение сырых снимков данных в формате JSON.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from pydantic import BaseModel, Field
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Настройка логгера модуля
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic-модели
# ---------------------------------------------------------------------------


class OddsOutcome(BaseModel):
    """Исход ставки (например, победа команды или ничья)."""

    name: str = Field(..., description="Название исхода")
    price: float = Field(..., description="Коэффициент в десятичном формате")
    point: float | None = Field(None, description="Линия (для тоталов / форы)")


class OddsMarket(BaseModel):
    """Один рынок (тип ставки) от одного букмекера."""

    key: str = Field(..., description="Ключ рынка, например h2h, spreads, totals")
    last_update: datetime = Field(..., description="Время последнего обновления рынка (UTC)")
    outcomes: list[OddsOutcome] = Field(default_factory=list, description="Список исходов")


class BookmakerEntry(BaseModel):
    """Запись одного букмекера для события."""

    key: str = Field(..., description="Идентификатор букмекера")
    title: str = Field(..., description="Отображаемое название букмекера")
    last_update: datetime = Field(..., description="Время последнего обновления (UTC)")
    markets: list[OddsMarket] = Field(default_factory=list, description="Рынки букмекера")


class OddsSnapshot(BaseModel):
    """Снимок коэффициентов на одно спортивное событие."""

    id: str = Field(..., description="Идентификатор события в API")
    sport_key: str = Field(..., description="Ключ вида спорта")
    sport_title: str = Field(..., description="Отображаемое название вида спорта")
    commence_time: datetime = Field(..., description="Время начала события (UTC)")
    home_team: str = Field(..., description="Название домашней команды")
    away_team: str = Field(..., description="Название гостевой команды")
    bookmakers: list[BookmakerEntry] = Field(default_factory=list, description="Данные букмекеров")
    snapshot_ts_utc: datetime = Field(..., description="Время снятия снимка (UTC)")


# ---------------------------------------------------------------------------
# Вспомогательная функция: создание сессии с автоматическими повторными попытками
# ---------------------------------------------------------------------------


def _build_session(max_retries: int = 3, backoff_factor: float = 1.5) -> requests.Session:
    """
    Создаёт HTTP-сессию с настроенной логикой повторных попыток.

    Параметры
    ----------
    max_retries : int
        Максимальное количество повторных попыток при ошибках соединения или 5xx.
    backoff_factor : float
        Множитель экспоненциальной задержки между попытками.

    Возвращает
    ----------
    requests.Session
        Настроенная сессия.
    """
    retry_strategy = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ---------------------------------------------------------------------------
# Основной клиент
# ---------------------------------------------------------------------------


class OddsAPIClient:
    """
    Клиент для работы с The Odds API v4.

    Параметры
    ----------
    api_key : str
        Ключ API, полученный на https://the-odds-api.com.
    base_url : str
        Базовый URL API (по умолчанию v4).
    request_delay_sec : float
        Задержка в секундах между запросами для соблюдения ограничений частоты.
    timeout_sec : float
        Тайм-аут одного HTTP-запроса в секундах.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.the-odds-api.com/v4",
        request_delay_sec: float = 0.5,
        timeout_sec: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.request_delay_sec = request_delay_sec
        self.timeout_sec = timeout_sec
        self._session = _build_session()
        logger.info("OddsAPIClient инициализирован. Базовый URL: %s", self.base_url)

    # ------------------------------------------------------------------
    # Внутренние методы
    # ------------------------------------------------------------------

    def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """
        Выполняет GET-запрос к API с обработкой ошибок.

        Параметры
        ----------
        endpoint : str
            Путь эндпоинта (без базового URL).
        params : dict, optional
            Параметры строки запроса.

        Возвращает
        ----------
        Any
            Десериализованный JSON-ответ.

        Вызывает
        --------
        requests.HTTPError
            При получении статуса 4xx (кроме 429) или неустранимого 5xx.
        RuntimeError
            При исчерпании лимита запросов (429) после всех попыток.
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        query: dict[str, Any] = {"apiKey": self.api_key}
        if params:
            query.update(params)

        logger.debug(
            "Запрос: GET %s | параметры: %s", url, {k: v for k, v in query.items() if k != "apiKey"}
        )

        # Пауза для соблюдения ограничений частоты запросов
        time.sleep(self.request_delay_sec)

        response = self._session.get(url, params=query, timeout=self.timeout_sec)

        # Логируем остаток квоты из заголовков ответа
        remaining = response.headers.get("x-requests-remaining")
        used = response.headers.get("x-requests-used")
        if remaining is not None:
            logger.info("Осталось запросов: %s | использовано: %s", remaining, used)

        if response.status_code == 429:
            # Попытки уже исчерпаны адаптером — сообщаем об ошибке
            raise RuntimeError(
                "Превышен лимит запросов к The Odds API (429). " "Повторные попытки исчерпаны."
            )

        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Публичные методы
    # ------------------------------------------------------------------

    def get_sports(self) -> list[dict]:
        """
        Получает список доступных видов спорта.

        Возвращает
        ----------
        list[dict]
            Список словарей с полями key, group, title, description, active, has_outrights.
        """
        logger.info("Запрос списка видов спорта...")
        data: list[dict] = self._get("sports")
        logger.info("Получено видов спорта: %d", len(data))
        return data

    def get_odds(
        self,
        sport: str,
        regions: list[str],
        markets: list[str],
        event_ids: list[str] | None = None,
    ) -> list[dict]:
        """
        Получает коэффициенты для заданного вида спорта и набора рынков.

        Параметры
        ----------
        sport : str
            Ключ вида спорта, например 'soccer_epl'.
        regions : list[str]
            Регионы букмекеров: 'uk', 'eu', 'us', 'au'.
        markets : list[str]
            Типы рынков: 'h2h', 'spreads', 'totals'.
        event_ids : list[str], optional
            Список идентификаторов событий для фильтрации (до 10 штук).

        Возвращает
        ----------
        list[dict]
            Сырые данные событий с коэффициентами от API.
        """
        params: dict[str, Any] = {
            "regions": ",".join(regions),
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        }
        if event_ids:
            # API допускает до 10 идентификаторов событий в одном запросе
            if len(event_ids) > 10:
                logger.warning(
                    "Передано %d идентификаторов событий, API поддерживает до 10. "
                    "Будут использованы первые 10.",
                    len(event_ids),
                )
            params["eventIds"] = ",".join(event_ids[:10])

        logger.info(
            "Запрос коэффициентов: спорт=%s, регионы=%s, рынки=%s",
            sport,
            regions,
            markets,
        )
        data: list[dict] = self._get(f"sports/{sport}/odds", params=params)
        logger.info("Получено событий с коэффициентами: %d", len(data))
        return data

    def save_raw_snapshot(
        self,
        data: list[dict],
        sport: str,
        league: str,
        output_dir: Path,
    ) -> Path:
        """
        Сохраняет сырой снимок коэффициентов в формате JSON.

        Файл сохраняется по пути:
            <output_dir>/data/raw/odds_api/<sport>/<league>/<YYYY-MM-DD>/
                odds_<sport>_<league>_<UTC-timestamp>.json

        Параметры
        ----------
        data : list[dict]
            Сырой ответ API для сохранения.
        sport : str
            Ключ вида спорта (используется в пути).
        league : str
            Код лиги (используется в пути).
        output_dir : Path
            Корневая директория проекта (обычно data/).

        Возвращает
        ----------
        Path
            Абсолютный путь к сохранённому файлу.
        """
        now_utc = datetime.now(timezone.utc)
        date_str = now_utc.strftime("%Y-%m-%d")
        ts_str = now_utc.strftime("%Y%m%dT%H%M%SZ")

        # Формируем директорию для сохранения
        save_dir = output_dir / "data" / "raw" / "odds_api" / sport / league / date_str
        save_dir.mkdir(parents=True, exist_ok=True)

        filename = f"odds_{sport}_{league}_{ts_str}.json"
        file_path = save_dir / filename

        # Оборачиваем данные с метаданными снимка
        payload = {
            "snapshot_ts_utc": now_utc.isoformat(),
            "sport": sport,
            "league": league,
            "record_count": len(data),
            "data": data,
        }

        with file_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)

        logger.info(
            "Снимок сохранён: %s (%d событий)",
            file_path,
            len(data),
        )
        return file_path

    def validate_snapshot(self, raw: list[dict], snapshot_ts_utc: datetime) -> list[OddsSnapshot]:
        """
        Валидирует сырые данные API через Pydantic-модель OddsSnapshot.

        Параметры
        ----------
        raw : list[dict]
            Сырой список событий из API.
        snapshot_ts_utc : datetime
            Время снятия снимка (UTC).

        Возвращает
        ----------
        list[OddsSnapshot]
            Список валидных снимков. Невалидные записи пропускаются с предупреждением.
        """
        snapshots: list[OddsSnapshot] = []
        for item in raw:
            try:
                item_with_ts = {**item, "snapshot_ts_utc": snapshot_ts_utc}
                snapshots.append(OddsSnapshot.model_validate(item_with_ts))
            except Exception as exc:
                logger.warning(
                    "Невалидная запись события (id=%s): %s",
                    item.get("id", "неизвестно"),
                    exc,
                )
        logger.info(
            "Валидация: %d из %d записей прошли проверку",
            len(snapshots),
            len(raw),
        )
        return snapshots
