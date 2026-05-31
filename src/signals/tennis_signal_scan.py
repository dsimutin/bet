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
_TENNIS_SPORT_KEYS = ["tennis_atp", "tennis_wta"]
_DEFAULT_EDGE_THRESHOLD = 1.5  # minimum edge % to emit a signal
_DEFAULT_MIN_ODDS = 1.25
_DEFAULT_MAX_ODDS = 8.00
_PREFERRED_BOOKS = ["pinnacle", "bet365", "draftkings", "fanduel", "betfair_ex_uk"]

# Grand Slam tournaments use best-of-5 for men
_GRAND_SLAM_KEYWORDS = ["roland garros", "french open", "wimbledon", "us open", "australian open"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_tennis_signals(
    model_path: Path,
    api_key: str,
    edge_threshold: float = _DEFAULT_EDGE_THRESHOLD,
    min_odds: float = _DEFAULT_MIN_ODDS,
    max_odds: float = _DEFAULT_MAX_ODDS,
    markov_model_path: Path | None = None,
) -> dict[str, Any]:
    """Scan upcoming ATP matches and return value-bet signals."""
    from src.models.tennis_elo import TennisEloModel
    from src.models.tennis_markov import TennisMarkovModel

    t0 = time.perf_counter()

    if not model_path.exists():
        _log.info("[tennis] No model at %s — skipping scan", model_path)
        return _empty_result("no_model")

    try:
        elo_model = TennisEloModel.load(model_path)
        _log.info(
            "[tennis] Loaded ELO model: %d players, %d matches",
            elo_model.params.n_players, elo_model.params.n_matches,
        )
    except Exception as exc:
        _log.warning("[tennis] Failed to load ELO model: %s", exc)
        return _empty_result(f"model_load_error: {exc}")

    # Load Markov model if available (sits next to ELO model)
    markov_model: TennisMarkovModel | None = None
    _markov_path = markov_model_path or model_path.parent / "tennis_markov_atp_latest.pkl"
    if _markov_path.exists():
        try:
            markov_model = TennisMarkovModel.load(_markov_path)
            _log.info("[tennis] Loaded Markov model: %d players", markov_model.params.n_players)
        except Exception as exc:
            _log.warning("[tennis] Markov model load failed: %s — using ELO only", exc)

    # Build rankings-based name resolver (ATP + WTA) and rank lookup
    _name_resolver: dict[str, str] = {}
    _rank_lookup: dict[str, int] = {}   # full_name → current rank
    try:
        from src.ingest.atp_rankings import build_name_resolver, get_rankings
        _name_resolver = build_name_resolver(top_n=300)
        for tour in ("atp", "wta"):
            for row in get_rankings(top_n=300, tour=tour):
                _rank_lookup[row["full_name"]] = row["rank"]
        _log.info("[tennis] Name resolver: %d entries, rank lookup: %d players",
                  len(_name_resolver), len(_rank_lookup))
    except Exception as exc:
        _log.debug("[tennis] Rankings unavailable: %s", exc)

    # Inject live serve stats from Tennis Abstract (current-season accuracy boost)
    if markov_model is not None:
        try:
            from src.ingest.tennis_abstract import fetch_serve_stats
            live_stats = fetch_serve_stats()
            if live_stats:
                markov_model.inject_live_serve_stats(live_stats)
                _log.info("[tennis] Live serve stats injected: %d players", len(live_stats))
        except Exception as exc:
            _log.debug("[tennis] Live stats fetch skipped: %s", exc)

    # Use ELO model as the primary reference (has retirement detection, form, H2H)
    model = elo_model

    events = _fetch_atp_events(api_key)
    if events is None:
        return _empty_result("api_error")
    if not events:
        return _empty_result("no_upcoming_events")

    _log.info("[tennis] Got %d upcoming ATP events", len(events))

    signals: list[dict[str, Any]] = []
    skipped_no_data = 0

    for event in events:
        player1_raw = str(event.get("home_team", "")).strip()
        player2_raw = str(event.get("away_team", "")).strip()
        if not player1_raw or not player2_raw:
            continue

        # Resolve abbreviated names ("N. Djokovic") to full names in model
        player1 = _resolve_player_name(player1_raw, model, _name_resolver)
        player2 = _resolve_player_name(player2_raw, model, _name_resolver)

        # Prefer surface-specific prediction; infer surface from description if available
        surface = _infer_surface(event)
        best_of = _infer_best_of(event)

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

        # Skip if either player retired recently (injury risk)
        if model.retired_recently(player1) or model.retired_recently(player2):
            injured = [p for p in [player1, player2] if model.retired_recently(p)]
            _log.debug("[tennis] Skipping %s vs %s — recent retirement: %s", player1, player2, injured)
            skipped_no_data += 1
            continue

        # Capper consensus (passive enrichment, does not filter signals)
        from src.ingest.capper_consensus import get_capper_consensus
        consensus_p1 = get_capper_consensus(player1, player2)
        consensus_p2 = get_capper_consensus(player2, player1)

        # Markov model (primary when serve data available) + ELO (always)
        elo_breakdown = model.predict_proba_breakdown(player1, player2, surface)
        elo_breakdown["surface"] = surface

        if markov_model is not None:
            markov_bd = markov_model.predict_proba_breakdown(player1, player2, surface, best_of)
            if markov_bd["markov_prob"] is not None:
                # Both models agree on direction → higher confidence
                model_prob_p1 = markov_bd["prob"]
                breakdown = {**elo_breakdown, "markov_prob": markov_bd["markov_prob"],
                             "p1_serve": markov_bd["p1_serve"], "p2_serve": markov_bd["p2_serve"],
                             "model_source": "markov+elo"}
            else:
                model_prob_p1 = elo_breakdown["final_prob"]
                breakdown = {**elo_breakdown, "model_source": "elo_only"}
        else:
            model_prob_p1 = elo_breakdown["final_prob"]
            breakdown = {**elo_breakdown, "model_source": "elo_only"}

        # Context for signal enrichment
        context = {
            "p1_form": model.get_recent_form(player1, surface),
            "p2_form": model.get_recent_form(player2, surface),
            "p1_retired_recently": model.retired_recently(player1),
            "p2_retired_recently": model.retired_recently(player2),
            "consensus_p1": consensus_p1,
            "consensus_p2": consensus_p2,
            "p1_rank": _rank_lookup.get(player1),
            "p2_rank": _rank_lookup.get(player2),
        }

        event_signals = _check_event(
            event=event,
            player1=player1,
            player2=player2,
            model_prob_p1=model_prob_p1,
            breakdown=breakdown,
            edge_threshold=edge_threshold,
            min_odds=min_odds,
            max_odds=max_odds,
            model=model,
            context=context,
            best_of=best_of,
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
    breakdown: dict[str, Any],
    edge_threshold: float,
    min_odds: float,
    max_odds: float,
    model: Any,
    context: dict[str, Any] | None = None,
    best_of: int = 3,
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
                is_p1 = player == player1
                days_rest = model.days_since_last_match(player)
                opp_days_rest = model.days_since_last_match(opponent)
                serve_pct = model.get_serve_win_pct(player, breakdown.get("surface", "hard"))
                hold_pct = model.get_hold_rate(player, breakdown.get("surface", "hard"))
                ctx = context or {}
                form = ctx.get("p1_form" if is_p1 else "p2_form")
                p_rank = ctx.get("p1_rank" if is_p1 else "p2_rank")
                opp_rank = ctx.get("p2_rank" if is_p1 else "p1_rank")
                signals.append({
                    "signal_id": f"ten_{event_id[:8]}_{book_key}_{player[:4].replace(' ', '')}",
                    "sport": "tennis",
                    "tour": "ATP" if "atp" in event.get("_sport_key", "atp") else "WTA",
                    "player": player,
                    "opponent": opponent,
                    "surface": breakdown.get("surface", "hard"),
                    "event_id": event_id,
                    "commence_time": commence,
                    "bookmaker": book_key,
                    "entry_odds": round(entry_odds, 3),
                    "model_prob": round(model_prob, 4),
                    "market_prob": round(fair_prob, 4),
                    "edge_pct": round(edge_pct, 2),
                    "reference_fair_odds": round(fair_model_odds, 3),
                    # V2 breakdown
                    "elo_prob": round(breakdown["elo_prob"] if is_p1 else 1.0 - breakdown["elo_prob"], 4),
                    "serve_adj": round(breakdown.get("serve_adj", 0.0) * (1 if is_p1 else -1), 4),
                    "h2h_adj": round(breakdown.get("h2h_adj", 0.0) * (1 if is_p1 else -1), 4),
                    "days_since_last_match": days_rest,
                    "opp_days_since_last_match": opp_days_rest,
                    "serve_win_pct": round(serve_pct, 3) if serve_pct is not None else None,
                    "hold_pct": round(hold_pct, 3) if hold_pct is not None else None,
                    "recent_form": round(form, 3) if form is not None else None,
                    "markov_prob": breakdown.get("markov_prob"),
                    "p_serve": breakdown.get("p1_serve") if is_p1 else breakdown.get("p2_serve"),
                    "best_of": best_of,
                    "model_source": breakdown.get("model_source", "elo_only"),
                    "rank": p_rank,
                    "opp_rank": opp_rank,
                    "capper_support": (ctx.get("consensus_p1") if is_p1 else ctx.get("consensus_p2") or {}).get("support"),
                    "capper_tips": (ctx.get("consensus_p1") if is_p1 else ctx.get("consensus_p2") or {}).get("n_tips", 0),
                    "capper_avg_odds": (ctx.get("consensus_p1") if is_p1 else ctx.get("consensus_p2") or {}).get("avg_odds"),
                    "status": "paper",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                })

    return signals


def _resolve_player_name(
    name: str,
    model: Any,
    resolver: dict[str, str] | None = None,
) -> str:
    """Resolve abbreviated name ("N. Djokovic") to full name in model ("Novak Djokovic").

    Resolution order:
    1. Exact match in model → return as-is
    2. Rankings resolver (ATP/WTA player CSV) → full canonical name
    3. Scan model's known players by last-name + first-initial

    Falls back to the original name if no match found.
    """
    # 1. Exact model match
    if model.has_enough_data(name, min_matches=1):
        return name

    # 2. Rankings resolver lookup
    if resolver:
        resolved = resolver.get(name) or resolver.get(name.lower())
        if resolved and model.has_enough_data(resolved, min_matches=1):
            _log.debug("[tennis] Rankings resolved '%s' → '%s'", name, resolved)
            return resolved

    parts = name.strip().split()
    if len(parts) < 2:
        return name

    # Check if first token looks like an initial ("N." or "N")
    first = parts[0].rstrip(".")
    last = parts[-1].lower()
    is_abbreviated = len(first) == 1

    # 3. Scan model's known players for last-name + first-initial match
    candidates = []
    for known in model.known_players():
        kparts = known.strip().split()
        if not kparts:
            continue
        k_last = kparts[-1].lower()
        k_first_init = kparts[0][0].lower() if kparts[0] else ""
        if k_last == last:
            if is_abbreviated and k_first_init == first.lower():
                candidates.append(known)
            elif not is_abbreviated and known.lower() == name.lower():
                return known

    if len(candidates) == 1:
        _log.debug("[tennis] Model-scan resolved '%s' → '%s'", name, candidates[0])
        return candidates[0]

    return name


def _match_odds_fuzzy(
    player1: str, player2: str, odds_map: dict[str, float]
) -> tuple[float | None, float | None]:
    """Fuzzy player name matching for tennis.

    Handles formats like:
    - "Novak Djokovic" vs "N. Djokovic" (Odds API abbreviated)
    - "Carlos Alcaraz" vs "C. Alcaraz"
    """
    def _parts(name: str) -> tuple[str, str]:
        """Return (first_initial, last_name) both lowercased."""
        parts = name.strip().split()
        if not parts:
            return "", ""
        last = parts[-1].lower()
        first_init = parts[0][0].lower() if parts[0] else ""
        return first_init, last

    p1_init, p1_last = _parts(player1)
    p2_init, p2_last = _parts(player2)

    odds_p1 = None
    odds_p2 = None
    for name, price in odds_map.items():
        n_init, n_last = _parts(name)
        # Match if last name matches AND first initial matches (or one side has no initial)
        if n_last == p1_last and (not n_init or not p1_init or n_init == p1_init):
            odds_p1 = price
        elif n_last == p2_last and (not n_init or not p2_init or n_init == p2_init):
            odds_p2 = price
    return odds_p1, odds_p2


def _infer_surface(event: dict[str, Any]) -> str:
    """Try to infer surface from sport_title or description field in event."""
    from src.ingest.tennis_atp import infer_surface
    title = str(event.get("sport_title", "") or event.get("tournament", ""))
    return infer_surface(title)


def _infer_best_of(event: dict[str, Any]) -> int:
    """Detect best-of-5 for Grand Slam men's matches."""
    sport_key = event.get("_sport_key", "")
    # WTA always best-of-3
    if "wta" in sport_key:
        return 3
    title = str(event.get("sport_title", "") or "").lower()
    if any(gs in title for gs in _GRAND_SLAM_KEYWORDS):
        return 5
    return 3


def _fetch_atp_events(api_key: str) -> list[dict[str, Any]] | None:
    """Fetch upcoming ATP/WTA events with h2h odds from The Odds API."""
    all_events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for sport_key in _TENNIS_SPORT_KEYS:
        url = (
            f"{_ODDS_API_BASE}/sports/{sport_key}/odds"
            f"?apiKey={api_key}&regions=eu,uk,us&markets=h2h&oddsFormat=decimal&dateFormat=iso"
        )
        try:
            req = _urllib.Request(url, headers={"User-Agent": "bet-analytics/1.0"})
            with _urllib.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            if isinstance(data, list):
                for event in data:
                    eid = event.get("id", "")
                    if eid not in seen_ids:
                        seen_ids.add(eid)
                        # Tag with sport key for surface inference
                        event["_sport_key"] = sport_key
                        all_events.append(event)
                _log.info("[tennis] %s: %d events", sport_key, len(data) if isinstance(data, list) else 0)
        except Exception as exc:
            _log.warning("[tennis] Odds API error for %s: %s", sport_key, exc)

    return all_events if all_events else None


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


def scan_tennis_debug(model_path: Path, api_key: str) -> dict[str, Any]:
    """Diagnostic scan — returns raw edge values without threshold filtering.

    Useful for diagnosing why signals are not generated.
    """
    from src.models.tennis_elo import TennisEloModel
    from src.models.tennis_markov import TennisMarkovModel

    if not model_path.exists():
        return {"error": "no_model"}

    elo_model = TennisEloModel.load(model_path)

    markov_model = None
    markov_path = model_path.parent / "tennis_markov_atp_latest.pkl"
    if markov_path.exists():
        try:
            markov_model = TennisMarkovModel.load(markov_path)
            from src.ingest.tennis_abstract import fetch_serve_stats
            live_stats = fetch_serve_stats()
            if live_stats:
                markov_model.inject_live_serve_stats(live_stats)
        except Exception:
            pass

    events = _fetch_atp_events(api_key)
    if not events:
        return {"error": "no_events", "events_raw": events}

    debug_rows = []
    for event in events[:30]:
        p1_raw = str(event.get("home_team", ""))
        p2_raw = str(event.get("away_team", ""))
        p1 = _resolve_player_name(p1_raw, elo_model)
        p2 = _resolve_player_name(p2_raw, elo_model)
        surface = _infer_surface(event)
        best_of = _infer_best_of(event)

        p1_known = elo_model.has_enough_data(p1)
        p2_known = elo_model.has_enough_data(p2)

        if not p1_known or not p2_known:
            debug_rows.append({
                "p1": p1, "p2": p2, "surface": surface,
                "skip": "insufficient_data",
                "p1_matches": elo_model._match_count.get(p1, 0),
                "p2_matches": elo_model._match_count.get(p2, 0),
            })
            continue

        elo_bd = elo_model.predict_proba_breakdown(p1, p2, surface)
        model_prob = elo_bd["final_prob"]

        if markov_model is not None:
            m_bd = markov_model.predict_proba_breakdown(p1, p2, surface, best_of)
            if m_bd["markov_prob"] is not None:
                model_prob = m_bd["prob"]

        # Best odds from any bookmaker
        best_odds_p1 = max(
            (float(o.get("price", 0))
             for bk in event.get("bookmakers", [])
             for mkt in bk.get("markets", []) if mkt.get("key") == "h2h"
             for o in mkt.get("outcomes", []) if o.get("name") == p1),
            default=None,
        )

        edge_p1 = None
        if best_odds_p1 and model_prob > 0:
            fair_model_odds = 1.0 / model_prob
            edge_p1 = round((best_odds_p1 / fair_model_odds - 1) * 100, 2)

        debug_rows.append({
            "p1": p1, "p2": p2, "surface": surface, "best_of": best_of,
            "model_prob_p1": round(model_prob, 4),
            "best_odds_p1": best_odds_p1,
            "edge_p1_pct": edge_p1,
            "n_bookmakers": len(event.get("bookmakers", [])),
        })

    debug_rows.sort(key=lambda r: abs(r.get("edge_p1_pct") or 0), reverse=True)
    return {
        "n_events": len(events),
        "model_players": elo_model.params.n_players,
        "rows": debug_rows,
    }
