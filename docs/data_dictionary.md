# Словарь данных — Betting Analytics MVP

**Версия схемы:** v1  
**Дата:** 2026-05-26  
**Язык документации:** Русский

---

## Общие соглашения

| Соглашение | Описание |
|-----------|---------|
| `_utc` суффикс | Все timestamp-поля хранятся в UTC (ISO 8601 / Unix epoch) |
| `_id` суффикс | Строковый или UUID-идентификатор, уникальный в пределах сущности |
| `nullable` | Поле может содержать NULL, если источник не предоставил значение |
| Parquet типы | Используются Apache Arrow типы (Int64, Float64, Utf8, Bool, Timestamp[us, UTC]) |
| Партиционирование | Parquet-файлы партиционированы по `sport` и дате события |

---

## 1. `normalized_odds` — нормализованные котировки

**Слой:** Staging  
**Формат хранения:** Parquet  
**Путь:** `data/staging/normalized_odds/`  
**Обновление:** При каждом прогоне `qc-normalizer`  
**Описание:** Единая нормализованная таблица котировок из всех источников. Дубли устранены. Схема котировок унифицирована.

| Поле | Тип | Nullable | Описание | Ограничения |
|------|-----|----------|---------|-------------|
| `normalized_event_id` | `Utf8` | Нет | Уникальный идентификатор события. Формат: `{sport}_{league}_{home_team}_{away_team}_{event_date}`. Детерминированный хэш для дедупликации между источниками | Уникален в пределах (event, market, bookmaker, snapshot_ts_utc) |
| `sport` | `Utf8` | Нет | Вид спорта в нижнем регистре | Допустимые значения: `soccer`, `tennis`, `basketball`, `hockey`, `esports` |
| `league` | `Utf8` | Нет | Лига/турнир. Нормализованное название | Пример: `russia_premier_league`, `champions_league` |
| `bookmaker` | `Utf8` | Нет | Идентификатор букмекера | Допустимые: `fonbet`, `winline`, `betboom`, `olimpbet`, `bet365`, `pinnacle`, `betfair_ex` |
| `market_key` | `Utf8` | Нет | Тип рынка | Допустимые: `h2h`, `totals`, `spreads`, `btts`, `correct_score`, `asian_handicap` |
| `selection` | `Utf8` | Нет | Выбор внутри рынка | Пример: для `h2h` — `home`, `draw`, `away`; для `totals` — `over_2.5`, `under_2.5` |
| `odds_decimal` | `Float64` | Нет | Котировка в десятичном формате (европейский стандарт) | `>= 1.01`, `<= 1001.0` |
| `snapshot_ts_utc` | `Timestamp[us, UTC]` | Нет | Момент снятия снимка котировки (UTC) | Не может быть в будущем |
| `event_time_utc` | `Timestamp[us, UTC]` | Нет | Запланированное время начала события (UTC) | Должно быть >= snapshot_ts_utc для pre-match |
| `is_live` | `Bool` | Нет | Флаг: котировка снята во время события (live) | `true` если snapshot после kick-off |
| `raw_source` | `Utf8` | Нет | Идентификатор исходного файла в raw layer | Формат: `raw/odds/YYYY-MM-DD/snapshot_{uuid}.parquet` |
| `home_team` | `Utf8` | Нет | Нормализованное название домашней команды | |
| `away_team` | `Utf8` | Нет | Нормализованное название гостевой команды | |
| `qc_score` | `Float64` | Нет | Оценка качества данных от 0.0 до 1.0 | `0.0` = полностью отбракован, `1.0` = идеальное качество |
| `ingest_ts_utc` | `Timestamp[us, UTC]` | Нет | Момент загрузки записи в staging | |
| `odds_open` | `Float64` | Да | Котировка на открытие рынка (если доступна из источника) | nullable |
| `odds_closing` | `Float64` | Да | Котировка на закрытие (closing line) из референсного источника | nullable — заполняется ретроспективно |

---

## 2. `raw_odds_snapshot` — сырые снимки котировок

