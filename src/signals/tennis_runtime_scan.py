"""Low-quota tennis runtime scanner for the Render free-tier deployment.

Fetches h2h + spreads (set handicap) + totals (total games) odds through the
shared Supabase-backed cache. Spread/total settlement is validated via set-score
parsing from Jeff Sackmann ATP data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.services.runtime_odds import get_tennis_h2h_events
from src.signals import tennis_signal_scan as research_scanner

# Markets enabled for live delivery
_ENABLED_MARKETS = {"h2h", "spreads", "totals"}


def scan_tennis_h2h_runtime(model_path: Path, api_key: str) -> dict[str, Any]:
    """Run ELO/Markov model against cached live odds for all enabled markets."""
    original_fetch = research_scanner._fetch_atp_events
    research_scanner._fetch_atp_events = get_tennis_h2h_events
    try:
        result = research_scanner.scan_tennis_signals(model_path=model_path, api_key=api_key)
    finally:
        research_scanner._fetch_atp_events = original_fetch

    all_signals = result.get("all_signals", [])
    enabled = [s for s in all_signals if s.get("market", "h2h") in _ENABLED_MARKETS]
    h2h_signals = [s for s in enabled if s.get("market", "h2h") == "h2h"]
    spread_signals = [s for s in enabled if s.get("market") == "spreads"]
    total_signals = [s for s in enabled if s.get("market") == "totals"]

    result["all_signals"] = enabled
    result["signals_count"] = len(enabled)
    result["h2h_count"] = len(h2h_signals)
    result["spread_count"] = len(spread_signals)
    result["total_count"] = len(total_signals)
    result["top_signals"] = sorted(enabled, key=lambda item: item.get("edge_pct", 0), reverse=True)[
        :5
    ]
    result["runtime_mode"] = "multimarket_cached"
    return result
