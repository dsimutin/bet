# Sports Betting Analytics MVP

Аналитическая платформа для поиска ценности в ставках на спорт через легальных российских букмекеров.

> **Status:** ✅ Production Ready | **Tests:** 331/331 passing | **API Quota:** 417/500/month (safe)

---

## 📖 Documentation Quick Links

| Document | Purpose |
|----------|---------|
| **[DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)** | 🚀 Deploy to Render (step-by-step) |
| **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** | ⚡ Daily operations & troubleshooting |
| **[RELEASE_AUDIT_2026-06-02.md](RELEASE_AUDIT_2026-06-02.md)** | 📋 Full audit report (11 sections) |
| **[scripts/health_check.py](scripts/health_check.py)** | 🏥 Pre-deployment verification |

---

## ⚠️ ВАЖНЫЙ ДИСКЛЕЙМЕР — ПРОЧТИТЕ ПРЕЖДЕ ВСЕГО

> **Только бумажная торговля (Paper Trading).**
> Данный проект является аналитическим инструментом и **не размещает ставки автоматически**.
> Все результаты — симуляция на исторических и синтетических данных.
>
> **Не является инвестиционным советом.**
> Проект не гарантирует прибыльность и не несёт ответственности за финансовые решения пользователя.
> Ставки на спорт связаны с риском потери средств.
>
> **Только легальные российские букмекеры.**
> Целевые книги (Фонбет, Винлайн, БетБум и др.) — исключительно лицензированные операторы РФ (лицензия ФНС).
> Зарубежные источники (Pinnacle, Betfair) используются **только** для построения справедливой линии и аналитики — без размещения ставок.
>
> **Никакой автоматизации ставок.**
> Интеграция с API букмекеров предназначена только для сбора котировок.
> Автоматическое размещение ставок через API или веб-скрапинг противоречит условиям использования букмекеров и не реализовано в проекте.

---

## Что делает проект

- **Сбор котировок** — агрегирует коэффициенты с легальных российских букмекеров и справочных источников через The Odds API
- **Нормализация и девиггирование** — удаляет маржу из котировок для получения справедливых вероятностей
- **Построение справочной линии** — рассчитывает среднерыночную справедливую линию (market average devigged) как эталон
- **Поиск ценности** — сравнивает котировки целевых БК со справедливой линией и выявляет преимущество (edge)
- **Бэктестирование стратегий** — воспроизводит торговые решения на исторических данных с защитой от look-ahead bias
- **Генерация сигналов** — формирует аналитические сигналы по активным стратегиям для предстоящих матчей
- **Уведомления** — отправляет форматированные сигналы в Telegram (только для ознакомления, не торговые команды)

## Что проект НЕ делает

- **Не размещает ставки автоматически** — ни через API, ни через браузерную автоматизацию
- **Не обходит ограничения букмекеров** — соблюдает `robots.txt`, не эмулирует пользовательское поведение
- **Не предоставляет доступ к нелегальным платформам** — не содержит интеграций с зарубежными БК для размещения ставок

---

## Быстрый старт

### 1. Установка зависимостей

```bash
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt
# Для разработки дополнительно:
pip install -r requirements-dev.txt
```

### 2. Настройка окружения

```bash
cp .env.example .env
# Отредактируйте .env — добавьте ключи API (необязательно для демо)
```

### 3. Запуск демо-пайплайна (без API-ключей)

```bash
python demo_pipeline.py
```

Демо работает полностью офлайн на синтетических данных и сохраняет результаты в `data/reports/`.

### 4. Запуск тестов

```bash
pytest                      # Все тесты
pytest -m unit              # Только юнит-тесты
pytest --cov=src            # С покрытием кода
```

### 5. Historical value model

Модель обучается на исторических CSV в формате football-data.co.uk, калибрует
рыночные вероятности по прошлым матчам и проверяет paper-trading сигналы на
walk-forward окнах, включая последние 7 и 30 дней.

```bash
uv run --extra dev python -m src.models.run_historical_value_model \
  --input data/raw/football_data/E0/2425/data.csv \
  --output-dir data/reports \
  --bookmaker-prefix B365 \
  --min-edge-pct 2.0 \
  --min-signal-probability 0.45
```

