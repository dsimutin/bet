# FIX LOG — Ruflo Full Audit

## Fix 1: apscheduler добавлен в pyproject.toml [P1]

**Проблема:** `apscheduler>=3.10.0` присутствовал в `requirements-render.txt`, но отсутствовал в `pyproject.toml`. Чистая установка через `pip install -e ".[dev]"` не устанавливала пакет. Тест `test_scheduler_enabled_when_active_mode_true` падал с `ModuleNotFoundError: No module named 'apscheduler'`.

**Исправление:** `pyproject.toml` строка 46 — добавлена зависимость `"apscheduler>=3.10.0"` в `[project.dependencies]`.

**Тест-регрессия:** `tests/test_telegram_delivery.py::TestSchedulerStates::test_scheduler_enabled_when_active_mode_true` — PASSED.

---

## Fix 2: Тесты для src/backtest/run_backtest.py [P1]

**Проблема:** `src/backtest/run_backtest.py` имел 0% test coverage. Модуль содержит BacktestConfig (с pydantic-валидацией), WalkForwardBacktester (walk-forward engine), BacktestTrade, BacktestMetrics — всё критично для бумажного бэктеста.

**Исправление:** Создан `tests/test_backtest_core.py` с 14 тестами:
- `test_config_paper_trading_only_cannot_be_false` — нельзя отключить paper trading
- `test_config_end_date_must_be_after_start` — валидация дат
- `test_config_valid` — корректная конфигурация
- `test_backtester_missing_odds_column_raises` — валидация входных данных
- `test_backtester_missing_results_column_raises` — валидация входных данных
- `test_backtester_run_returns_trades_and_metrics` — основной smoke
- `test_backtester_dataset_hash_reproducible` — воспроизводимость
- `test_backtester_high_edge_threshold_produces_no_trades` — пустой результат при высоком пороге
- `test_backtester_fold_dates_never_overlap` — **anti-leakage**: фолды не пересекаются
- `test_backtest_trade_schema` — схема объекта сделки
- `test_walk_forward_brier_log_loss_helper` — Brier score корректен
- `test_walk_forward_brier_perfect_prediction` — идеальный прогноз
- `test_walk_forward_brier_random_prediction` — случайное угадывание
- `test_walk_forward_empty_input` — пустые данные

**Coverage:** `src/backtest/run_backtest.py` 0% → 60%.

**Тест-регрессия:** 14/14 PASSED.

---

## Статус полного тест-сюита

| Метрика | До | После |
|---|---|---|
| Число тестов | 277 | 291 |
| Провалы | 1 (apscheduler) | 0 |
| Coverage (src) | 61% | 64% |
| Backtest coverage | 0% | 60% |

**Команда верификации:**
```bash
python -m pytest tests/ -q
# 291 passed in ~42s
```
