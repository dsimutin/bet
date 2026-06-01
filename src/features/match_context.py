"""Match context features: fatigue (rest days) and recent form from historical data.

These features are appended to football signals AFTER Dixon-Coles prediction
as post-processing adjustments. They do NOT require model retraining.

Fatigue adjustment:
  < 3 days rest  → −7% to attack lambda  (very tired)
  3–4 days rest  → −3% to attack lambda  (somewhat tired)
  ≥ 5 days rest  → no adjustment

Recent form adjustment (last 5 matches):
  Form score 0.0–1.0 (0=5 losses, 1=5 wins)
  Applied as small multiplier on model probability: ±5% max

Anti-leakage: only matches with Date < match_date are used.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

_log = logging.getLogger(__name__)

# Points per result for form scoring
_FORM_PTS = {"W": 1.0, "D": 0.5, "L": 0.0}

# How much fatigue adjusts the attack probability
_FATIGUE_ADJ = {
    "severe": 0.93,   # < 3 days rest
    "mild": 0.97,     # 3–4 days
    "none": 1.0,      # ≥ 5 days
}

# Max probability adjustment from recent form: ±0.05
_FORM_MAX_ADJ = 0.05


def compute_match_context(
    home_team: str,
    away_team: str,
    match_date: date,
    staging_dir: Path,
    league: str,
) -> dict[str, Any]:
    """Return fatigue + recent form context for a match.

    Parameters
    ----------
    home_team, away_team : str
        Team names as they appear in the staging CSV.
    match_date : date
        Date of the upcoming match.
    staging_dir : Path
        Directory containing {LEAGUE}_latest.csv files.
    league : str
        e.g. 'EPL', 'BUNDESLIGA'.

    Returns
    -------
    dict with keys:
        rest_days_home, rest_days_away,
        form_home (0–1), form_away (0–1),
        form_str_home (e.g. 'WWDLW'), form_str_away,
        fatigue_home ('severe'|'mild'|'none'),
        fatigue_away,
        prob_adj_home, prob_adj_away  (additive, e.g. +0.03)
    """
    csv_path = staging_dir / f"{league.upper()}_latest.csv"
    if not csv_path.exists():
        return _empty_context()

    try:
        df = _load_csv(csv_path, match_date)
    except Exception as exc:
        _log.warning("[context] Failed to load %s: %s", csv_path, exc)
        return _empty_context()

    if df.empty:
        return _empty_context()

    rest_home = _days_since_last_match(df, home_team, match_date)
    rest_away = _days_since_last_match(df, away_team, match_date)

    form_home, form_str_home = _recent_form(df, home_team, match_date, n=5)
    form_away, form_str_away = _recent_form(df, away_team, match_date, n=5)

    fatigue_home = _fatigue_level(rest_home)
    fatigue_away = _fatigue_level(rest_away)

    # Probability adjustment: form above/below 0.5 shifts probability ±FORM_MAX_ADJ
    prob_adj_home = round((form_home - 0.5) * 2 * _FORM_MAX_ADJ, 4) if form_home is not None else 0.0
    prob_adj_away = round((form_away - 0.5) * 2 * _FORM_MAX_ADJ, 4) if form_away is not None else 0.0

    # Fatigue further drags down probability
    fatigue_drag_home = {
        "severe": -0.05, "mild": -0.02, "none": 0.0
    }.get(fatigue_home, 0.0)
    fatigue_drag_away = {
        "severe": -0.05, "mild": -0.02, "none": 0.0
    }.get(fatigue_away, 0.0)

    prob_adj_home = round(prob_adj_home + fatigue_drag_home, 4)
    prob_adj_away = round(prob_adj_away + fatigue_drag_away, 4)

    return {
        "rest_days_home": rest_home,
        "rest_days_away": rest_away,
        "form_home": round(form_home, 3) if form_home is not None else None,
        "form_away": round(form_away, 3) if form_away is not None else None,
        "form_str_home": form_str_home,
        "form_str_away": form_str_away,
        "fatigue_home": fatigue_home,
        "fatigue_away": fatigue_away,
        "prob_adj_home": prob_adj_home,
        "prob_adj_away": prob_adj_away,
    }


def apply_context_to_signal(signal: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Merge context into signal and adjust model probability if home/away selection."""
    signal = {**signal, **{f"ctx_{k}": v for k, v in ctx.items()}}

    selection = str(signal.get("selection", "")).lower()
    prob = signal.get("model_probability", signal.get("model_prob"))
    if not isinstance(prob, (int, float)):
        return signal

    if selection == "home":
        adj = ctx.get("prob_adj_home", 0.0)
    elif selection == "away":
        adj = ctx.get("prob_adj_away", 0.0)
    else:
        adj = 0.0

    if adj != 0.0:
        adjusted = max(0.05, min(0.95, float(prob) + adj))
        signal["model_probability"] = round(adjusted, 4)
        signal["context_adj"] = adj

    return signal


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _load_csv(path: Path, cutoff: date) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    df["_date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce").dt.date
    df = df.dropna(subset=["_date", "HomeTeam", "AwayTeam"])
    # Anti-leakage: only past matches
    df = df[df["_date"] < cutoff].copy()
    return df


def _days_since_last_match(df: pd.DataFrame, team: str, match_date: date) -> int | None:
    mask = (df["HomeTeam"] == team) | (df["AwayTeam"] == team)
    dates = df.loc[mask, "_date"].dropna()
    if dates.empty:
        return None
    last = max(dates)
    return (match_date - last).days


def _recent_form(
    df: pd.DataFrame, team: str, match_date: date, n: int = 5
) -> tuple[float | None, str]:
    """Return (form_score 0-1, form_string e.g. 'WWDLW') for last n matches."""
    rows = []
    for _, row in df.iterrows():
        is_home = row["HomeTeam"] == team
        is_away = row["AwayTeam"] == team
        if not is_home and not is_away:
            continue
        ftr = str(row.get("FTR", "")).strip().upper()
        if ftr not in ("H", "D", "A"):
            continue
        if is_home:
            result = "W" if ftr == "H" else ("D" if ftr == "D" else "L")
        else:
            result = "W" if ftr == "A" else ("D" if ftr == "D" else "L")
        rows.append((row["_date"], result))

    rows.sort(key=lambda x: x[0], reverse=True)
    last_n = rows[:n]
    if not last_n:
        return None, ""

    form_str = "".join(r for _, r in reversed(last_n))
    score = sum(_FORM_PTS[r] for _, r in last_n) / len(last_n)
    return score, form_str


def _fatigue_level(rest_days: int | None) -> str:
    if rest_days is None:
        return "none"
    if rest_days < 3:
        return "severe"
    if rest_days < 5:
        return "mild"
    return "none"


def _empty_context() -> dict[str, Any]:
    return {
        "rest_days_home": None,
        "rest_days_away": None,
        "form_home": None,
        "form_away": None,
        "form_str_home": "",
        "form_str_away": "",
        "fatigue_home": "none",
        "fatigue_away": "none",
        "prob_adj_home": 0.0,
        "prob_adj_away": 0.0,
    }
