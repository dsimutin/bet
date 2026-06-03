"""Quota-efficient football runtime scanner using shared cached h2h odds."""

from __future__ import annotations
import hashlib
from datetime import date, datetime
from pathlib import Path
from typing import Any
import pandas as pd
from src.services.runtime_odds import get_football_h2h_odds

_LEAGUE_TO_SPORT_KEY = {
    "EPL": "soccer_epl",
    "BUNDESLIGA": "soccer_germany_bundesliga",
    "LALIGA": "soccer_spain_la_liga",
    "SERIEA": "soccer_italy_serie_a",
    "LIGUE1": "soccer_france_ligue_one",
}


def generate_football_signals_runtime(
    model: Any,
    league: str,
    scan_date: date,
    staging_dir: Path | None,
    odds_api_key: str,
    calibrator: Any | None = None,
) -> list[dict[str, Any]]:
    """Generate Dixon-Coles h2h signals from shared cached live odds."""
    sport_key = _LEAGUE_TO_SPORT_KEY.get(league.upper())
    if not sport_key or not odds_api_key:
        return []
    raw_events = get_football_h2h_odds(sport_key, odds_api_key)
    if not raw_events:
        return []

    from src.ingest.live_odds_adapter import LiveOddsFootballDataAdapter
    from src.models.production_signal_engine import ProductionDixonColesSignalEngine

    adapter = LiveOddsFootballDataAdapter(
        bookmaker_prefix="B365",
        preferred_bookmakers=["bet365", "pinnacle"],
        allow_bookmaker_fallback=True,
    )
    candidates = adapter.convert(raw_events).dataframe
    if candidates.empty:
        return []

    engine = ProductionDixonColesSignalEngine(
        model=model,
        min_edge_pct=2.0,
        bookmaker_prefix="B365",
        calibrator=calibrator,
    )
    signals = engine.generate_signals(candidates)
    dataset_hash = (
        "sha256:" + hashlib.sha256(candidates.to_csv(index=False).encode("utf-8")).hexdigest()
    )

    # Enrich signals with fatigue + recent form context
    if staging_dir is not None:
        try:
            from src.features.match_context import apply_context_to_signal, compute_match_context

            enriched = []
            for signal in signals:
                match_date = scan_date
                raw_event_time = str(signal.get("event_time_utc") or "").replace("Z", "+00:00")
                if raw_event_time:
                    try:
                        match_date = datetime.fromisoformat(raw_event_time).date()
                    except ValueError:
                        match_date = scan_date
                ctx = compute_match_context(
                    home_team=str(signal.get("home_team", "")),
                    away_team=str(signal.get("away_team", "")),
                    match_date=match_date,
                    staging_dir=staging_dir,
                    league=league,
                )
                enriched.append(apply_context_to_signal(signal, ctx))
            signals = enriched
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "[football_scan] context enrichment failed: %s", exc
            )

    # Optionally enrich with injuries from API-Football (if API_FOOTBALL_KEY configured)
    try:
        from src.ingest.apifootball_injuries import (
            format_injuries_for_signal,
            get_injuries_for_match,
            is_configured,
        )

        if is_configured():
            match_date_str = scan_date.isoformat()
            for signal in signals:
                inj = get_injuries_for_match(
                    str(signal.get("home_team", "")),
                    str(signal.get("away_team", "")),
                    match_date_str,
                    league,
                )
                signal["injuries"] = inj
                inj_line = format_injuries_for_signal(inj)
                if inj_line:
                    signal["injuries_text"] = inj_line
                signal["injuries_context_mode"] = "informational_only"
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("[football_scan] injuries enrichment failed: %s", exc)

    for signal in signals:
        signal.setdefault("sport", "football")
        signal.setdefault("league", league)
        signal.setdefault("dataset_hash", dataset_hash)
    return signals
