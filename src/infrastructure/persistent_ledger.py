"""Explicit ledger backend facade for the durable paper signal ledger.

Backend selection is governed by environment variables:
  SUPABASE_LEDGER_ENABLED + DATABASE_URL  — use Supabase as authoritative store
  ALLOW_LOCAL_LEDGER_FALLBACK             — allow JSON fallback when Supabase is
                                            configured but unreachable (default: true)
  WRITE_LOCAL_LEDGER_MIRROR               — always write a local JSON mirror even when
                                            Supabase is the authoritative backend (default: true)
  REQUIRE_DATABASE                        — fail hard when Supabase is unavailable
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from src.models.signal_ledger import SignalLedger

_log = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = True) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def load_ledger(path: Path) -> SignalLedger:
    """Load the ledger, merging local JSON with Supabase when configured."""
    from src.infrastructure import supabase_ledger

    local = SignalLedger.load_or_create(path)

    if not supabase_ledger.is_enabled():
        return local

    allow_fallback = _env_bool("ALLOW_LOCAL_LEDGER_FALLBACK", True)
    try:
        remote_entries = supabase_ledger.load_entries()
        local_entries = local.entries()
        merged = dict(local_entries)
        merged.update(remote_entries)
        missing_remote = set(local_entries) - set(remote_entries)
        if missing_remote:
            _log.info("[ledger] Migrating %d local-only entries into Supabase", len(missing_remote))
            supabase_ledger.save_entries(merged)
        return SignalLedger(entries=merged)
    except Exception as exc:
        if supabase_ledger.is_required() or not allow_fallback:
            _log.error("[ledger] Supabase load failed and fallback is disabled: %s", exc)
            raise
        _log.warning("[ledger] Supabase load failed; using local JSON: %s", exc)
        return local


def save_ledger(ledger: SignalLedger, path: Path) -> Path:
    """Save ledger to Supabase (authoritative) and optionally to local JSON mirror."""
    from src.infrastructure import supabase_ledger

    write_mirror = _env_bool("WRITE_LOCAL_LEDGER_MIRROR", True)

    if supabase_ledger.is_enabled():
        try:
            supabase_ledger.save_entries(ledger.entries())
        except Exception as exc:
            if supabase_ledger.is_required():
                _log.error("[ledger] Supabase save failed (REQUIRE_DATABASE=true): %s", exc)
                raise
            _log.warning("[ledger] Supabase save failed; JSON mirror still written: %s", exc)

    if not supabase_ledger.is_enabled() or write_mirror:
        path.parent.mkdir(parents=True, exist_ok=True)
        return ledger.save(path)

    return path
