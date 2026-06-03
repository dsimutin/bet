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
from datetime import datetime, timezone
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from src.infrastructure import odds_cache

_log = logging.getLogger(__name__)


def _should_retry_request(exc: BaseException) -> bool:
    """Retry on transient errors, but not on quota exhaustion (HTTP 429)."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code != 429
    return isinstance(exc, (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError))


def _csv_env(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def _int_seconds(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(int(os.environ.get(name, str(default))), minimum)
    except ValueError:
        return default


def _cache_storage_ttl_seconds() -> int:
    # Backward-compatible alias, but runtime freshness is checked separately.
    try:
        return max(
            int(
                os.environ.get(
                    "CACHE_STORAGE_TTL_SECONDS", os.environ.get("ODDS_CACHE_TTL_SECONDS", "28800")
                )
            ),
            300,
        )
    except ValueError:
        return 28800


def _priority_max_age_seconds() -> int:
    return _int_seconds("PRIORITY_ODDS_MAX_AGE_SECONDS", 900, minimum=60)


def _watchlist_max_age_seconds() -> int:
    return _int_seconds("WATCHLIST_ODDS_MAX_AGE_SECONDS", 3600, minimum=60)


def _wrap_snapshot(events: list[dict[str, Any]], fetched_at: str | None = None) -> dict[str, Any]:
    return {
        "events": events,
        "fetched_at_utc": fetched_at or datetime.now(timezone.utc).isoformat(),
    }


def _unwrap_snapshot(value: Any) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(value, dict) and isinstance(value.get("events"), list):
        return list(value["events"]), str(value.get("fetched_at_utc") or "")
    if isinstance(value, list):
        return value, None
    return [], None


def _annotate_snapshot(
    events: list[dict[str, Any]], fetched_at: str | None
) -> list[dict[str, Any]]:
    if not fetched_at:
        fetched_at = datetime.now(timezone.utc).isoformat()
    annotated: list[dict[str, Any]] = []
    for event in events:
        enriched = dict(event)
        enriched["_odds_snapshot_ts_utc"] = fetched_at
        annotated.append(enriched)
    return annotated


def _snapshot_age_seconds(fetched_at: str | None) -> float | None:
    if not fetched_at:
        return None
    try:
        dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()


def _freshness_tier(fetched_at: str | None, *, exotic: bool = False) -> str:
    age = _snapshot_age_seconds(fetched_at)
    if age is None:
        return "stale_blocked"
    if not exotic and age <= _priority_max_age_seconds():
        return "priority"
    max_watch = (
        _int_seconds("EXOTIC_WATCHLIST_ODDS_MAX_AGE_SECONDS", 14400, minimum=60)
        if exotic
        else _watchlist_max_age_seconds()
    )
    if age <= max_watch:
        return "watchlist"
    return "stale_blocked"


def get_football_h2h_odds(sport_key: str, api_key: str) -> list[dict[str, Any]]:
    """Return cached live football h2h odds for one league."""
    regions = _csv_env("FOOTBALL_ODDS_REGIONS", "eu")
    cache_key = f"odds:football:{sport_key}:regions={','.join(regions)}:markets=h2h"
    cached_events, cached_at = _unwrap_snapshot(odds_cache.get(cache_key))
    if cached_events and _freshness_tier(cached_at) == "priority":
        _log.info(
            "[runtime-odds] football priority cache hit %s (%d events)",
            sport_key,
            len(cached_events),
        )
        return _annotate_snapshot(cached_events, cached_at)

    try:
        data, headers = _fetch_odds(sport_key, api_key, regions, ["h2h"])
    except Exception:
        if cached_events:
            _log.warning(
                "[runtime-odds] football refresh failed for %s; using cached %s snapshot",
                sport_key,
                _freshness_tier(cached_at),
            )
            return _annotate_snapshot(cached_events, cached_at)
        raise
    fetched_at = datetime.now(timezone.utc).isoformat()
    odds_cache.set(cache_key, _wrap_snapshot(data, fetched_at), _cache_storage_ttl_seconds())
    _log_quota("football", sport_key, headers, len(data))
    return _annotate_snapshot(data, fetched_at)


def get_tennis_h2h_events(api_key: str) -> list[dict[str, Any]]:
    """Return cached h2h odds for currently active tennis competitions.

    Source priority:
    1. odds-api.io (if ODDS_API_IO_KEY configured) — free, 100 req/hour, no IP restrictions
    2. The Odds API (if ODDS_API_IO_KEY not set) — requires IP allowlist, may return HTTP 403

    See docs/TENNIS_ODDS_SOURCES.md for setup instructions and alternatives.
    """
    # Primary: odds-api.io when ODDS_API_IO_KEY is configured
    try:
        from src.ingest.oddsapiio_tennis import (
            is_configured as io_ok,
            fetch_tennis_events_as_odds_api_format,
        )

        if io_ok():
            cache_key = "odds:tennis:oddsapiio:h2h"
            cached_events, cached_at = _unwrap_snapshot(odds_cache.get(cache_key))
            if cached_events and _freshness_tier(cached_at) == "priority":
                _log.info(
                    "[runtime-odds] tennis cache hit via odds-api.io (%d events)",
                    len(cached_events),
                )
                return _annotate_snapshot(cached_events, cached_at)
            io_events = fetch_tennis_events_as_odds_api_format()
            if io_events:
                fetched_at = datetime.now(timezone.utc).isoformat()
                odds_cache.set(
                    cache_key, _wrap_snapshot(io_events, fetched_at), _cache_storage_ttl_seconds()
                )
                _log.info("[runtime-odds] tennis odds-api.io: %d events cached", len(io_events))
                return _annotate_snapshot(io_events, fetched_at)
            _log.warning(
                "[runtime-odds] odds-api.io returned no tennis events — falling back to The Odds API"
            )
    except Exception as exc:
        _log.warning("[runtime-odds] odds-api.io tennis failed: %s — falling back", exc)

    # Fallback: The Odds API (blocked by IP allowlist on Render free tier)
    regions = _csv_env("TENNIS_ODDS_REGIONS", "eu")
    max_keys = _int_env("TENNIS_MAX_ACTIVE_KEYS", 2, minimum=1, maximum=6)
    keys = get_active_tennis_keys(api_key)[:max_keys]

    events: list[dict[str, Any]] = []
    seen: set[str] = set()

    for sport_key in keys:
        cache_key = f"odds:tennis:{sport_key}:regions={','.join(regions)}:markets=h2h"
        cached_events, cached_at = _unwrap_snapshot(odds_cache.get(cache_key))
        if cached_events and _freshness_tier(cached_at) == "priority":
            data = _annotate_snapshot(cached_events, cached_at)
            _log.info("[runtime-odds] tennis cache hit %s (%d events)", sport_key, len(data))
        else:
            try:
                data, headers = _fetch_odds(sport_key, api_key, regions, ["h2h"])
                fetched_at = datetime.now(timezone.utc).isoformat()
                odds_cache.set(
                    cache_key, _wrap_snapshot(data, fetched_at), _cache_storage_ttl_seconds()
                )
                data = _annotate_snapshot(data, fetched_at)
                _log_quota("tennis", sport_key, headers, len(data))
            except Exception as exc:
                _log.warning(
                    "[runtime-odds] tennis %s fetch failed (IP allowlist?): %s", sport_key, exc
                )
                continue

        for event in data:
            event_id = str(event.get("id", ""))
            if not event_id or event_id in seen:
                continue
            seen.add(event_id)
            enriched = dict(event)
            enriched["_sport_key"] = sport_key
            events.append(enriched)

    return events


@retry(
    retry=retry_if_exception(_should_retry_request),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=16),
    reraise=True,
)
def _fetch_active_sports(api_key: str) -> list[dict[str, Any]]:
    """Fetch active sports list from The Odds API with exponential backoff."""
    from src.services.odds_gateway import fetch_the_odds_api_json

    result = fetch_the_odds_api_json(
        "/sports",
        api_key=api_key,
        query={},
        source="runtime_odds.active_sports",
        priority_refresh=False,
    )
    return result.payload if isinstance(result.payload, list) else []


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
    from src.services.odds_gateway import fetch_the_odds_api_json

    try:
        result = fetch_the_odds_api_json(
            f"/sports/{sport_key}/odds",
            api_key=api_key,
            query={
                "regions": ",".join(regions),
                "markets": ",".join(markets),
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
            source="runtime_odds.fetch_odds",
            sport_key=sport_key,
            markets=markets,
            regions=regions,
            priority_refresh=True,
        )
        payload = result.payload
        headers = result.headers
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
