"""Quota-efficient Odds API helpers shared by football and tennis runtime paths.

The Odds API charges by market and region. Production delivery therefore uses
h2h only, one configurable region by default, and a shared Supabase-backed TTL
cache. Research scripts may still request wider market coverage explicitly.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from src.infrastructure import odds_cache

_log = logging.getLogger(__name__)
_BASE = "https://api.the-odds-api.com/v4"


def _should_retry_request(exc: Exception) -> bool:
    """Retry on transient errors, but not on quota exhaustion (HTTP 429)."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code != 429
    return isinstance(exc, (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError))


def _csv_env(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def _ttl_seconds() -> int:
    # Default 28800s (8h) so that scans at 7:00 and 15:00 UTC share one cache window.
    # Set ODDS_CACHE_TTL_SECONDS to override. Minimum 300s enforced.
    try:
        return max(int(os.environ.get("ODDS_CACHE_TTL_SECONDS", "28800")), 300)
    except ValueError:
        return 28800


def get_football_h2h_odds(sport_key: str, api_key: str) -> list[dict[str, Any]]:
    """Return cached live football h2h odds for one league."""
    regions = _csv_env("FOOTBALL_ODDS_REGIONS", "eu")
    cache_key = f"odds:football:{sport_key}:regions={','.join(regions)}:markets=h2h"
    cached = odds_cache.get(cache_key)
    if isinstance(cached, list):
        _log.info("[runtime-odds] football cache hit %s (%d events)", sport_key, len(cached))
        return cached

    data, headers = _fetch_odds(sport_key, api_key, regions, ["h2h"])
    odds_cache.set(cache_key, data, _ttl_seconds())
    _log_quota("football", sport_key, headers, len(data))
    return data


def get_tennis_h2h_events(api_key: str) -> list[dict[str, Any]]:
    """Return cached h2h odds for currently active tennis competitions.

    Primary source: The Odds API.
    Fallback: api-sports.io tennis (if API_FOOTBALL_KEY configured and primary fails).
    """
    regions = _csv_env("TENNIS_ODDS_REGIONS", "eu")
    max_keys = _int_env("TENNIS_MAX_ACTIVE_KEYS", 2, minimum=1, maximum=6)
    keys = get_active_tennis_keys(api_key)[:max_keys]

    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    primary_failed = False

    for sport_key in keys:
        cache_key = f"odds:tennis:{sport_key}:regions={','.join(regions)}:markets=h2h"
        cached = odds_cache.get(cache_key)
        if isinstance(cached, list):
            data = cached
            _log.info("[runtime-odds] tennis cache hit %s (%d events)", sport_key, len(data))
        else:
            try:
                data, headers = _fetch_odds(sport_key, api_key, regions, ["h2h"])
                odds_cache.set(cache_key, data, _ttl_seconds())
                _log_quota("tennis", sport_key, headers, len(data))
            except Exception as exc:
                _log.warning("[runtime-odds] tennis %s fetch failed: %s", sport_key, exc)
                primary_failed = True
                continue

        for event in data:
            event_id = str(event.get("id", ""))
            if not event_id or event_id in seen:
                continue
            seen.add(event_id)
            enriched = dict(event)
            enriched["_sport_key"] = sport_key
            events.append(enriched)

    # Fallback: api-sports.io tennis when The Odds API is blocked or returns nothing
    if (primary_failed or not events):
        try:
            from src.ingest.apisports_tennis import fetch_tennis_events_as_odds_api_format, is_configured as apisports_ok
            if apisports_ok():
                fallback = fetch_tennis_events_as_odds_api_format(days_ahead=1)
                for event in fallback:
                    event_id = str(event.get("id", ""))
                    if not event_id or event_id in seen:
                        continue
                    seen.add(event_id)
                    events.append(event)
                if fallback:
                    _log.info("[runtime-odds] tennis fallback (api-sports.io): %d events", len(fallback))
        except Exception as exc:
            _log.debug("[runtime-odds] tennis fallback failed: %s", exc)

    return events


@retry(
    retry=retry_if_exception(_should_retry_request),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=16),
    reraise=True,
)
def _fetch_active_sports(api_key: str) -> list[dict[str, Any]]:
    """Fetch active sports list from The Odds API with exponential backoff."""
    req = urllib.request.Request(
        f"{_BASE}/sports?apiKey={api_key}",
        headers={"User-Agent": "bet-analytics/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def get_active_tennis_keys(api_key: str) -> list[str]:
    """Cache the free sports-list lookup and return active ATP/WTA tennis keys."""
    cache_key = "sports:active-tennis"
    cached = odds_cache.get(cache_key)
    if isinstance(cached, list) and cached:
        return [str(item) for item in cached]

    try:
        sports = _fetch_active_sports(api_key)
        keys = [
            str(item["key"])
            for item in sports
            if item.get("active") and "tennis" in str(item.get("key", "")).lower()
        ]
    except Exception as exc:
        _log.warning("[runtime-odds] tennis sport discovery failed (after retries): %s", exc)
        keys = []

    if not keys:
        keys = ["tennis_atp", "tennis_wta"]
    odds_cache.set(cache_key, keys, 86400)
    return keys


@retry(
    retry=retry_if_exception(_should_retry_request),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=16),
    reraise=True,
)
def _fetch_odds(
    sport_key: str,
    api_key: str,
    regions: list[str],
    markets: list[str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    query = (
        f"apiKey={api_key}&regions={','.join(regions)}&markets={','.join(markets)}"
        "&oddsFormat=decimal&dateFormat=iso"
    )
    req = urllib.request.Request(
        f"{_BASE}/sports/{sport_key}/odds?{query}",
        headers={"User-Agent": "bet-analytics/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read())
            headers = {
                "remaining": resp.headers.get("x-requests-remaining", "?"),
                "used": resp.headers.get("x-requests-used", "?"),
                "last": resp.headers.get("x-requests-last", "?"),
            }
            # Record quota usage
            try:
                from src.monitoring.api_quota_monitor import record_odds_api_request
                record_odds_api_request(1)
            except Exception:
                pass
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("The Odds API quota exhausted (HTTP 429)") from exc
        raise
    if not isinstance(payload, list):
        return [], headers
    return payload, headers


def _log_quota(sport: str, sport_key: str, headers: dict[str, str], events: int) -> None:
    _log.info(
        "[runtime-odds] %s %s events=%d quota_remaining=%s used=%s last_cost=%s",
        sport,
        sport_key,
        events,
        headers.get("remaining", "?"),
        headers.get("used", "?"),
        headers.get("last", "?"),
    )


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(min(int(os.environ.get(name, str(default))), maximum), minimum)
    except ValueError:
        return default
