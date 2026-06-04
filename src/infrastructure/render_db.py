"""PostgreSQL adapter for Render.com deployments.

Strategy:
  - Ledger and model .pkl files stay on Persistent Disk (no ORM needed).
  - PostgreSQL stores: model metadata, drift reports, audit log, run history.
  - Falls back to SQLite (file on disk) when DATABASE_URL is not set (local dev).

Usage:
    from src.infrastructure.render_db import get_db
    db = get_db()
    db.log_model_version(meta_dict)
    db.log_drift_report(report_dict)
    rows = db.fetch_model_history("EPL", limit=10)
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

# ──────────────────────────────────────────────────────────────────
# Connection helpers
# ──────────────────────────────────────────────────────────────────


def _is_postgres() -> bool:
    return bool(os.environ.get("DATABASE_URL", "").startswith("postgres"))


@contextmanager
def _pg_cursor() -> Generator:
    """PostgreSQL connection (requires psycopg2-binary)."""
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as e:
        raise RuntimeError("psycopg2-binary not installed. Run: pip install psycopg2-binary") from e

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        conn.commit()
    finally:
        conn.close()


@contextmanager
def _sqlite_cursor(db_path: Path) -> Generator:
    """SQLite fallback for local development."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────

_DDL_POSTGRES = """
CREATE TABLE IF NOT EXISTS model_versions (
    id           SERIAL PRIMARY KEY,
    model_id     TEXT        NOT NULL,
    league       TEXT        NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'candidate',
    brier_score  REAL,
    log_loss     REAL,
    n_matches    INTEGER,
    triggered_by TEXT,
    meta_json    JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS drift_reports (
    id                  SERIAL PRIMARY KEY,
    drift_detected      BOOLEAN NOT NULL,
    kelly_multiplier    REAL    NOT NULL,
    recent_accuracy     REAL,
    baseline_accuracy   REAL,
    cusum_value         REAL,
    threshold           REAL,
    reason              TEXT,
    retrain_recommended BOOLEAN,
    report_json         JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cron_run_log (
    id         SERIAL PRIMARY KEY,
    job_name   TEXT        NOT NULL,
    status     TEXT        NOT NULL,  -- 'success' | 'failure' | 'skip'
    duration_s REAL,
    message    TEXT,
    meta_json  JSONB,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS api_quota_daily (
    usage_date DATE    NOT NULL,
    api_name   TEXT    NOT NULL,
    calls      INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (usage_date, api_name)
);

CREATE TABLE IF NOT EXISTS api_quota_provider_records (
    id                 SERIAL PRIMARY KEY,
    provider           TEXT,
    endpoint           TEXT,
    sport_key          TEXT,
    markets            TEXT,
    regions            TEXT,
    response_status    INTEGER,
    x_requests_used    INTEGER,
    x_requests_remaining INTEGER,
    x_requests_last    INTEGER,
    source             TEXT,
    record_json        JSONB,
    requested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS api_quota_provider_monthly_usage (
    month      TEXT    NOT NULL,
    api_name   TEXT    NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (month, api_name)
);
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS model_versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id     TEXT    NOT NULL,
    league       TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'candidate',
    brier_score  REAL,
    log_loss     REAL,
    n_matches    INTEGER,
    triggered_by TEXT,
    meta_json    TEXT,
    created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS drift_reports (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    drift_detected      INTEGER NOT NULL,
    kelly_multiplier    REAL    NOT NULL,
    recent_accuracy     REAL,
    baseline_accuracy   REAL,
    cusum_value         REAL,
    threshold           REAL,
    reason              TEXT,
    retrain_recommended INTEGER,
    report_json         TEXT,
    created_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS cron_run_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name   TEXT    NOT NULL,
    status     TEXT    NOT NULL,
    duration_s REAL,
    message    TEXT,
    meta_json  TEXT,
    started_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS api_quota_daily (
    usage_date TEXT    NOT NULL,
    api_name   TEXT    NOT NULL,
    calls      INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT    NOT NULL,
    PRIMARY KEY (usage_date, api_name)
);

CREATE TABLE IF NOT EXISTS api_quota_provider_records (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    provider           TEXT,
    endpoint           TEXT,
    sport_key          TEXT,
    markets            TEXT,
    regions            TEXT,
    response_status    INTEGER,
    x_requests_used    INTEGER,
    x_requests_remaining INTEGER,
    x_requests_last    INTEGER,
    source             TEXT,
    record_json        TEXT,
    requested_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS api_quota_provider_monthly_usage (
    month      TEXT    NOT NULL,
    api_name   TEXT    NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT    NOT NULL,
    PRIMARY KEY (month, api_name)
);
"""


# ──────────────────────────────────────────────────────────────────
# Database class
# ──────────────────────────────────────────────────────────────────


