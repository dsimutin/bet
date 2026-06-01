"""Shared TTL cache for quota-efficient Odds API access.

Supabase is used when DATABASE_URL is configured. A process-memory fallback keeps
local development simple. Cache rows are backend-only and expire automatically
by timestamp; stale rows may remain physically stored without affecting reads.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

_log = logging.getLogger(__name__)
_memory: dict[str, tuple[datetime, Any]] = {}
_lock = Lock()


def _db_enabled() -> bool:
    return bool(os.environ.get("DATABASE_URL", "").strip())


def _connect():
    import psycopg2

    return psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)


def _ensure_schema() -> None:
    if not _db_enabled():
        return
    ddl = """
    create table if not exists public.bot_odds_cache (
        cache_key text primary key,
        payload jsonb not null,
        fetched_at timestamptz not null default now(),
        expires_at timestamptz not null
    );
    create index if not exists bot_odds_cache_expires_idx
        on public.bot_odds_cache (expires_at);
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)


def get(cache_key: str) -> Any | None:
    now = datetime.now(timezone.utc)
    with _lock:
        item = _memory.get(cache_key)
        if item and item[0] > now:
            return item[1]
        if item:
            _memory.pop(cache_key, None)

    if not _db_enabled():
        return None
    try:
        _ensure_schema()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select payload, expires_at from public.bot_odds_cache where cache_key = %s",
                    (cache_key,),
                )
                row = cur.fetchone()
        if not row:
            return None
        payload, expires_at = row
        if expires_at <= now:
            return None
        value = payload if not isinstance(payload, str) else json.loads(payload)
        with _lock:
            _memory[cache_key] = (expires_at, value)
        return value
    except Exception as exc:
        _log.warning("Odds cache read failed for %s: %s", cache_key, exc)
        return None


def set(cache_key: str, payload: Any, ttl_seconds: int) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(ttl_seconds, 1))
    with _lock:
        _memory[cache_key] = (expires_at, payload)

    if not _db_enabled():
        return
    try:
        from psycopg2.extras import Json

        _ensure_schema()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.bot_odds_cache (cache_key, payload, fetched_at, expires_at)
                    values (%s, %s, now(), %s)
                    on conflict (cache_key) do update set
                        payload = excluded.payload,
                        fetched_at = now(),
                        expires_at = excluded.expires_at
                    """,
                    (cache_key, Json(payload), expires_at),
                )
    except Exception as exc:
        _log.warning("Odds cache write failed for %s: %s", cache_key, exc)
