# Обзор open source экосистемы — Betting Analytics MVP

**Версия:** 0.1.0  
**Дата:** 2026-05-26  
**Автор:** repo-scout agent  
**Язык документации:** Русский

---

## Сводная таблица

| Репозиторий | Лицензия | Активность | Оценка пригодности | Потенциал повторного использования |
|------------|---------|-----------|-------------------|-----------------------------------|
| `sports-betting` (skforecast) | MIT | Высокая | 7/10 | Бэктест-движок, метрики |
| `penaltyblog` | MIT | Средняя | 8/10 | Dixon-Coles модель, devigging |
| `OddsHarvester` | MIT | Средняя | 6/10 | Парсинг The Odds API |
| `football-data-api` (wrappers) | MIT / Apache 2.0 | Варьируется | 5/10 | HTTP-клиент для Football-Data.co.uk |
| `betfairlightweight` | MIT | Высокая | 9/10 | Betfair Exchange API + исторические данные |
| `soccer-xg` | MIT | Низкая | 4/10 | xG модели (вспомогательное) |
| `pybet` | GPL-3.0 | Низкая | 3/10 | Устаревший, GPL-ограничения |
| `soccerway-scraper` | Нет лицензии | Низкая | 2/10 | Скрапер — правовые риски |

---

## Детальные описания

---

### 1. `sports-betting` (iguanatestsuite / skforecast экосистема)

**Репозиторий:** https://github.com/skforecast/skforecast (смежный)  
**Прямой линк:** https://github.com/ML-KULeuven/sports-betting  
**Лицензия:** MIT  
**Язык:** Python  
**Активность:** Последний коммит < 6 месяцев, открытые PR обрабатываются  
**Звёзды:** ~400+  

#### Описание

Библиотека для машинного обучения в контексте спортивного прогнозирования. Включает:
- Абстракции `Bettor` и `OddsComparisonBettor` — базовый класс ставочной стратегии
- Симулятор бэктестов на основе исторических данных
- Интеграция с scikit-learn пайплайнами
- Метрики: Yield, ROI, n_bets, coverage

#### Что можно повторно использовать

```python
# Пример: базовый класс стратегии
from sportsbet.evaluation import backtest_betting

# Интерфейс для walk-forward бэктеста
results = backtest_betting(
    bettor=my_strategy,
    X=features,
    Y=odds_and_outcomes,
    cv=TimeSeriesSplit(n_splits=5),
)
```

- **Интерфейс `Bettor`** — хорошая отправная точка для собственных стратегий H001–H005
- **Метрики-функции** — можно использовать напрямую или адаптировать
- **CV-схема** — совместима с нашим walk-forward дизайном

#### Ограничения

- Ориентирован на sklearn-совместимые модели, не на rule-based стратегии
- Нет нативной поддержки многоисточниковой нормализации котировок
- Timestamp-aware merge не реализован — придётся добавлять самостоятельно
- Нет поддержки CLV как метрики

#### Решение о включении

**Использовать частично.** Заимствовать интерфейс `Bettor` и метрики. Timestamp-aware merge и devigging реализовать самостоятельно.

---

### 2. `penaltyblog`

**Репозиторий:** https://github.com/martineastwood/penaltyblog  
**Лицензия:** MIT  
**Язык:** Python  
**Активность:** Средняя — коммиты 1–2 раза в квартал, документация актуальна  
**Звёзды:** ~300+  

#### Описание

Коллекция статистических и вероятностных инструментов для анализа футбола:
- **Dixon-Coles модель** — двумерное Пуассоновское распределение с коррекцией
- **Деривинг (devigging)** — несколько методов (нормализация, Shin, power)
- **Рейтинговые системы:** Elo, Pi-rating, Massey
- **xG модели** (базовые)
- **Оценка калибровки** (Brier score, reliability diagram)

#### Что можно повторно использовать

```python
from penaltyblog.models import DixonColes
from penaltyblog.implied import power_method, shin_method

# Devigging (для H001 — расчёт fair odds)
fair_probs = shin_method(
    home_odds=1.90,
    draw_odds=3.60,
    away_odds=4.20
)
# {'home': 0.485, 'draw': 0.295, 'away': 0.220}

# Dixon-Coles (для H005 — вероятностная модель)
model = DixonColes()
model.fit(goals_home, goals_away, teams_home, teams_away, weights)
probs = model.predict_result("Arsenal", "Chelsea")
```

