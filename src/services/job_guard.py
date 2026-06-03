"""Job guard for idempotent manual and scheduled jobs."""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from threading import Lock
from typing import Any, Iterator

_locks: dict[str, Lock] = {}
_registry_lock = Lock()


@contextmanager
def job_guard(job_name: str) -> Iterator[bool]:
    """Yield true only when the caller owns the local and optional DB lock."""
    with _registry_lock:
        lock = _locks.setdefault(job_name, Lock())
    acquired = lock.acquire(blocking=False)
    db_conn: Any | None = None
    db_lock_owned = False
    db_lock_id = _advisory_lock_id(job_name)
    try:
        if not acquired:
            yield False
            return
        if _use_postgres_guard():
            try:
                db_conn = _connect_postgres()
                db_acquired = _try_pg_advisory_lock(db_conn, db_lock_id)
            except Exception:
                if db_conn is not None:
                    db_conn.close()
                    db_conn = None
                yield False
                return
            if not db_acquired:
                yield False
                return
            db_lock_owned = True
        yield True
    finally:
        if db_conn is not None and db_lock_owned:
            _release_pg_advisory_lock(db_conn, db_lock_id)
        elif db_conn is not None:
            db_conn.close()
        if acquired:
            lock.release()


def _use_postgres_guard() -> bool:
    if os.environ.get("JOB_GUARD_USE_DB_LOCK", "true").lower() in {"0", "false", "no"}:
        return False
    return os.environ.get("DATABASE_URL", "").startswith("postgres")


def _advisory_lock_id(job_name: str) -> int:
    digest = hashlib.sha256(f"bet-job-guard:{job_name}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _connect_postgres() -> Any:
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("psycopg2-binary is required for DB-backed job guard") from exc
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.autocommit = True
    return conn


def _try_pg_advisory_lock(conn: Any, lock_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,))
        row = cur.fetchone()
    return bool(row and row[0])


def _release_pg_advisory_lock(conn: Any, lock_id: int) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
    finally:
        conn.close()