class RenderDB:
    """Thin database wrapper — PostgreSQL in prod, SQLite in dev."""

    def __init__(self, sqlite_path: Path | None = None) -> None:
        self._postgres = _is_postgres()
        self._sqlite_path = (
            sqlite_path or Path(os.environ.get("DATA_DIR", "data")) / "core" / "metadata.db"
        )
        self._init_schema()

    def _init_schema(self) -> None:
        if self._postgres:
            with _pg_cursor() as cur:
                cur.execute(_DDL_POSTGRES)
        else:
            with _sqlite_cursor(self._sqlite_path) as cur:
                for stmt in _DDL_SQLITE.split(";"):
                    s = stmt.strip()
                    if s:
                        cur.execute(s)

    # ── Model versions ──────────────────────────────────────────────

    def log_model_version(self, meta: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if self._postgres:
            import psycopg2.extras

            with _pg_cursor() as cur:
                cur.execute(
                    """INSERT INTO model_versions
                       (model_id, league, status, brier_score, log_loss,
                        n_matches, triggered_by, meta_json, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        meta.get("model_id", ""),
                        meta.get("league", ""),
                        meta.get("status", "candidate"),
                        meta.get("brier_score"),
                        meta.get("log_loss"),
                        meta.get("n_matches"),
                        meta.get("triggered_by"),
                        psycopg2.extras.Json(meta),
                        now,
                    ),
                )
        else:
            with _sqlite_cursor(self._sqlite_path) as cur:
                cur.execute(
                    """INSERT INTO model_versions
                       (model_id, league, status, brier_score, log_loss,
                        n_matches, triggered_by, meta_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        meta.get("model_id", ""),
                        meta.get("league", ""),
                        meta.get("status", "candidate"),
                        meta.get("brier_score"),
                        meta.get("log_loss"),
                        meta.get("n_matches"),
                        meta.get("triggered_by"),
                        json.dumps(meta, ensure_ascii=False),
                        now,
                    ),
                )

    def fetch_model_history(self, league: str, limit: int = 10) -> list[dict]:
        q_pg = "SELECT * FROM model_versions WHERE league=%s ORDER BY created_at DESC LIMIT %s"
        q_sq = "SELECT * FROM model_versions WHERE league=? ORDER BY created_at DESC LIMIT ?"
        if self._postgres:
            with _pg_cursor() as cur:
                cur.execute(q_pg, (league, limit))
                return [dict(r) for r in cur.fetchall()]
        else:
            with _sqlite_cursor(self._sqlite_path) as cur:
                cur.execute(q_sq, (league, limit))
                return [dict(r) for r in cur.fetchall()]

    # ── Drift reports ───────────────────────────────────────────────

    def log_drift_report(self, report: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if self._postgres:
            import psycopg2.extras

            with _pg_cursor() as cur:
                cur.execute(
                    """INSERT INTO drift_reports
                       (drift_detected, kelly_multiplier, recent_accuracy,
                        baseline_accuracy, cusum_value, threshold, reason,
                        retrain_recommended, report_json, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        report.get("drift_detected", False),
                        report.get("kelly_multiplier", 1.0),
                        report.get("recent_accuracy"),
                        report.get("baseline_accuracy"),
                        report.get("cusum_value"),
                        report.get("threshold"),
                        report.get("reason", ""),
                        report.get("retrain_recommended", False),
                        psycopg2.extras.Json(report),
                        now,
                    ),
                )
        else:
            with _sqlite_cursor(self._sqlite_path) as cur:
                cur.execute(
                    """INSERT INTO drift_reports
                       (drift_detected, kelly_multiplier, recent_accuracy,
                        baseline_accuracy, cusum_value, threshold, reason,
                        retrain_recommended, report_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        int(report.get("drift_detected", False)),
                        report.get("kelly_multiplier", 1.0),
                        report.get("recent_accuracy"),
                        report.get("baseline_accuracy"),
                        report.get("cusum_value"),
                        report.get("threshold"),
                        report.get("reason", ""),
                        int(report.get("retrain_recommended", False)),
                        json.dumps(report, ensure_ascii=False),
                        now,
                    ),
                )

    # ── Cron run log ────────────────────────────────────────────────

    def log_cron_run(
        self,
        job_name: str,
        status: str,
        duration_s: float | None = None,
        message: str = "",
        meta: dict | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if self._postgres:
            import psycopg2.extras

            with _pg_cursor() as cur:
                cur.execute(
                    """INSERT INTO cron_run_log
                       (job_name, status, duration_s, message, meta_json, started_at)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (job_name, status, duration_s, message, psycopg2.extras.Json(meta or {}), now),
                )
        else:
            with _sqlite_cursor(self._sqlite_path) as cur:
                cur.execute(
                    """INSERT INTO cron_run_log
                       (job_name, status, duration_s, message, meta_json, started_at)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        job_name,
                        status,
                        duration_s,
                        message,
                        json.dumps(meta or {}, ensure_ascii=False),
                        now,
                    ),
                )

    def fetch_last_run(self, job_name: str) -> dict | None:
        q_pg = "SELECT * FROM cron_run_log WHERE job_name=%s ORDER BY started_at DESC LIMIT 1"
        q_sq = "SELECT * FROM cron_run_log WHERE job_name=? ORDER BY started_at DESC LIMIT 1"
        if self._postgres:
            with _pg_cursor() as cur:
                cur.execute(q_pg, (job_name,))
                row = cur.fetchone()
                return dict(row) if row else None
        with _sqlite_cursor(self._sqlite_path) as cur:
            cur.execute(q_sq, (job_name,))
            row = cur.fetchone()
            return dict(row) if row else None

    # ── API quota state ─────────────────────────────────────────────

    def record_api_quota_request(self, api_name: str, calls: int, usage_date: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if self._postgres:
            with _pg_cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO api_quota_daily (usage_date, api_name, calls, updated_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (usage_date, api_name) DO UPDATE SET
                        calls = api_quota_daily.calls + EXCLUDED.calls,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (usage_date, api_name, calls, now),
                )
        else:
            with _sqlite_cursor(self._sqlite_path) as cur:
                cur.execute(
                    """
                    INSERT INTO api_quota_daily (usage_date, api_name, calls, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (usage_date, api_name) DO UPDATE SET
                        calls = calls + excluded.calls,
                        updated_at = excluded.updated_at
                    """,
                    (usage_date, api_name, calls, now),
                )

    def record_api_quota_provider_headers(self, record: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        requested_at = str(record.get("requested_at_utc") or now)
        api_name = str(record.get("api_name") or "")
        month = requested_at[:7]
        used = _parse_int(record.get("x_requests_used"))
        if self._postgres:
            import psycopg2.extras

            with _pg_cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO api_quota_provider_records
                    (provider, endpoint, sport_key, markets, regions, response_status,
                     x_requests_used, x_requests_remaining, x_requests_last, source,
                     record_json, requested_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    _provider_record_values(record, psycopg2.extras.Json(record), requested_at),
                )
                if api_name and used is not None:
                    cur.execute(
                        """
                        INSERT INTO api_quota_provider_monthly_usage
                        (month, api_name, used, updated_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (month, api_name) DO UPDATE SET
                            used = GREATEST(api_quota_provider_monthly_usage.used, EXCLUDED.used),
                            updated_at = EXCLUDED.updated_at
                        """,
                        (month, api_name, used, now),
                    )
        else:
            with _sqlite_cursor(self._sqlite_path) as cur:
                cur.execute(
                    """
                    INSERT INTO api_quota_provider_records
                    (provider, endpoint, sport_key, markets, regions, response_status,
                     x_requests_used, x_requests_remaining, x_requests_last, source,
                     record_json, requested_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _provider_record_values(
                        record, json.dumps(record, ensure_ascii=False), requested_at
                    ),
                )
                if api_name and used is not None:
                    cur.execute(
                        """
                        INSERT INTO api_quota_provider_monthly_usage
                        (month, api_name, used, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT (month, api_name) DO UPDATE SET
                            used = max(used, excluded.used),
                            updated_at = excluded.updated_at
                        """,
                        (month, api_name, used, now),
                    )

    def fetch_api_quota_monthly_usage(self, api_name: str, month: str) -> int:
        if self._postgres:
            with _pg_cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(SUM(calls), 0) AS used
                    FROM api_quota_daily
                    WHERE api_name=%s AND usage_date::text LIKE %s
                    """,
                    (api_name, f"{month}%"),
                )
                row = cur.fetchone()
                local_used = int(row["used"] or 0) if row else 0
                cur.execute(
                    """
                    SELECT COALESCE(MAX(used), 0) AS used
                    FROM api_quota_provider_monthly_usage
                    WHERE api_name=%s AND month=%s
                    """,
                    (api_name, month),
                )
                row = cur.fetchone()
                provider_used = int(row["used"] or 0) if row else 0
                return max(local_used, provider_used)
        with _sqlite_cursor(self._sqlite_path) as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(calls), 0) AS used
                FROM api_quota_daily
                WHERE api_name=? AND usage_date LIKE ?
                """,
                (api_name, f"{month}%"),
            )
            row = cur.fetchone()
            local_used = int(row["used"] or 0) if row else 0
            cur.execute(
                """
                SELECT COALESCE(MAX(used), 0) AS used
                FROM api_quota_provider_monthly_usage
                WHERE api_name=? AND month=?
                """,
                (api_name, month),
            )
            row = cur.fetchone()
            provider_used = int(row["used"] or 0) if row else 0
            return max(local_used, provider_used)


def get_db() -> RenderDB:
    """Module-level factory — returns the appropriate DB for the environment."""
    return RenderDB()


def _parse_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _provider_record_values(
    record: dict[str, Any], record_json: Any, requested_at: str
) -> tuple[Any, ...]:
    return (
        record.get("provider"),
        record.get("endpoint"),
        record.get("sport_key"),
        record.get("markets"),
        record.get("regions"),
        _parse_int(record.get("response_status")),
        _parse_int(record.get("x_requests_used")),
        _parse_int(record.get("x_requests_remaining")),
        _parse_int(record.get("x_requests_last")),
        record.get("source"),
        record_json,
        requested_at,
    )
