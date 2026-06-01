"""Low-quota tennis runtime scanner for the Render free-tier deployment.

Production delivery is restricted to h2h until market-specific settlement for
spreads and totals is validated. Odds are fetched through the shared Supabase-
backed cache so football, tennis, scheduled jobs and Telegram menu refreshes do
not spend API credits independently.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.services.runtime_odds import get_tennis_h2h_events
from src.signals import tennis_signal_scan as research_scanner


def scan_tennis_h2h_runtime(model_path: Path, api_key: str) -> dict[str, Any]:
    """Run the existing ELO/Markov model against cached h2h-only live odds."""
    original_fetch = research_scanner._fetch_atp_events  # type: ignore[assignment]
    research_scanner._fetch_atp_events = get_tennis_h2h_events  # type: ignore[assignment]
    try:
        result = research_scanner.scan_tennis_signals(model_path=model_path, api_key=api_key)
    finally:
        research_scanner._fetch_atp_events = original_fetch  # type: ignore[assignment]

    h2h_signals = [
        signal for signal in result.get("all_signals", []) if signal.get("market", "h2h") == "h2h"
    ]
    result["all_signals"] = h2h_signals
    result["signals_count"] = len(h2h_signals)
    result["top_signals"] = sorted(
        h2h_signals, key=lambda item: item.get("edge_pct", 0), reverse=True
    )[:5]
    result["runtime_mode"] = "h2h_low_quota_cached"
    return result
