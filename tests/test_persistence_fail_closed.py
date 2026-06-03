from __future__ import annotations

import pytest

from src.infrastructure.persistent_ledger import (
    get_ledger_backend,
    LocalJsonLedgerBackend,
    load_ledger,
)


def test_production_requires_database_url(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("REQUIRE_DATABASE", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        get_ledger_backend(tmp_path / "ledger.json")


def test_development_can_use_local_json_backend(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    backend = get_ledger_backend(tmp_path / "ledger.json")
    assert isinstance(backend, LocalJsonLedgerBackend)


def test_production_database_failure_is_fail_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("REQUIRE_DATABASE", "true")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:secret@example/db")
    monkeypatch.setattr("src.infrastructure.supabase_ledger.is_enabled", lambda: True)
    monkeypatch.setattr(
        "src.infrastructure.supabase_ledger.load_entries",
        lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        load_ledger(tmp_path / "ledger.json")


def test_today_picks_loads_authoritative_backend(monkeypatch, tmp_path) -> None:
    class FakeLedger:
        def entries(self):
            return {"s1": {"signal_id": "s1", "ledger_status": "open"}}

    calls = []

    def fake_load_ledger(path):
        calls.append(path)
        return FakeLedger()

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))
    monkeypatch.setattr("src.infrastructure.persistent_ledger.load_ledger", fake_load_ledger)

    import importlib
    import src.web.today_picks as today_picks

    importlib.reload(today_picks)

    assert today_picks._load_entries() == [{"signal_id": "s1", "ledger_status": "open"}]
    assert calls == [tmp_path / "ledger.json"]


def test_today_picks_reads_authoritative_backend(monkeypatch, tmp_path) -> None:
    test_today_picks_loads_authoritative_backend(monkeypatch, tmp_path)


def test_dashboard_reads_authoritative_backend(monkeypatch, tmp_path) -> None:
    class FakeLedger:
        def summary(self):
            return {"total_signals": 7}

        def entries(self):
            return {"s1": {"signal_id": "s1"}}

    calls = []

    def fake_load_ledger(path):
        calls.append(path)
        return FakeLedger()

    ledger_path = tmp_path / "authority.json"
    monkeypatch.setenv("LEDGER_PATH", str(ledger_path))
    monkeypatch.setattr("src.infrastructure.persistent_ledger.load_ledger", fake_load_ledger)

    import importlib
    import src.web.app as dashboard

    importlib.reload(dashboard)

    assert dashboard._ledger_summary()["total_signals"] == 7
    assert dashboard._recent_signals() == [{"signal_id": "s1"}]
    assert calls == [ledger_path, ledger_path]
