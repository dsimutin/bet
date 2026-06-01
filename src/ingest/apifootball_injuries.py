"""Optional injury data from API-Football (api-football.com).

Free tier: 100 requests/day.
Configure: API_FOOTBALL_KEY=your_key in environment.

Returns a list of absent/doubtful players per team,
compatible with IllnessFeatureBuilder.

Usage:
    from src.ingest.apifootball_injuries import fetch_injuries_for_fixture
    injuries = fetch_injuries_for_fixture(fixture_id, api_key)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

_log = logging.getLogger(__name__)
_BASE = "https://v3.football.api-sports.io"


def _should_retry_apifootball(exc: Exception) -> bool:
    """Retry on transient errors, but not on quota exhaustion (429) or auth (403)."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code not in (429, 403)
    return isinstance(exc, (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError))


# League IDs on API-Football
_LEAGUE_IDS: dict[str, int] = {
    "EPL": 39,
    "BUNDESLIGA": 78,
    "LALIGA": 140,
    "SERIEA": 135,
    "LIGUE1": 61,
}


def get_api_football_key() -> str:
    return os.environ.get("API_FOOTBALL_KEY", "").strip()


def is_configured() -> bool:
    return bool(get_api_football_key())


def fetch_fixture_id(
    home_team: str,
    away_team: str,
    match_date: str,
    league: str,
    api_key: str,
) -> int | None:
    """Find API-Football fixture ID for a match."""
    league_id = _LEAGUE_IDS.get(league.upper())
    if not league_id:
        return None

    url = f"{_BASE}/fixtures" f"?date={match_date}&league={league_id}&season={match_date[:4]}"
    try:
        data = _get(url, api_key)
        for fixture in data.get("response", []):
            teams = fixture.get("teams", {})
            h = str(teams.get("home", {}).get("name", "")).lower()
            a = str(teams.get("away", {}).get("name", "")).lower()
            if home_team.lower() in h or h in home_team.lower():
                if away_team.lower() in a or a in away_team.lower():
                    return int(fixture["fixture"]["id"])
    except Exception as exc:
        _log.warning("[injuries] fetch_fixture_id failed: %s", exc)
    return None


def fetch_injuries_for_fixture(
    fixture_id: int,
    api_key: str,
) -> list[dict[str, Any]]:
    """Fetch injury report for a fixture. Returns list of absent/doubtful players."""
    url = f"{_BASE}/injuries?fixture={fixture_id}"
    try:
        data = _get(url, api_key)
        result = []
        for item in data.get("response", []):
            player = item.get("player", {})
            team = item.get("team", {})
            result.append(
                {
                    "player_name": player.get("name", "?"),
                    "player_id": str(player.get("id", "")),
                    "team_name": team.get("name", "?"),
                    "team_id": str(team.get("id", "")),
                    "type": item.get("type", "?"),  # e.g. "Missing Fixture"
                    "reason": item.get("reason", "?"),  # e.g. "Knee Injury"
                }
            )
        return result
    except Exception as exc:
        _log.warning("[injuries] fetch_injuries failed fixture=%s: %s", fixture_id, exc)
        return []


def get_injuries_for_match(
    home_team: str,
    away_team: str,
    match_date: str,
    league: str,
) -> dict[str, list[dict[str, Any]]]:
    """High-level: fetch injuries for both teams. Returns {home: [...], away: [...]}.

    Returns empty dicts if API key not configured or request fails.
    match_date: 'YYYY-MM-DD'
    """
    api_key = get_api_football_key()
    if not api_key:
        return {"home": [], "away": [], "available": False}

    fixture_id = fetch_fixture_id(home_team, away_team, match_date, league, api_key)
    if fixture_id is None:
        return {"home": [], "away": [], "available": False, "note": "fixture_not_found"}

    injuries = fetch_injuries_for_fixture(fixture_id, api_key)
    home_injuries = [i for i in injuries if home_team.lower() in i["team_name"].lower()]
    away_injuries = [i for i in injuries if away_team.lower() in i["team_name"].lower()]

    _log.info(
        "[injuries] %s vs %s: %d home / %d away injuries",
        home_team,
        away_team,
        len(home_injuries),
        len(away_injuries),
    )

    return {
        "home": home_injuries,
        "away": away_injuries,
        "available": True,
        "fixture_id": fixture_id,
    }


def format_injuries_for_signal(injuries: dict[str, Any]) -> str:
    """Format injuries dict into a short text line for Telegram signal."""
    if not injuries.get("available"):
        return ""

    home = injuries.get("home", [])
    away = injuries.get("away", [])

    def _fmt(lst: list[dict]) -> str:
        if not lst:
            return "нет травм"
        names = [f"{p['player_name']} ({p['reason']})" for p in lst[:3]]
        more = f" +ещё {len(lst)-3}" if len(lst) > 3 else ""
        return ", ".join(names) + more

    return f"🏥 Травмы: хозяева — {_fmt(home)} | гости — {_fmt(away)}"


@retry(
    retry=retry_if_exception(_should_retry_apifootball),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=16),
    reraise=True,
)
def _get(url: str, api_key: str) -> dict[str, Any]:
    """Fetch JSON from API-Football with exponential backoff on transient errors."""
    req = urllib.request.Request(
        url,
        headers={
            "x-rapidapi-host": "v3.football.api-sports.io",
            "x-rapidapi-key": api_key,
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())
