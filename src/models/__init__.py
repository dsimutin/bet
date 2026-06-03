"""Historical modelling utilities for paper-trading research.

The durable SignalLedger adapter is installed explicitly at startup by the
application entry point (health_app lifespan) so that import-time side effects
do not fire during tests, CLI tools, or migrations. If DATABASE_URL is set and
SUPABASE_LEDGER_ENABLED is true the adapter will be activated; otherwise the
local JSON ledger is used as-is.
"""

import os as _os


def _install_persistence_if_configured() -> None:
    """Install Supabase adapter when the runtime environment is configured for it."""
    if not _os.environ.get("DATABASE_URL", "").strip():
        return
    enabled = _os.environ.get("SUPABASE_LEDGER_ENABLED", "true").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return
    try:
        from src.models.runtime_patches import install_signal_ledger_persistence

        install_signal_ledger_persistence()
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "[ledger] Supabase persistence install failed: %s", exc
        )
        if _os.environ.get("REQUIRE_DATABASE", "").strip().lower() in {"1", "true", "yes", "on"}:
            raise


_install_persistence_if_configured()