**Слой:** Raw  
**Формат хранения:** Parquet (сжатие zstd)  
**Путь:** `data/raw/odds/YYYY-MM-DD/snapshot_{uuid}.parquet`  
**Обновление:** Только добавление. Никогда не изменять и не удалять.  
**Описание:** Полный ответ API в структурированном виде. Хранится «как есть» для воспроизводимости.

| Поле | Тип | Nullable | Описание |
|------|-----|----------|---------|
| `snapshot_id` | `Utf8` | Нет | UUID снимка, уникален глобально |
| `source_api` | `Utf8` | Нет | Идентификатор API-источника: `the_odds_api`, `betfair_hist`, `sportsdataio`, `football_data_co_uk` |
| `fetched_at_utc` | `Timestamp[us, UTC]` | Нет | Точное время HTTP-запроса |
| `sport` | `Utf8` | Нет | Спорт согласно классификации источника |
| `event_id_raw` | `Utf8` | Нет | Оригинальный идентификатор события в источнике |
| `event_name_raw` | `Utf8` | Да | Оригинальное название события |
| `commence_time_raw` | `Utf8` | Да | Время начала события — сырая строка из API |
| `bookmaker_key_raw` | `Utf8` | Нет | Оригинальный ключ букмекера |
| `market_key_raw` | `Utf8` | Нет | Оригинальный ключ рынка |
| `outcomes_json` | `Utf8` | Нет | JSON-массив исходов: `[{"name": "...", "price": ...}]` |
| `api_response_hash` | `Utf8` | Нет | SHA-256 хэш полного JSON-ответа для дедупликации |
| `http_status` | `Int32` | Нет | HTTP-статус ответа |
| `remaining_requests` | `Int32` | Да | Остаток запросов по лимиту API (из заголовков ответа) |
| `raw_json_path` | `Utf8` | Да | Путь к полному JSON-файлу (если сохранён отдельно) |

---

## 3. `result_core` — результаты матчей

**Слой:** Core  
**Формат хранения:** Parquet  
**Путь:** `data/core/result_core/`  
**Описание:** Финальные результаты матчей, обогащённые из нескольких источников. Используется для расчёта P&L и оценки стратегий.

| Поле | Тип | Nullable | Описание | Ограничения |
|------|-----|----------|---------|-------------|
| `result_id` | `Utf8` | Нет | UUID записи результата | Уникален |
| `normalized_event_id` | `Utf8` | Нет | Связь с `normalized_odds` | FK |
| `sport` | `Utf8` | Нет | Вид спорта | |
| `league` | `Utf8` | Нет | Лига | |
| `home_team` | `Utf8` | Нет | Домашняя команда | |
| `away_team` | `Utf8` | Нет | Гостевая команда | |
| `event_time_utc` | `Timestamp[us, UTC]` | Нет | Плановое время начала | |
| `score_home_ft` | `Int32` | Да | Голы домашней команды (полное время) | nullable до завершения |
| `score_away_ft` | `Int32` | Да | Голы гостей (полное время) | nullable до завершения |
| `score_home_ht` | `Int32` | Да | Счёт на перерыве — домашние | nullable |
| `score_away_ht` | `Int32` | Да | Счёт на перерыве — гости | nullable |
| `result_1x2` | `Utf8` | Да | Итог: `home_win`, `draw`, `away_win` | nullable до завершения |
| `total_goals` | `Int32` | Да | Сумма голов (FT) | nullable |
| `status` | `Utf8` | Нет | Статус: `scheduled`, `in_play`, `finished`, `cancelled`, `postponed` | |
| `confirmed_at_utc` | `Timestamp[us, UTC]` | Да | Время подтверждения финального результата | nullable |
| `source` | `Utf8` | Нет | Источник результата: `football_data_co_uk`, `sportsdataio`, `manual` | |
| `extra_time` | `Bool` | Нет | Флаг: матч дошёл до дополнительного времени | |
| `penalties` | `Bool` | Нет | Флаг: серия пенальти | |
| `ingest_ts_utc` | `Timestamp[us, UTC]` | Нет | Время загрузки в core | |