Результаты сохраняются в `historical_value_model_report.json` и
`historical_value_model_report.md`. Это исследовательский baseline, не гарантия
прибыльности и не механизм реальных ставок.

Чтобы получить Telegram-ready paper-сигналы по текущим/ручным линиям, передайте
отдельный CSV с колонками `Date`, `HomeTeam`, `AwayTeam`, `B365H`, `B365D`,
`B365A`:

```bash
uv run --extra dev python -m src.models.run_historical_value_model \
  --input data/raw/football_data/E0/2425/data.csv \
  --upcoming-input data/manual/upcoming_odds.csv \
  --output-dir data/reports \
  --min-edge-pct 2.0 \
  --min-signal-probability 0.45 \
  --consensus \
  --telegram-payload
```

Историю можно собрать сразу по нескольким лигам football-data.co.uk без ручной
подготовки файлов:

```bash
uv run --extra dev python -m src.models.run_historical_value_model \
  --download-football-data \
  --leagues E0,SP1,D1,I1,F1 \
  --seasons 2122,2223,2324,2425,2526 \
  --upcoming-input data/manual/upcoming_odds.csv \
  --output-dir data/reports \
  --consensus \
  --telegram-payload
```

С флагом `--consensus` сигнал попадёт в Telegram payload только если две
независимые модели согласны по исходу:

- historical market calibration;
- Poisson team-strength model по голам и силе команд.

Команда сохранит `consensus_signals_YYYYMMDD.json` и dry-run Telegram payload.
По умолчанию включён quality gate: если validation на последних 30 днях не даёт
минимальный win-rate/ROI/размер выборки, список сигналов будет пустым, а причина
сохранится в `consensus_quality_gate.json`.

Для цели "меньше сигналов, но выше процент сбывания" включайте строгий профиль:

```bash
uv run --extra dev python -m src.models.run_historical_value_model \
  --download-football-data \
  --leagues E0,SP1,D1,I1,F1 \
  --seasons 2122,2223,2324,2425,2526 \
  --upcoming-input data/manual/upcoming_odds.csv \
  --output-dir data/reports \
  --consensus \
  --high-hit-mode \
  --telegram-payload
```

`--high-hit-mode` поднимает минимальную вероятность сигнала до 58%, требует
минимум 55% win-rate на свежем validation-окне и ограничивает входные
коэффициенты сверху `1.85` через `--max-entry-odds`. Такой режим специально
может возвращать пустой список: это означает, что на текущих данных нет
достаточно сильного сигнала для Telegram.

В безопасном `run_signal_pipeline` дополнительно включён
`--auto-high-hit-profile`: перед доставкой он перебирает исторические пороги
`min_probability / max_odds / min_edge`, выбирает профиль с целевым hit-rate
на walk-forward данных и сохраняет `high_hit_profile_report.json`. Если
`--require-auto-high-hit-profile` включён и ни один профиль не достиг цели,
доставка блокируется. Цель можно менять флагами `--target-hit-rate` и
`--min-profile-bets`.

Чтобы брать предстоящие матчи и реальные коэффициенты напрямую из The Odds API,
используйте безопасный pipeline: он сначала строит свежий probability benchmark,
закрывает старые open-сигналы результатами из football-data, а потом запускает
live/current сигналы с model-quality и ledger-quality gates:

```bash
uv run --extra dev python -m src.models.run_signal_pipeline \
  --leagues E0,SP1,D1,I1,F1 \
  --seasons 2122,2223,2324,2425,2526 \
  --live-odds \
  --live-sport-keys soccer_epl,soccer_spain_la_liga,soccer_germany_bundesliga \
  --odds-regions eu,uk \
  --preferred-bookmakers bet365,pinnacle \
  --output-dir data/reports \
  --ledger-path data/core/paper_signal_ledger.json \
  --min-ledger-settled 20 \
  --min-ledger-win-rate 0.55 \
  --min-ledger-roi-pct 0.0 \
  --telegram-payload
```

