# CLAUDE.md — Sports Betting Analytics MVP
> Манифест навыков и спецификация проекта для Ruflo multi-agent orchestration

---

## 1. Обзор проекта

**Название:** Sports Betting Analytics MVP  
**Цель:** Аналитическая платформа для поиска ценности (value) в ставках на спорт через легальных российских букмекеров. Исключительно **бумажная торговля (paper trading)** — реальные ставки не размещаются.

| Параметр | Значение |
|---|---|
| Регион | Россия, только легальные букмекеры |
| Режим | Paper trading only — реальных ставок нет |
| Стек | Python 3.11+, pandas, polars, parquet, SQLite, pydantic v2, pytest |
| Оркестрация | Ruflo multi-agent (MCP) |
| Формат данных | Parquet (staging), SQLite (ledger), JSON (signals) |

**Ключевые ограничения:**
- **ЗАПРЕЩЕНО:** автоматическое размещение ставок
- **ЗАПРЕЩЕНО:** нарушение ToS букмекеров (scraping без разрешения, создание ботов для ставок)
- **ЗАПРЕЩЕНО:** использование инсайдерской информации
- Все операции — аналитические и образовательные

---

## 2. Реестр агентов

| Агент | Model Tier | Основная ответственность |
|---|---|---|
| `odds-ingest` | haiku | Загрузка коэффициентов с разрешённых источников (football-data.co.uk, The Odds API) |
| `illness-news` | haiku | Сбор данных о травмах, дисквалификациях и составах команд |
| `qc-normalizer` | haiku | Нормализация имён команд, QC коэффициентов, devigging, расчёт implied probability |
| `backtester` | sonnet | Walk-forward бэктест стратегий, расчёт CLV, метрики ROI/RoI/Kelly |
| `modeler` | opus | Вероятностные модели (Dixon-Coles, Poisson), байесовская оценка гипотез |
| `signal-engine` | sonnet | Генерация сигналов value bet, фильтрация по edge threshold |
| `reporter` | haiku | Markdown отчёты, ежедневный дайджест, таблицы метрик |
| `telegram-bot` | haiku | Telegram уведомления в dry-run режиме (без реальной отправки в prod) |
| `repo-scout` | sonnet | Исследование open-source инструментов (pinnacle-fair-odds, betfair-datascientists и др.) |

---

## 3. Skills (slash-команды)

### `/scan-signals`
**Описание:** Дневной скан рынков для поиска value bets  
**Trigger:** Ежедневно в 10:00 UTC или вручную  
**Pipeline агентов:** `odds-ingest` → `qc-normalizer` → `signal-engine` → `telegram-bot`

```yaml
input:
  date: "today"            # YYYY-MM-DD (UTC)
  leagues:                 # список лиг из configs/markets.yaml
    - "EPL"
    - "Bundesliga"
    - "RPL"                # Российская Премьер-Лига

output:
  signals_json: "data/signals/YYYY-MM-DD_signals.json"
  telegram_dry_run: true   # уведомление в stdout, не в реальный Telegram

model: sonnet
```

**Формат выходного сигнала:**
```json
{
  "signal_id": "uuid4",
  "match_id": "string",
  "market": "1X2 | BTTS | O/U",
  "selection": "H | D | A | Yes | No | Over | Under",
  "edge_pct": 0.04,
  "model_prob": 0.52,
  "market_prob": 0.48,
  "book_odds": 2.10,
  "recommended_stake_kelly_fraction": 0.5,
  "generated_at_utc": "ISO8601",
  "dataset_hash": "sha256"
}
```

---

### `/run-backtest`
**Описание:** Walk-forward бэктест стратегии по историческим данным  
**Trigger:** Вручную или автоматически после загрузки новых данных  
**Pipeline агентов:** `qc-normalizer` → `backtester` → `reporter`

```yaml
input:
  strategy_id: "string"    # ID из configs/strategy_rules.yaml
  date_range:
    start: "YYYY-MM-DD"
    end: "YYYY-MM-DD"
  league: "string"         # из configs/markets.yaml
  fold_size_weeks: 4       # размер тестового окна walk-forward

output:
  metrics_json: "data/reports/backtest_STRATEGY_ID.json"
  report_md: "data/reports/backtest_STRATEGY_ID_YYYY-MM-DD.md"

models:
  backtester: sonnet
  reporter: haiku
```