---

## 4. `injury_event` — события травм/дисквалификаций

**Слой:** Staging  
**Формат хранения:** Parquet  
**Путь:** `data/staging/injury_event/`  
**Описание:** Нормализованные события изменения статуса игроков. Критически важно для стратегии H004 (illness_shock). Timestamp-aware мёрж обязателен.

| Поле | Тип | Nullable | Описание | Ограничения |
|------|-----|----------|---------|-------------|
| `injury_event_id` | `Utf8` | Нет | UUID события | Уникален |
| `player_id` | `Utf8` | Нет | Идентификатор игрока (нормализованный). Формат: `{source}_{source_player_id}` | |
| `player_name` | `Utf8` | Нет | Имя игрока (отображаемое) | |
| `team_id` | `Utf8` | Нет | Идентификатор команды | |
| `team_name` | `Utf8` | Нет | Название команды | |
| `normalized_event_id` | `Utf8` | Да | Связь с ближайшим предстоящим матчем команды | nullable — может быть неизвестен |
| `status` | `Utf8` | Нет | Статус игрока | Допустимые: `available`, `doubtful`, `out`, `suspended`, `returned` |
| `status_prev` | `Utf8` | Да | Предыдущий статус (для отслеживания изменений) | nullable при первом появлении |
| `injury_type` | `Utf8` | Да | Тип травмы/причина (если указана) | nullable |
| `tag` | `Utf8` | Нет | Семантический тег: `key_player_out`, `starter_doubt`, `gk_out`, `squad_rotation` | |
| `report_ts_utc` | `Timestamp[us, UTC]` | Нет | Время публикации сообщения/отчёта (UTC) | Ключевой для временно́й фильтрации |
| `source` | `Utf8` | Нет | Источник новости | |
| `source_quality` | `Utf8` | Нет | Уровень качества источника | Допустимые: `official_report`, `official_vendor`, `paid_vendor`, `news_scrape`, `social_signal` |
| `source_url` | `Utf8` | Да | URL исходного материала | nullable |
| `expected_minutes_proxy` | `Int32` | Да | Прокси ожидаемых минут участия в следующем матче (0 = точно не играет, 90 = полный матч) | 0–90, nullable |
| `confidence_score` | `Float64` | Нет | Уверенность в точности информации (0.0–1.0). Зависит от source_quality | `[0.0, 1.0]` |
| `raw_source_ref` | `Utf8` | Нет | Ссылка на raw-запись | |
| `is_late_breaking` | `Bool` | Нет | Флаг: новость поступила менее чем за 6 часов до матча | Критично для H004 |

**Маппинг source_quality → confidence_score:**

| source_quality | Типичный confidence |
|---------------|---------------------|
| `official_report` | 0.95–1.0 |
| `official_vendor` | 0.85–0.95 |
| `paid_vendor` | 0.70–0.85 |
| `news_scrape` | 0.50–0.70 |
| `social_signal` | 0.20–0.50 |

---

## 5. `signal_record` — записи торговых сигналов

**Слой:** Core  
**Формат хранения:** Parquet + SQLite (для live-запросов)  
**Путь:** `data/core/signal_record/`  
**Описание:** Каждая запись соответствует одному сгенерированному сигналу. Является центральной сущностью для отслеживания P&L и оценки качества стратегии.