Pipeline сохраняет `model_probability_benchmark.json`, `football_data_combined.csv`,
raw live odds, upcoming CSV, quality-gate отчёты, сигналы и ledger. По умолчанию
он также вызывает settlement перед новой доставкой, чтобы `ledger-quality gate`
смотрел на уже закрытые исходы. Отчёт settlement сохраняется в
`signal_ledger_settlement_report.json`. Для исследовательских прогонов settlement
можно выключить флагом `--no-settle-ledger` или указать отдельный файл
результатов через `--settle-results-input`. Для реальной отправки замените
`--telegram-payload` на `--send-telegram`; без этого создаётся только dry-run
payload.

Если платный/live API не нужен, можно складывать бесплатные источники в inbox
`data/staging/free_sources/`: CSV/JSON из ручных таблиц, Telegram export JSON,
Discord JSONL/NDJSON или обычные TXT-блоки из каналов и календарей событий.
Для структурированных файлов поддерживаются колонки `date`, `home_team`,
`away_team`, `home_odds`, `draw_odds`, `away_odds` или уже football-data-имена
`Date`, `HomeTeam`, `AwayTeam`, `B365H`, `B365D`, `B365A`. Дополнительно можно
передать `source_channel`, `bookmaker`, `source_event_id`.

Для сырых сообщений достаточно блока вида:

```text
29/05/2026
Arsenal vs Chelsea
1X2: 1.90 3.40 4.20
```

```bash
uv run --extra dev python -m src.models.run_signal_pipeline \
  --leagues E0 \
  --seasons 2122,2223,2324,2425,2526 \
  --free-source-inbox data/staging/free_sources \
  --production-dixon-coles \
  --output-dir data/reports \
  --ledger-path data/core/paper_signal_ledger.json \
  --telegram-payload
```

Загрузчик нормализует эти строки в общий формат, объединяет их с ручным
`--upcoming-input` или live odds, сохраняет `free_source_inbox_report.json` и
дальше прогоняет тот же quality-gated signal/ledger/Telegram контур.
Если источник публичный и не содержит приватных сообщений, такие `.csv`,
`.json`, `.jsonl`, `.ndjson` и `.txt` файлы можно коммитить в
`data/staging/free_sources/`, чтобы scheduled workflow видел их без API.
Для автоматического обновления inbox без ручного коммита добавьте публичные URL
или локальные exports в `configs/free_sources.yaml`; scheduled workflow перед
сканированием запустит `src.ingest.run_free_source_ingest`, сложит свежие файлы
в `data/staging/free_sources/` и сохранит `free_source_ingest_report.json`.

Каждый найденный сигнал записывается в `paper_signal_ledger.json`. Повторный
запуск с тем же `signal_id` не отправит дубль в Telegram; для отладки это можно
переопределить флагом `--allow-duplicate-signals`. Ledger хранит open/settled
статусы, P&L, CLV и summary-метрики, чтобы проверять реальный процент
сбывания после завершения матчей.

`--require-ledger-quality` включает предохранитель на реальной истории уже
доставленных сигналов. Пока закрыто меньше `--min-ledger-settled` сигналов,
работает warmup-режим; после накопления выборки Telegram delivery блокируется,
если фактические `win_rate` или `roi_pct` ниже заданных порогов. Отчёт
сохраняется в `ledger_delivery_quality_gate.json`.

`--require-model-quality` использует последний
`model_probability_benchmark.json` как допуск моделей. Для `--consensus` по
умолчанию проверяются именно модели текущей стратегии:
`historical_calibration` и `poisson_team_strength`; с `--model-quality-mode all`
обе должны побеждать `market_implied` по Brier/log loss на свежем
walk-forward окне. Если нужно исследовать другой набор, используйте
`--model-quality-candidates historical_calibration,dixon_coles_time_decay` и
`--model-quality-mode any|all`. Для исследовательских прогонов можно поставить
`--model-quality-scope overall`, но для бота лучше держать свежую проверку.
Отчёт сохраняется в `model_delivery_quality_gate.json`.

