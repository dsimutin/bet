from __future__ import annotations

import pytest

from src.infrastructure.persistent_ledger import get_ledger_backend, LocalJsonLedgerBackend


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
