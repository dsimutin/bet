"""Compatibility facade for the durable paper signal ledger.

SignalLedger itself is transparently synchronised with Supabase by
``src.models.runtime_patches``.  This module keeps explicit call sites readable
without performing duplicate database round trips.
"""

from __future__ import annotations

from pathlib import Path

from src.models.signal_ledger import SignalLedger


def load_ledger(path: Path) -> SignalLedger:
    return SignalLedger.load_or_create(path)


def save_ledger(ledger: SignalLedger, path: Path) -> Path:
    return ledger.save(path)