| Поле | Тип | Nullable | Описание | Ограничения |
|------|-----|----------|---------|-------------|
| `signal_id` | `Utf8` | Нет | UUID сигнала | Уникален, глобально |
| `strategy_id` | `Utf8` | Нет | Идентификатор стратегии. Формат: `H001_v1.2.3` | Включает версию |
| `normalized_event_id` | `Utf8` | Нет | Связь с событием | FK → normalized_odds |
| `bookmaker` | `Utf8` | Нет | Букмекер, на которого направлен сигнал | |
| `market_key` | `Utf8` | Нет | Рынок | |
| `selection` | `Utf8` | Нет | Выбор внутри рынка | |
| `entry_odds` | `Float64` | Нет | Котировка на момент генерации сигнала (десятичная) | `>= 1.01` |
| `reference_fair_odds` | `Float64` | Нет | Справедливая котировка (fair odds), вычисленная devigger'ом из референсного рынка | `>= 1.01` |
| `edge_pct` | `Float64` | Нет | Оценочный край в процентах: `(entry_odds / reference_fair_odds - 1) * 100` | Обычно `> 0` для ставки |
| `clv_proxy_expected` | `Float64` | Да | Ожидаемый CLV (Closing Line Value) на основе исторической модели | nullable — рассчитывается не всеми стратегиями |
| `clv_actual` | `Float64` | Да | Фактический CLV после закрытия линии | nullable — заполняется ретроспективно |
| `status` | `Utf8` | Нет | Статус сигнала | Допустимые: `paper`, `live`, `cancelled`, `void`, `settled` |
| `confidence` | `Float64` | Нет | Уверенность модели в сигнале (0.0–1.0) | `[0.0, 1.0]` |
| `explain` | `Utf8` | Нет | JSON-массив строк с объяснением сигнала | Сериализованный `List[str]` |
| `timestamp_utc` | `Timestamp[us, UTC]` | Нет | Время генерации сигнала | |
| `event_time_utc` | `Timestamp[us, UTC]` | Нет | Время начала события | |
| `stake_units` | `Float64` | Нет | Размер ставки в единицах банкролла (flat = 1.0 для paper) | `> 0` |
| `outcome` | `Utf8` | Да | Исход после расчёта: `win`, `loss`, `push`, `void` | nullable до расчёта |
| `profit_units` | `Float64` | Да | Прибыль/убыток в единицах банкролла | nullable до расчёта |
| `settled_at_utc` | `Timestamp[us, UTC]` | Да | Время расчёта ставки | nullable |
| `backtest_run_id` | `Utf8` | Да | ID прогона бэктеста (если из бэктеста) | nullable для live-сигналов |

---

## 6. `backtest_trade` — сделки бэктеста

**Слой:** Core  
**Формат хранения:** Parquet  
**Путь:** `data/core/backtest_trade/`  
**Описание:** Каждая запись — одна «сделка» в рамках walk-forward бэктеста. Полностью воспроизводимо по `backtest_run_id`.

| Поле | Тип | Nullable | Описание | Ограничения |
|------|-----|----------|---------|-------------|
| `trade_id` | `Utf8` | Нет | UUID сделки | Уникален |
| `backtest_run_id` | `Utf8` | Нет | UUID прогона бэктеста — связывает все сделки одного прогона | FK → backtest_run |
| `strategy_id` | `Utf8` | Нет | Идентификатор стратегии с версией | |
| `dataset_version` | `Utf8` | Нет | Версия датасета (тег или дата снимка) | |
| `dataset_hash` | `Utf8` | Нет | SHA-256 хэш входного датасета | Для воспроизводимости |
| `normalized_event_id` | `Utf8` | Нет | Событие | |
| `window_type` | `Utf8` | Нет | Тип окна: `train` или `test` | Допустимые: `train`, `test` |
| `fold_number` | `Int32` | Нет | Номер фолда в walk-forward | `>= 1` |
| `bookmaker` | `Utf8` | Нет | Букмекер | |
| `market_key` | `Utf8` | Нет | Рынок | |
| `selection` | `Utf8` | Нет | Выбор | |
| `entry_odds` | `Float64` | Нет | Котировка входа (доступная в момент имитации ставки) | |
| `closing_odds` | `Float64` | Да | Котировка на закрытие (только для расчёта CLV, не для решения о ставке!) | nullable |
| `fair_odds_at_entry` | `Float64` | Нет | Fair odds на момент входа | |
| `edge_pct` | `Float64` | Нет | Край на момент входа | |
| `stake_units` | `Float64` | Нет | Размер ставки | |
| `outcome` | `Utf8` | Нет | Исход: `win`, `loss`, `push`, `void` | |
| `profit_units` | `Float64` | Нет | Прибыль/убыток | |
| `event_time_utc` | `Timestamp[us, UTC]` | Нет | Время события | |
| `bet_simulated_at_utc` | `Timestamp[us, UTC]` | Нет | Имитированное время ставки (строго до event_time_utc) | Обязательно < event_time_utc |
| `clv` | `Float64` | Да | Closing Line Value: `log(closing_odds / entry_odds)` | nullable если closing_odds недоступны |
| `injury_flags` | `Utf8` | Да | JSON: список тегов травм, учтённых при генерации сигнала | nullable |

