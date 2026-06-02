"""Bayesian zero-shot signal scanner for exotic football leagues.

No historical match data required. Uses global Bayesian priors derived from
European football (home/draw/away base rates) shrunk toward market odds.

Edge signal fires when devigged market diverges enough from priors that
the correction implies model_prob > market_prob by at least MIN_EDGE_PCT.

Confidence is always "low" — these signals require human review before acting.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exotic league registry  (The Odds API sport_key → display info)
# ---------------------------------------------------------------------------

EXOTIC_LEAGUES: dict[str, dict[str, str]] = {
    "soccer_vietnam_v_league_1": {
        "name": "V-League (Вьетнам)",
        "region": "asia",
        "league_code": "VLEAGUE",
    },
    "soccer_thailand_thai_league": {
        "name": "Thai League (Таиланд)",
        "region": "asia",
        "league_code": "THAI",
    },
    "soccer_australia_aleague": {
        "name": "A-League (Австралия)",
        "region": "oceania",
        "league_code": "ALEAGUE",
    },
    "soccer_usa_mls": {
        "name": "MLS (США)",
        "region": "americas",
        "league_code": "MLS",
    },
    "soccer_brazil_campeonato": {
        "name": "Série A (Бразилия)",
        "region": "americas",
        "league_code": "BRAZIL_A",
    },
    "soccer_argentina_primera_division": {
        "name": "Liga Profesional (Аргентина)",
        "region": "americas",
        "league_code": "ARGENTINA",
    },
    "soccer_turkey_super_league": {
        "name": "Süper Lig (Турция)",
        "region": "europe",
        "league_code": "TURKEY",
    },
    "soccer_netherlands_eredivisie": {
        "name": "Eredivisie (Нидерланды)",
        "region": "europe",
        "league_code": "EREDIVISIE",
    },
    "soccer_portugal_primeira_liga": {
        "name": "Primeira Liga (Португалия)",
        "region": "europe",
        "league_code": "PORTUGAL",
    },
    "soccer_russia_premier_league": {
        "name": "РПЛ (Россия)",
        "region": "europe",
        "league_code": "RPL",
    },
    "soccer_belgium_first_div": {
        "name": "Pro League (Бельгия)",
        "region": "europe",
        "league_code": "BELGIUM",
    },
    "soccer_japan_j_league": {
        "name": "J1 League (Япония)",
        "region": "asia",
        "league_code": "JLEAGUE",
    },
    "soccer_south_korea_kleague1": {
        "name": "K League 1 (Корея)",
        "region": "asia",
        "league_code": "KLEAGUE",
    },
    "soccer_mexico_ligamx": {
        "name": "Liga MX (Мексика)",
        "region": "americas",
        "league_code": "LIGAMX",
    },
}

# ---------------------------------------------------------------------------
# Bayesian priors by region
# Based on global football-data.co.uk aggregated stats 2018-2024
# ---------------------------------------------------------------------------

_REGION_PRIORS: dict[str, dict[str, float]] = {
    "europe": {"home": 0.455, "draw": 0.255, "away": 0.290},
    "americas": {"home": 0.435, "draw": 0.270, "away": 0.295},
    "asia": {"home": 0.450, "draw": 0.260, "away": 0.290},
    "oceania": {"home": 0.440, "draw": 0.265, "away": 0.295},
}

# Shrinkage weight: fraction of prior vs market in Bayesian blend.
# Higher = more prior-driven (use higher when data is sparse / exotic)
_SHRINKAGE = 0.35  # 35% prior, 65% market — higher than 25% to widen signal range

# Minimum edge (model_prob > market_prob) to fire a signal
# With 35% shrinkage: edge = 35 * (prior - market_prob)
# Fires when e.g. draw @6+ odds (prior 26%), or home @3.5+ (prior 45%)
_MIN_EDGE_PCT = 2.5  # Conservative but reachable; lower than main leagues' 2% due to no history

# Maximum bookmaker margin — skip events with unusually high vig
_MAX_MARGIN_PCT = 12.0


def scan_exotic_leagues(
    api_key: str,
    leagues: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Scan all (or selected) exotic leagues and return zero-shot signals.

    Args:
        api_key: The Odds API key.
        leagues: Optional list of sport_key strings to restrict scan.
                 Defaults to all EXOTIC_LEAGUES.
    Returns:
        List of signal dicts, each with confidence="low".
    """
    if not api_key:
        _log.debug("[exotic] no API key — skipping exotic scan")
        return []

    active = leagues or list(EXOTIC_LEAGUES.keys())
    all_signals: list[dict[str, Any]] = []

    for sport_key in active:
        meta = EXOTIC_LEAGUES.get(sport_key)
        if not meta:
            continue
        try:
            events = _fetch_events(sport_key, api_key)
            if not events:
                continue
            signals = _score_events(events, sport_key, meta)
            all_signals.extend(signals)
            if signals:
                _log.info("[exotic] %s → %d signal(s)", meta["name"], len(signals))
        except Exception as exc:
            _log.warning("[exotic] %s failed: %s", sport_key, exc)

    return all_signals