#### Ограничения

- Нет встроенной поддержки rolling/walk-forward переобучения модели
- Нет хранения результатов — только вычисления
- Нет поддержки Asian Handicap рынков
- Shin-метод может быть нестабилен при малом overround (< 101%)

#### Решение о включении

**Использовать активно.** `penaltyblog.implied` — основа для `src/core/devigger.py`. `DixonColes` — основа для H005. Обёртка для rolling window и версионирования — наша собственная разработка.

**Конкретный план:**
```
src/core/devigger.py     ← обёртка над penaltyblog.implied
src/agents/modeler.py    ← использует penaltyblog.models.DixonColes
```

---

### 3. `OddsHarvester`

**Репозиторий:** https://github.com/47-studio-org/OddsHarvester (и форки)  
**Лицензия:** MIT  
**Язык:** Python  
**Активность:** Средняя — фокус на The Odds API  
**Звёзды:** ~150+  

#### Описание

Инструмент для массового сбора котировок через The Odds API:
- Rate limiting и retry логика
- Кэширование ответов
- Парсинг всех типов рынков (h2h, spreads, totals)
- Базовая нормализация в pandas DataFrame

#### Что можно повторно использовать

```python
# Паттерн Rate Limiting + Retry
class OddsAPIClient:
    def __init__(self, api_key: str, requests_per_month: int = 500):
        self.session = requests.Session()
        self.remaining_requests = requests_per_month
    
    def get_odds(self, sport: str, markets: List[str]) -> dict:
        # Проверка remaining_requests из заголовков
        response = self.session.get(...)
        self.remaining_requests = int(
            response.headers.get("x-requests-remaining", 0)
        )
        return response.json()
```

- **Паттерны работы с The Odds API** — rate limiting, обработка заголовков
- **Структура ответа** — документация маппинга полей
- **Retry с backoff** — для нестабильного соединения

#### Ограничения

- Нет поддержки российских букмекеров (Fonbet, Winline недоступны через The Odds API)
- Нет сохранения в Parquet (только in-memory / CSV)
- Нет Pydantic-валидации схем
- Устаревший синтаксис в некоторых форках

#### Решение о включении

**Использовать как справочник.** Паттерны rate limiting и обработки заголовков API. Реализацию `src/ingest/odds_api.py` писать с нуля с Pydantic-моделями и Parquet-хранением.

---

### 4. `football-data-api` wrappers

**Репозиторий (пример):** https://github.com/jokecamp/FootballData  
**Альтернатива:** https://github.com/octonion/sports  
**Лицензия:** MIT / Apache 2.0  
**Язык:** Python, R  
**Активность:** Варьируется (многие форки заброшены)  

#### Описание

Обёртки над Football-Data.co.uk — исторические CSV по лигам. Основные паттерны:
- Загрузка CSV по URL шаблону: `https://www.football-data.co.uk/mmz4281/{season}/{league}.csv`
- Нормализация столбцов (разные лиги — разные наборы столбцов)
- Конвертация типов данных

#### Что можно повторно использовать

```python
# Шаблон URL для Football-Data.co.uk
LEAGUES = {
    "E0": "England Premier League",
    "D1": "Germany Bundesliga",
    "SP1": "Spain La Liga",
    "I1": "Italy Serie A",
    "F1": "France Ligue 1",
    "R1": "Russia Premier League",  # RPL
}

SEASONS = ["2223", "2324", "2425"]

URL_TEMPLATE = (
    "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
)

# Колонки closing odds от bookmakers
CLOSING_ODDS_COLS = {
    "B365H": "Bet365 Home",
    "B365D": "Bet365 Draw",
    "B365A": "Bet365 Away",
    "PSH": "Pinnacle Home",
    "PSD": "Pinnacle Draw",
    "PSA": "Pinnacle Away",
    "WHH": "William Hill Home",
    # ...
}
```

#### Ограничения

