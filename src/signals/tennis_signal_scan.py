"""Tennis (ATP) signal scanner — value bet detection via ELO vs market odds.

Pipeline:
  1. Fetch upcoming ATP events from The Odds API (sport_key=tennis_atp)
  2. For each match, predict win probability with TennisEloModel
  3. Devig bookmaker odds to get fair implied probability
  4. Emit signal when ELO model finds edge above threshold

Paper trading only — no real bets.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request as _urllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
_TENNIS_SPORT_KEY = "tennis_atp"
_DEFAULT_EDGE_THRESHOLD = 3.0  # minimum edge % to emit a signal
_DEFAULT_MIN_ODDS = 1.30
_DEFAULT_MAX_ODDS = 6.00
_PREFERRED_BOOKS = ["pinnacle", "bet365", "draftkings", "fanduel", "betfair_ex_uk"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_tennis_signals(
    model_path: Path,
    api_key: str,
    edge_threshold: float = _DEFAULT_EDGE_THRESHOLD,
    min_odds: float = _DEFAULT_MIN_ODDS,
    max_odds: float = _DEFAULT_MAX_ODDS,
) -> dict[str, Any]:
    """Scan upcoming ATP matches and return value-bet signals."""
    from src.models.tennis_elo import TennisEloModel

    t0 = time.perf_counter()

    if not model_path.exists():
        _log.info("[tennis] No model at %s — skipping scan", model_path)
        return _empty_result("no_model")

    try:
        model = TennisEloModel.load(model_path)
        _log.info(
            "[tennis] Loaded ELO model: %d players, %d matches",
            model.params.n_players, model.params.n_matches,
        )
    except Exception as exc:
        _log.warning("[tennis] Failed to load ELO model: %s", exc)
        return _empty_result(f"model_load_error: {exc}")

    events = _fetch_atp_events(api_key)
    if events is None:
        return _empty_result("api_error")
    if not events:
        return _empty_result("no_upcoming_events")

    _log.info("[tennis] Got %d upcoming ATP events", len(events))

    signals: list[dict[str, Any]] = []
    skipped_no_data = 0

    for event in events:
        player1 = str(event.get("home_team", "")).strip()
        player2 = str(event.get("away_team", "")).strip()
        if not player1 or not player2:
            continue

        # Prefer surface-specific prediction; infer surface from description if available
        surface = _infer_surface(event)
        model_prob_p1 = model.predict_proba(player1, player2, surface)

        p1_known = model.has_enough_data(player1)
        p2_known = model.has_enough_data(player2)
        if not p1_known or not p2_known:
            missing = []
            if not p1_known:
                missing.append(player1)
            if not p2_known:
                missing.append(player2)
            _log.debug("[tennis] Skipping %s vs %s — insufficient data: %s", player1, player2, missing)
            skipped_no_data += 1
            continue

        event_signals = _check_event(
            event=event,
            player1=player1,
            player2=player2,
            model_prob_p1=model_prob_p1,
            edge_threshold=edge_threshold,
            min_odds=min_odds,
            max_odds=max_odds,
        )
        signals.extend(event_signals)

    duration = time.perf_counter() - t0
    _log.info(
        "[tennis] Scan done: %d signals, %d events, %d skipped_no_data in %.1fs",
        len(signals), len(events), skipped_no_data, duration,
    )
    return {
        "sport": "tennis",
        "tour": "ATP",
        "signals_count": len(signals),
        "events_checked": len(events),
        "skipped_no_data": skipped_no_data,
        "top_signals": sorted(signals, key=lambda s: s.get("edge_pct", 0), reverse=True)[:5],
        "all_signals": signals,
        "duration_s": duration,
        "status": "ok",
        "no_signal_reason": "" if signals else "no_edge_found",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_event(
    event: dict[str, Any],
    player1: str,
    player2: str,
    model_prob_p1: float,
    edge_threshold: float,
    min_odds: float,
    max_odds: float,
) -> list[dict[str, Any]]:
    """Check one event across all bookmakers, return signals with edge above threshold."""
    signals: list[dict[str, Any]] = []
    event_id = event.get("id", "")
    commence = event.get("commence_time", "")

    model_prob_p2 = 1.0 - model_prob_p1

    for bookmaker in event.get("bookmakers", []):
        book_key = bookmaker.get("key", "")
        for market in bookmaker.get("markets", []):
            if market.get("key") != "h2h":
                continue
            outcomes = market.get("outcomes", [])
            if len(outcomes) < 2:
                continue

            # Map outcome name → decimal odds
            odds_map: dict[str, float] = {}
            for o in outcomes:
                name = str(o.get("name", "")).strip()
                price = float(o.get("price", 0))
                if price > 1.0:
                    odds_map[name] = price

            odds_p1 = odds_map.get(player1)
            odds_p2 = odds_map.get(player2)

            # Try partial name match if exact fails (handle "J. Sinner" vs "Jannik Sinner")
            if odds_p1 is None or odds_p2 is None:
                odds_p1, odds_p2 = _match_odds_fuzzy(player1, player2, odds_map)

            if odds_p1 is None or odds_p2 is None:
                continue

            # Devig (multiplicative method for 2-outcome market)
            sum_implied = 1 / odds_p1 + 1 / odds_p2
            if sum_implied <= 0:
                continue
            fair_prob_p1 = (1 / odds_p1) / sum_implied
            fair_prob_p2 = (1 / odds_p2) / sum_implied

            # Check each player's side for edge
            for player, entry_odds, model_prob, fair_prob in [
                (player1, odds_p1, model_prob_p1, fair_prob_p1),
                (player2, odds_p2, model_prob_p2, fair_prob_p2),
            ]:
                if entry_odds < min_odds or entry_odds > max_odds:
                    continue
                fair_model_odds = 1.0 / model_prob if model_prob > 0 else 99.0
                edge_pct = (entry_odds / fair_model_odds - 1) * 100  # EV edge vs model
                if edge_pct < edge_threshold:
                    continue
                # Also require model prob to beat devigged market
                if model_prob <= fair_prob:
                    continue

                opponent = player2 if player == player1 else player1
                signals.append({
                    "signal_id": f"ten_{event_id[:8]}_{book_key}_{player[:4].replace(' ', '')}",
                    "sport": "tennis",
                    "tour": "ATP",
                    "player": player,
                    "opponent": opponent,
                    "event_id": event_id,
                    "commence_time": commence,
                    "bookmaker": book_key,
                    "entry_odds": round(entry_odds, 3),
                    "model_prob": round(model_prob, 4),
                    "market_prob": round(fair_prob, 4),
                    "edge_pct": round(edge_pct, 2),
                    "reference_fair_odds": round(fair_model_odds, 3),
                    "status": "paper",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                })

    return signals


def _match_odds_fuzzy(
    player1: str, player2: str, odds_map: dict[str, float]
) -> tuple[float | None, float | None]:
    """Fuzzy player name matching: try last-name lookup as fallback."""
    def last_name(name: str) -> str:
        parts = name.strip().split()
        return parts[-1].lower() if parts else ""

    p1_last = last_name(player1)
    p2_last = last_name(player2)

    odds_p1 = None
    odds_p2 = None
    for name, price in odds_map.items():
        nl = last_name(name)
        if nl == p1_last:
            odds_p1 = price
        elif nl == p2_last:
            odds_p2 = price
    return odds_p1, odds_p2


def _infer_surface(event: dict[str, Any]) -> str:
    """Try to infer surface from sport_title or description field in event."""
    from src.ingest.tennis_atp import infer_surface
    title = str(event.get("sport_title", "") or event.get("tournament", ""))
    return infer_surface(title)


def _fetch_atp_events(api_key: str) -> list[dict[str, Any]] | None:
    """Fetch upcoming ATP events with h2h odds from The Odds API."""
    url = (
        f"{_ODDS_API_BASE}/sports/{_TENNIS_SPORT_KEY}/odds"
        f"?apiKey={api_key}&regions=eu,uk&markets=h2h&oddsFormat=decimal&dateFormat=iso"
    )
    try:
        req = _urllib.Request(url, headers={"User-Agent": "bet-analytics/1.0"})
        with _urllib.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data if isinstance(data, list) else []
    except Exception as exc:
        _log.warning("[tennis] Odds API error: %s", exc)
        return None


def _empty_result(reason: str) -> dict[str, Any]:
    return {
        "sport": "tennis",
        "tour": "ATP",
        "signals_count": 0,
        "events_checked": 0,
        "skipped_no_data": 0,
        "top_signals": [],
        "all_signals": [],
        "duration_s": 0.0,
        "status": "skip",
        "no_signal_reason": reason,
    }
