"""Tennis odds via odds-api.io — free tier, no IP restrictions.

Free tier: 100 requests/hour, 2 bookmakers simultaneously.
Register at https://odds-api.io/ (email only, no credit card required).

Configure:
  ODDS_API_IO_KEY=your_key
  ODDS_API_IO_BOOKMAKERS=singbet,bet365   (optional, max 2 on free tier)

Converts odds-api.io format to The Odds API v4 schema so the rest of the
tennis pipeline works without changes.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error
from typing import Any

_log = logging.getLogger(__name__)
_BASE = "https://api2.odds-api.io/v3"


def is_configured() -> bool:
    return bool(os.environ.get("ODDS_API_IO_KEY", "").strip())


def get_key() -> str:
    return os.environ.get("ODDS_API_IO_KEY", "").strip()


def _get_bookmakers() -> list[str]:
    raw = os.environ.get("ODDS_API_IO_BOOKMAKERS", "singbet,bet365")
    bms = [b.strip().lower() for b in raw.split(",") if b.strip()]
    return bms[:2]  # free tier: max 2 bookmakers per call


def _fetch(path: str, params: dict[str, str]) -> Any:
    api_key = get_key()
    if not api_key:
        raise RuntimeError("ODDS_API_IO_KEY not configured")
    params = {**params, "apiKey": api_key}
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{_BASE}{path}?{query}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "bet-analytics/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("odds-api.io quota exhausted (HTTP 429)") from exc
        raise


def _player_name(event: dict[str, Any], side: str) -> str:
    """Extract player name handling both 'home'/'away' and 'home_team'/'away_team' field names."""
    # odds-api.io may return either field name style
    key_a = f"{side}_team"  # home_team / away_team
    key_b = side            # home / away
    return str(event.get(key_a) or event.get(key_b) or "").strip()


def _market_key(market_name: str) -> str:
    """Map odds-api.io market names to The Odds API v4 market keys."""
    name = market_name.lower()
    if name in ("moneyline", "match winner", "h2h", "winner", "1x2"):
        return "h2h"
    if name in ("spread", "handicap", "game spread"):
        return "spreads"
    if name in ("total", "over/under", "game total"):
        return "totals"
    return name


def _convert_to_odds_api_format(
    event: dict[str, Any],
    odds: dict[str, Any],
) -> dict[str, Any] | None:
    """Convert a single event + its odds to The Odds API v4 event format."""
    event_id = str(event.get("id", ""))
    home = _player_name(event, "home")
    away = _player_name(event, "away")
    start_time = str(event.get("startTime", event.get("commence_time", "")))

    if not event_id or not home or not away:
        return None

    # Odds may be embedded in event dict or provided separately
    raw_bookmakers = odds.get("bookmakers") if isinstance(odds, dict) else None
    if raw_bookmakers is None:
        raw_bookmakers = event.get("bookmakers", [])

    if not isinstance(raw_bookmakers, list):
        return None

    bookmakers_out: list[dict[str, Any]] = []
    for bk in raw_bookmakers:
        bk_name = str(bk.get("name") or bk.get("key") or "unknown")
        bk_key = bk_name.lower().replace(" ", "_")
        raw_markets = bk.get("markets", [])
        if not isinstance(raw_markets, list):
            continue

        markets_out: list[dict[str, Any]] = []
        for market in raw_markets:
            mk_name = str(market.get("name") or market.get("key") or "")
            mk_key = _market_key(mk_name)

            raw_outcomes = market.get("outcomes", [])
            if not isinstance(raw_outcomes, list):
                continue

            outcomes_out: list[dict[str, Any]] = []
            for outcome in raw_outcomes:
                o_name = str(outcome.get("name", "")).strip()
                try:
                    price = float(outcome.get("price", 0))
                except (TypeError, ValueError):
                    continue
                if price > 1.01 and o_name:
                    outcomes_out.append({"name": o_name, "price": price})

            if len(outcomes_out) >= 2:
                markets_out.append({"key": mk_key, "outcomes": outcomes_out})

        if markets_out:
            bookmakers_out.append({
                "key": bk_key,
                "title": bk_name,
                "markets": markets_out,
            })

    if not bookmakers_out:
        return None

    return {
        "id": event_id,
        "sport_key": "tennis_atp",
        "sport_title": "Tennis",
        "home_team": home,
        "away_team": away,
        "commence_time": start_time,
        "bookmakers": bookmakers_out,
    }


def fetch_tennis_events_as_odds_api_format() -> list[dict[str, Any]]:
    """Fetch upcoming tennis events and return in The Odds API v4 format.

    Uses /events to list upcoming tennis matches, then /odds for each event.
    Results are cached externally by runtime_odds.py via odds_cache.
    """
    if not is_configured():
        return []

    # Step 1: get upcoming tennis events
    try:
        events_raw = _fetch("/events", {"sport": "tennis", "status": "upcoming"})
    except Exception as exc:
        _log.warning("[oddsapiio] events fetch failed: %s", exc)
        return []

    if not isinstance(events_raw, list):
        # Some responses are wrapped: {"data": [...]}
        if isinstance(events_raw, dict):
            events_raw = events_raw.get("data", events_raw.get("results", []))
        if not isinstance(events_raw, list):
            _log.warning("[oddsapiio] unexpected events response: %s", type(events_raw))
            return []

    if not events_raw:
        _log.info("[oddsapiio] no upcoming tennis events returned")
        return []

    _log.info("[oddsapiio] %d upcoming tennis events found", len(events_raw))

    # Step 2: fetch odds per event
    bookmakers_str = ",".join(_get_bookmakers())
    results: list[dict[str, Any]] = []

    for event in events_raw[:40]:  # cap to avoid quota exhaustion
        if not isinstance(event, dict):
            continue
        event_id = event.get("id")
        if not event_id:
            continue

        try:
            odds_raw = _fetch("/odds", {
                "eventId": str(event_id),
                "bookmakers": bookmakers_str,
            })
        except Exception as exc:
            _log.debug("[oddsapiio] odds fetch failed event=%s: %s", event_id, exc)
            # Try to use odds embedded directly in event dict
            odds_raw = {}

        converted = _convert_to_odds_api_format(event, odds_raw if isinstance(odds_raw, dict) else {})
        if converted:
            results.append(converted)

    _log.info("[oddsapiio] %d tennis events with h2h odds ready", len(results))
    return results
