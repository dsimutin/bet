# RUFLO FULL AUDIT — Sports Betting Analytics Bot
**Дата:** 2026-06-02  
**Ветка аудита:** audit/ruflo-full-verification (основана на all-the-best)  
**Аудитор:** Claude Code (claude-sonnet-4-6) через Ruflo-совместимый multi-agent pipeline

---

## ИТОГОВЫЙ ВЕРДИКТ

```
CONDITIONALLY READY FOR PAPER TRADING
```

Система функциональна в офлайн-режиме. Для полноценного paper trading требуется:
1. `THE_ODDS_API_KEY` — для live coэффициентов
2. `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — для уведомлений
3. Деплой на Render с `ACTIVE_MODE=true`

---

## 1. Что реально работает (проверено запуском)

| Компонент | Статус | Доказательство |
|---|---|---|
| Установка (`pip install -e ".[dev]"`) | РАБОТАЕТ | Успешно |
| Все unit/integration тесты | 291/291 PASSED | `pytest tests/ -q` |
| Offline smoke test | РАБОТАЕТ | `python -m src.models.run_daily_bot_smoke` → сигнал + ledger + Telegram dry-run payload |
| Dixon-Coles model (fit + predict) | РАБОТАЕТ | 96% coverage, тесты проходят |
| Calibrator (isotonic regression) | РАБОТАЕТ | 90% coverage |
| Model registry (save/load/promote) | РАБОТАЕТ | 92% coverage |
| Quality gate (Brier/log-loss vs baseline) | РАБОТАЕТ | 89% coverage |
| Devigging (multiplicative + power) | РАБОТАЕТ | 7 тестов в test_devig.py |
| Signal ledger (add/settle/persist) | РАБОТАЕТ | 7 тестов в test_signal_ledger.py |
| Settlement | РАБОТАЕТ | 3 теста в test_settle_signal_ledger.py |
| Telegram dry-run sender | РАБОТАЕТ | Payload генерируется без реального запроса |
| Anti-leakage: closing odds только post-factum | РАБОТАЕТ | CLV расчёт использует closing_odds; entry signal не использует |
| Anti-leakage: illness filter_by_cutoff | РАБОТАЕТ (в тестах) | 7 тестов в test_no_lookahead.py |
| Walk-forward fold разбиение без overlap | РАБОТАЕТ | test_backtester_fold_dates_never_overlap |
| APScheduler (active mode) | РАБОТАЕТ (после добавления зависимости) | Была P1: отсутствовала в pyproject.toml |
| FastAPI /health endpoint | РАБОТАЕТ | Тесты в test_telegram_delivery.py |
| Dataset hash (SHA-256) | РАБОТАЕТ | Все сигналы содержат dataset_hash |
| Paper trading flag (нельзя отключить) | РАБОТАЕТ | paper_trading_only=False в BacktestConfig поднимает ValueError |

---

## 2. Что не реализовано или реализовано частично

| Компонент | Статус | Описание |
|---|---|---|
| Данные о травмах игроков | NOT_IMPLEMENTED | IllnessFeatureBuilder реализован, но нет реального источника данных. Нет apifootball_injuries.py |
| Illness → signal probability | NOT_IMPLEMENTED | Признаки травм никогда не вызываются из production pipeline (illness_features=None) |
| Tennis ELO model | NOT_IMPLEMENTED | Нет src/models/tennis_elo.py, нет surface-specific логики |
| Tennis signal generation | NOT_IMPLEMENTED | Нет src/signals/tennis_signal_scan.py |
| Tennis settlement | NOT_IMPLEMENTED | src/models/settle_tennis_signals.py существует, но без ELO модели нет сигналов |
| src/backtest/walk_forward.py | PARTIALLY_IMPLEMENTED | Модуль работает (CLI), но нет данных для запуска без реальных CSV |
| Cron entrypoints (0% coverage) | UNTESTED | run_settle.py, run_trainer.py не покрыты тестами |
| RPL (Российская Премьер-Лига) | NOT_CONFIGURED | В CLAUDE.md есть, в configs/markets.yaml — нет активного источника |
| Live Telegram MTProto collector | UNVERIFIED_EXTERNAL_DEPENDENCY | Требует TELEGRAM_API_ID/HASH/SESSION_STR |

---

## 3. Найденные и исправленные проблемы

### P1 — Серьёзные (ИСПРАВЛЕНЫ)

| # | Проблема | Файл | Исправление | Тест |
|---|---|---|---|---|
| 1 | `apscheduler` отсутствовал в `pyproject.toml` | pyproject.toml:46 | Добавлена зависимость `apscheduler>=3.10.0` | test_scheduler_enabled_when_active_mode_true |
| 2 | `src/backtest/run_backtest.py` — 0% coverage | tests/ | Создан tests/test_backtest_core.py (14 тестов) | 14/14 passed |
| 3 | `src/backtest/walk_forward.py` — 0% coverage | tests/ | Добавлены тесты _brier_log_loss + fold integrity | 3/3 passed |

### P2 — Важные (задокументированы, не исправлены — требуют external API)

| # | Проблема | Файл | Требуемое действие |
|---|---|---|---|
| 4 | Illness pipeline не интегрирован в production path | src/signals/run_signal_scan.py:551 | Добавить реальный источник (apifootball.com / transfermarkt) + вызов IllnessFeatureBuilder.filter_by_cutoff() |
| 5 | Tennis полностью не реализован | src/ingest/active_sports_resolver.py:235 | Реализовать tennis_elo.py + tennis_signal_scan.py или убрать из active_sports_resolver |
| 6 | `src/cron/run_settle.py` — 0% coverage | tests/ | Нужны тесты для cron entrypoints |
| 7 | `data/models/*.pkl` в .gitignore — противоречивые строки | .gitignore | Уже корректно: последняя строка `data/models/*.pkl` игнорирует pkl. Но есть строка `!data/models/*.pkl` которая создаёт путаницу |

### P3 — Улучшения

| # | Проблема |
|---|---|
| 8 | `StarleteDeprecationWarning` в тестах — нужен httpx2 |
| 9 | DixonColes convergence warnings в тестах (маленький датасет) — ожидаемое поведение на синтетических данных |

---

## 4. Математическая проверка

### Devigging
- Multiplicative метод: корректен. overround = sum(1/p_i), fair_prob = p_i/overround
- Power/Shin метод: корректен. Бинарный поиск k в [0.5, 5.0], 64 итерации
- Оба метода возвращают сумму fair_probs ≈ 1.0

### Dixon-Coles
- Реализован low-score correction (tau функция для 0-0, 1-0, 0-1, 1-1)
- Time decay через экспоненциальное взвешивание (xi=0.0018, ~1.3 года половина жизни)
- Оптимизация: L-BFGS-B, maxiter=200, ftol=1e-8
- При несходимости: warnings.warn() + продолжает с лучшей найденной точкой (не падает)
- partial_fit: дедупликация по (match_date, home_team, away_team)
- dataset_hash: SHA-256 от нормализованного DataFrame

### Calibrator
- Isotonic regression через sklearn
- Используется как post-hoc вероятностная калибровка
- Calibrator сохраняется рядом с моделью в registry

### Quality Gate (Brier)
- Сравнение кандидата с market_implied baseline
- Режим "all": все кандидаты должны превосходить baseline
- Режим "any": достаточно одного
- n_predictions threshold: 100 (корректно, достаточный объём)
- Примечание: используется мультикласс Brier через binary decomposition (H/D/A отдельно), не multiclass. Технически это корректно для оценки каждого исхода отдельно.

### CLV
- Формула: ((entry_odds / closing_odds) - 1) * 100
- Closing odds НЕ используются при генерации сигнала (проверено grep и тестом)
- CLV рассчитывается только post-factum после settlement

### Kelly
- Fractional Kelly с дефолтом 0.25 (quarter-Kelly)
- max_stake_units = 2.0 (защита от oversizing)
- При отрицательном Kelly → stake = 0 (фильтрация в apply_risk_filters)

---

## 5. Anti-leakage проверка

| Сценарий | Проверен | Тест |
|---|---|---|
| Closing odds как input для сигнала | НЕТ (grep подтвердил) | test_backtest_entry_odds_from_pre_close_column |
| Injury data после cutoff_ts | НЕТ утечки в isolation | test_illness_filter_blocks_future_events |
| Walk-forward: train/test не пересекаются | ДА | test_walk_forward_no_future_data, test_backtester_fold_dates_never_overlap |
| Signal timestamp < event_start | Тест есть | test_signal_timestamp_before_match |
| DixonColes params не содержат closing cols | ДА | test_backtest_entry_odds_from_pre_close_column |

**Критическое замечание:** Illness features существуют только в тестах. В production `illness_features=None` всегда. Это значит anti-leakage guard для травм проверен только на классе, но не в реальном pipeline.

---

## 6. Состояние игроков

| Аспект | Статус | Детали |
|---|---|---|
| Реальный источник данных | NOT_IMPLEMENTED | Нет apifootball_injuries.py или аналога |
| IllnessEvent модель | IMPLEMENTED | src/features/illness_features.py — полная Pydantic схема |
| IllnessFeatureBuilder.filter_by_cutoff | IMPLEMENTED (не вызывается) | Метод корректен, но нигде не вызывается из production |
| Интеграция в вероятностную модель | NOT_IMPLEMENTED | Dixon-Coles не получает признаки травм |
| Интеграция как risk filter | NOT_IMPLEMENTED | apply_risk_filters проверяет illness_features only if not None |
| Автоматический ingest | NOT_IMPLEMENTED | Нет cron задачи для загрузки травм |

**Вывод:** Illness pipeline — scaffolding. Состояние игроков не влияет на сигналы ни в качестве вероятностного признака, ни в качестве фильтра риска.

---

## 7. End-to-end команды

```bash
# Офлайн smoke (РАБОТАЕТ)
python -m src.models.run_daily_bot_smoke --output-dir data/reports/smoke

# Полный тест-сюит (РАБОТАЕТ)
python -m pytest tests/ -q

# С покрытием (61% overall → 64% после исправлений)
python -m pytest tests/ --cov=src --cov-report=term-missing -q

# Конкретные anti-leakage тесты
python -m pytest tests/test_no_lookahead.py -v

# Новые backtest тесты
python -m pytest tests/test_backtest_core.py -v
```

---

## 8. Внешние блокеры (UNVERIFIED_EXTERNAL_DEPENDENCY)

| Зависимость | Env var | Статус | Инструкция проверки |
|---|---|---|---|
| The Odds API | THE_ODDS_API_KEY | Не проверен | `curl "https://api.the-odds-api.com/v4/sports?apiKey=YOUR_KEY"` |
| Telegram Bot | TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID | Не проверен (dry-run проверен) | `python -m src.cron.run_telegram_test` с реальными токенами |
| PostgreSQL | DATABASE_URL | Не проверен (SQLite fallback работает) | Автоматически инжектируется Render |
| Render disk | /data mount | Не проверен | Проверить в Render Dashboard после деплоя |
| Odds API IO (tennis) | ODDS_API_IO_KEY | Не проверен | Добавить ключ и запустить tennis scan |

---

## 9. Оставшиеся задачи по приоритету

### Блокеры для полного paper trading (P1)
1. **Добавить THE_ODDS_API_KEY** на Render → проверить live signal scan
2. **Добавить TELEGRAM_BOT_TOKEN/CHAT_ID** → проверить реальную доставку

### Важные улучшения (P2)
3. **Illness data source**: Интегрировать публичный API (apifootball.com бесплатный tier или transfermarkt) → вызвать IllnessFeatureBuilder.filter_by_cutoff() перед генерацией сигнала
4. **Cron тесты**: Добавить тесты для `src/cron/run_settle.py` (0% coverage)
5. **Tennis**: Либо реализовать tennis_elo.py + tennis_signal_scan.py, либо явно отключить tennis в active_sports_resolver

### Технический долг (P3)
6. Убрать дублирующую строку `!data/models/*.pkl` из .gitignore
7. Обновить httpx → httpx2 для FastAPI TestClient
8. Добавить тесты для `src/cron/run_trainer.py` (49% coverage)
