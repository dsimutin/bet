"""Runtime patches that make SignalLedger durable on Render free tier.

All existing football, tennis, reporting and Telegram code imports the same
``SignalLedger`` class. Installing this patch once at package import keeps that
public API unchanged while synchronising every load/save with Supabase whenever
``DATABASE_URL`` is configured.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


def install_signal_ledger_persistence() -> None:
    from src.infrastructure import supabase_ledger
    from src.models.signal_ledger import SignalLedger

    if getattr(SignalLedger, "_supabase_runtime_patch_installed", False):
        return

    original_load = SignalLedger.load_or_create.__func__
    original_save = SignalLedger.save

    @classmethod
    def load_or_create(cls, path: Path):
        local = original_load(cls, path)
        local_entries = local.entries()
        if not supabase_ledger.is_enabled():
            return local

        try:
            remote_entries = supabase_ledger.load_entries()
            merged: dict[str, dict[str, Any]] = dict(local_entries)
            # Remote state wins because settlement and delivery may be fresher.
            merged.update(remote_entries)
            missing_remote = set(local_entries) - set(remote_entries)
            if missing_remote:
                _log.info("Importing %d legacy ledger rows into Supabase", len(missing_remote))
                supabase_ledger.save_entries(merged)
            return cls(entries=merged)
        except Exception:
            if supabase_ledger.is_required():
                raise
            _log.exception("Supabase ledger load failed; falling back to local JSON")
            return local

    def save(self, path: Path):
        mirror_path = original_save(self, path)
        if supabase_ledger.is_enabled():
            try:
                supabase_ledger.save_entries(self.entries())
            except Exception:
                if supabase_ledger.is_required():
                    raise
                _log.exception("Supabase ledger save failed; JSON mirror remains available")
        return mirror_path

    SignalLedger.load_or_create = load_or_create
    SignalLedger.save = save
    SignalLedger._supabase_runtime_patch_installed = True
