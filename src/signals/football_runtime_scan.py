"""Quota-efficient football runtime scanner using shared cached h2h odds."""
from __future__ import annotations
import hashlib
from datetime import date
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
    dataset_hash = "sha256:" + hashlib.sha256(candidates.to_csv(index=False).encode("utf-8")).hexdigest()
    for signal in signals:
        signal.setdefault("sport", "football")
        signal.setdefault("league", league)
        signal.setdefault("dataset_hash", dataset_hash)
    return signals