Когда матчи завершились, ledger можно закрыть результатами из football-data CSV:

```bash
uv run --extra dev python -m src.models.settle_signal_ledger \
  --ledger-path data/core/paper_signal_ledger.json \
  --results-input data/reports/football_data_combined.csv
```

После этого summary внутри ledger покажет фактические `win_rate`, `roi_pct`,
число открытых и закрытых сигналов.

Перед допуском модели в Telegram полезно сравнить качество вероятностей на
walk-forward окнах. Benchmark показывает Brier score, log loss и top-1 accuracy
для market baseline, historical calibration, Poisson и Dixon-Coles/time-decay:

```bash
uv run --extra dev python -m src.models.run_model_benchmark \
  --download-football-data \
  --leagues E0,SP1,D1,I1,F1 \
  --seasons 2122,2223,2324,2425,2526 \
  --output-dir data/reports \
  --min-train-matches 120 \
  --dixon-coles-max-iterations 80
```

Сохраняются `model_probability_benchmark.json` и
`model_probability_benchmark.md`. Для точности ниже лучше: модель должна
побеждать рынок по Brier/log loss на walk-forward данных, иначе её нельзя
считать преимуществом для Telegram-сигналов.

### Production Dixon-Coles model

Для настоящего value betting добавлен production-слой модели, независимый от
рыночного edge:

- `src/models/dixon_coles.py` — Dixon-Coles параметры, time decay,
  `partial_fit`, `predict_1x2`, `predict_ou`, `predict_btts`, `dataset_hash`.
- `src/models/calibrator.py` — multiclass Platt-style калибровка вероятностей.
- `src/models/model_registry.py` — версии моделей в `data/models/`, metadata и
  promotion в production; рядом с моделью сохраняется `calibration_<model_id>.pkl`.
- `src/models/trainer.py` и `src/models/run_daily_trainer.py` — ежедневное
  обучение/дообучение и leakage-free OOS Brier validation: validation-модель
  обучается только на матчах до holdout-окна, а production-модель после этого
  обучается на полном cutoff-наборе.
- `src/models/predictor.py` — сравнение `model_prob` против bookmaker odds и
  devigged market probability: это уже model value, а не арбитраж.

Пример обучения из football-data:

```bash
uv run --extra dev python -m src.models.run_daily_trainer \
  --league EPL \
  --download-football-data \
  --football-data-leagues E0 \
  --seasons 2122,2223,2324,2425,2526 \
  --model-dir data/models \
  --output-dir data/reports
```

Альтернатива из ветки `admiring-thompson`, оставленная в основном контуре:
OpenFootball GitHub raw. Это бесплатные реальные результаты матчей без API-ключа
и без букмекерских котировок, поэтому источник подходит для обучения
Dixon-Coles и закрытия paper ledger, но не заменяет live/upcoming odds:

```bash
uv run --extra dev python -m src.models.run_daily_trainer \
  --league EPL \
  --download-openfootball \
  --openfootball-leagues EPL \
  --openfootball-seasons 2021-22,2022-23,2023-24,2024-25 \
  --model-dir data/models \
  --output-dir data/reports
```

`src/ingest/openfootball.py` сохраняет кэш в `data/raw/openfootball/` и пишет
совместимый `data/reports/openfootball_combined.csv` с колонками
`Date/HomeTeam/AwayTeam/FTHG/FTAG/FTR`. В `run_signal_pipeline` можно включить
обучение production-модели от OpenFootball через
`--production-train-openfootball`; сам benchmark рынка всё ещё использует
football-data, потому что там есть historical odds.

Модель сохраняет `.pkl` и `.meta.json` с `model_id`, `trained_on`,
`dataset_hash`, `brier_score`, статусом версии, числом матчей и ссылкой на
calibration sidecar. В signal layer используется `ModelValuePredictor` с
загруженным calibrator: сигнал допустим только когда откалиброванный
`model_prob` даёт `edge_vs_fair` выше порога и проходит минимальную уверенность.

Чтобы live/current pipeline использовал production Dixon-Coles вместо
рыночной calibration/consensus логики, добавьте флаг `--production-dixon-coles`:

```bash
uv run --extra dev python -m src.models.run_signal_pipeline \
  --leagues E0 \
  --seasons 2122,2223,2324,2425,2526 \
  --live-odds \
  --live-sport-keys soccer_epl \
  --production-dixon-coles \
  --production-model-dir data/models \
  --production-league EPL \
  --telegram-payload
```

В этом режиме сигнал строится как `model_prob - fair_market_probability`, а в
payload/ledger попадает `model_id`, `market_probability`,
`fair_market_probability`, `edge_vs_market_pct`, `edge_vs_fair_pct` и
`paper_stake_units`. Размер бумажной ставки считается консервативным fractional
Kelly от откалиброванной вероятности и ограничен сверху, чтобы даже сильный
edge не разгонял paper exposure.
По умолчанию `run_signal_pipeline` перед production-сигналами также запускает
`run_daily_trainer` на свежем `football_data_combined.csv`; это можно отключить
через `--no-train-production-model`, если в `data/models` уже лежит нужная
production-версия. Trainer промоутит модель только если она проходит абсолютный
OOS Brier gate `--max-brier-score` (по умолчанию `0.60`) и улучшает текущую
production-версию; иначе версия сохраняется как `candidate` и не используется
для Telegram-сигналов. Если production-версии нет, signal step не падает без
объяснения: он сохраняет `production_model_quality_gate.json` с причиной
`no_promoted_production_model` и отдаёт пустой список сигналов.

Для реальной отправки в Telegram нужны `TELEGRAM_BOT_TOKEN` и
`TELEGRAM_CHAT_ID` в `.env`, а также явный флаг `--send-telegram`. Без него
создаётся только dry-run payload для проверки текста сообщения.

Для быстрой офлайн-проверки полного дневного контура без API и без реального
Telegram можно запустить smoke runner:

```bash
uv run --extra dev python -m src.models.run_daily_bot_smoke \
  --output-dir data/reports/smoke
```

Он создаёт synthetic history/upcoming fixtures, обучает модель, генерирует
paper-сигнал с `paper_stake_units`, предварительно закрывает старый open-сигнал
по synthetic результатам, пишет `paper_signal_ledger.json` и сохраняет dry-run
Telegram payload. Это быстрый sanity check перед включением scheduled delivery.

### Automation

Render Cron владеет регулярными production-запусками:

- `daily-trainer` — 06:00 UTC обучает/промоутит Dixon-Coles production-модель.
- `signal-pipeline` — 08:15 UTC генерирует paper-сигналы.
- `settle-ledger` — 23:00 UTC закрывает open-сигналы результатами матчей.

GitHub Actions остаются для CI, manual/emergency запусков и артефактов:

- `CI` — на каждый push/PR проверяет тесты, типы и форматирование.
- `Daily Model Trainer` — `workflow_dispatch`/`workflow_call`, без daily schedule,
  чтобы не дублировать Render Cron и не писать модели конкурентно.
- `Model Benchmark` — раз в неделю и вручную строит walk-forward benchmark на
  football-data и сохраняет отчёты как artifact.
- `Live Signal Pipeline` — `workflow_dispatch` и integration push, без daily
  schedule; использует live odds только при заданном `THE_ODDS_API_KEY`, иначе
  может работать с `data/staging/free_sources/*.csv|*.json|*.jsonl|*.ndjson|*.txt`.
- `Wake Render free service` — точечно будит Render перед cron-окнами. Постоянный
  14-минутный keep-alive отключён; `Keep Render Alive` оставлен manual-only.

Для реальной доставки из GitHub Actions добавьте `TELEGRAM_BOT_TOKEN` и
`TELEGRAM_CHAT_ID`; `THE_ODDS_API_KEY` нужен только для live odds, а free-source
inbox может работать без него. Затем запустите `Live Signal Pipeline` вручную с
`send_telegram=true`. GitHub manual/emergency запуск по умолчанию остаётся
dry-run: он сохраняет артефакты и ledger, но не отправляет сообщения без явного
ручного разрешения. Чтобы GitHub manual/emergency workflow действительно
отправлял сигналы, добавьте Repository Variable `SCHEDULED_SEND_TELEGRAM=true`;
workflow всё равно потребует Telegram secrets и продолжит писать paper ledger
перед отправкой.
Production-модели, calibration sidecar, `paper_signal_ledger.json` и ключевые
quality-gate отчёты коммитятся обратно в репозиторий, поэтому следующий
scheduled run стартует с накопленной памятью, а не с пустого checkout.