---

## 7. `paper_ledger` — бумажный журнал

**Слой:** Core  
**Формат хранения:** SQLite + Parquet дамп  
**Путь:** `data/core/paper_ledger.db` / `data/core/paper_ledger/`  
**Описание:** Хронологический журнал всех бумажных ставок. Основа для ежедневного P&L-отчёта и Telegram-рассылки.

| Поле | Тип | Nullable | Описание | Ограничения |
|------|-----|----------|---------|-------------|
| `ledger_id` | `Utf8` | Нет | UUID записи журнала | Уникален |
| `signal_id` | `Utf8` | Нет | Связь с `signal_record` | FK |
| `strategy_id` | `Utf8` | Нет | Стратегия | |
| `entry_date_utc` | `Timestamp[us, UTC]` | Нет | Дата/время «открытия» бумажной ставки | |
| `event_time_utc` | `Timestamp[us, UTC]` | Нет | Время события | |
| `bookmaker` | `Utf8` | Нет | Букмекер | |
| `market_key` | `Utf8` | Нет | Рынок | |
| `selection` | `Utf8` | Нет | Выбор | |
| `entry_odds` | `Float64` | Нет | Котировка входа | |
| `stake_units` | `Float64` | Нет | Размер ставки в единицах | |
| `cumulative_bank_before` | `Float64` | Нет | Банкролл до ставки (в единицах) | |
| `outcome` | `Utf8` | Да | Исход: `win`, `loss`, `push`, `void`, `pending` | |
| `profit_units` | `Float64` | Да | Прибыль/убыток | nullable до расчёта |
| `cumulative_bank_after` | `Float64` | Да | Банкролл после расчёта | nullable до расчёта |
| `settled_at_utc` | `Timestamp[us, UTC]` | Да | Время расчёта | nullable |
| `notes` | `Utf8` | Да | Произвольные заметки | nullable |
| `clv_actual` | `Float64` | Да | Фактический CLV после закрытия линии | nullable |

---

## 8. Связи между сущностями (ER-диаграмма)

```
raw_odds_snapshot
       │  (raw_source)
       ▼
normalized_odds ◄──── injury_event
       │                  │
       │ (normalized_      │ (normalized_
       │  event_id)        │  event_id)
       ▼                  ▼
  signal_record ◄──── backtest_trade
       │
       │ (signal_id)
       ▼
  paper_ledger

result_core ──► (normalized_event_id) ──► signal_record (settlement)
```

---

## 9. Индексы и партиционирование

| Таблица | Партиционирование | Ключевые индексы |
|---------|-----------------|-----------------|
| `normalized_odds` | `sport`, `event_date` | `normalized_event_id`, `bookmaker`, `snapshot_ts_utc` |
| `raw_odds_snapshot` | `source_api`, `fetched_date` | `snapshot_id`, `api_response_hash` |
| `result_core` | `sport`, `event_date` | `normalized_event_id`, `status` |
| `injury_event` | `team_id`, `report_date` | `player_id`, `report_ts_utc`, `is_late_breaking` |
| `signal_record` | `strategy_id`, `signal_date` | `signal_id`, `status`, `timestamp_utc` |
| `backtest_trade` | `backtest_run_id`, `fold_number` | `trade_id`, `dataset_hash` |
| `paper_ledger` | `entry_year_month` | `ledger_id`, `signal_id`, `outcome` |

---

*Документ является нормативным источником схем данных для всех агентов системы. При изменении схемы — обновить версию (`schema_version`) и зафиксировать в CHANGELOG.*