**BacktestMetrics schema (pydantic):**
```python
class BacktestMetrics(BaseModel):
    strategy_id: str
    date_range: tuple[date, date]
    total_bets: int
    roi_pct: float
    clv_mean: float          # Closing Line Value
    sharpe_ratio: float
    max_drawdown_pct: float
    significance_p: float    # p-value (H0: ROI <= 0)
    dataset_hash: str        # воспроизводимость
    folds: list[FoldResult]
```

---

### `/build-report`
**Описание:** Ежедневный Markdown отчёт по сигналам и бумажному леджеру  
**Trigger:** Ежедневно после `/scan-signals`  
**Pipeline агентов:** `reporter`

```yaml
input:
  signals_file: "data/signals/YYYY-MM-DD_signals.json"
  ledger_db: "data/paper_ledger.sqlite"

output:
  report: "data/reports/YYYY-MM-DD_report.md"

model: haiku
```

**Структура отчёта:**
- Сводка дня: количество сигналов, средний edge, лиги
- Таблица активных сигналов
- P&L бумажного леджера: YTD, последние 30 дней, последние 7 дней
- Топ-5 стратегий по ROI
- Предупреждения по качеству данных (QC flags)

---

### `/ingest-odds`
**Описание:** Загрузка и нормализация исторических / live коэффициентов  
**Trigger:** Вручную или по расписанию  
**Pipeline агентов:** `odds-ingest` → `qc-normalizer`

```yaml
input:
  sport: "football"        # football | tennis | basketball
  league: "string"         # EPL, Bundesliga, RPL, ...
  date_range:
    start: "YYYY-MM-DD"
    end: "YYYY-MM-DD"
  source: "football-data-co-uk | odds-api"

output:
  parquet_files: "data/staging/{league}/{YYYY-MM}/*.parquet"
  qc_report: "data/staging/{league}/qc_YYYY-MM-DD.json"

model: haiku
```

**Источники (только разрешённые):**
- `football-data.co.uk` — исторические данные, публичный CSV
- `The Odds API` — агрегатор, API key required
- Прямой scraping букмекеров — **ЗАПРЕЩЁН**

---

### `/check-injuries`
**Описание:** Мониторинг травм и составов перед матчами  
**Trigger:** За 4 часа до матча или ежедневно в 09:00 UTC  
**Pipeline агентов:** `illness-news` → `signal-engine` (обновление risk filter)

```yaml
input:
  matches:                 # список предстоящих матчей
    - match_id: "string"
      kickoff_utc: "ISO8601"
      home_team: "string"
      away_team: "string"

output:
  injury_events: "data/injuries/YYYY-MM-DD_injuries.json"

model: haiku
```

> **КРИТИЧНО — Anti-leakage:** Данные о травмах используются ТОЛЬКО если они стали публично известны **ДО** времени ставки (`bet_cutoff_utc`). Любые данные после cutoff = **ЗАПРЕЩЕНЫ** в бэктесте и в сигналах.

**Injury event schema:**
```json
{
  "player_id": "string",
  "team": "string",
  "match_id": "string",
  "injury_type": "string",
  "published_at_utc": "ISO8601",
  "bet_cutoff_utc": "ISO8601",
  "is_pre_cutoff": true,
  "source_url": "string",
  "source_quality": 0.85
}
```

---

### `/validate-strategy`
**Описание:** Глубокая статистическая валидация гипотезы  
**Trigger:** Вручную перед запуском стратегии в paper trading  
**Pipeline агентов:** `backtester` → `modeler` → `reporter`

```yaml
input:
  hypothesis_id: "string"  # из docs/hypotheses.md

output:
  validation_report: "data/reports/validation_{hypothesis_id}.md"

models:
  modeler: opus
  backtester: sonnet
  reporter: haiku
```

**Включает:**
- In-sample / Out-of-sample split (80/20)
- CLV analysis (Closing Line Value как прокси качества)
- Significance test (bootstrap p-value)
- Overfitting check (IS vs OOS ROI gap)
- Рекомендация: запускать / отклонить / требует доработки

---

### `/memory-search`
**Описание:** Поиск по персистентной памяти агентов (Ruflo memory store)  
**Trigger:** Вручную или вызов из другого агента

```yaml
input:
  query: "string"
  namespace: "signals | backtest | odds_patterns | strategies"
  top_k: 10
  filters:
    date_from: "YYYY-MM-DD"   # опционально
    league: "string"           # опционально

output:
  results: список релевантных записей с score схожести
```