---

## Структура проекта

```
bet/
├── src/                        # Основной исходный код
│   ├── ingest/                 # Загрузка данных из источников
│   │   ├── odds_api.py         # Клиент The Odds API
│   │   ├── football_data_co_uk.py  # Исторические результаты
│   │   └── openfootball.py     # Бесплатные реальные результаты с GitHub raw
│   ├── normalize/              # Нормализация и девиггирование котировок
│   ├── features/               # Инженерия признаков
│   │   └── illness_features.py # Признаки травм/дисквалификаций (с защитой от look-ahead bias)
│   ├── signals/                # Генерация торговых сигналов
│   ├── backtest/               # Движок бэктестирования
│   ├── reporting/              # Формирование отчётов
│   └── integrations/           # Внешние интеграции (Telegram и др.)
├── configs/                    # YAML-конфигурации
│   ├── books.yaml              # Целевые и справочные букмекеры
│   ├── markets.yaml            # Конфигурация рынков ставок
│   ├── sports.yaml             # Виды спорта и лиги
│   ├── sources.yaml            # Источники данных
│   └── strategy_rules.yaml     # Параметры стратегий
├── data/                       # Данные (не в git)
│   ├── raw/                    # Сырые снимки из API
│   ├── staging/                # Промежуточные обработанные данные
│   ├── core/                   # Финальный слой аналитики
│   └── reports/                # Сформированные отчёты и сигналы
├── tests/                      # Тесты
├── notebooks/                  # Jupyter-ноутбуки для исследований
├── docs/                       # Документация
├── demo_pipeline.py            # Демо-пайплайн (без API-ключей)
├── requirements.txt            # Основные зависимости
├── requirements-dev.txt        # Зависимости для разработки
├── pyproject.toml              # Конфигурация проекта и инструментов
└── .env.example                # Шаблон переменных окружения
```

---

## Ключевые концепции

### Девиггирование (Devigging)

Букмекеры закладывают в котировки маржу (вигор / juice), из-за чего сумма подразумеваемых вероятностей превышает 100%. Девиггирование нормализует вероятности к сумме 1.0, получая «справедливую» оценку.

Пример: котировки 1X2 = 1.80 / 3.60 / 4.20 → сумма = 1/1.80 + 1/3.60 + 1/4.20 ≈ 1.068 (маржа ~6.8%) → после девиггирования вероятности в сумме дают 1.00.

### Справочная линия (Reference Line)

Среднерыночная справедливая линия, рассчитанная по девиггированным котировкам эффективных рынков (Pinnacle, Betfair). Используется как эталон для оценки преимущества над котировками российских БК.

### Ценность и преимущество (Edge)

`edge = target_fair_odds / reference_fair_odds - 1`

Если edge > 1.5% — потенциальная ценность обнаружена. Система логирует событие для бумажной торговли.

### Ценность закрывающей линии (CLV — Closing Line Value)

Метрика качества ставки: насколько котировка в момент входа была лучше закрывающей линии рынка. Положительный CLV в долгосрочной перспективе коррелирует с прибыльностью.

### Защита от заглядывания в будущее (Look-ahead Bias)

Критический принцип бэктестирования: любые данные (травмы, новости, составы) фильтруются строгим временны́м барьером `cutoff_ts = время начала матча`. Использование событий с `report_ts >= cutoff_ts` делает бэктест недействительным.

---

## Описание стратегий

### 1. `ref_value_soccer_1x2` — Ценность относительно справочной линии
Сравнивает котировки российских БК со среднерыночной справедливой линией. Фиксирует сигнал при `edge >= 1.5%` и `margin <= 6%`. **Активна.**

