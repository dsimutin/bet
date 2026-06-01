"""Low-quota tennis runtime scanner for the Render free-tier deployment.

The research scanner supports h2h, spreads and totals across many regions.  That
is useful offline but expensive in production: The Odds API charges per market
per region.  Until market-specific settlement is validated, runtime delivery is
restricted to h2h and a small configurable region set.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from src.signals import tennis_signal_scan as research_scanner

_log = logging.getLogger(__name__)


def scan_tennis_h2h_runtime(model_path: Path, api_key: str) -> dict[str, Any]:
    """Run the existing model with a quota-efficient h2h-only odds fetcher."""
    original_fetch = research_scanner._fetch_atp_events
    research_scanner._fetch_atp_events = _fetch_h2h_events  # type: ignore[attr-defined]
    try:
        result = research_scanner.scan_tennis_signals(model_path=model_path, api_key=api_key)
    finally:
        research_scanner._fetch_atp_events = original_fetch  # type: ignore[attr-defined]

    h2h_signals = [
        signal for signal in result.get("all_signals", [])
        if signal.get("market", "h2h") == "h2h"
    ]
    result["all_signals"] = h2h_signals
    result["signals_count"] = len(h2h_signals)
    result["top_signals"] = sorted(
        h2h_signals, key=lambda item: item.get("edge_pct", 0), reverse=True
    )[:5]
    result["runtime_mode"] = "h2h_low_quota"
    result["odds_regions"] = _regions()
    return result


def _regions() -> list[str]:
    raw = os.environ.get("TENNIS_ODDS_REGIONS", "eu,uk")
    return [value.strip() for value in raw.split(",") if value.strip()]


def _fetch_h2h_events(api_key: str) -> list[dict[str, Any]] | None:
    """Fetch h2h odds only and log API quota headers for diagnostics."""
    all_events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    regions = ",".join(_regions())
    sport_keys = research_scanner._discover_tennis_keys(api_key)

    for sport_key in sport_keys:
        url = (
            f"{research_scanner._ODDS_API_BASE}/sports/{sport_key}/odds"
            f"?apiKey={api_key}&regions={regions}&markets=h2h"
            "&oddsFormat=decimal&dateFormat=iso"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bet-analytics/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                remaining = resp.headers.get("x-requests-remaining", "?")
                used = resp.headers.get("x-requests-used", "?")
                last_cost = resp.headers.get("x-requests-last", "?")
            _log.info(
                "[tennis-runtime] %s events=%d quota_remaining=%s used=%s last_cost=%s",
                sport_key,
                len(data) if isinstance(data, list) else 0,
                remaining,
                used,
                last_cost,
            )
            if not isinstance(data, list):
                continue
            for event in data:
                event_id = str(event.get("id", ""))
                if not event_id or event_id in seen_ids:
                    continue
                seen_ids.add(event_id)
                event["_sport_key"] = sport_key
                all_events.append(event)
        except urllib.error.HTTPError as exc:
            _log.warning("[tennis-runtime] %s HTTP %s %s", sport_key, exc.code, exc.reason)
        except Exception as exc:
            _log.warning("[tennis-runtime] %s fetch failed: %s", sport_key, exc)

    return all_events if all_events else None