---

### `/status`
**Описание:** Текущий статус системы и бумажного леджера  
**Trigger:** Вручную по запросу

**Выводит:**
- Бумажный P&L: YTD, MTD, WTD (units и %)
- Активные стратегии: ID, статус, ROI за последние 30 дней
- Data quality scores по лигам (last ingest)
- Последний запуск `/scan-signals`: timestamp, количество сигналов
- Статус агентов Ruflo
- Использование памяти по namespace

---

## 4. Memory Namespaces

Ruflo сохраняет состояние агентов в четырёх namespace'ах:

| Namespace | Содержимое | Ключ поиска |
|---|---|---|
| `signals` | Все сгенерированные сигналы с исходами (resolved после матча) | `signal_id`, `match_id`, `strategy_id`, `date` |
| `backtest` | Результаты бэктестов: метрики per strategy per date range | `strategy_id`, `date_range`, `league`, `dataset_hash` |
| `odds_patterns` | Повторяющиеся паттерны движения линий: steam moves, reverse line movement, sharp action | `pattern_type`, `league`, `book`, `date` |
| `strategies` | Статус валидации гипотез: pending / validated / rejected / monitoring | `hypothesis_id`, `status`, `validation_date` |

Инициализация памяти: `npx ruflo@latest memory init`  
Просмотр: `npx ruflo@latest memory list --namespace signals`

---

## 5. Anti-Leakage Rules (критически важно)

```
╔══════════════════════════════════════════════════════════════════╗
║              ПРАВИЛА ПРОТИВ УТЕЧКИ ДАННЫХ (DATA LEAKAGE)        ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  1. CLOSING ODDS — ЗАПРЕЩЕНЫ как input для решения о ставке.    ║
║     Используются ТОЛЬКО для расчёта CLV post-factum.            ║
║                                                                  ║
║  2. ТРАВМЫ/СОСТАВЫ: данные, ставшие известными ПОСЛЕ            ║
║     bet_cutoff_utc — АБСОЛЮТНО ЗАПРЕЩЕНЫ в бэктесте и           ║
║     в генерации сигналов.                                        ║
║                                                                  ║
║  3. WALK-FORWARD: тестовый fold не имеет доступа ни к           ║
║     каким данным из будущего обучающей выборки.                  ║
║     Обучение строго до test_start_date.                          ║
║                                                                  ║
║  4. ВРЕМЕННЫЕ МЕТКИ: все timestamps в UTC. Локальное время       ║
║     не используется нигде в pipeline.                            ║
║                                                                  ║
║  5. ВОСПРОИЗВОДИМОСТЬ: каждый backtest и каждый сигнал          ║
║     должен содержать dataset_hash (SHA-256 входных данных).      ║
║     Без hash — результат считается невалидным.                   ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
```

Тест на утечку: `pytest tests/test_no_lookahead.py -v`

---

## 6. Cost Routing Rules

Выбор модели определяется сложностью задачи:

| Задача | Агент | Модель | Обоснование |
|---|---|---|---|
| Нормализация имён команд | `qc-normalizer` | haiku | Простое сопоставление строк |
| QC коэффициентов | `qc-normalizer` | haiku | Детерминированные проверки |
| Генерация отчётов | `reporter` | haiku | Шаблонный текст по данным |
| Telegram уведомления | `telegram-bot` | haiku | Форматирование сообщений |
| Загрузка данных | `odds-ingest`, `illness-news` | haiku | I/O операции |
| Генерация сигналов | `signal-engine` | sonnet | Многошаговая логика |
| Walk-forward бэктест | `backtester` | sonnet | Численные вычисления |
| Исследование репозиториев | `repo-scout` | sonnet | Анализ кода |
| Вероятностные модели | `modeler` | opus | Сложный статистический вывод |
| Валидация гипотез | `modeler` | opus | Байесовский анализ |

**Принцип:** haiku для I/O и форматирования, sonnet для анализа, opus только для моделирования.

---

## 7. Установка и запуск