- Данные только по завершённым матчам (исторические, нет live)
- Структура CSV меняется между лигами и сезонами
- Нет официального API — только CSV-файлы
- Задержка публикации данных — несколько дней после матча
- РПЛ покрыта неполно (R1, некоторые сезоны отсутствуют)

#### Решение о включении

**Реализовать `src/ingest/football_data.py` самостоятельно** с использованием выявленных URL-паттернов и схемы колонок. Существующие обёртки слишком простые для интеграции с нашим Parquet-хранением.

---

### 5. `betfairlightweight`

**Репозиторий:** https://github.com/liampauling/betfairlightweight  
**Лицензия:** MIT  
**Язык:** Python  
**Активность:** Высокая — активный мейнтейнер, регулярные релизы  
**Звёзды:** ~600+  
**PyPI:** `pip install betfairlightweight`  

#### Описание

Наиболее зрелая Python-библиотека для работы с Betfair:
- **Betfair Exchange API** — betting, orders, market data
- **Betfair Streaming API** — real-time котировки через WebSocket
- **Historical Data** — парсинг `.bz2` архивов с тик-данными торгов
- Полная документация, типизация, активное сообщество

#### Что можно повторно использовать

```python
import betfairlightweight
from betfairlightweight.filters import streaming_market_filter

# Клиент для исторических данных (ключевое для нашего проекта)
client = betfairlightweight.APIClient("username", "password", app_key="key")

# Парсинг исторических .bz2 архивов
trading = betfairlightweight.APIClient(...)
stream = trading.streaming.create_historical_generator_stream(
    file_path="/path/to/historical_data.bz2",
    listener=betfairlightweight.StreamListener(max_latency=None),
)

for market_books in stream:
    for market_book in market_books:
        # Получить last_price_traded (LTP) — прокси closing odds
        for runner in market_book.runners:
            ltp = runner.last_price_traded
            # ... конвертировать в decimal odds: 1 / (1 - ltp) для lay
```

#### Ограничения

- Для использования API нужен верифицированный аккаунт Betfair
- Исторические данные платные (через historicdata.betfair.com)
- Betfair недоступен для российских IP (нужен VPN или проксирование)
- Формат исторических данных сложен (nested JSON stream)

#### Решение о включении

**Использовать как зависимость.** Добавить в `requirements.txt`. Реализовать `src/ingest/betfair_hist.py` на основе этой библиотеки. Является основным инструментом для получения sharp closing line (reference odds для H001, H002).

---

### 6. `soccer-xg`

**Репозиторий:** https://github.com/rjtavares/football-crunching (и аналоги)  
**Лицензия:** MIT  
**Язык:** Python / Jupyter  
**Активность:** Низкая (преимущественно notebooks, не библиотека)  

#### Описание

Коллекция Jupyter Notebooks по xG (expected goals) моделированию:
- Логистическая регрессия для вычисления xG из shot data
- Анализ команд и игроков по xG метрикам

#### Что можно повторно использовать

Алгоритмические паттерны xG-моделей — только как справочник. Прямое использование нецелесообразно из-за зависимости от Statsbomb / Wyscout данных, которые не интегрированы в наш стек.

#### Решение о включении

**Не включать.** Только справочное чтение для понимания xG моделирования при разработке H005.

---

### 7. `pybet`

**Репозиторий:** https://github.com/phatpaul/pybet (и форки)  
**Лицензия:** GPL-3.0  
**Язык:** Python  
**Активность:** Низкая, последний коммит > 2 лет  

#### Описание

Устаревшая библиотека для вычисления котировок и маржи. Содержит базовые утилиты: конвертация форматов котировок (decimal / fractional / moneyline), расчёт маржи.

#### Что можно повторно использовать

Логика конвертации котировок — примитивна и легко реализуется самостоятельно.

#### Решение о включении

**Не включать.** GPL-лицензия создаёт юридические ограничения. Функциональность тривиальна для самостоятельной реализации (< 50 строк кода).

```python
# Реализуем сами в src/utils/odds_conversion.py
def decimal_to_implied_prob(decimal_odds: float) -> float:
    return 1.0 / decimal_odds

def american_to_decimal(american_odds: int) -> float:
    if american_odds > 0:
        return american_odds / 100 + 1
    return 100 / abs(american_odds) + 1

def fractional_to_decimal(numerator: int, denominator: int) -> float:
    return numerator / denominator + 1
```

