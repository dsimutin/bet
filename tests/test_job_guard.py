from __future__ import annotations

import src.services.job_guard as guard
import pytest
from src.services.job_guard import job_guard


class _FakeCursor:
    def __init__(self, conn) -> None:
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, query: str, params: tuple[int]) -> None:
        self._conn.queries.append((query, params))

    def fetchone(self) -> tuple[bool]:
        return (self._conn.lock_result,)


class _FakeConnection:
    def __init__(self, lock_result: bool) -> None:
        self.lock_result = lock_result
        self.queries: list[tuple[str, tuple[int]]] = []
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _disable_db_guard_by_default(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_job_guard_allows_single_runner() -> None:
    with job_guard("unit_job") as acquired:
        assert acquired is True


def test_job_guard_skips_parallel_runner() -> None:
    with job_guard("parallel_job") as first:
        with job_guard("parallel_job") as second:
            assert first is True
            assert second is False


def test_job_guard_releases_after_exception() -> None:
    try:
        with job_guard("exception_job") as acquired:
            assert acquired is True
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with job_guard("exception_job") as acquired_again:
        assert acquired_again is True


def test_job_guard_uses_postgres_advisory_lock(monkeypatch) -> None:
    conn = _FakeConnection(lock_result=True)
    monkeypatch.setenv("DATABASE_URL", "postgres://example")
    monkeypatch.setattr(guard, "_connect_postgres", lambda: conn)

    with job_guard("db_job") as acquired:
        assert acquired is True

    assert any("pg_try_advisory_lock" in query for query, _ in conn.queries)
    assert any("pg_advisory_unlock" in query for query, _ in conn.queries)
    assert conn.closed is True


def test_job_guard_skips_when_postgres_lock_is_held(monkeypatch) -> None:
    conn = _FakeConnection(lock_result=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://example")
    monkeypatch.setattr(guard, "_connect_postgres", lambda: conn)

    with job_guard("db_held_job") as acquired:
        assert acquired is False

    assert any("pg_try_advisory_lock" in query for query, _ in conn.queries)
    assert not any("pg_advisory_unlock" in query for query, _ in conn.queries)
    assert conn.closed is True


def test_job_guard_fails_closed_when_postgres_lock_errors(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://example")
    monkeypatch.setattr(guard, "_connect_postgres", lambda: (_ for _ in ()).throw(RuntimeError))

    with job_guard("db_error_job") as acquired:
        assert acquired is False
