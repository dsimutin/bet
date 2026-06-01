"""Persistent paper-ledger facade.

Render's free tier has an ephemeral filesystem.  The existing JSON ledger remains
useful as a local mirror, while Supabase PostgreSQL becomes the source of truth
when ``DATABASE_URL`` is configured.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.models.signal_ledger import SignalLedger
from src.infrastructure import supabase_ledger

_log = logging.getLogger(__name__)


def load_ledger(path: Path) -> SignalLedger:
    """Load from Supabase when available; otherwise use the existing JSON ledger."""
    if supabase_ledger.is_enabled():
        try:
            remote_entries = supabase_ledger.load_entries()
            if remote_entries:
                _log.info("Using Supabase ledger as source of truth (%d entries)", len(remote_entries))
                return SignalLedger(entries=remote_entries)
            local = SignalLedger.load_or_create(path)
            if local.entries():
                _log.info("Supabase ledger is empty; importing %d local entries", len(local.entries()))
                supabase_ledger.save_entries(local.entries())
            return local
        except Exception:
            if supabase_ledger.is_required():
                raise
            _log.exception("Supabase load failed; falling back to local JSON ledger")
    return SignalLedger.load_or_create(path)


def save_ledger(ledger: SignalLedger, path: Path) -> Path:
    """Save the JSON mirror and then upsert authoritative state to Supabase."""
    mirror_path = ledger.save(path)
    if supabase_ledger.is_enabled():
        try:
            supabase_ledger.save_entries(ledger.entries())
        except Exception:
            if supabase_ledger.is_required():
                raise
            _log.exception("Supabase save failed; local JSON mirror was written")
    return mirror_path