```bash
# 1. Установка Ruflo
npx ruflo@latest init

# 2. Регистрация MCP-сервера в Claude Code
claude mcp add ruflo -- npx ruflo@latest mcp start

# 3. Инициализация памяти агентов
npx ruflo@latest memory init

# 4. Настройка окружения
cp .env.example .env
# Добавить в .env:
#   ODDS_API_KEY=your_key_here
#   TELEGRAM_BOT_TOKEN=your_token_here (опционально, для dry-run не нужен)
#   TELEGRAM_CHAT_ID=your_chat_id_here

# 5. Установка зависимостей Python
pip install -e ".[dev]"

# 6. Запуск демо-пайплайна (API ключи не нужны)
python demo_pipeline.py

# 7. Запуск тестов
pytest tests/ -v -m unit

# 8. Первый скан сигналов
/scan-signals

# 9. Построить отчёт
/build-report
```

**Проверка anti-leakage тестов:**
```bash
pytest tests/test_no_lookahead.py tests/test_signal_validation.py -v
```

---

## 8. Ключевые файлы проекта

```
bet/
├── CLAUDE.md                          # ← этот файл: манифест для Ruflo/Claude Code
├── demo_pipeline.py                   # демо без API ключей
├── pyproject.toml                     # зависимости и конфигурация инструментов
│
├── configs/
│   ├── books.yaml                     # легальные российские букмекеры и их источники данных
│   ├── markets.yaml                   # лиги, рынки, идентификаторы
│   ├── sources.yaml                   # конфигурация источников данных
│   ├── sports.yaml                    # виды спорта и правила нормализации
│   └── strategy_rules.yaml            # определения торговых стратегий
│
├── src/
│   ├── ingest/
│   │   ├── football_data_co_uk.py     # загрузчик с football-data.co.uk (CSV)
│   │   └── odds_api.py                # клиент The Odds API
│   ├── normalize/
│   │   ├── odds_normalizer.py         # devigging, implied prob, Kelly
│   │   ├── qc_report.py               # QC проверки и флаги качества данных
│   │   └── team_names.py              # канонизация имён команд
│   ├── features/
│   │   ├── clv_features.py            # Closing Line Value расчёт
│   │   └── illness_features.py        # признаки травм с anti-leakage guard
│   ├── backtest/
│   │   └── run_backtest.py            # walk-forward бэктест движок
│   ├── signals/
│   │   └── run_signal_scan.py         # генерация сигналов value bet
│   ├── reporting/
│   │   └── build_daily_report.py      # построение ежедневного Markdown отчёта
│   └── integrations/
│       └── telegram_sender.py         # Telegram dry-run отправитель
│
├── data/
│   ├── raw/                           # сырые данные от источников (не изменяются)
│   ├── staging/                       # нормализованные parquet файлы
│   ├── signals/                       # JSON сигналы по датам
│   ├── reports/                       # Markdown отчёты и JSON метрики
│   └── paper_ledger.sqlite            # бумажный леджер ставок
│
├── docs/
│   ├── architecture.md                # архитектура системы
│   ├── backtest_methodology.md        # методология бэктеста и anti-leakage
│   ├── data_dictionary.md             # словарь данных и схемы
│   ├── hypotheses.md                  # реестр торговых гипотез
│   └── open_source_landscape.md       # обзор open-source инструментов
│
├── tests/
│   ├── test_no_lookahead.py           # тесты на утечку данных из будущего
│   ├── test_signal_validation.py      # валидация схемы сигналов
│   ├── test_devig.py                  # тесты devigging алгоритмов
│   └── test_clv.py                    # тесты расчёта CLV
│
└── notebooks/                         # Jupyter ноутбуки для исследований
```

---

## 9. Глоссарий

| Термин | Определение |
|---|---|
| **Value bet** | Ставка, где model_prob > market_prob (edge > 0) |
| **CLV** | Closing Line Value — разница между котировкой в момент ставки и closing odds |
| **Devigging** | Удаление маржи букмекера для получения fair probability |
| **Walk-forward** | Метод бэктеста: обучение на прошлом, тест на будущем, сдвиг окна |
| **Edge** | Преимущество модели над рынком в процентных пунктах |
| **Kelly fraction** | Оптимальный размер ставки по критерию Келли (используем 0.25-0.5 Kelly) |
| **Dataset hash** | SHA-256 от входного parquet файла для воспроизводимости |
| **Paper trading** | Симулированные ставки без реальных денег |
| **Dry-run** | Режим выполнения без внешних side effects (нет реальных API вызовов) |

---

*Последнее обновление: 2026-05-26 | Версия проекта: 0.1.0*
