"""Tennis odds fetcher via api-sports.io (tennis.api-sports.io).

Free tier: 100 requests/day (~3,000/month), no IP restrictions.
Register at https://api-sports.io/ (email only, no credit card).

Configure: API_FOOTBALL_KEY=your_key (shared with football injuries module)

Provides pre-match h2h odds for upcoming ATP/WTA matches.
Used as fallback when The Odds API is unavailable (HTTP 403 / quota exhausted).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error
from datetime import date, datetime, timezone
from typing import Any

_log = logging.getLogger(__name__)
_BASE = "https://v1.tennis.api-sports.io"


def is_configured() -> bool:
    return bool(os.environ.get("API_FOOTBALL_KEY", "").strip())


def get_key() -> str:
    return os.environ.get("API_FOOTBALL_KEY", "").strip()


def _get(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    """Make authenticated GET request to tennis.api-sports.io."""
    api_key = get_key()
    if not api_key:
        raise RuntimeError("API_FOOTBALL_KEY not configured")
    query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"{_BASE}{path}?{query}" if query else f"{_BASE}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "x-rapidapi-host": "v1.tennis.api-sports.io",
            "x-rapidapi-key": api_key,
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def get_today_fixtures() -> list[dict[str, Any]]:
    """Return today's ATP/WTA fixtures with odds."""
    today = date.today().isoformat()
    try:
        data = _get("/fixtures", {"date": today})
        return data.get("response", [])
    except Exception as exc:
        _log.warning("[apisports_tennis] fixtures fetch failed: %s", exc)
        return []


def get_odds_for_fixture(fixture_id: int) -> list[dict[str, Any]]:
    """Return bookmaker h2h odds for one fixture."""
    try:
        data = _get("/odds", {"fixture": str(fixture_id), "bookmaker": "1"})
        return data.get("response", [])
    except Exception as exc:
        _log.debug("[apisports_tennis] odds fetch failed for %d: %s", fixture_id, exc)
        return []


def fetch_tennis_events_as_odds_api_format(
    days_ahead: int = 1,
) -> list[dict[str, Any]]:
    """Fetch upcoming tennis matches and return in The Odds API compatible format.

    Returns list of event dicts with the same schema as The Odds API v4 /odds:
      id, sport_key, home_team, away_team, commence_time, bookmakers
    This allows the tennis scanner to consume api-sports.io data without changes.
    """
    if not is_configured():
        return []

    today = date.today()
    all_events: list[dict[str, Any]] = []

    for day_offset in range(days_ahead + 1):
        from datetime import timedelta
        target_date = (today + timedelta(days=day_offset)).isoformat()
        try:
            data = _get("/fixtures", {"date": target_date})
            fixtures = data.get("response", [])
        except Exception as exc:
            _log.warning("[apisports_tennis] fixtures %s failed: %s", target_date, exc)
            continue

        for fixture in fixtures:
            try:
                event = _convert_fixture(fixture)
                if event:
                    all_events.append(event)
            except Exception as exc:
                _log.debug("[apisports_tennis] convert failed: %s", exc)

    _log.info("[apisports_tennis] fetched %d events", len(all_events))
    return all_events


def _convert_fixture(fixture: dict[str, Any]) -> dict[str, Any] | None:
    """Convert api-sports.io fixture to The Odds API v4 event format."""
    f = fixture.get("fixture", {})
    fixture_id = f.get("id")
    if not fixture_id:
        return None

    players = fixture.get("players", [])
    if len(players) < 2:
        return None

    p1_name = players[0].get("player", {}).get("name", "")
    p2_name = players[1].get("player", {}).get("name", "")
    if not p1_name or not p2_name:
        return None

    start_dt = f.get("date", "")

    # Fetch odds for this fixture (costs 1 API call)
    bookmakers = _build_bookmakers(fixture_id, p1_name, p2_name)

    return {
        "id": str(fixture_id),
        "sport_key": "tennis_atp",
        "sport_title": "Tennis ATP",
        "home_team": p1_name,
        "away_team": p2_name,
        "commence_time": start_dt,
        "bookmakers": bookmakers,
    }


def _build_bookmakers(
    fixture_id: int,
    p1_name: str,
    p2_name: str,
) -> list[dict[str, Any]]:
    """Fetch odds and return in The Odds API bookmakers list format."""
    odds_data = get_odds_for_fixture(fixture_id)
    bookmakers: list[dict[str, Any]] = []

    for book in odds_data:
        bk_name = book.get("bookmaker", {}).get("name", "unknown").lower().replace(" ", "_")
        bets = book.get("bets", [])

        h2h_bet = next((b for b in bets if b.get("name", "").lower() in ("match winner", "winner")), None)
        if not h2h_bet:
            continue

        outcomes: list[dict[str, Any]] = []
        for val in h2h_bet.get("values", []):
            label = str(val.get("value", ""))
            try:
                price = float(val.get("odd", 0))
            except (TypeError, ValueError):
                continue
            if price < 1.01:
                continue
            # Map "Home"/"Away" labels to player names
            if label.lower() in ("home", "1"):
                outcomes.append({"name": p1_name, "price": price})
            elif label.lower() in ("away", "2"):
                outcomes.append({"name": p2_name, "price": price})

        if len(outcomes) >= 2:
            bookmakers.append({
                "key": bk_name,
                "title": book.get("bookmaker", {}).get("name", bk_name),
                "markets": [{"key": "h2h", "outcomes": outcomes}],
            })

    return bookmakers
