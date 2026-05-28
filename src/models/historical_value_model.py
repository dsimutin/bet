"""
Historical value model for football-data.co.uk style datasets.

The model intentionally starts simple and auditable:
1. Convert bookmaker 1X2 odds into no-vig market probabilities.
2. Learn historical calibration buckets from past matches only.
3. Predict calibrated probabilities for future windows.
4. Paper-backtest only selections whose expected value clears a threshold.

This is not a profit guarantee. It is a leakage-aware baseline that can be
replaced later with stronger models such as Poisson/Dixon-Coles or gradient
boosting once the data layer is stable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd

Selection = Literal["home", "draw", "away"]
SELECTIONS: tuple[Selection, Selection, Selection] = ("home", "draw", "away")


@dataclass(frozen=True)
class HistoricalValueModelConfig:
    bookmaker_prefix: str = "B365"
    min_edge_pct: float = 2.0
    min_signal_probability: float = 0.45
    max_signal_odds: float | None = None
    require_recent_quality: bool = True
    quality_window_days: int = 30
    min_quality_bets: int = 30
    min_quality_win_rate: float = 0.45
    min_quality_roi_pct: float = 0.0
    min_train_matches: int = 60
    test_window_days: int = 30
    recent_windows_days: tuple[int, ...] = (7, 30)
    smoothing_alpha: float = 20.0
    probability_bins: tuple[float, ...] = (0.0, 0.2, 0.35, 0.5, 0.65, 0.8, 1.01)
    max_bets_per_match: int = 1


@dataclass(frozen=True)
class CalibrationBucket:
    selection: Selection
    probability_bin: str
    trials: int
    wins: int
    empirical_probability: float


@dataclass(frozen=True)
class ValuePrediction:
    event_id: str
    match_date: str
    home_team: str
    away_team: str
    selection: Selection
    odds: float
    market_probability: float
    calibrated_probability: float
    edge_pct: float
    bookmaker_key: str | None = None
    bookmaker_title: str | None = None


@dataclass(frozen=True)
class BacktestBet:
    event_id: str
    match_date: str
    home_team: str
    away_team: str
    selection: Selection
    odds: float
    calibrated_probability: float
    edge_pct: float
    result: Literal["win", "loss"]
    profit_units: float


@dataclass(frozen=True)
class BacktestSummary:
    window_name: str
    train_start: str | None
    train_end: str | None
    test_start: str | None
    test_end: str | None
    n_train_matches: int
    n_test_matches: int
    n_bets: int
    profit_units: float
    roi_pct: float
    win_rate: float
    avg_edge_pct: float
    max_drawdown_units: float
    low_sample: bool


@dataclass(frozen=True)
class HistoricalModelReport:
    generated_at_utc: str
    config: dict[str, Any]
    fold_summaries: list[BacktestSummary]
    recent_summaries: list[BacktestSummary]
    calibration: list[CalibrationBucket]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at_utc": self.generated_at_utc,
            "config": self.config,
            "fold_summaries": [asdict(item) for item in self.fold_summaries],
            "recent_summaries": [asdict(item) for item in self.recent_summaries],
            "calibration": [asdict(item) for item in self.calibration],
            "notes": self.notes,
        }


class HistoricalValueModel:
    """Calibrated-market baseline model for historical 1X2 football odds."""

    OUTCOME_TO_SELECTION: dict[str, Selection] = {"H": "home", "D": "draw", "A": "away"}
    SELECTION_TO_SUFFIX: dict[Selection, str] = {"home": "H", "draw": "D", "away": "A"}
    SELECTION_RU: dict[Selection, str] = {"home": "П1", "draw": "X", "away": "П2"}

    def __init__(self, config: HistoricalValueModelConfig | None = None) -> None:
        self.config = config or HistoricalValueModelConfig()
        self.calibration_: dict[tuple[Selection, str], CalibrationBucket] = {}

    def fit(self, matches: pd.DataFrame) -> HistoricalValueModel:
        prepared = self.prepare_matches(matches)
        long_df = self._to_long_training_frame(prepared)
        buckets: dict[tuple[Selection, str], CalibrationBucket] = {}

        if long_df.empty:
            self.calibration_ = {}
            return self

        grouped = long_df.groupby(["selection", "probability_bin"], observed=True)
        for (selection, probability_bin), group in grouped:
            trials = int(len(group))
            wins = int(group["won"].sum())
            buckets[(selection, probability_bin)] = CalibrationBucket(
                selection=selection,
                probability_bin=str(probability_bin),
                trials=trials,
                wins=wins,
                empirical_probability=round(wins / trials, 6) if trials else 0.0,
            )

        self.calibration_ = buckets
        return self

    def predict_match(self, row: pd.Series) -> list[ValuePrediction]:
        odds = self._extract_odds(row)
        if odds is None:
            return []

        market_probs = self._devig_probs(odds)
        match_date = self._date_to_iso(row["match_date"])
        event_id = self._event_id(row)
        predictions: list[ValuePrediction] = []

        for idx, selection in enumerate(SELECTIONS):
            raw_prob = market_probs[idx]
            decimal_odds = odds[idx]
            calibrated_prob = self._calibrated_probability(selection, raw_prob)
            edge_pct = (calibrated_prob * decimal_odds - 1.0) * 100
            predictions.append(
                ValuePrediction(
                    event_id=event_id,
                    match_date=match_date,
                    home_team=str(row["home_team"]),
                    away_team=str(row["away_team"]),
                    selection=selection,
                    odds=round(decimal_odds, 4),
                    market_probability=round(raw_prob, 6),
                    calibrated_probability=round(calibrated_prob, 6),
                    edge_pct=round(edge_pct, 4),
                    bookmaker_key=self._optional_row_str(row, "source_bookmaker_key"),
                    bookmaker_title=self._optional_row_str(row, "source_bookmaker_title"),
                )
            )
        return predictions

    def walk_forward_backtest(
        self, matches: pd.DataFrame
    ) -> tuple[list[BacktestBet], list[BacktestSummary]]:
        prepared = self.prepare_matches(matches)
        if prepared.empty:
            return [], []

        all_bets: list[BacktestBet] = []
        summaries: list[BacktestSummary] = []
        min_date = prepared["match_date"].min()
        max_date = prepared["match_date"].max()
        test_start = min_date + timedelta(days=self.config.test_window_days)

        while test_start <= max_date:
            test_end = min(test_start + timedelta(days=self.config.test_window_days - 1), max_date)
            train_df = prepared[prepared["match_date"] < test_start]
            test_df = prepared[
                (prepared["match_date"] >= test_start) & (prepared["match_date"] <= test_end)
            ]

            bets = self._fit_and_backtest_window(train_df, test_df)
            all_bets.extend(bets)
            summaries.append(
                self._summarize_window(
                    window_name=f"fold_{len(summaries) + 1}",
                    train_df=train_df,
                    test_df=test_df,
                    bets=bets,
                )
            )
            test_start = test_end + timedelta(days=1)

        return all_bets, summaries

    def recent_validation(self, matches: pd.DataFrame) -> list[BacktestSummary]:
        prepared = self.prepare_matches(matches)
        if prepared.empty:
            return []

        max_date = prepared["match_date"].max()
        summaries: list[BacktestSummary] = []

        for days in self.config.recent_windows_days:
            test_start = max_date - timedelta(days=days - 1)
            train_df = prepared[prepared["match_date"] < test_start]
            test_df = prepared[prepared["match_date"] >= test_start]
            bets = self._fit_and_backtest_window(train_df, test_df)
            summaries.append(
                self._summarize_window(
                    window_name=f"last_{days}_days",
                    train_df=train_df,
                    test_df=test_df,
                    bets=bets,
                )
            )

        return summaries

    def build_report(self, matches: pd.DataFrame) -> HistoricalModelReport:
        _, folds = self.walk_forward_backtest(matches)
        recent = self.recent_validation(matches)
        prepared = self.prepare_matches(matches)
        self.fit(prepared)

        notes = [
            "Paper trading only: no real stakes are placed.",
            "Model uses only market odds and historical outcomes before each test window.",
            "Recent windows are diagnostics, not proof of profitability.",
        ]

        return HistoricalModelReport(
            generated_at_utc=datetime.now(timezone.utc).isoformat(),
            config=asdict(self.config),
            fold_summaries=folds,
            recent_summaries=recent,
            calibration=sorted(
                self.calibration_.values(),
                key=lambda item: (item.selection, item.probability_bin),
            ),
            notes=notes,
        )

    def generate_signals(
        self,
        history_matches: pd.DataFrame,
        candidate_matches: pd.DataFrame,
        max_signals: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Train on historical matches and create Telegram-ready paper signals.

        ``history_matches`` must contain finished matches with results.
        ``candidate_matches`` may contain upcoming fixtures or manually imported
        current lines. Only candidate data is scored; it is never used for fit.
        """
        history = self.prepare_matches(history_matches)
        candidates = self.prepare_prediction_matches(candidate_matches)
        if len(history) < self.config.min_train_matches or candidates.empty:
            return []
        if self.config.require_recent_quality and not self.passes_quality_gate(history):
            return []

        self.fit(history)
        signals: list[dict[str, Any]] = []
        generated_at = datetime.now(timezone.utc).isoformat()
        history_hash = "sha256:" + hashlib.sha256(
            history[["match_date", "home_team", "away_team"]]
            .astype(str)
            .to_csv(index=False)
            .encode()
        ).hexdigest()

        for _, row in candidates.iterrows():
            predictions = [
                prediction
                for prediction in self.predict_match(row)
                if prediction.edge_pct >= self.config.min_edge_pct
                and prediction.calibrated_probability >= self.config.min_signal_probability
                and self._odds_allowed(prediction.odds)
            ]
            predictions = sorted(predictions, key=lambda item: item.edge_pct, reverse=True)
            for prediction in predictions[: self.config.max_bets_per_match]:
                sig = self._prediction_to_signal(prediction, generated_at)
                sig["dataset_hash"] = history_hash
                signals.append(sig)

        return sorted(signals, key=lambda item: item["edge_pct"], reverse=True)[:max_signals]

    def quality_gate_report(self, history_matches: pd.DataFrame) -> dict[str, Any]:
        prepared = self.prepare_matches(history_matches)
        if prepared.empty:
            return {
                "passed": False,
                "reason": "no_historical_matches",
                "summary": None,
            }

        original_windows = self.config.recent_windows_days
        object.__setattr__(self.config, "recent_windows_days", (self.config.quality_window_days,))
        try:
            summaries = self.recent_validation(prepared)
        finally:
            object.__setattr__(self.config, "recent_windows_days", original_windows)

        summary = summaries[0] if summaries else None
        if summary is None:
            return {"passed": False, "reason": "no_recent_validation_window", "summary": None}

        failures: list[str] = []
        if summary.n_bets < self.config.min_quality_bets:
            failures.append(f"n_bets {summary.n_bets} < {self.config.min_quality_bets}")
        if summary.win_rate < self.config.min_quality_win_rate:
            failures.append(
                f"win_rate {summary.win_rate:.2%} < {self.config.min_quality_win_rate:.2%}"
            )
        if summary.roi_pct < self.config.min_quality_roi_pct:
            failures.append(
                f"roi_pct {summary.roi_pct:.2f}% < {self.config.min_quality_roi_pct:.2f}%"
            )

        return {
            "passed": not failures,
            "reason": "; ".join(failures) if failures else "passed",
            "summary": asdict(summary),
        }

    def passes_quality_gate(self, history_matches: pd.DataFrame) -> bool:
        return bool(self.quality_gate_report(history_matches)["passed"])

    def save_signals(self, signals: list[dict[str, Any]], output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        output_path = output_dir / f"model_signals_{date_str}.json"
        output_path.write_text(
            json.dumps(signals, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return output_path

    def write_report(self, report: HistoricalModelReport, output_dir: Path) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "historical_value_model_report.json"
        md_path = output_dir / "historical_value_model_report.md"

        json_path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        md_path.write_text(self._format_markdown(report), encoding="utf-8")
        return json_path, md_path

    def prepare_matches(self, matches: pd.DataFrame) -> pd.DataFrame:
        if matches.empty:
            return matches.copy()

        df = matches.copy()
        rename_map = {
            "Date": "match_date",
            "HomeTeam": "home_team",
            "AwayTeam": "away_team",
            "FTR": "result_ft",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

        required = {"match_date", "home_team", "away_team", "result_ft"}
        odds_cols = self._odds_columns()
        missing = (required | set(odds_cols)) - set(df.columns)
        if missing:
            raise KeyError(f"Missing required historical columns: {sorted(missing)}")

        df["match_date"] = pd.to_datetime(df["match_date"], dayfirst=True, errors="coerce").dt.date
        for col in odds_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        odds_col_list = list(odds_cols)
        df = df.dropna(subset=["match_date", "home_team", "away_team", "result_ft", *odds_col_list])
        df = df[df["result_ft"].isin(["H", "D", "A"])]
        df = df[(df[odds_col_list] > 1.0).all(axis=1)]
        return df.sort_values(["match_date", "home_team", "away_team"]).reset_index(drop=True)

    def prepare_prediction_matches(self, matches: pd.DataFrame) -> pd.DataFrame:
        if matches.empty:
            return matches.copy()

        df = matches.copy()
        rename_map = {
            "Date": "match_date",
            "HomeTeam": "home_team",
            "AwayTeam": "away_team",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

        required = {"match_date", "home_team", "away_team"}
        odds_cols = self._odds_columns()
        missing = (required | set(odds_cols)) - set(df.columns)
        if missing:
            raise KeyError(f"Missing required prediction columns: {sorted(missing)}")

        df["match_date"] = pd.to_datetime(df["match_date"], dayfirst=True, errors="coerce").dt.date
        for col in odds_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        odds_col_list = list(odds_cols)
        df = df.dropna(subset=["match_date", "home_team", "away_team", *odds_col_list])
        df = df[(df[odds_col_list] > 1.0).all(axis=1)]
        return df.sort_values(["match_date", "home_team", "away_team"]).reset_index(drop=True)

    def _fit_and_backtest_window(
        self, train_df: pd.DataFrame, test_df: pd.DataFrame
    ) -> list[BacktestBet]:
        if len(train_df) < self.config.min_train_matches or test_df.empty:
            return []

        self.fit(train_df)
        bets: list[BacktestBet] = []
        for _, row in test_df.iterrows():
            candidates = [
                prediction
                for prediction in self.predict_match(row)
                if prediction.edge_pct >= self.config.min_edge_pct
                and prediction.calibrated_probability >= self.config.min_signal_probability
                and self._odds_allowed(prediction.odds)
            ]
            candidates = sorted(candidates, key=lambda item: item.edge_pct, reverse=True)
            for prediction in candidates[: self.config.max_bets_per_match]:
                bets.append(self._settle_prediction(prediction, row))
        return bets

    def _settle_prediction(self, prediction: ValuePrediction, row: pd.Series) -> BacktestBet:
        actual = self.OUTCOME_TO_SELECTION[str(row["result_ft"])]
        won = prediction.selection == actual
        profit = prediction.odds - 1.0 if won else -1.0
        return BacktestBet(
            event_id=prediction.event_id,
            match_date=prediction.match_date,
            home_team=prediction.home_team,
            away_team=prediction.away_team,
            selection=prediction.selection,
            odds=prediction.odds,
            calibrated_probability=prediction.calibrated_probability,
            edge_pct=prediction.edge_pct,
            result="win" if won else "loss",
            profit_units=round(profit, 4),
        )

    def _prediction_to_signal(
        self, prediction: ValuePrediction, generated_at: str
    ) -> dict[str, Any]:
        fair_odds = 1.0 / prediction.calibrated_probability
        confidence = self._confidence_label(prediction)
        return {
            "signal_id": f"model_{prediction.event_id}_{prediction.selection}",
            "strategy_id": "historical_value_model_v1",
            "event_id": prediction.event_id,
            "home_team": prediction.home_team,
            "away_team": prediction.away_team,
            "event_date": prediction.match_date,
            "bookmaker": prediction.bookmaker_key or self.config.bookmaker_prefix,
            "bookmaker_title": prediction.bookmaker_title
            or prediction.bookmaker_key
            or self.config.bookmaker_prefix,
            "market_key": "h2h",
            "selection": prediction.selection,
            "selection_ru": self.SELECTION_RU[prediction.selection],
            "entry_odds": prediction.odds,
            "reference_fair_odds": round(fair_odds, 4),
            "edge_pct": prediction.edge_pct,
            "model_probability": prediction.calibrated_probability,
            "paper_stake_units": _paper_stake_units(
                model_probability=prediction.calibrated_probability,
                decimal_odds=prediction.odds,
            ),
            "confidence": confidence,
            "timestamp_utc": generated_at,
            "explain_formatted": (
                f"Модельная вероятность {prediction.calibrated_probability:.1%}; "
                f"порог {self.config.min_signal_probability:.1%}; edge {prediction.edge_pct:.2f}%."
            ),
            "status": "paper",
        }

    def _confidence_label(self, prediction: ValuePrediction) -> str:
        if prediction.calibrated_probability >= 0.6 and prediction.edge_pct >= 5.0:
            return "high"
        if prediction.calibrated_probability >= 0.5 and prediction.edge_pct >= 3.0:
            return "medium"
        return "low"

    def _odds_allowed(self, odds: float) -> bool:
        return self.config.max_signal_odds is None or odds <= self.config.max_signal_odds

    def _summarize_window(
        self,
        window_name: str,
        train_df: pd.DataFrame,
        test_df: pd.DataFrame,
        bets: list[BacktestBet],
    ) -> BacktestSummary:
        profit = sum(bet.profit_units for bet in bets)
        n_bets = len(bets)
        wins = sum(1 for bet in bets if bet.result == "win")
        avg_edge = sum(bet.edge_pct for bet in bets) / n_bets if n_bets else 0.0
        return BacktestSummary(
            window_name=window_name,
            train_start=(
                self._date_to_iso(train_df["match_date"].min()) if not train_df.empty else None
            ),
            train_end=(
                self._date_to_iso(train_df["match_date"].max()) if not train_df.empty else None
            ),
            test_start=(
                self._date_to_iso(test_df["match_date"].min()) if not test_df.empty else None
            ),
            test_end=self._date_to_iso(test_df["match_date"].max()) if not test_df.empty else None,
            n_train_matches=int(len(train_df)),
            n_test_matches=int(len(test_df)),
            n_bets=n_bets,
            profit_units=round(profit, 4),
            roi_pct=round(profit / n_bets * 100, 4) if n_bets else 0.0,
            win_rate=round(wins / n_bets, 4) if n_bets else 0.0,
            avg_edge_pct=round(avg_edge, 4),
            max_drawdown_units=round(self._max_drawdown([bet.profit_units for bet in bets]), 4),
            low_sample=n_bets < 30,
        )

    def _to_long_training_frame(self, matches: pd.DataFrame) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for _, row in matches.iterrows():
            odds = self._extract_odds(row)
            if odds is None:
                continue
            probs = self._devig_probs(odds)
            actual = self.OUTCOME_TO_SELECTION[str(row["result_ft"])]
            for idx, selection in enumerate(SELECTIONS):
                probability = probs[idx]
                rows.append(
                    {
                        "selection": selection,
                        "probability_bin": self._probability_bin(probability),
                        "market_probability": probability,
                        "won": 1 if selection == actual else 0,
                    }
                )
        return pd.DataFrame(rows)

    def _calibrated_probability(self, selection: Selection, market_probability: float) -> float:
        bucket_name = self._probability_bin(market_probability)
        bucket = self.calibration_.get((selection, bucket_name))
        if bucket is None:
            return market_probability

        alpha = self.config.smoothing_alpha
        smoothed = (bucket.wins + alpha * market_probability) / (bucket.trials + alpha)
        return min(max(smoothed, 0.001), 0.999)

    def _extract_odds(self, row: pd.Series) -> tuple[float, float, float] | None:
        cols = self._odds_columns()
        try:
            odds = (float(row[cols[0]]), float(row[cols[1]]), float(row[cols[2]]))
        except (KeyError, TypeError, ValueError):
            return None
        if any(value <= 1.0 for value in odds):
            return None
        return odds

    def _odds_columns(self) -> tuple[str, str, str]:
        prefix = self.config.bookmaker_prefix
        return (f"{prefix}H", f"{prefix}D", f"{prefix}A")

    def _devig_probs(self, odds: tuple[float, float, float]) -> tuple[float, float, float]:
        implied = [1.0 / value for value in odds]
        total = sum(implied)
        return (implied[0] / total, implied[1] / total, implied[2] / total)

    def _probability_bin(self, probability: float) -> str:
        bins = self.config.probability_bins
        for lower, upper in zip(bins, bins[1:]):
            if lower <= probability < upper:
                return f"{lower:.2f}-{upper:.2f}"
        return f"{bins[-2]:.2f}-{bins[-1]:.2f}"

    def _event_id(self, row: pd.Series) -> str:
        source_event_id = self._optional_row_str(row, "source_event_id")
        if source_event_id:
            return source_event_id
        match_date = self._date_to_iso(row["match_date"])
        home = str(row["home_team"]).strip().lower().replace(" ", "_")
        away = str(row["away_team"]).strip().lower().replace(" ", "_")
        return f"soccer__{home}__{away}__{match_date}"

    def _optional_row_str(self, row: pd.Series, key: str) -> str | None:
        value = row.get(key)
        if value is None or pd.isna(value):
            return None
        text = str(value).strip()
        return text or None

    def _date_to_iso(self, value: Any) -> str:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    def _max_drawdown(self, profits: list[float]) -> float:
        peak = 0.0
        equity = 0.0
        max_drawdown = 0.0
        for profit in profits:
            equity += profit
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        return max_drawdown

    def _format_markdown(self, report: HistoricalModelReport) -> str:
        lines = [
            "# Historical Value Model Report",
            "",
            f"Generated UTC: {report.generated_at_utc}",
            "",
            "## Notes",
        ]
        lines.extend(f"- {note}" for note in report.notes)
        lines.extend(["", "## Recent Validation", ""])
        lines.extend(self._summary_table(report.recent_summaries))
        lines.extend(["", "## Walk-Forward Folds", ""])
        lines.extend(self._summary_table(report.fold_summaries))
        return "\n".join(lines) + "\n"

    def _summary_table(self, summaries: list[BacktestSummary]) -> list[str]:
        if not summaries:
            return ["_No validation windows available._"]
        lines = [
            "| Window | Train | Test | Bets | ROI | Profit | Win rate | Low sample |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
        for item in summaries:
            train = f"{item.train_start}..{item.train_end}"
            test = f"{item.test_start}..{item.test_end}"
            lines.append(
                "| "
                f"{item.window_name} | {train} | {test} | {item.n_bets} | "
                f"{item.roi_pct:.2f}% | {item.profit_units:.2f} | "
                f"{item.win_rate:.2%} | {item.low_sample} |"
            )
        return lines


def _paper_stake_units(
    model_probability: float,
    decimal_odds: float,
    max_units: float = 2.0,
    kelly_fraction: float = 0.10,
) -> float:
    net_odds = decimal_odds - 1.0
    if net_odds <= 0:
        return 0.0
    kelly = (model_probability * decimal_odds - 1.0) / net_odds
    stake = max(0.0, kelly * kelly_fraction * max_units)
    return round(min(max_units, stake), 2)