### 2. `closing_line_timing_soccer` — Тайминг по закрывающей линии
Аналитический трекер: снимает котировки в 6 временны́х окнах (от 7 дней до 5 минут до матча) и вычисляет CLV. Используется для калибровки других стратегий. **Активна (аналитический режим).**

### 3. `illness_shock_soccer` — Реакция на травмы/болезни
Отслеживает новости о статусе ключевых игроков и ищет окна, когда рынок ещё не отреагировал на изменение. **Отключена** до подключения надёжного фида травм.

### 4. `favorite_longshot_soccer` — Смещение фаворит-аутсайдер
Децильный анализ системного ценового смещения у российских БК. Ищет постоянно переоценённые или недооценённые сегменты вероятностей. **Активна (аналитический режим).**

---

## Тестирование

```bash
# Запуск всех тестов
pytest

# Только быстрые юнит-тесты (без API)
pytest -m unit

# С отчётом о покрытии
pytest --cov=src --cov-report=html
open htmlcov/index.html

# Конкретный модуль
pytest tests/test_illness_features.py -v
```

---

## Участие в разработке

1. Создайте ветку от `main`: `git checkout -b feature/your-feature`
2. Следуйте стилю кода: `black src/ tests/` и `isort src/ tests/`
3. Проверьте типы: `mypy src/`
4. Убедитесь, что тесты проходят: `pytest`
5. Откройте Pull Request с описанием изменений

**Важно для новых модулей:**
- Все временны́е данные хранить в UTC
- При работе с данными о событиях всегда применять `filter_by_cutoff` перед передачей в модель
- Новые стратегии добавлять с `paper_trading_only: true` по умолчанию

---

## Правовые аспекты и соответствие требованиям

### Лицензирование букмекеров
Все целевые операторы (Фонбет, Винлайн, БетБум, Олимпбет, Пари, Лига Ставок) работают на основании лицензий, выданных Федеральной налоговой службой РФ в рамках саморегулируемых организаций (СРО) согласно Федеральному закону № 244-ФЗ.

### Сбор данных
- Уважаем `robots.txt` всех источников
- Соблюдаем задержки между запросами (не менее 3 секунд для скрапинга)
- Используем официальные API там, где они доступны (The Odds API)
- Не обходим системы защиты от автоматизации

### Зарубежные источники
Pinnacle, Betfair Exchange и аналогичные площадки используются **исключительно** в аналитических целях через агрегаторы данных. Прямой доступ к этим ресурсам из РФ заблокирован Роскомнадзором, и проект не предоставляет инструментов для его обхода.

### Ответственная игра
Если ставки на спорт причиняют вред — обратитесь на горячую линию помощи зависимым: **8-800-700-44-51** (бесплатно по России).

---

## Render Deployment

### Архитектура на Render

```
Render Web Service  — src/web/health_app.py
  GET /health              → liveness probe (Render healthCheckPath)
  GET /health/readiness    → deep readiness: model, ledger, staging
  GET /health/model        → last model Brier score + age
  GET /health/ledger       → settlement stats
  GET /health/drift        → CUSUM drift status
  GET /health/disk         → persistent disk usage
  GET /health/all          → all checks combined

Render Cron: daily-trainer  06:00 UTC → src/cron/run_trainer.py
Render Cron: signal-pipeline 08:15 UTC → src/cron/run_signals.py
Render Cron: settle-ledger  23:00 UTC → src/cron/run_settle.py

Render PostgreSQL — metadata, model versions, cron run log (audit)
Render Persistent Disk /data — models (.pkl), ledger (.json), staging CSVs
```

### Обязательные env vars на Render