---

### 8. `soccerway-scraper` и аналоги

**Репозиторий:** различные форки на GitHub  
**Лицензия:** Нет явной лицензии  
**Язык:** Python / Selenium  
**Активность:** Низкая, хрупкая реализация  

#### Описание

Скраперы для сбора котировок с сайтов букмекеров. Конкретно — Soccerway, Flashscore и аналоги.

#### Оценка

| Аспект | Оценка |
|--------|--------|
| Правовые риски | Высокие (нарушение ToS, robots.txt) |
| Техническая хрупкость | Высокая (ломается при изменении структуры сайта) |
| Качество данных | Низкое (ошибки парсинга, задержки) |
| Поддержка | Отсутствует |

#### Решение о включении

**Не включать.** Правовые и технические риски слишком высоки. Для российских букмекеров (Fonbet, Winline) необходим собственный осторожный скрапинг с соблюдением `robots.txt` и rate limiting ≥ 2 сек.

---

## Матрица принятых решений

| Компонент системы | Используемые библиотеки | Разрабатывается сами |
|------------------|------------------------|---------------------|
| Devigging (fair odds) | `penaltyblog.implied` | Обёртка + кэширование |
| Dixon-Coles модель | `penaltyblog.models` | Rolling window pipeline |
| Betfair исторические данные | `betfairlightweight` | Парсер + Parquet сохранение |
| The Odds API клиент | Паттерны OddsHarvester | Полная реализация с Pydantic |
| Football-Data.co.uk | URL-паттерны из анализа | Полный инжест-модуль |
| Бэктест интерфейс | Интерфейс sports-betting | Walk-forward движок |
| Конвертация котировок | — | `src/utils/odds_conversion.py` |
| Timestamp-aware merge | — | `src/utils/timestamps.py` |
| Нормализация событий | — | `src/agents/qc_normalizer.py` |
| Telegram интеграция | `python-telegram-bot` | Bot handlers |

---

## Зависимости для установки

```toml
# pyproject.toml / requirements.txt

# Ключевые внешние библиотеки
penaltyblog = ">=0.5.0"          # Dixon-Coles, devigging
betfairlightweight = ">=2.19.0"  # Betfair API + historical
python-telegram-bot = ">=20.0"   # Telegram Bot API

# Инфраструктура
pandas = ">=2.0.0"
polars = ">=0.19.0"
pyarrow = ">=14.0"               # Parquet поддержка
pydantic = ">=2.0.0"
structlog = ">=23.0"
requests = ">=2.31.0"
APScheduler = ">=3.10.0"

# Тестирование и качество
pytest = ">=7.4"
pytest-cov = ">=4.1"
ruff = ">=0.1.0"
mypy = ">=1.5"
```

---

## Лицензионная совместимость

| Библиотека | Лицензия | Совместимость с MIT проектом |
|-----------|---------|------------------------------|
| `penaltyblog` | MIT | Полная совместимость |
| `betfairlightweight` | MIT | Полная совместимость |
| `python-telegram-bot` | LGPL-3.0 | Совместима при динамической линковке |
| `pandas` | BSD-3 | Полная совместимость |
| `polars` | MIT | Полная совместимость |
| `pydantic` | MIT | Полная совместимость |
| `pybet` | **GPL-3.0** | **Несовместима — исключена** |

---

## Рекомендации для дальнейшего мониторинга

1. **`streaksports/soccer-cli`** — интерактивный CLI для матчей, может быть источником данных
2. **`openfootball`** — открытые датасеты футбольных результатов (GitHub), хорошее дополнение к Football-Data.co.uk
3. **`statsbombpy`** — StatsBomb открытые данные (xG, события матча) — для H005 если решим использовать shot data
4. **`socceraction`** — VAEP и xT модели оценки действий игроков — перспективно для v0.2.0

---

*Документ подготовлен агентом `repo-scout` в рамках фазы исследования экосистемы. Пересматривать при добавлении новых источников данных или смене стека.*
