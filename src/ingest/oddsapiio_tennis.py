"""Tennis odds via odds-api.io — free tier, no IP restrictions.

Free tier: 100 requests/hour, 2 bookmakers simultaneously.
Register at https://odds-api.io/ (email only, no credit card required).

Configure:
  ODDS_API_IO_KEY=your_key
  ODDS_API_IO_BOOKMAKERS=pinnacle,bet365   (optional, comma-separated slugs, max 2 free)

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

# Well-known bookmaker slugs on odds-api.io (in preference order for devigging)
_PREFERRED_BOOKMAKERS = [
    "pinnacle", "bet365", "betway", "unibet", "william-hill",
    "1xbet", "betfair", "marathonbet", "bwin", "betsson",
]


def is_configured() -> bool:
    return bool(os.environ.get("ODDS_API_IO_KEY", "").strip())


def get_key() -> str:
    return os.environ.get("ODDS_API_IO_KEY", "").strip()


def _fetch(path: str, params: dict[str, str], method: str = "GET") -> Any:
    api_key = get_key()
    if not api_key:
        raise RuntimeError("ODDS_API_IO_KEY not configured")
    params = {**params, "apiKey": api_key}
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{_BASE}{path}?{query}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "bet-analytics/1.0", "Accept": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body_txt = ""
        try:
            body_txt = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        if exc.code == 429:
            raise RuntimeError(f"odds-api.io quota exhausted (HTTP 429): {body_txt}") from exc
        _log.debug("[oddsapiio] HTTP %d for %s: %s", exc.code, path, body_txt)
        raise


def _get_available_bookmakers() -> list[str]:
    """Fetch available bookmaker slugs and return top picks for devigging."""
    try:
        data = _fetch("/bookmakers", {})
        bks = data if isinstance(data, list) else data.get("data", data.get("results", []))
        slugs = []
        for bk in bks:
            slug = str(bk.get("slug") or bk.get("key") or bk.get("name", "")).lower().replace(" ", "-")
            if slug:
                slugs.append(slug)
        # Prefer known sharp bookmakers for devigging
        preferred = [s for s in _PREFERRED_BOOKMAKERS if s in slugs]
        rest = [s for s in slugs if s not in preferred]
        ordered = preferred + rest
        _log.info("[oddsapiio] %d bookmakers available, sample: %s", len(ordered), ordered[:5])
        return ordered
    except Exception as exc:
        _log.warning("[oddsapiio] bookmakers fetch failed: %s — using defaults", exc)
        return _PREFERRED_BOOKMAKERS


def _choose_bookmakers() -> list[str]:
    """Choose up to 2 bookmakers from env or available list (free tier: max 2)."""
    env_raw = os.environ.get("ODDS_API_IO_BOOKMAKERS", "").strip()
    if env_raw:
        bms = [b.strip().lower() for b in env_raw.split(",") if b.strip()]
        return bms[:2]
    # Auto-discover
    available = _get_available_bookmakers()
    return available[:2]


def _player_name(event: dict[str, Any], side: str) -> str:
    """Extract player name, handle both 'home'/'away' and 'home_team'/'away_team'."""
    return str(
        event.get(f"{side}_team") or event.get(side) or ""
    ).strip()


def _market_key(market_name: str) -> str:
    name = market_name.lower()
    if name in ("moneyline", "match winner", "h2h", "winner", "1x2", "match_winner"):
        return "h2h"
    if name in ("spread", "handicap", "game spread", "set handicap"):
        return "spreads"
    if name in ("total", "over/under", "game total", "total games"):
        return "totals"
    return name


def _convert_to_odds_api_format(
    event: dict[str, Any],
    odds: dict[str, Any],
) -> dict[str, Any] | None:
    """Convert odds-api.io event+odds dicts to The Odds API v4 event format."""
    event_id = str(event.get("id", ""))
    home = _player_name(event, "home")
    away = _player_name(event, "away")
    start_time = str(event.get("startTime") or event.get("commence_time") or "")

    if not event_id or not home or not away:
        return None

    # Bookmakers can be in odds response or embedded in event
    raw_bks = None
    if isinstance(odds, dict):
        raw_bks = odds.get("bookmakers")
    if raw_bks is None:
        raw_bks = event.get("bookmakers", [])

    if not isinstance(raw_bks, list) or not raw_bks:
        return None

    bookmakers_out: list[dict[str, Any]] = []
    for bk in raw_bks:
        bk_name = str(bk.get("name") or bk.get("title") or bk.get("key") or "unknown")
        bk_key = bk_name.lower().replace(" ", "-")
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
    """Fetch tennis events with odds and return in The Odds API v4 format.

    Strategy:
    1. GET /events?sport=tennis  (no status filter — includes live + upcoming)
    2. For each event: GET /odds?eventId=X&bookmakers=BK1,BK2
    3. Convert to v4 format
    """
    if not is_configured():
        return []

    bookmakers = _choose_bookmakers()
    bookmakers_str = ",".join(bookmakers)
    _log.info("[oddsapiio] fetching tennis events (bookmakers: %s)", bookmakers_str)

    # Step 1: get events — NO status filter so we get live + upcoming (Roland Garros!)
    try:
        events_raw = _fetch("/events", {"sport": "tennis"})
    except Exception as exc:
        _log.warning("[oddsapiio] /events fetch failed: %s", exc)
        # Try alternative: fetch events for specific league
        return _fetch_via_leagues(bookmakers_str)

    # Handle wrapped responses
    if isinstance(events_raw, dict):
        events_raw = events_raw.get("data") or events_raw.get("results") or events_raw.get("events") or []
    if not isinstance(events_raw, list):
        _log.warning("[oddsapiio] unexpected /events response type: %s", type(events_raw).__name__)
        return _fetch_via_leagues(bookmakers_str)

    _log.info("[oddsapiio] /events returned %d tennis events", len(events_raw))

    if not events_raw:
        # Try league-based fallback
        return _fetch_via_leagues(bookmakers_str)

    return _fetch_odds_for_events(events_raw, bookmakers_str)


def _fetch_odds_for_events(
    events_raw: list[dict[str, Any]],
    bookmakers_str: str,
) -> list[dict[str, Any]]:
    """Fetch odds for a list of events and return converted results."""
    results: list[dict[str, Any]] = []
    no_bk_count = 0

    for event in events_raw[:50]:  # cap at 50 to protect quota
        if not isinstance(event, dict):
            continue
        event_id = event.get("id")
        if not event_id:
            continue

        home = _player_name(event, "home")
        away = _player_name(event, "away")

        # Some APIs embed bookmakers directly in the event object
        if event.get("bookmakers"):
            converted = _convert_to_odds_api_format(event, {})
            if converted:
                results.append(converted)
            continue

        # Fetch odds separately
        odds_raw: dict[str, Any] = {}
        try:
            resp = _fetch("/odds", {
                "eventId": str(event_id),
                "bookmakers": bookmakers_str,
            })
            if isinstance(resp, dict):
                odds_raw = resp
            elif isinstance(resp, list) and resp:
                # Some APIs return list of odds objects
                odds_raw = {"bookmakers": resp}
        except Exception as exc:
            _log.debug("[oddsapiio] /odds failed for %s vs %s: %s", home, away, exc)

        converted = _convert_to_odds_api_format(event, odds_raw)
        if converted:
            results.append(converted)
        else:
            no_bk_count += 1
            _log.debug("[oddsapiio] no bookmakers for %s vs %s (event_id=%s)", home, away, event_id)

    _log.info(
        "[oddsapiio] %d events with odds, %d without bookmakers",
        len(results),
        no_bk_count,
    )
    return results


def _fetch_via_leagues(bookmakers_str: str) -> list[dict[str, Any]]:
    """Fallback: discover active tennis leagues and fetch events per league."""
    _log.info("[oddsapiio] trying league-based discovery for tennis")
    try:
        leagues_raw = _fetch("/leagues", {"sport": "tennis"})
        if isinstance(leagues_raw, dict):
            leagues_raw = leagues_raw.get("data") or leagues_raw.get("results") or []
        if not isinstance(leagues_raw, list):
            _log.warning("[oddsapiio] /leagues response unexpected: %s", type(leagues_raw).__name__)
            return []

        # Filter for currently active Grand Slams and ATP events
        active_slugs: list[str] = []
        for league in leagues_raw:
            slug = str(league.get("slug") or league.get("key") or "")
            name = str(league.get("name") or "").lower()
            if any(kw in name for kw in ("roland garros", "french open", "atp", "wta")):
                active_slugs.append(slug)
        active_slugs = active_slugs[:5]  # cap
        _log.info("[oddsapiio] found %d active tennis leagues: %s", len(active_slugs), active_slugs)

    except Exception as exc:
        _log.warning("[oddsapiio] /leagues failed: %s", exc)
        return []

    all_events: list[dict[str, Any]] = []
    for slug in active_slugs:
        try:
            events_raw = _fetch("/events", {"sport": "tennis", "league": slug})
            if isinstance(events_raw, dict):
                events_raw = events_raw.get("data") or events_raw.get("results") or []
            if isinstance(events_raw, list):
                all_events.extend(events_raw)
        except Exception as exc:
            _log.debug("[oddsapiio] /events?league=%s failed: %s", slug, exc)

    if not all_events:
        _log.warning("[oddsapiio] no tennis events found via league discovery")
        return []

    return _fetch_odds_for_events(all_events, bookmakers_str)


def diagnostic_info() -> dict[str, Any]:
    """Run diagnostic and return status dict for /debug/tennis-raw endpoint."""
    if not is_configured():
        return {"status": "not_configured", "message": "ODDS_API_IO_KEY not set in environment"}

    info: dict[str, Any] = {"api": "odds-api.io", "base_url": _BASE}

    # Test bookmakers endpoint
    try:
        avail = _get_available_bookmakers()
        info["available_bookmakers"] = avail[:10]
        info["bookmakers_ok"] = True
    except Exception as exc:
        info["bookmakers_error"] = str(exc)
        info["bookmakers_ok"] = False

    # Choose bookmakers
    chosen = _choose_bookmakers()
    info["chosen_bookmakers"] = chosen

    # Test events endpoint
    try:
        events_raw = _fetch("/events", {"sport": "tennis"})
        if isinstance(events_raw, dict):
            events_raw = events_raw.get("data") or events_raw.get("results") or events_raw.get("events") or []
        if isinstance(events_raw, list):
            info["events_count"] = len(events_raw)
            info["events_sample"] = [
                {
                    "id": e.get("id"),
                    "home": _player_name(e, "home"),
                    "away": _player_name(e, "away"),
                    "startTime": e.get("startTime"),
                    "has_bookmakers": bool(e.get("bookmakers")),
                }
                for e in events_raw[:5]
            ]
            info["events_ok"] = True
        else:
            info["events_raw_type"] = type(events_raw).__name__
            info["events_ok"] = False
    except Exception as exc:
        info["events_error"] = str(exc)
        info["events_ok"] = False

    # Full fetch
    try:
        events = fetch_tennis_events_as_odds_api_format()
        info["events_with_odds"] = len(events)
        info["final_status"] = "ok" if events else "no_events_with_odds"
    except Exception as exc:
        info["fetch_error"] = str(exc)
        info["final_status"] = "error"

    return info
