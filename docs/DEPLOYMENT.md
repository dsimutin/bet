# Deployment

Render free tier is suitable only for development and a limited paper pilot without
SLA. Reliable production scheduling requires a paid always-on service or an external
reliable scheduler.

## Required Render Env Vars

- `APP_ENV=production`
- `DATABASE_URL`
- `REQUIRE_DATABASE=true`
- `ALLOW_LOCAL_LEDGER_FALLBACK=false`
- `WRITE_LOCAL_LEDGER_MIRROR=true`
- `THE_ODDS_API_KEY`
- `ADMIN_API_TOKEN`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_WEBHOOK_SECRET`
- `TELEGRAM_ALLOWED_CHAT_IDS`
- `PAPER_TRADING_ONLY=true`
- `QUOTA_STATE_BACKEND=database`

## Quota Safety

Use these defaults unless a provider plan changes:

- `CACHE_STORAGE_TTL_SECONDS=28800`
- `PRIORITY_ODDS_MAX_AGE_SECONDS=900`
- `WATCHLIST_ODDS_MAX_AGE_SECONDS=3600`
- `EXOTIC_WATCHLIST_ODDS_MAX_AGE_SECONDS=14400`
- `THE_ODDS_API_MIN_REMAINING_HARD_STOP=25`
- `THE_ODDS_API_MIN_REMAINING_PRIORITY_REFRESH=50`
- `QUOTA_STATE_BACKEND=database`

Quota counters and provider `x-requests-*` headers are persisted in the metadata
database. `QUOTA_STATE_BACKEND=local_json` is only a development/debug fallback;
do not use it for production paper runs because it can reset quota state.

## First Pilot Check

1. Deploy with all required env vars.
2. Verify public liveness: `GET /health`.
3. Verify readiness: `GET /ready`.
4. Register Telegram webhook through protected `POST /webhook/telegram/setup`.
5. Run protected `POST /trigger` only after quota and readiness are acceptable.
6. Confirm settlement with the scheduled settlement job or `src.cron.run_settle`.

Do not configure real-bet integrations.
