# UNVERIFIED EXTERNAL DEPENDENCIES

Эти зависимости не могут быть проверены в изолированной среде без ключей/конфигурации.

## EXT-001: The Odds API

- **Env var:** `THE_ODDS_API_KEY`
- **Используется в:** `src/ingest/odds_api.py`, `src/ingest/live_odds_adapter.py`
- **Влияние при отсутствии:** Pipeline использует только исторические staging CSV. Live сигналы не генерируются.
- **Проверка после настройки:**
  ```bash
  THE_ODDS_API_KEY=your_key python -m src.cron.run_signals
  ```
- **Известные ограничения:** 500 запросов/месяц на бесплатном тарифе. Quota monitor реализован в `src/monitoring/api_quota_monitor.py`.

## EXT-002: Telegram Bot

- **Env vars:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- **Используется в:** `src/integrations/telegram_sender.py`, `src/cron/run_signals.py`
- **Влияние при отсутствии:** Dry-run режим — сообщения печатаются в stdout. РАБОТАЕТ.
- **Проверка после настройки:**
  ```bash
  TELEGRAM_BOT_TOKEN=your_token TELEGRAM_CHAT_ID=your_chat_id python -m src.cron.run_telegram_test
  ```

## EXT-003: PostgreSQL (Render)

- **Env var:** `DATABASE_URL`
- **Используется в:** `src/infrastructure/render_db.py`
- **Влияние при отсутствии:** SQLite fallback в `data/core/metadata.db`. Функциональность сохранена.
- **Проверка:** Автоматически инжектируется Render при наличии `bet-db` в render.yaml.

## EXT-004: Render Persistent Disk

- **Mount path:** `/data`
- **Размер:** 10 GB
- **Влияние при отсутствии:** Ledger, модели, staging данные не сохраняются между рестартами.
- **Проверка:** `curl https://your-render-app.onrender.com/health/disk`

## EXT-005: Odds API IO (tennis)

- **Env var:** `ODDS_API_IO_KEY`
- **Используется в:** `src/ingest/oddsapiio_tennis.py`
- **Влияние при отсутствии:** Tennis сигналы не генерируются (tennis модель также не реализована — см. F-005).
- **Примечание:** Tennis pipeline помечен как NOT_IMPLEMENTED в этом аудите.
