"""Historical modelling utilities for paper-trading research.

Runtime note: install the durable SignalLedger adapter at package import so all
football, tennis, reporting and Telegram menu code shares the same Supabase-backed
history when DATABASE_URL is configured. Local development still works with JSON.
"""

try:
    from src.models.runtime_patches import install_signal_ledger_persistence

    install_signal_ledger_persistence()
except Exception:
    # Keep imports safe for local tooling and migrations before DATABASE_URL exists.
    pass
