"""
Словари нормализации названий команд и лиг.

Содержит маппинги для популярных лиг: АПЛ, Российская Премьер-Лига,
Бундеслига, Ла Лига, Серия А и другие. Используется для приведения
разных написаний названий к единому каноническому виду.

Примечание: для неизвестных вариантов предусмотрен нечёткий поиск
(fuzzy matching) — реализуется на уровне вызывающего кода при необходимости.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Маппинг названий команд
# ---------------------------------------------------------------------------

TEAM_NAME_MAP: dict[str, str] = {
    # ------------------------------------------------------------------
    # Английская Премьер-лига (АПЛ)
    # ------------------------------------------------------------------
    "Man City": "Manchester City",
    "Manch City": "Manchester City",
    "Manchester C": "Manchester City",
    "Man. City": "Manchester City",
    "Man C": "Manchester City",
    "MCFC": "Manchester City",
    "Man United": "Manchester United",
    "Man Utd": "Manchester United",
    "Manchester Utd": "Manchester United",
    "Manchester U": "Manchester United",
    "Man. United": "Manchester United",
    "MUFC": "Manchester United",
    "Arsenal FC": "Arsenal",
    "AFC": "Arsenal",
    "Chelsea FC": "Chelsea",
    "CFC": "Chelsea",
    "Liverpool FC": "Liverpool",
    "LFC": "Liverpool",
    "Tottenham Hotspur": "Tottenham",
    "Spurs": "Tottenham",
    "Tottenham H": "Tottenham",
    "Tottenham Hotspurs": "Tottenham",
    "Newcastle Utd": "Newcastle United",
    "Newcastle U": "Newcastle United",
    "NUFC": "Newcastle United",
    "Aston Villa FC": "Aston Villa",
    "AV": "Aston Villa",
    "West Ham Utd": "West Ham United",
    "West Ham": "West Ham United",
    "WHU": "West Ham United",
    "Brighton & Hove": "Brighton",
    "Brighton & Hove Albion": "Brighton",
    "BHAFC": "Brighton",
    "Wolverhampton": "Wolves",
    "Wolverhampton W": "Wolves",
    "Wolverhampton Wanderers": "Wolves",
    "WWFC": "Wolves",
    "Brentford FC": "Brentford",
    "Fulham FC": "Fulham",
    "Crystal Palace FC": "Crystal Palace",
    "Everton FC": "Everton",
    "Nottingham Forest": "Nottm Forest",
    "Nottingham F": "Nottm Forest",
    "Nott'm Forest": "Nottm Forest",
    "Bournemouth": "AFC Bournemouth",
    "AFC Bmouth": "AFC Bournemouth",
    "Luton Town": "Luton",
    "Luton Town FC": "Luton",
    "Sheffield United": "Sheffield Utd",
    "Sheffield Utd": "Sheffield Utd",
    "Sheffield U": "Sheffield Utd",
    "Burnley FC": "Burnley",
    # ------------------------------------------------------------------
    # Немецкая Бундеслига
    # ------------------------------------------------------------------
    "FC Bayern München": "Bayern Munich",
    "Bayern München": "Bayern Munich",
    "Bayern Munchen": "Bayern Munich",
    "FC Bayern": "Bayern Munich",
    "FCB": "Bayern Munich",
    "Borussia Dortmund": "Dortmund",
    "BVB": "Dortmund",
    "Borussia D": "Dortmund",
    "RB Leipzig": "RB Leipzig",
    "Rasenballsport Leipzig": "RB Leipzig",
    "Bayer 04 Leverkusen": "Bayer Leverkusen",
    "Bayer Leverkusen": "Bayer Leverkusen",
    "B04": "Bayer Leverkusen",
    "Borussia Mönchengladbach": "M'gladbach",
    "Borussia M'gladbach": "M'gladbach",
    "Borussia Mgladbach": "M'gladbach",
    "Gladbach": "M'gladbach",
    "VfB Stuttgart": "Stuttgart",
    "SV Werder Bremen": "Werder Bremen",
    "Werder": "Werder Bremen",
    "Eintracht Frankfurt": "Ein Frankfurt",
    "Frankfurt": "Ein Frankfurt",
    "TSG 1899 Hoffenheim": "Hoffenheim",
    "Hoffenheim": "Hoffenheim",
    "SC Freiburg": "Freiburg",
    "FC Union Berlin": "Union Berlin",
    "1. FC Köln": "FC Koln",
    "FC Köln": "FC Koln",
    "1. FSV Mainz 05": "Mainz",
    "Mainz 05": "Mainz",
    "VfL Wolfsburg": "Wolfsburg",
    "Hertha BSC": "Hertha Berlin",
    "SV Darmstadt 98": "Darmstadt",
    # ------------------------------------------------------------------
    # Испанская Ла Лига
    # ------------------------------------------------------------------
    "FC Barcelona": "Barcelona",
    "Barça": "Barcelona",
    "Barca": "Barcelona",
    "FCB": "Barcelona",
    "Real Madrid CF": "Real Madrid",
    "Real Madrid C.F.": "Real Madrid",
    "RM": "Real Madrid",
    "Club Atlético de Madrid": "Atletico Madrid",
    "Atlético Madrid": "Atletico Madrid",
    "Atletico de Madrid": "Atletico Madrid",
    "Atletico": "Atletico Madrid",
    "ATM": "Atletico Madrid",
    "Sevilla FC": "Sevilla",
    "Real Betis": "Betis",
    "Real Betis Balompié": "Betis",
    "Valencia CF": "Valencia",
    "Villarreal CF": "Villarreal",
    "Athletic Bilbao": "Ath Bilbao",
    "Athletic Club": "Ath Bilbao",
    "Real Sociedad": "Sociedad",
    "RC Celta de Vigo": "Celta Vigo",
    "Celta de Vigo": "Celta Vigo",
    "Getafe CF": "Getafe",
    "RCD Mallorca": "Mallorca",
    "Girona FC": "Girona",
    "UD Las Palmas": "Las Palmas",
    "CA Osasuna": "Osasuna",
    # ------------------------------------------------------------------
    # Итальянская Серия А
    # ------------------------------------------------------------------
    "Juventus FC": "Juventus",
    "Juve": "Juventus",
    "AC Milan": "AC Milan",
    "Milan": "AC Milan",
    "FC Internazionale": "Inter",
    "Inter Milan": "Inter",
    "Internazionale": "Inter",
    "FC Inter": "Inter",
    "AS Roma": "Roma",
    "SS Lazio": "Lazio",
    "SSC Napoli": "Napoli",
    "Atalanta BC": "Atalanta",
    "Fiorentina": "Fiorentina",
    "ACF Fiorentina": "Fiorentina",
    "Torino FC": "Torino",
    "Bologna FC": "Bologna",
    "UC Sampdoria": "Sampdoria",
    "Udinese Calcio": "Udinese",
    # ------------------------------------------------------------------
    # Российская Премьер-Лига (РПЛ)
    # ------------------------------------------------------------------
    "ЦСКА": "CSKA Moscow",
    "ЦСКА Москва": "CSKA Moscow",
    "ПФК ЦСКА": "CSKA Moscow",
    "CSKA": "CSKA Moscow",
    "Спартак": "Spartak Moscow",
    "Спартак Москва": "Spartak Moscow",
    "ФК Спартак": "Spartak Moscow",
    "Spartak": "Spartak Moscow",
    "Зенит": "Zenit St Petersburg",
    "Зенит СПб": "Zenit St Petersburg",
    "ФК Зенит": "Zenit St Petersburg",
    "Zenit": "Zenit St Petersburg",
    "FC Zenit": "Zenit St Petersburg",
    "Локомотив": "Lokomotiv Moscow",
    "Локомотив Москва": "Lokomotiv Moscow",
    "ФК Локомотив": "Lokomotiv Moscow",
    "Lokomotiv": "Lokomotiv Moscow",
    "Динамо": "Dynamo Moscow",
    "Динамо Москва": "Dynamo Moscow",
    "ФК Динамо": "Dynamo Moscow",
    "Dynamo": "Dynamo Moscow",
    "Краснодар": "Krasnodar",
    "ФК Краснодар": "Krasnodar",
    "FK Krasnodar": "Krasnodar",
    "Рубин": "Rubin Kazan",
    "Рубин Казань": "Rubin Kazan",
    "Rubin": "Rubin Kazan",
    "Ростов": "FK Rostov",
    "ФК Ростов": "FK Rostov",
    "Rostov": "FK Rostov",
    "Крылья Советов": "Krylia Sovetov",
    "Krylia": "Krylia Sovetov",
    "Сочи": "FC Sochi",
    "ФК Сочи": "FC Sochi",
    "Химки": "FK Khimki",
    "ФК Химки": "FK Khimki",
    "Урал": "FC Ural",
    "ФК Урал": "FC Ural",
    "Урал Екатеринбург": "FC Ural",
    "Нижний Новгород": "FC Nizhny Novgorod",
    "ФК Нижний Новгород": "FC Nizhny Novgorod",
    "Пари НН": "FC Nizhny Novgorod",
    "Оренбург": "FC Orenburg",
    "ФК Оренбург": "FC Orenburg",
    "Балтика": "FC Baltika",
    "ФК Балтика": "FC Baltika",
    "Балтика Калининград": "FC Baltika",
    "Факел": "FC Fakel",
    "ФК Факел": "FC Fakel",
    "Факел Воронеж": "FC Fakel",
    "Ахмат": "FC Akhmat",
    "ФК Ахмат": "FC Akhmat",
    "Ахмат-Грозный": "FC Akhmat",
}


def normalize_team_name(name: str) -> str:
    """
    Возвращает нормализованное название команды по словарю TEAM_NAME_MAP.

    При отсутствии точного совпадения возвращает исходное название без изменений.
    Для нечёткого поиска рекомендуется использовать библиотеку rapidfuzz
    с порогом схожести >= 85 (реализуется на уровне вызывающего кода).

    Параметры
    ----------
    name : str
        Исходное название команды (в любом регистре и написании).

    Возвращает
    ----------
    str
        Нормализованное каноническое название или исходное при отсутствии совпадения.
    """
    stripped = name.strip()
    # Точное совпадение (с учётом регистра исходной строки)
    if stripped in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[stripped]
    # Поиск без учёта регистра
    lower = stripped.lower()
    for key, value in TEAM_NAME_MAP.items():
        if key.lower() == lower:
            return value
    # Совпадение не найдено — возвращаем исходное название
    return stripped


# ---------------------------------------------------------------------------
# Маппинг названий лиг
# ---------------------------------------------------------------------------

LEAGUE_NAME_MAP: dict[str, str] = {
    # Англия
    "E0": "English Premier League",
    "EPL": "English Premier League",
    "Premier League": "English Premier League",
    "soccer_epl": "English Premier League",
    "england_premier_league": "English Premier League",
    "АПЛ": "English Premier League",
    "Английская Премьер-лига": "English Premier League",
    "E1": "English Championship",
    "Championship": "English Championship",
    "soccer_england_championship": "English Championship",
    # Германия
    "D1": "Bundesliga",
    "Бундеслига": "Bundesliga",
    "soccer_germany_bundesliga": "Bundesliga",
    "1. Bundesliga": "Bundesliga",
    "D2": "2. Bundesliga",
    "soccer_germany_bundesliga2": "2. Bundesliga",
    # Испания
    "SP1": "La Liga",
    "Ла Лига": "La Liga",
    "soccer_spain_la_liga": "La Liga",
    "Primera Division": "La Liga",
    "SP2": "La Liga 2",
    "Segunda Division": "La Liga 2",
    "soccer_spain_segunda_division": "La Liga 2",
    # Италия
    "I1": "Serie A",
    "Серия А": "Serie A",
    "soccer_italy_serie_a": "Serie A",
    "I2": "Serie B",
    "soccer_italy_serie_b": "Serie B",
    # Франция
    "F1": "Ligue 1",
    "Лига 1": "Ligue 1",
    "soccer_france_ligue_one": "Ligue 1",
    "F2": "Ligue 2",
    "soccer_france_ligue_two": "Ligue 2",
    # Нидерланды
    "N1": "Eredivisie",
    "Эредивизи": "Eredivisie",
    "soccer_netherlands_eredivisie": "Eredivisie",
    # Португалия
    "P1": "Primeira Liga",
    "soccer_portugal_primeira_liga": "Primeira Liga",
    # Бельгия
    "B1": "Belgian First Division A",
    "Jupiler Pro League": "Belgian First Division A",
    # Турция
    "T1": "Süper Lig",
    "Super Lig": "Süper Lig",
    "soccer_turkey_super_league": "Süper Lig",
    # Шотландия
    "SC0": "Scottish Premiership",
    "soccer_scotland_premiership": "Scottish Premiership",
    # Россия
    "РПЛ": "Russian Premier League",
    "Российская Премьер-лига": "Russian Premier League",
    "soccer_russia_premier_league": "Russian Premier League",
    "Russian Premier League": "Russian Premier League",
    # Лиги УЕФА
    "UCL": "UEFA Champions League",
    "Лига Чемпионов": "UEFA Champions League",
    "soccer_uefa_champs_league": "UEFA Champions League",
    "Champions League": "UEFA Champions League",
    "UEL": "UEFA Europa League",
    "Лига Европы": "UEFA Europa League",
    "soccer_uefa_europa_league": "UEFA Europa League",
    "Europa League": "UEFA Europa League",
    "UECL": "UEFA Conference League",
    "Лига Конференций": "UEFA Conference League",
    "soccer_uefa_conference_league": "UEFA Conference League",
    "Conference League": "UEFA Conference League",
}


def normalize_league_name(name: str) -> str:
    """
    Возвращает нормализованное название лиги по словарю LEAGUE_NAME_MAP.

    При отсутствии точного совпадения возвращает исходное название без изменений.

    Параметры
    ----------
    name : str
        Исходное название или код лиги.

    Возвращает
    ----------
    str
        Нормализованное каноническое название лиги.
    """
    stripped = name.strip()
    if stripped in LEAGUE_NAME_MAP:
        return LEAGUE_NAME_MAP[stripped]
    lower = stripped.lower()
    for key, value in LEAGUE_NAME_MAP.items():
        if key.lower() == lower:
            return value
    return stripped
