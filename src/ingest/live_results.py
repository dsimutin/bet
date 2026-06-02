"""Live results lookup from The Odds API for current/recent matches.

Fallback when historical data doesn't have today's results.
Useful for paper trading where settlement needs live match outcomes.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

_log = logging.getLogger(__name__)
_BASE = "https://api.the-odds-api.com/v4"

# Map league codes to The Odds API sport keys
_LEAGUE_TO_SPORT = {
    "EPL": "soccer_epl",
    "BUNDESLIGA": "soccer_germany_bundesliga",
    "LALIGA": "soccer_spain_la_liga",
    "SERIEA": "soccer_italy_serie_a",
    "LIGUE1": "soccer_france_ligue_one",
}


def get_live_match_result(
    home_team: str,
    away_team: str,
    match_date: date,
    league: str,
    api_key: str,
) -> dict[str, Any] | None:
    """Query The Odds API for a recently completed match result.

    Caches results aggressively (6 hours) since match results don't change.
    Returns:
        {
            "home_team": "...",
            "away_team": "...",
            "result_ft": "H" | "D" | "A",
            "match_date": YYYY-MM-DD,
            "source": "odds_api"
        }
        or None if not found / not completed.
    """
    if not api_key or league.upper() not in _LEAGUE_TO_SPORT:
        return None

    sport_key = _LEAGUE_TO_SPORT[league.upper()]

    # Check cache first — results are immutable once matched
    cache_key = f"live_result:{sport_key}:{league}:{home_team}:{away_team}:{match_date}"
    from src.infrastructure import odds_cache
    cached = odds_cache.get(cache_key)
    if isinstance(cached, dict):
        _log.debug("[live_results] cache hit: %s", cache_key)
        return cached
    if cached == "NOT_FOUND":  # Explicitly cache miss to avoid repeated API calls
        return None

    # Query a window around the match date (±3 days to catch delayed results)
    try:
        start_date = (match_date - timedelta(days=1)).isoformat()
        end_date = (match_date + timedelta(days=2)).isoformat()

        query = (
            f"apiKey={api_key}&sport={sport_key}"
            f"&date_format=iso&commenceTimeFrom={start_date}T00:00:00Z"
            f"&commenceTimeTo={end_date}T23:59:59Z"
        )
        url = f"{_BASE}/sports/{sport_key}/odds?{query}"

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "bet-analytics/1.0"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        if not isinstance(data, list):
            odds_cache.set(cache_key, "NOT_FOUND", 6 * 3600)
            return None

        # Find matching event and check if it's completed with a result
        for event in data:
            h_name = str(event.get("home_team", "")).lower().strip()
            a_name = str(event.get("away_team", "")).lower().strip()
            completed = event.get("completed", False)
            bookmakers = event.get("bookmakers", [])

            # Fuzzy match team names
            if not _teams_match(home_team, h_name) or not _teams_match(away_team, a_name):
                continue

            # If completed, try to extract result from bookmaker odds
            # (H, D, A outcomes present = match likely completed)
            if completed and bookmakers:
                result_ft = _infer_result_from_odds(event, home_team, away_team)
                if result_ft:
                    result = {
                        "home_team": home_team,
                        "away_team": away_team,
                        "result_ft": result_ft,
                        "match_date": str(match_date),
                        "source": "odds_api",
                    }
                    # Cache for 6 hours (result immutable)
                    odds_cache.set(cache_key, result, 6 * 3600)
                    _log.info(
                        "[live_results] found & cached %s: %s vs %s → %s",
                        league,
                        home_team,
                        away_team,
                        result_ft,
                    )
                    return result

        # Not found, cache the miss for 1 hour
        odds_cache.set(cache_key, "NOT_FOUND", 1 * 3600)
        return None

    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            _log.warning("[live_results] API quota exhausted")
        else:
            _log.warning("[live_results] HTTP %d: %s", exc.code, exc)
        return None
    except Exception as exc:
        _log.warning("[live_results] lookup failed: %s", exc)
        return None


def _teams_match(signal_team: str, api_team: str) -> bool:
    """Check if team names match (loose matching for different naming conventions)."""
    sig = " ".join(signal_team.lower().split())
    api = " ".join(api_team.lower().split())

    # Exact match
    if sig == api:
        return True

    # One contains the other (e.g. "Manchester United FC" vs "Manchester United")
    if sig in api or api in sig:
        return True

    return False


def _infer_result_from_odds(
    event: dict[str, Any], home_team: str, away_team: str
) -> Literal["H", "D", "A"] | None:
    """Infer match result from odds movement (simplified heuristic).

    This is NOT accurate for live betting — only works if match is clearly
    completed (moneyline odds collapsed to extreme values or event marked completed).

    Returns None if we cannot confidently infer the result.
    """
    bookmakers = event.get("bookmakers", [])
    if not bookmakers:
        return None

    # Use first bookmaker's h2h odds if available
    for bm in bookmakers:
        markets = bm.get("markets", [])
        for market in markets:
            if market.get("key") != "h2h":
                continue
            outcomes = market.get("outcomes", [])
            if not outcomes or len(outcomes) < 3:
                continue

            # outcomes: [home, draw, away]
            h_odds = float(outcomes[0].get("price", 0) or 0)
            d_odds = float(outcomes[1].get("price", 0) or 0)
            a_odds = float(outcomes[2].get("price", 0) or 0)

            # If one outcome is extremely low (<1.1), match likely ended with that result
            if h_odds > 0 and h_odds < 1.1:
                return "H"
            if d_odds > 0 and d_odds < 1.1:
                return "D"
            if a_odds > 0 and a_odds < 1.1:
                return "A"

    return None
