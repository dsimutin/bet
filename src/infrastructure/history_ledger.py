"""Historical ledger reader for Telegram menu pages.

The helper merges the legacy JSON mirror with Supabase rows. Remote rows win for
matching signal IDs because delivery and settlement state may be fresher there.
Previously unseen JSON rows are imported into Supabase so older football and
tennis paper bets remain visible after Render sleep or redeploy.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.infrastructure import supabase_ledger
from src.models.signal_ledger import SignalLedger

_log = logging.getLogger(__name__)


def load_history_ledger(path: Path) -> SignalLedger:
    local = SignalLedger.load_or_create(path)
    local_entries = local.entries()

    if not supabase_ledger.is_enabled():
        return local

    try:
        remote_entries = supabase_ledger.load_entries()
        merged = dict(local_entries)
        merged.update(remote_entries)
        missing_remote = set(local_entries) - set(remote_entries)
        if missing_remote:
            _log.info("Importing %d legacy ledger rows into Supabase", len(missing_remote))
            supabase_ledger.save_entries(merged)
        return SignalLedger(entries=merged)
    except Exception:
        if supabase_ledger.is_required():
            raise
        _log.exception("Supabase history load failed; using local JSON mirror")
        return local
