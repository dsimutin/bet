# Production Readiness

This app is prepared only for a limited paper-trading pilot. It must run with
`PAPER_TRADING_ONLY=true`; it does not place real bookmaker bets.

## Supported Production Markets

- Football top leagues: H2H / 1X2 paper signals.
- Tennis: H2H paper signals.
- Exotic football: research watchlist only.
- Tennis spreads: experimental, `ENABLE_TENNIS_SPREADS=false` by default.
- Tennis totals: experimental, `ENABLE_TENNIS_TOTALS=false` by default.

## Required Production Controls

- `APP_ENV=production`
- `DATABASE_URL` configured and reachable.
- `REQUIRE_DATABASE=true`
- `ALLOW_LOCAL_LEDGER_FALLBACK=false`
- `ADMIN_API_TOKEN` configured.
- `TELEGRAM_WEBHOOK_SECRET` configured before webhook setup.
- `TELEGRAM_ALLOWED_CHAT_IDS` configured.

## Limits

- Injuries are informational-only until a player-importance model, lineup certainty,
  calibration, and historical backtest exist.
- Free Render has no production SLA. Use paid always-on infrastructure or a reliable
  external scheduler for production scheduling.
- Local filesystem data on Render is a diagnostic mirror, not durable authority.
- Model artifacts require a durable source: versioned repository artifacts, object
  storage, or Supabase Storage.
- External credentials and provider availability cannot be fully verified locally.