def _fetch_events(sport_key: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch upcoming h2h odds for an exotic league with caching."""
    from src.infrastructure import odds_cache
    from src.services.runtime_odds import _fetch_odds
    from src.monitoring.api_quota_monitor import record_odds_api_request

    cache_key = f"exotic_odds:{sport_key}"
    cached = odds_cache.get(cache_key)
    if isinstance(cached, list):
        _log.debug("[exotic] cache hit %s (%d events)", sport_key, len(cached))
        return cached

    try:
        from src.services.runtime_odds import _csv_env
        regions = _csv_env("FOOTBALL_ODDS_REGIONS", "eu")
        data, _headers = _fetch_odds(sport_key, api_key, regions, ["h2h"])
        odds_cache.set(cache_key, data, 4 * 3600)  # 4h cache
        return data
    except Exception as exc:
        _log.debug("[exotic] fetch failed %s: %s", sport_key, exc)
        return []


def _score_events(
    events: list[dict[str, Any]],
    sport_key: str,
    meta: dict[str, str],
) -> list[dict[str, Any]]:
    """Apply Bayesian zero-shot scoring to a list of events."""
    region = meta.get("region", "europe")
    priors = _REGION_PRIORS.get(region, _REGION_PRIORS["europe"])
    signals: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for event in events:
        try:
            sig = _score_event(event, sport_key, meta, priors, now)
            if sig:
                signals.append(sig)
        except Exception as exc:
            _log.debug("[exotic] score_event error: %s", exc)

    return signals


def _score_event(
    event: dict[str, Any],
    sport_key: str,
    meta: dict[str, str],
    priors: dict[str, float],
    now: datetime,
) -> dict[str, Any] | None:
    """Score a single event. Returns signal dict or None."""
    home_team = str(event.get("home_team", "")).strip()
    away_team = str(event.get("away_team", "")).strip()
    commence_time_raw = str(event.get("commence_time", ""))
    if not home_team or not away_team or not commence_time_raw:
        return None

    try:
        event_time = datetime.fromisoformat(commence_time_raw.replace("Z", "+00:00"))
    except ValueError:
        return None

    # Skip already-started or >7 days out
    hours_until = (event_time - now).total_seconds() / 3600
    if hours_until <= 0 or hours_until > 168:
        return None

    # Extract h2h odds from bookmakers
    h_odds, d_odds, a_odds, bookmaker_name = _best_h2h_odds(event)
    if h_odds is None:
        return None

    # Devig: compute fair market probabilities (multiplicative)
    raw_probs = [1.0 / h_odds, 1.0 / d_odds, 1.0 / a_odds]
    overround = sum(raw_probs)
    margin_pct = (overround - 1.0) * 100
    if margin_pct > _MAX_MARGIN_PCT:
        return None  # Too much vig to find value

    market_prob_h = raw_probs[0] / overround
    market_prob_d = raw_probs[1] / overround
    market_prob_a = raw_probs[2] / overround

    # Bayesian blend: model_prob = shrinkage * prior + (1 - shrinkage) * market
    model_prob_h = _SHRINKAGE * priors["home"] + (1 - _SHRINKAGE) * market_prob_h
    model_prob_d = _SHRINKAGE * priors["draw"] + (1 - _SHRINKAGE) * market_prob_d
    model_prob_a = _SHRINKAGE * priors["away"] + (1 - _SHRINKAGE) * market_prob_a

    # Find best edge
    candidates = [
        ("home", model_prob_h, market_prob_h, h_odds),
        ("draw", model_prob_d, market_prob_d, d_odds),
        ("away", model_prob_a, market_prob_a, a_odds),
    ]
    best = max(candidates, key=lambda x: x[1] - x[2])
    selection, model_prob, market_prob, entry_odds = best

    edge_pct = (model_prob - market_prob) * 100
    if edge_pct < _MIN_EDGE_PCT:
        return None

    dataset_hash = "sha256:" + hashlib.sha256(
        f"{sport_key}:{home_team}:{away_team}:{commence_time_raw}".encode()
    ).hexdigest()[:12]

    _RU = {"home": f"П1 ({home_team})", "draw": "Ничья", "away": f"П2 ({away_team})"}

    return {
        "signal_id": str(uuid.uuid4()),
        "strategy_id": "bayesian_zero_shot_v1",
        "sport": "football",
        "league": meta["league_code"],
        "league_name": meta["name"],
        "home_team": home_team,
        "away_team": away_team,
        "selection": selection,
        "selection_ru": _RU[selection],
        "entry_odds": round(entry_odds, 3),
        "model_probability": round(model_prob, 4),
        "fair_market_probability": round(market_prob, 4),
        "edge_pct": round(edge_pct, 2),
        "edge_vs_fair_pct": round(edge_pct, 2),
        "margin_pct": round(margin_pct, 2),
        "model_source": "bayesian_zero_shot",
        "confidence": "low",
        "confidence_note": "Нет исторических данных по лиге. Байесовский прайор на основе глобальной статистики.",
        "bookmaker": bookmaker_name,
        "event_time_utc": event_time.isoformat(),
        "commence_time": event_time.isoformat(),
        "snapshot_ts_utc": now.isoformat(),
        "stake_units": 0.5,  # Half stake due to low confidence
        "dataset_hash": dataset_hash,
        "recommendation_tier": "watchlist",  # Never auto-priority for exotic
        "recommendation_reason": "exotic league: low confidence signal",
    }


def _best_h2h_odds(event: dict[str, Any]) -> tuple[float | None, float | None, float | None, str]:
    """Extract best available h2h odds from bookmakers. Returns (h, d, a, bookmaker_name)."""
    bookmakers = event.get("bookmakers", [])
    # Prefer pinnacle > bet365 > first available
    preferred = ["pinnacle", "betfair", "bet365"]
    ordered = sorted(
        bookmakers,
        key=lambda b: next(
            (i for i, p in enumerate(preferred) if p in str(b.get("key", "")).lower()),
            len(preferred),
        ),
    )
    for bm in ordered:
        for market in bm.get("markets", []):
            if market.get("key") != "h2h":
                continue
            outcomes = market.get("outcomes", [])
            if len(outcomes) < 3:
                continue
            try:
                h = float(outcomes[0].get("price", 0))
                d = float(outcomes[1].get("price", 0))
                a = float(outcomes[2].get("price", 0))
                if h > 1.0 and d > 1.0 and a > 1.0:
                    return h, d, a, str(bm.get("title", bm.get("key", "unknown")))
            except (TypeError, ValueError):
                continue
    return None, None, None, ""
