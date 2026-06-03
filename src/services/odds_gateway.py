"""Gateway for The Odds API HTTP requests.

All production/runtime Odds API calls should go through this module so quota checks,
provider headers, and URL redaction stay consistent.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_BASE = "https://api.the-odds-api.com/v4"


@dataclass(frozen=True)
class OddsGatewayResponse:
    payload: Any
    headers: dict[str, str]
    redacted_url: str


def fetch_the_odds_api_json(
    path: str,
    *,
    api_key: str,
    query: dict[str, str],
    source: str,
    sport_key: str = "",
    markets: list[str] | None = None,
    regions: list[str] | None = None,
    priority_refresh: bool = False,
) -> OddsGatewayResponse:
    """Fetch JSON from The Odds API after quota checks and record provider headers."""
    from src.monitoring.api_quota_monitor import (
        can_make_odds_api_request,
        record_odds_api_provider_headers,
    )

    if not can_make_odds_api_request(priority_refresh=priority_refresh):
        raise RuntimeError("The Odds API request blocked by quota hard stop")
    params = {"apiKey": api_key, **query}
    url = f"{_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "bet-analytics/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read()
        payload = json.loads(body)
        headers = {
            "remaining": resp.headers.get("x-requests-remaining", ""),
            "used": resp.headers.get("x-requests-used", ""),
            "last": resp.headers.get("x-requests-last", ""),
        }
        record_odds_api_provider_headers(
            {
                "provider": "the_odds_api",
                "endpoint": path,
                "sport_key": sport_key,
                "markets": ",".join(markets or []),
                "regions": ",".join(regions or []),
                "requested_at_utc": datetime.now(timezone.utc).isoformat(),
                "response_status": getattr(resp, "status", 200),
                "x_requests_used": headers["used"],
                "x_requests_remaining": headers["remaining"],
                "x_requests_last": headers["last"],
                "source": source,
            }
        )
    return OddsGatewayResponse(payload=payload, headers=headers, redacted_url=redact_url(url))


def redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = [
        (key, "[REDACTED]" if key.lower() in {"apikey", "api_key", "key"} else value)
        for key, value in pairs
    ]
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(redacted),
            parsed.fragment,
        )
    )
