# NEXT ACTIONS (по приоритету)

## P1 — Обязательно для полноценного paper trading

### 1. Добавить API ключи и деплоить на Render
```bash
# В Render Dashboard → Environment Variables:
THE_ODDS_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
ACTIVE_MODE=true
```
Проверить: `curl https://your-app.onrender.com/health/active`

### 2. Первый реальный signal scan
```bash
THE_ODDS_API_KEY=your_key python -m src.cron.run_signals
```
Проверить что: сигнал содержит реальные матчи, ledger сохранён, Telegram получил уведомление.

## P2 — Важные улучшения

### 3. Интегрировать источник данных о травмах

Создать `src/ingest/apifootball_injuries.py`:
```python
# GET https://v3.football.api-sports.io/injuries?fixture={id}
# Сохранять в data/injuries/YYYY-MM-DD_injuries.json как IllnessEvent[]
```
Добавить вызов в `src/signals/run_signal_scan.py` перед `generate_signal()`.

### 4. Добавить тесты для cron/run_settle.py (0% coverage)

Необходимые тесты:
- Settlement с пустым ledger
- Settlement с несколькими open сигналами
- Идемпотентность (повторный settlement)
- Запись settlement report

### 5. Прояснить статус Tennis

Опция A: Реализовать `src/models/tennis_elo.py` с surface-specific прайорами  
Опция B: Удалить `tennis_atp`/`tennis_wta` из `active_sports_resolver.py` строки 235-236

## P3 — Технический долг

### 6. Убрать противоречивую строку в .gitignore
Удалить строку `!data/models/*.pkl` (строка ~22), оставить только запрещающую `data/models/*.pkl`.

### 7. Обновить httpx2 для FastAPI тестов
```bash
pip install httpx2
```
Устранит `StarletteDeprecationWarning` в test_telegram_delivery.py.

### 8. Увеличить coverage для walk_forward.py
Текущий: 25%. Нужны тесты для `run_walk_forward()` с синтетическими CSV-файлами.
