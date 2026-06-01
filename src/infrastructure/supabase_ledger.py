"""Supabase/PostgreSQL persistence adapter for the paper signal ledger.

The Render free tier has an ephemeral local filesystem.  This adapter keeps the
JSON ledger API used by the rest of the project while storing the authoritative
state in PostgreSQL whenever ``DATABASE_URL`` is configured.

Secrets are read only from environment variables.  Never commit DATABASE_URL.
"""

from __future__ import annotations

import logging
import os
from typing import Any

_log = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    """Return True when PostgreSQL ledger persistence should be used."""
    return bool(os.environ.get("DATABASE_URL", "").strip()) and _env_bool(
        "SUPABASE_LEDGER_ENABLED", True
    )


def is_required() -> bool:
    """Return True when a database failure must abort the job instead of falling back."""
    return _env_bool("REQUIRE_DATABASE", False)


def _connect():
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - dependency is installed on Render
        raise RuntimeError("psycopg2-binary is required for Supabase persistence") from exc

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(database_url, connect_timeout=10)


def ensure_schema() -> None:
    """Create the minimal ledger table when migrations have not been run yet."""
    if not is_enabled():
        return

    ddl = """
    create table if not exists public.bot_signal_ledger (
        signal_id text primary key,
        sport text,
        strategy_id text,
        event_id text,
        event_date date,
        market_key text,
        selection text,
        ledger_status text not null default 'open',
        delivery_status text not null default 'registered',
        payload jsonb not null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now()
    );
    create index if not exists bot_signal_ledger_status_idx
        on public.bot_signal_ledger (ledger_status, sport);
    create index if not exists bot_signal_ledger_event_idx
        on public.bot_signal_ledger (event_date, event_id);
    create index if not exists bot_signal_ledger_delivery_idx
        on public.bot_signal_ledger (delivery_status);
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
    _log.info("Supabase ledger schema is ready")


def load_entries() -> dict[str, dict[str, Any]]:
    """Load the complete authoritative ledger from PostgreSQL."""
    if not is_enabled():
        return {}
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select signal_id, payload from public.bot_signal_ledger")
            rows = cur.fetchall()
    entries = {str(signal_id): dict(payload) for signal_id, payload in rows}
    _log.info("Loaded %d ledger entries from Supabase", len(entries))
    return entries


def save_entries(entries: dict[str, dict[str, Any]]) -> None:
    """Upsert all in-memory entries into PostgreSQL.

    The ledger remains append-oriented.  Existing rows are updated because delivery
    and settlement fields evolve after registration.
    """
    if not is_enabled():
        return
    ensure_schema()
    try:
        from psycopg2.extras import Json, execute_batch
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("psycopg2-binary is required for Supabase persistence") from exc

    sql = """
    insert into public.bot_signal_ledger (
        signal_id, sport, strategy_id, event_id, event_date, market_key, selection,
        ledger_status, delivery_status, payload, created_at, updated_at
    ) values (
        %(signal_id)s, %(sport)s, %(strategy_id)s, %(event_id)s, nullif(%(event_date)s, '')::date,
        %(market_key)s, %(selection)s, %(ledger_status)s, %(delivery_status)s,
        %(payload)s, coalesce(nullif(%(created_at)s, '')::timestamptz, now()), now()
    )
    on conflict (signal_id) do update set
        sport = excluded.sport,
        strategy_id = excluded.strategy_id,
        event_id = excluded.event_id,
        event_date = excluded.event_date,
        market_key = excluded.market_key,
        selection = excluded.selection,
        ledger_status = excluded.ledger_status,
        delivery_status = excluded.delivery_status,
        payload = excluded.payload,
        updated_at = now()
    """

    records = []
    for signal_id, entry in entries.items():
        records.append(
            {
                "signal_id": signal_id,
                "sport": entry.get("sport", "football"),
                "strategy_id": entry.get("strategy_id"),
                "event_id": entry.get("event_id") or entry.get("normalized_event_id"),
                "event_date": entry.get("event_date") or "",
                "market_key": entry.get("market_key") or entry.get("market") or "h2h",
                "selection": entry.get("selection"),
                "ledger_status": entry.get("ledger_status", "open"),
                "delivery_status": entry.get("delivery_status", "registered"),
                "payload": Json(entry),
                "created_at": entry.get("ledger_created_at_utc") or "",
            }
        )

    if not records:
        return
    with _connect() as conn:
        with conn.cursor() as cur:
            execute_batch(cur, sql, records, page_size=200)
    _log.info("Upserted %d ledger entries into Supabase", len(records))
