"""Signal generation from registered production Dixon-Coles models."""

from __future__ import annotations

import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

import pandas as pd

from src.models.calibrator import ProbabilityCalibrator
from src.models.dixon_coles import DixonColesModel
from src.models.predictor import ModelValuePredictor


class ProductionDixonColesSignalEngine:
    """Creates value signals from model probabilities versus market probabilities."""

    SELECTION_RU = {"home": "П1", "draw": "X", "away": "П2"}

    def __init__(
        self,
        model: DixonColesModel,
        min_edge_pct: float = 2.0,
        min_model_probability: float = 0.45,
        max_entry_odds: float | None = None,
        max_signals: int = 10,
        bookmaker_prefix: str = "B365",
        calibrator: ProbabilityCalibrator | None = None,
        paper_bankroll_units: float = 100.0,
        kelly_fraction: float = 0.25,
        max_stake_units: float = 2.0,
    ) -> None:
        self.model = model
        self.min_edge_pct = min_edge_pct
        self.min_model_probability = min_model_probability
        self.max_entry_odds = max_entry_odds
        self.max_signals = max_signals
        self.bookmaker_prefix = bookmaker_prefix
        self.calibrator = calibrator
        self.paper_bankroll_units = paper_bankroll_units
        self.kelly_fraction = kelly_fraction
        self.max_stake_units = max_stake_units

    def generate_signals(self, candidate_matches: pd.DataFrame) -> list[dict[str, Any]]:
        candidates = _prepare_candidates(candidate_matches, self.bookmaker_prefix)
        predictor = ModelValuePredictor(self.model, calibrator=self.calibrator)
        generated_at = datetime.now(timezone.utc).isoformat()
        signals: list[dict[str, Any]] = []
        for _, row in candidates.iterrows():
            odds = (
                float(row[f"{self.bookmaker_prefix}H"]),
                float(row[f"{self.bookmaker_prefix}D"]),
                float(row[f"{self.bookmaker_prefix}A"]),
            )
            values = predictor.signals(
                str(row["home_team"]),
                str(row["away_team"]),
                odds_1x2=odds,
                min_edge_pct=self.min_edge_pct,
                min_model_prob=self.min_model_probability,
            )
            if self.max_entry_odds is not None:
                values = [item for item in values if item.odds <= self.max_entry_odds]
            for value in values:
                stake_units = _paper_stake_units(
                    model_probability=value.model_prob,
                    odds=value.odds,
                    bankroll_units=self.paper_bankroll_units,
                    kelly_fraction=self.kelly_fraction,
                    max_stake_units=self.max_stake_units,
                )
                signals.append(
                    _to_signal(value, row, generated_at, self.bookmaker_prefix, stake_units)
                )
        return sorted(signals, key=lambda item: item["edge_vs_fair_pct"], reverse=True)[
            : self.max_signals
        ]

    def save_signals(self, signals: list[dict[str, Any]], output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        output_path = output_dir / f"production_dc_signals_{date_str}.json"
        output_path.write_text(
            json.dumps(signals, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return output_path


def _prepare_candidates(matches: pd.DataFrame, bookmaker_prefix: str) -> pd.DataFrame:
    df = matches.copy()
    df = df.rename(
        columns={
            "Date": "match_date",
            "HomeTeam": "home_team",
            "AwayTeam": "away_team",
        }
    )
    odds_cols = [f"{bookmaker_prefix}H", f"{bookmaker_prefix}D", f"{bookmaker_prefix}A"]
    required = {"match_date", "home_team", "away_team", *odds_cols}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"Missing production signal columns: {sorted(missing)}")
    df["match_date"] = pd.to_datetime(df["match_date"], dayfirst=True, errors="coerce").dt.date
    for col in odds_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["match_date", "home_team", "away_team", *odds_cols])
    df = df[(df[odds_cols] > 1.0).all(axis=1)]
    df = df.sort_values(["match_date", "home_team", "away_team"]).reset_index(drop=True)

    # Warn when snapshot_ts_utc / event_time_utc are missing — production signals should
    # always carry these to verify snapshot_ts_utc < event_time_utc (no post-KO odds).
    if "event_time_utc" not in df.columns:
        _log.warning(
            "candidate matches missing event_time_utc — cannot verify odds were captured "
            "before kick-off; signals marked as entry_odds_source=pre_match_unverified"
        )
    if "snapshot_ts_utc" not in df.columns:
        _log.warning("candidate matches missing snapshot_ts_utc — odds timestamp unknown")

    return df


def _to_signal(
    value: Any,
    row: pd.Series,
    generated_at: str,
    bookmaker_prefix: str,
    paper_stake_units: float,
) -> dict[str, Any]:
    event_id = _event_id(row)
    edge_vs_fair_pct = round(value.edge_vs_fair * 100.0, 4)
    signal = {
        "signal_id": f"production_dc_{event_id}_{value.selection}_{_bookmaker_key(row, bookmaker_prefix)}",
        "strategy_id": "production_dixon_coles_value_v1",
        "model_id": value.model_id,
        "event_id": event_id,
        "home_team": str(row["home_team"]),
        "away_team": str(row["away_team"]),
        "event_date": str(row["match_date"]),
        "bookmaker": _bookmaker_key(row, bookmaker_prefix),
        "bookmaker_title": _optional_row_str(row, "source_bookmaker_title") or bookmaker_prefix,
        "market_key": "h2h",
        "source_event_id": _optional_row_str(row, "source_event_id") or event_id,
        "source_sport_key": _optional_row_str(row, "source_sport_key") or "",
        "source_bookmaker_key": _optional_row_str(row, "source_bookmaker_key") or "",
        "source_bookmaker_title": _optional_row_str(row, "source_bookmaker_title") or "",
        "source_market_key": _optional_row_str(row, "source_market_key") or "h2h",
        "source_last_update_utc": _optional_row_str(row, "source_last_update_utc") or "",
        "selection": value.selection,
        "selection_ru": ProductionDixonColesSignalEngine.SELECTION_RU[value.selection],
        "entry_odds": value.odds,
        "entry_odds_source": (
            "snapshot"
            if "snapshot_ts_utc" in row.index and pd.notna(row.get("snapshot_ts_utc"))
            else "pre_match_unverified"
        ),
        "snapshot_ts_utc": str(row.get("snapshot_ts_utc") or ""),
        "event_time_utc": str(row.get("event_time_utc") or ""),
        "reference_fair_odds": round(1.0 / value.model_prob, 4),
        "edge_pct": edge_vs_fair_pct,
        "edge_vs_market_pct": round(value.edge_vs_market * 100.0, 4),
        "edge_vs_fair_pct": edge_vs_fair_pct,
        "paper_stake_units": paper_stake_units,
        "stake_units": paper_stake_units,
        "model_probability": value.model_prob,
        "market_probability": value.market_prob,
        "fair_market_probability": value.fair_market_prob,
        "confidence": _confidence(value.model_prob, edge_vs_fair_pct),
        "timestamp_utc": generated_at,
        "explain_formatted": (
            f"Dixon-Coles model {value.model_id or 'unversioned'}: "
            f"model {value.model_prob:.1%}, fair market {value.fair_market_prob:.1%}, "
            f"edge {edge_vs_fair_pct:.2f}pp, paper stake {paper_stake_units:.2f}u."
        ),
        "status": "paper",
    }
    from src.models.timestamp_policy import verify_pre_match_timestamps

    signal["timestamp_verification_status"] = verify_pre_match_timestamps(signal)
    age = _age_seconds(signal.get("snapshot_ts_utc"), generated_at)
    signal["odds_snapshot_age_seconds"] = age
    signal["odds_freshness_tier"] = _odds_freshness_tier(age)
    return signal


def recompute_signal_metrics(
    signal: dict[str, Any],
    *,
    adjusted_probability: float | None = None,
    kelly_fraction: float = 0.25,
    max_stake_units: float = 2.0,
    bankroll_units: float = 100.0,
) -> dict[str, Any]:
    """Recompute probability-derived paper metrics after post-model adjustments.

    `edge_pct` and `edge_vs_fair_pct` are ROI edge percentages:
    `(entry_odds * model_probability - 1) * 100`.
    """
    updated = dict(signal)
    raw_probability = (
        adjusted_probability
        if adjusted_probability is not None
        else updated.get("model_probability", updated.get("model_prob"))
    )
    if raw_probability is None:
        raw_probability = 0.0
    probability = max(0.0001, min(0.9999, float(raw_probability)))
    odds = float(updated.get("entry_odds", 0.0) or 0.0)
    fair_odds = 1.0 / probability
    edge_roi_pct = (odds / fair_odds - 1.0) * 100.0 if odds > 0 else 0.0

    market_probability_raw = updated.get("market_probability", updated.get("market_prob", 0.0))
    try:
        market_probability = float(market_probability_raw)
    except (TypeError, ValueError):
        market_probability = (1.0 / odds) if odds > 1.0 else 0.0
    edge_vs_market_pct = (probability - market_probability) * 100.0
    stake_units = _paper_stake_units(
        model_probability=probability,
        odds=odds,
        bankroll_units=bankroll_units,
        kelly_fraction=kelly_fraction,
        max_stake_units=max_stake_units,
    )

    updated.update(
        {
            "model_probability": round(probability, 4),
            "model_prob": round(probability, 4),
            "reference_fair_odds": round(fair_odds, 4),
            "edge_pct": round(edge_roi_pct, 4),
            "edge_vs_fair_pct": round(edge_roi_pct, 4),
            "edge_vs_market_pct": round(edge_vs_market_pct, 4),
            "confidence": _confidence(probability, edge_roi_pct),
            "stake_units": stake_units,
            "paper_stake_units": stake_units,
        }
    )
    return updated


def _paper_stake_units(
    model_probability: float,
    odds: float,
    bankroll_units: float,
    kelly_fraction: float,
    max_stake_units: float,
) -> float:
    b = max(odds - 1.0, 1e-9)
    q = 1.0 - model_probability
    kelly = max((b * model_probability - q) / b, 0.0)
    stake = bankroll_units * kelly_fraction * kelly
    return round(min(max(stake, 0.0), max_stake_units), 4)


def _event_id(row: pd.Series) -> str:
    source = _optional_row_str(row, "source_event_id")
    if source:
        return source
    home = str(row["home_team"]).strip().lower().replace(" ", "_")
    away = str(row["away_team"]).strip().lower().replace(" ", "_")
    return f"soccer__{home}__{away}__{row['match_date']}"


def _bookmaker_key(row: pd.Series, bookmaker_prefix: str) -> str:
    return _optional_row_str(row, "source_bookmaker_key") or bookmaker_prefix


def _optional_row_str(row: pd.Series, key: str) -> str | None:
    value = row.get(key)
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _age_seconds(snapshot_raw: Any, now_raw: Any) -> int | None:
    try:
        snapshot = datetime.fromisoformat(str(snapshot_raw).replace("Z", "+00:00"))
        now = datetime.fromisoformat(str(now_raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if snapshot.tzinfo is None:
        snapshot = snapshot.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(
        0, int((now.astimezone(timezone.utc) - snapshot.astimezone(timezone.utc)).total_seconds())
    )


def _odds_freshness_tier(age_seconds: int | None) -> str:
    import os

    if age_seconds is None:
        return "stale_blocked"
    priority_max = int(os.environ.get("PRIORITY_ODDS_MAX_AGE_SECONDS", "900"))
    watchlist_max = int(os.environ.get("WATCHLIST_ODDS_MAX_AGE_SECONDS", "3600"))
    if age_seconds <= priority_max:
        return "priority"
    if age_seconds <= watchlist_max:
        return "watchlist"
    return "stale_blocked"


def _confidence(model_probability: float, edge_pct: float) -> str:
    if model_probability >= 0.6 and edge_pct >= 5.0:
        return "high"
    if model_probability >= 0.5 and edge_pct >= 3.0:
        return "medium"
    return "low"
