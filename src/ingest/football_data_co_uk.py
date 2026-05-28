"""
Модуль для загрузки исторических данных матчей и коэффициентов
с сайта football-data.co.uk.

Используется исключительно как исторический/справочный источник данных.
Загрузка производится в виде CSV-файлов и сохраняется в data/raw/football_data/.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Настройка логгера модуля
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Справочная информация (только исторический источник)
# ---------------------------------------------------------------------------

# ВНИМАНИЕ: данный модуль является справочным источником исторических данных.
# Не использовать для получения данных в режиме реального времени.
_SOURCE_NOTE: Final[str] = "football-data.co.uk — исторический источник (только архивные данные)"

# ---------------------------------------------------------------------------
# Маппинг столбцов: football-data.co.uk -> нормализованные имена
# ---------------------------------------------------------------------------

COLUMN_MAPPING: Final[dict[str, str]] = {
    # Основная информация о матче
    "Div": "league_code",
    "Date": "match_date",
    "Time": "match_time",
    "HomeTeam": "home_team",
    "AwayTeam": "away_team",
    "FTHG": "goals_home_ft",
    "FTAG": "goals_away_ft",
    "FTR": "result_ft",  # H / D / A
    "HTHG": "goals_home_ht",
    "HTAG": "goals_away_ht",
    "HTR": "result_ht",
    # Статистика матча
    "HS": "shots_home",
    "AS": "shots_away",
    "HST": "shots_on_target_home",
    "AST": "shots_on_target_away",
    "HF": "fouls_home",
    "AF": "fouls_away",
    "HC": "corners_home",
    "AC": "corners_away",
    "HY": "yellow_cards_home",
    "AY": "yellow_cards_away",
    "HR": "red_cards_home",
    "AR": "red_cards_away",
    # Коэффициенты Bet365
    "B365H": "b365_odds_home",
    "B365D": "b365_odds_draw",
    "B365A": "b365_odds_away",
    # Коэффициенты Betfair
    "BFH": "betfair_odds_home",
    "BFD": "betfair_odds_draw",
    "BFA": "betfair_odds_away",
    # Коэффициенты William Hill
    "WHH": "wh_odds_home",
    "WHD": "wh_odds_draw",
    "WHA": "wh_odds_away",
    # Коэффициенты Pinnacle
    "PSH": "pinnacle_odds_home",
    "PSD": "pinnacle_odds_draw",
    "PSA": "pinnacle_odds_away",
    # Средние рыночные коэффициенты
    "MaxH": "max_odds_home",
    "MaxD": "max_odds_draw",
    "MaxA": "max_odds_away",
    "AvgH": "avg_odds_home",
    "AvgD": "avg_odds_draw",
    "AvgA": "avg_odds_away",
}


def _build_session(max_retries: int = 3, backoff_factor: float = 1.5) -> requests.Session:
    """
    Создаёт HTTP-сессию с логикой повторных попыток.

    Параметры
    ----------
    max_retries : int
        Максимальное количество попыток при ошибках сети или 5xx.
    backoff_factor : float
        Множитель экспоненциальной задержки между попытками.

    Возвращает
    ----------
    requests.Session
        Настроенная сессия requests.
    """
    retry = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ---------------------------------------------------------------------------
# Основной загрузчик
# ---------------------------------------------------------------------------


class FootballDataLoader:
    """
    Загрузчик исторических CSV-данных с football-data.co.uk.

    СПРАВОЧНЫЙ ИСТОЧНИК — только для исторических исследований и бэктестинга.
    Не предназначен для получения данных в режиме реального времени.

    Параметры
    ----------
    timeout_sec : float
        Тайм-аут HTTP-запроса в секундах.
    """

    # Маппинг кодов лиг на URL CSV-файлов football-data.co.uk
    # Формат URL: https://www.football-data.co.uk/mmz4281/{season}/{league}.csv
    LEAGUE_URLS: Final[dict[str, str]] = {
        # Англия
        "E0": "https://www.football-data.co.uk/mmz4281/{season}/E0.csv",  # Премьер-лига
        "E1": "https://www.football-data.co.uk/mmz4281/{season}/E1.csv",  # Чемпионшип
        "E2": "https://www.football-data.co.uk/mmz4281/{season}/E2.csv",  # Лига 1 Англии
        "E3": "https://www.football-data.co.uk/mmz4281/{season}/E3.csv",  # Лига 2 Англии
        # Германия
        "D1": "https://www.football-data.co.uk/mmz4281/{season}/D1.csv",  # Бундеслига
        "D2": "https://www.football-data.co.uk/mmz4281/{season}/D2.csv",  # 2-я Бундеслига
        # Испания
        "SP1": "https://www.football-data.co.uk/mmz4281/{season}/SP1.csv",  # Ла Лига
        "SP2": "https://www.football-data.co.uk/mmz4281/{season}/SP2.csv",  # Сегунда
        # Италия
        "I1": "https://www.football-data.co.uk/mmz4281/{season}/I1.csv",  # Серия А
        "I2": "https://www.football-data.co.uk/mmz4281/{season}/I2.csv",  # Серия Б
        # Франция
        "F1": "https://www.football-data.co.uk/mmz4281/{season}/F1.csv",  # Лига 1 Франции
        "F2": "https://www.football-data.co.uk/mmz4281/{season}/F2.csv",  # Лига 2 Франции
        # Нидерланды
        "N1": "https://www.football-data.co.uk/mmz4281/{season}/N1.csv",  # Эредивизи
        # Бельгия
        "B1": "https://www.football-data.co.uk/mmz4281/{season}/B1.csv",  # Жюпилер Про Лига
        # Португалия
        "P1": "https://www.football-data.co.uk/mmz4281/{season}/P1.csv",  # Примейра
        # Турция
        "T1": "https://www.football-data.co.uk/mmz4281/{season}/T1.csv",  # Суперлига
        # Греция
        "G1": "https://www.football-data.co.uk/mmz4281/{season}/G1.csv",  # Суперлига Греции
        # Шотландия
        "SC0": "https://www.football-data.co.uk/mmz4281/{season}/SC0.csv",  # Шотландская Премьершип
    }

    def __init__(self, timeout_sec: float = 30.0) -> None:
        self.timeout_sec = timeout_sec
        self._session = _build_session()
        logger.info("FootballDataLoader инициализирован. %s", _SOURCE_NOTE)

    def download_season(
        self,
        league: str,
        season: str,
        output_dir: Path,
    ) -> Path:
        """
        Загружает CSV с данными сезона и сохраняет в локальную директорию.

        Файл сохраняется по пути:
            <output_dir>/data/raw/football_data/<league>/<season>/data.csv

        Параметры
        ----------
        league : str
            Код лиги (например, 'E0' для АПЛ, 'D1' для Бундеслиги).
        season : str
            Код сезона в формате 'YYYY' (например, '2324' для 2023/24).
        output_dir : Path
            Корневая директория проекта (обычно data/).

        Возвращает
        ----------
        Path
            Путь к сохранённому CSV-файлу.

        Вызывает
        --------
        ValueError
            Если код лиги не найден в LEAGUE_URLS.
        requests.HTTPError
            При ошибке HTTP.
        """
        if league not in self.LEAGUE_URLS:
            raise ValueError(
                f"Неизвестный код лиги: '{league}'. "
                f"Доступные коды: {sorted(self.LEAGUE_URLS.keys())}"
            )

        url = self.LEAGUE_URLS[league].format(season=season)
        logger.info(
            "Загрузка исторических данных: лига=%s, сезон=%s, URL=%s",
            league,
            season,
            url,
        )

        response = self._session.get(url, timeout=self.timeout_sec)
        response.raise_for_status()

        # Формируем путь для сохранения
        save_dir = output_dir / "data" / "raw" / "football_data" / league / season
        save_dir.mkdir(parents=True, exist_ok=True)

        file_path = save_dir / "data.csv"
        file_path.write_bytes(response.content)

        logger.info(
            "Данные сохранены: %s (%d байт)",
            file_path,
            len(response.content),
        )
        return file_path

    def load_and_parse(self, csv_path: Path) -> pd.DataFrame:
        """
        Загружает и парсит CSV-файл football-data.co.uk.

        Применяет маппинг столбцов из COLUMN_MAPPING, обрабатывает
        дату/время и приводит типы данных.

        Параметры
        ----------
        csv_path : Path
            Путь к CSV-файлу (ранее скачанному через download_season).

        Возвращает
        ----------
        pd.DataFrame
            Нормализованный датафрейм с переименованными столбцами,
            распарсенными датами и очищенными числовыми полями.
        """
        logger.info("Парсинг CSV: %s", csv_path)

        # Читаем CSV; football-data.co.uk использует кодировку latin-1
        df = pd.read_csv(
            csv_path,
            encoding="latin-1",
            on_bad_lines="warn",
        )

        # Удаляем полностью пустые строки (часто встречаются в конце файлов)
        df = df.dropna(how="all")

        logger.info("Загружено строк: %d, столбцов: %d", len(df), len(df.columns))

        # Применяем маппинг столбцов (только те, что присутствуют в файле)
        cols_to_rename = {old: new for old, new in COLUMN_MAPPING.items() if old in df.columns}
        df = df.rename(columns=cols_to_rename)
        logger.debug("Переименовано столбцов: %d", len(cols_to_rename))

        # Парсим дату матча (football-data.co.uk использует DD/MM/YY или DD/MM/YYYY)
        if "match_date" in df.columns:
            df["match_date"] = pd.to_datetime(
                df["match_date"],
                dayfirst=True,
                errors="coerce",
            )
            invalid_dates = df["match_date"].isna().sum()
            if invalid_dates:
                logger.warning("Не удалось распарсить дат: %d", invalid_dates)

        # Приводим коэффициенты и числовые поля к float
        odds_cols = [
            c
            for c in df.columns
            if "odds" in c
            or "goals" in c
            or "shots" in c
            or "corners" in c
            or "cards" in c
            or "fouls" in c
        ]
        for col in odds_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        logger.info("Парсинг завершён. Итоговых строк: %d", len(df))
        return df
