"""Explicit authoritative ledger backend selection."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Protocol

from src.infrastructure import supabase_ledger
from src.models.signal_ledger import SignalLedger

_log = logging.getLogger(__name__)


class LedgerBackend(Protocol):
    def load(self) -> SignalLedger: ...
    def save(self, ledger: SignalLedger) -> None: ...
    def healthcheck(self) -> dict[str, object]: ...


class LocalJsonLedgerBackend:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> SignalLedger:
        return SignalLedger.load_or_create(self.path)

    def save(self, ledger: SignalLedger) -> None:
        ledger.save(self.path)

    def healthcheck(self) -> dict[str, object]:
        return {"backend": "local_json", "ok": True, "path": str(self.path)}


class SupabaseLedgerBackend:
    def load(self) -> SignalLedger:
        entries = supabase_ledger.load_entries()
        return SignalLedger(entries=entries)

    def save(self, ledger: SignalLedger) -> None:
        supabase_ledger.save_entries(ledger.entries())

    def healthcheck(self) -> dict[str, object]:
        try:
            supabase_ledger.ensure_schema()
            return {"backend": "supabase", "ok": True}
        except Exception as exc:
            return {"backend": "supabase", "ok": False, "error": _sanitize(str(exc))}


class MirroredLedgerBackend:
    def __init__(self, authority: LedgerBackend, mirror: LocalJsonLedgerBackend) -> None:
        self.authority = authority
        self.mirror = mirror

    def load(self) -> SignalLedger:
        return self.authority.load()

    def save(self, ledger: SignalLedger) -> None:
        self.authority.save(ledger)
        if _env_bool("WRITE_LOCAL_LEDGER_MIRROR", True):
            try:
                self.mirror.save(ledger)
            except Exception as exc:
                _log.warning("Local ledger mirror write failed: %s", _sanitize(str(exc)))

    def healthcheck(self) -> dict[str, object]:
        result = self.authority.healthcheck()
        result["mirror"] = self.mirror.healthcheck()
        return result


def get_ledger_backend(path: Path | None = None) -> LedgerBackend:
    path = path or Path(os.environ.get("LEDGER_PATH", "data/core/paper_signal_ledger.json"))
    app_env = os.environ.get("APP_ENV", "development").strip().lower()
    require_database = _env_bool("REQUIRE_DATABASE", app_env == "production")
    allow_local_fallback = _env_bool("ALLOW_LOCAL_LEDGER_FALLBACK", app_env != "production")

    if supabase_ledger.is_enabled():
        return MirroredLedgerBackend(SupabaseLedgerBackend(), LocalJsonLedgerBackend(path))
    if require_database or (app_env == "production" and not allow_local_fallback):
        raise RuntimeError("DATABASE_URL is required for authoritative production ledger")
    return LocalJsonLedgerBackend(path)


def load_ledger(path: Path) -> SignalLedger:
    return get_ledger_backend(path).load()


def save_ledger(ledger: SignalLedger, path: Path) -> Path:
    get_ledger_backend(path).save(ledger)
    return path


def ledger_healthcheck(path: Path | None = None) -> dict[str, object]:
    try:
        return get_ledger_backend(path).healthcheck()
    except Exception as exc:
        return {"backend": "unavailable", "ok": False, "error": _sanitize(str(exc))}


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _sanitize(text: str) -> str:
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url:
        text = text.replace(database_url, "[REDACTED_DATABASE_URL]")
    return text
