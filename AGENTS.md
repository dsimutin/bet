# AGENTS.md

This repository is a paper-trading sports analytics app. It ingests pre-match odds,
produces auditable paper signals, stores signal and settlement history, and reports
health/readiness for limited pilot operations.

## Non-Negotiable Invariants

- `PAPER_TRADING_ONLY=true` is the operating mode. Do not add or enable real bookmaker
  bet placement.
- Production signals must be pre-match: `snapshot_ts_utc < event_time_utc` and
  `event_time_utc > now`.
- Production UI and health must read the authoritative ledger backend, not a local JSON
  file as authority.
- Critical persistence errors must fail closed. Do not silently fall back to ephemeral
  local storage in production.
- Never log or commit secrets, API keys, webhook secrets, bearer tokens, or URLs that
  include credentials.

## Key Entrypoints

- Signal scan: `src/cron/run_signals.py`
- Settlement: `src/cron/run_settle.py`
- Web/dashboard: `src/web/app.py`
- Health/readiness/control plane: `src/web/health_app.py`
- Telegram bot/webhook: `src/web/telegram_bot.py`
- Runtime odds: `src/services/runtime_odds.py`

## Canonical Signal Rules

- Required timestamp fields: `event_time_utc`, `snapshot_ts_utc`,
  `timestamp_verification_status`.
- Live odds snapshots should preserve source identifiers and bookmaker/market metadata.
- Priority signals require `timestamp_verification_status == "verified_pre_match"` and
  priority-fresh odds.
- Odds cache storage TTL is not the same as signal freshness.
- New markets require backtests, scanner tests, settlement tests, feedback gating, and
  documentation before production feedback eligibility.

## Feedback Eligibility

Production feedback must exclude blocked, watchlist, experimental, legacy-invalid,
void, push, and expired entries. Only settled priority H2H paper entries with verified
pre-match timestamps and `win`/`loss` results may affect production thresholds.

## Commands

Install dependencies:

```bash
uv sync --extra dev
```

Targeted tests:

```bash
uv run --extra dev pytest -q tests/test_live_odds_adapter.py
uv run --extra dev pytest -q tests/test_tennis.py
uv run --extra dev pytest -q tests/test_settle_signal_ledger.py
```

Full release gate:

```bash
uv sync --extra dev
uv run --extra dev pytest -q
uv run --extra dev mypy src
uv run --extra dev black --check src tests
python scripts/health_check.py
```

## Secrets And Production

- Add new environment variable names only to `.env.example`, `render.yaml`, and docs.
- Use empty placeholders for secrets.
- In production, missing required database, admin, Telegram webhook, or allowed-chat
  configuration must fail readiness/startup rather than degrade silently.
