"""Jeff Sackmann ATP match results downloader for ELO model training.

Data source: https://github.com/JeffSackmann/tennis_atp (public domain)
Format: atp_matches_{year}.csv with one row per completed match.
"""

from __future__ import annotations

import logging
from io import StringIO
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

_log = logging.getLogger(__name__)

_BASE_URL = (
    "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_{year}.csv"
)

_SURFACE_MAP = {
    "Clay": "clay",
    "Grass": "grass",
    "Hard": "hard",
    "Carpet": "carpet",
    "clay": "clay",
    "grass": "grass",
    "hard": "hard",
    "carpet": "carpet",
}

# Tournament name fragments → surface (fallback when surface column missing)
_SURFACE_KEYWORDS: dict[str, list[str]] = {
    "clay": ["roland garros", "french open", "madrid", "rome", "monte carlo", "barcelona"],
    "grass": ["wimbledon", "queens", "halle", "stuttgart", "eastbourne"],
}


def download_atp_season(year: int, cache_dir: Path, use_cache: bool = True) -> pd.DataFrame:
    """Download one ATP season CSV and return normalized DataFrame."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"atp_{year}.csv"

    if use_cache and cache_path.exists():
        _log.info("[tennis_atp] Cache hit %d", year)
        return _parse(cache_path.read_text(encoding="utf-8", errors="replace"))

    url = _BASE_URL.format(year=year)
    _log.info("[tennis_atp] Downloading %s", url)
    req = Request(url, headers={"User-Agent": "bet-analytics/1.0"})
    try:
        with urlopen(req, timeout=20) as resp:
            content = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        _log.warning("[tennis_atp] Failed to download %d: %s", year, exc)
        return pd.DataFrame()

    cache_path.write_text(content, encoding="utf-8")
    return _parse(content)


def build_atp_dataset(years: list[int], cache_dir: Path, use_cache: bool = True) -> pd.DataFrame:
    """Combine multiple ATP seasons into one training DataFrame."""
    frames: list[pd.DataFrame] = []
    for year in years:
        df = download_atp_season(year, cache_dir, use_cache=use_cache)
        if not df.empty:
            frames.append(df)
    if not frames:
        _log.warning("[tennis_atp] No data downloaded for years %s", years)
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    _log.info(
        "[tennis_atp] Combined dataset: %d matches (%d–%d)", len(combined), years[0], years[-1]
    )
    return combined


def infer_surface(tourney_name: str) -> str:
    """Guess surface from tournament name when surface column is missing."""
    name_lower = str(tourney_name).lower()
    for surface, keywords in _SURFACE_KEYWORDS.items():
        if any(kw in name_lower for kw in keywords):
            return surface
    return "hard"


def _parse(content: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(StringIO(content), low_memory=False)
    except Exception as exc:
        _log.warning("[tennis_atp] CSV parse error: %s", exc)
        return pd.DataFrame()

    if df.empty:
        return df

    # Surface
    if "surface" in df.columns:
        df["surface"] = df["surface"].map(_SURFACE_MAP).fillna("hard")
    elif "tourney_name" in df.columns:
        df["surface"] = df["tourney_name"].apply(infer_surface)
    else:
        df["surface"] = "hard"

    # Match date
    if "tourney_date" in df.columns:
        df["match_date"] = pd.to_datetime(
            df["tourney_date"].astype(str).str[:8], format="%Y%m%d", errors="coerce"
        )
    else:
        df["match_date"] = pd.NaT

    keep = [
        "match_date", "tourney_name", "surface", "tourney_level", "round", "best_of",
        "winner_name", "loser_name", "winner_rank", "loser_rank",
        "score",  # needed for retirement/injury detection (RET, W/O)
        # Serve/return stats (Sackmann format)
        "w_svpt", "w_1stIn", "w_1stWon", "w_2ndWon", "w_SvGms", "w_bpSaved", "w_bpFaced",
        "l_svpt", "l_1stIn", "l_1stWon", "l_2ndWon", "l_SvGms", "l_bpSaved", "l_bpFaced",
    ]
    for col in ("b365w", "b365l", "psw", "psl"):
        if col in df.columns:
            keep.append(col)

    df = df[[c for c in keep if c in df.columns]].copy()
    df = df.dropna(subset=["winner_name", "loser_name", "match_date"])
    df = df[df["winner_name"].str.strip() != ""]
    df = df[df["loser_name"].str.strip() != ""]
    return df.reset_index(drop=True)