| Переменная | Обязательна | Назначение |
|---|---|---|
| `DATA_DIR` | Да | `/data` (Persistent Disk) |
| `MODEL_DIR` | Да | `/data/models` |
| `LEDGER_PATH` | Да | `/data/core/paper_signal_ledger.json` |
| `STAGING_DIR` | Да | `/data/staging` |
| `REPORTS_DIR` | Да | `/data/reports` |
| `DATABASE_URL` | Авто (Render) | PostgreSQL connection string |
| `TELEGRAM_BOT_TOKEN` | Нет | Уведомления (без — dry-run stdout) |
| `TELEGRAM_CHAT_ID` | Нет | Chat/channel для уведомлений |
| `THE_ODDS_API_KEY` | Нет | Live odds (без — staged data only) |
| `LEAGUES` | Нет | `EPL,BUNDESLIGA,LALIGA,SERIEA` |
| `PAPER_TRADING_ONLY` | Нет | `true` (всегда) |

### Ручной запуск cron jobs

```bash
# Обучение модели
python -m src.cron.run_trainer

# Генерация сигналов
python -m src.cron.run_signals

# Settlement + drift check
python -m src.cron.run_settle
```

### Проверка готовности к релизу

```bash
uv sync --extra dev
uv run --extra dev pytest -q
uv run --extra dev mypy src
uv run --extra dev black --check src tests
python scripts/health_check.py
```

После деплоя:

```bash
curl https://your-app.onrender.com/health
curl https://your-app.onrender.com/ready
curl -H "Authorization: Bearer $ADMIN_API_TOKEN" https://your-app.onrender.com/health/all
curl -H "Authorization: Bearer $ADMIN_API_TOKEN" https://your-app.onrender.com/health/canary
```

`/health/canary` is a read-only production canary: it checks runtime imports,
production config, ledger authority, model artifacts, Telegram config, quota
thresholds and recent reports without spending Odds API quota or writing ledger
entries. Telegram pick cards include a Trust Cockpit score, so each signal shows
timestamp/freshness/market trust context rather than raw edge alone.

### Telegram уведомления — проверка

```bash
# Dry-run (без токена, выводит в stdout)
python -m src.cron.run_signals

# Реальная отправка (с токеном)
TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=yyy python -m src.cron.run_signals
```

## Production Paper Pilot Constraints

- `PAPER_TRADING_ONLY=true` is mandatory. The app must not place real bets.
- Production ledger authority is Supabase/PostgreSQL. Local JSON is a diagnostic mirror only.
- Public liveness is only `GET /health`; deep health, trigger, debug, and webhook setup
  routes require `Authorization: Bearer $ADMIN_API_TOKEN`.
- Telegram webhook requests must include `X-Telegram-Bot-Api-Secret-Token`.
- Tennis spreads/totals are disabled by default and remain experimental.
- Injuries are informational-only until calibrated and backtested.
- Free Render is development / limited paper pilot infrastructure without SLA.

See `docs/PRODUCTION_READINESS.md`, `docs/DEPLOYMENT.md`,
`docs/LEDGER_MIGRATION.md`, and `docs/MODEL_LIMITATIONS.md`.

---

## Data Sources

| Источник | Тип | Данные | Статус |
|---|---|---|---|
| OpenFootball | Open-source GitHub | Исторические результаты | ✅ Включён |
| football-data.co.uk | Публичный CSV | Результаты + коэффициенты | ✅ Включён |
| The Odds API | Commercial API | Live + historical odds | ✅ Включён (ключ нужен) |
| Telegram channels | MTProto (Telethon) | Live odds/tips | ✅ Включён (session нужна) |
| Manual CSV | ManualCsvProvider | Ручной импорт | ✅ Включён |
| **Flashscore** | **Scraping** | **—** | **🚫 DISABLED** |

### Flashscore — почему отключён

Прямой scraping `flashscorekz.com` и любых Flashscore-сайтов **запрещён** их ToS и `robots.txt`.
Провайдер `FlashscoreProvider` реализован как disabled-заглушка — вызов `.fetch()` бросает `ProviderDisabledError` с чётким сообщением и перечнем легальных альтернатив.

Если потребуется данные Flashscore — единственный легальный путь: лицензионный API (если появится) или ручной CSV-экспорт через `ManualCsvProvider`.

```bash
# Убедиться, что provider disabled:
python -c "from src.ingest.providers import FlashscoreProvider; print(FlashscoreProvider().enabled)"
# → False
```
