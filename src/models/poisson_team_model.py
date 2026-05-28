"""Simple leakage-aware Poisson team-strength model for football 1X2 markets."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

import pandas as pd

Selection = Literal["home", "draw", "away"]
SELECTIONS: tuple[Selection, Selection, Selection] = ("home", "draw", "away")


@dataclass(frozen=True)
class PoissonTeamModelConfig:
    bookmaker_prefix: str = "B365"
    min_train_matches: int = 60
    max_goals: int = 8
    shrinkage_matches: float = 12.0
    min_edge_pct: float = 2.0
    min_signal_probability: float = 0.45
    max_signal_odds: float | None = None
    recent_window_days: int = 30
    min_quality_bets: int = 20
    min_quality_win_rate: float = 0.42
    min_quality_roi_pct: float = -5.0


@dataclass(frozen=True)
class PoissonPrediction:
    event_id: str
    match_date: str
    home_team: str
    away_team: str
    selection: Selection
    odds: float
    probability: float
    edge_pct: float
    expected_home_goals: float
    expected_away_goals: float
    bookmaker_key: str | None = None
    bookmaker_title: str | None = None


@dataclass(frozen=True)
class PoissonBacktestBet:
    event_id: str
    match_date: str
    home_team: str
    away_team: str
    selection: Selection
    odds: float
    probability: float
    edge_pct: float
    result: Literal["win", "loss"]
    profit_units: float


class PoissonTeamModel:
    """Predicts 1X2 probabilities from historical goals and team strengths."""

    OUTCOME_TO_SELECTION: dict[str, Selection] = {"H": "home", "D": "draw", "A": "away"}
    SELECTION_TO_SUFFIX: dict[Selection, str] = {"home": "H", "draw": "D", "away": "A"}

    def __init__(self, config: PoissonTeamModelConfig | None = None) -> None:
        self.config = config or PoissonTeamModelConfig()
        self.global_home_goals_: float = 1.35
        self.global_away_goals_: float = 1.10
        self.team_stats_: dict[str, dict[str, float]] = {}

    def fit(self, matches: pd.DataFrame) -> PoissonTeamModel:
        prepared = self.prepare_matches(matches)
        if prepared.empty:
            return self

        self.global_home_goals_ = max(float(prepared["goals_home_ft"].mean()), 0.1)
        self.global_away_goals_ = max(float(prepared["goals_away_ft"].mean()), 0.1)

        teams = sorted(set(prepared["home_team"]) | set(prepared["away_team"]))
        stats: dict[str, dict[str, float]] = {}
        for team in teams:
            home = prepared[prepared["home_team"] == team]
            away = prepared[prepared["away_team"] == team]
            stats[team] = {
                "home_for": self._shrunk_mean(home["goals_home_ft"], self.global_home_goals_),
                "home_against": self._shrunk_mean(home["goals_away_ft"], self.global_away_goals_),
                "away_for": self._shrunk_mean(away["goals_away_ft"], self.global_away_goals_),
                "away_against": self._shrunk_mean(away["goals_home_ft"], self.global_home_goals_),
            }

        self.team_stats_ = stats
        return self

    def predict_match(self, row: pd.Series) -> list[PoissonPrediction]:
        odds = self._extract_odds(row)
        if odds is None:
            return []

        home_team = str(row["home_team"])
        away_team = str(row["away_team"])
        home_lambda, away_lambda = self._expected_goals(home_team, away_team)
        probs = self._match_probabilities(home_lambda, away_lambda)
        event_id = self._event_id(row)
        match_date = self._date_to_iso(row["match_date"])

        predictions: list[PoissonPrediction] = []
        for idx, selection in enumerate(SELECTIONS):
            probability = probs[selection]
            edge_pct = (probability * odds[idx] - 1.0) * 100
            predictions.append(
                PoissonPrediction(
                    event_id=event_id,
                    match_date=match_date,
                    home_team=home_team,
                    away_team=away_team,
                    selection=selection,
                    odds=round(odds[idx], 4),
                    probability=round(probability, 6),
                    edge_pct=round(edge_pct, 4),
                    expected_home_goals=round(home_lambda, 4),
                    expected_away_goals=round(away_lambda, 4),
                    bookmaker_key=self._optional_row_str(row, "source_bookmaker_key"),
                    bookmaker_title=self._optional_row_str(row, "source_bookmaker_title"),
                )
            )
        return predictions

    def generate_signals(
        self,
        history_matches: pd.DataFrame,
        candidate_matches: pd.DataFrame,
        max_signals: int = 10,
    ) -> list[dict[str, Any]]:
        history = self.prepare_matches(history_matches)
        candidates = self.prepare_prediction_matches(candidate_matches)
        if len(history) < self.config.min_train_matches or candidates.empty:
            return []

        self.fit(history)
        generated_at = datetime.now(timezone.utc).isoformat()
        signals: list[dict[str, Any]] = []
        for _, row in candidates.iterrows():
            predictions = [
                item
                for item in self.predict_match(row)
                if item.edge_pct >= self.config.min_edge_pct
                and item.probability >= self.config.min_signal_probability
                and self._odds_allowed(item.odds)
            ]
            for prediction in sorted(predictions, key=lambda item: item.edge_pct, reverse=True)[:1]:
                signals.append(self._prediction_to_signal(prediction, generated_at))
        return sorted(signals, key=lambda item: item["edge_pct"], reverse=True)[:max_signals]

    def recent_quality_report(self, history_matches: pd.DataFrame) -> dict[str, Any]:
        prepared = self.prepare_matches(history_matches)
        if prepared.empty:
            return {"passed": False, "reason": "no_historical_matches", "summary": None}

        max_date = prepared["match_date"].max()
        test_start = max_date - timedelta(days=self.config.recent_window_days - 1)
        train_df = prepared[prepared["match_date"] < test_start]
        test_df = prepared[prepared["match_date"] >= test_start]
        bets = self._fit_and_backtest_window(train_df, test_df)
        summary = self._summarize(train_df, test_df, bets)

        failures: list[str] = []
        if summary["n_bets"] < self.config.min_quality_bets:
            failures.append(f"n_bets {summary['n_bets']} < {self.config.min_quality_bets}")
        if summary["win_rate"] < self.config.min_quality_win_rate:
            failures.append(
                f"win_rate {summary['win_rate']:.2%} < {self.config.min_quality_win_rate:.2%}"
            )
        if summary["roi_pct"] < self.config.min_quality_roi_pct:
            failures.append(
                f"roi_pct {summary['roi_pct']:.2f}% < {self.config.min_quality_roi_pct:.2f}%"
            )

        return {
            "passed": not failures,
            "reason": "; ".join(failures) if failures else "passed",
            "summary": summary,
        }

    def prepare_matches(self, matches: pd.DataFrame) -> pd.DataFrame:
        if matches.empty:
            return matches.copy()
        df = matches.copy()
        df = df.rename(
            columns={
                "Date": "match_date",
                "HomeTeam": "home_team",
                "AwayTeam": "away_team",
                "FTHG": "goals_home_ft",
                "FTAG": "goals_away_ft",
                "FTR": "result_ft",
            }
        )
        required = {
            "match_date",
            "home_team",
            "away_team",
            "goals_home_ft",
            "goals_away_ft",
            "result_ft",
            *self._odds_columns(),
        }
        missing = required - set(df.columns)
        if missing:
            raise KeyError(f"Missing required Poisson columns: {sorted(missing)}")

        df["match_date"] = pd.to_datetime(df["match_date"], dayfirst=True, errors="coerce").dt.date
        numeric_cols = ["goals_home_ft", "goals_away_ft", *self._odds_columns()]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["match_date", "home_team", "away_team", "result_ft", *numeric_cols])
        df = df[df["result_ft"].isin(["H", "D", "A"])]
        df = df[(df[list(self._odds_columns())] > 1.0).all(axis=1)]
        return df.sort_values(["match_date", "home_team", "away_team"]).reset_index(drop=True)

    def prepare_prediction_matches(self, matches: pd.DataFrame) -> pd.DataFrame:
        if matches.empty:
            return matches.copy()
        df = matches.copy()
        df = df.rename(
            columns={"Date": "match_date", "HomeTeam": "home_team", "AwayTeam": "away_team"}
        )
        required = {"match_date", "home_team", "away_team", *self._odds_columns()}
        missing = required - set(df.columns)
        if missing:
            raise KeyError(f"Missing required Poisson prediction columns: {sorted(missing)}")
        df["match_date"] = pd.to_datetime(df["match_date"], dayfirst=True, errors="coerce").dt.date
        for col in self._odds_columns():
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["match_date", "home_team", "away_team", *self._odds_columns()])
        df = df[(df[list(self._odds_columns())] > 1.0).all(axis=1)]
        return df.sort_values(["match_date", "home_team", "away_team"]).reset_index(drop=True)

    def _fit_and_backtest_window(
        self, train_df: pd.DataFrame, test_df: pd.DataFrame
    ) -> list[PoissonBacktestBet]:
        if len(train_df) < self.config.min_train_matches or test_df.empty:
            return []
        self.fit(train_df)
        bets: list[PoissonBacktestBet] = []
        for _, row in test_df.iterrows():
            candidates = [
                item
                for item in self.predict_match(row)
                if item.edge_pct >= self.config.min_edge_pct
                and item.probability >= self.config.min_signal_probability
                and self._odds_allowed(item.odds)
            ]
            for prediction in sorted(candidates, key=lambda item: item.edge_pct, reverse=True)[:1]:
                bets.append(self._settle_prediction(prediction, row))
        return bets

    def _settle_prediction(
        self, prediction: PoissonPrediction, row: pd.Series
    ) -> PoissonBacktestBet:
        actual = self.OUTCOME_TO_SELECTION[str(row["result_ft"])]
        won = prediction.selection == actual
        profit = prediction.odds - 1.0 if won else -1.0
        return PoissonBacktestBet(
            event_id=prediction.event_id,
            match_date=prediction.match_date,
            home_team=prediction.home_team,
            away_team=prediction.away_team,
            selection=prediction.selection,
            odds=prediction.odds,
            probability=prediction.probability,
            edge_pct=prediction.edge_pct,
            result="win" if won else "loss",
            profit_units=round(profit, 4),
        )

    def _summarize(
        self, train_df: pd.DataFrame, test_df: pd.DataFrame, bets: list[PoissonBacktestBet]
    ) -> dict[str, Any]:
        n_bets = len(bets)
        wins = sum(1 for bet in bets if bet.result == "win")
        profit = sum(bet.profit_units for bet in bets)
        return {
            "train_start": (
                self._date_to_iso(train_df["match_date"].min()) if not train_df.empty else None
            ),
            "train_end": (
                self._date_to_iso(train_df["match_date"].max()) if not train_df.empty else None
            ),
            "test_start": (
                self._date_to_iso(test_df["match_date"].min()) if not test_df.empty else None
            ),
            "test_end": (
                self._date_to_iso(test_df["match_date"].max()) if not test_df.empty else None
            ),
            "n_train_matches": int(len(train_df)),
            "n_test_matches": int(len(test_df)),
            "n_bets": n_bets,
            "profit_units": round(profit, 4),
            "roi_pct": round(profit / n_bets * 100, 4) if n_bets else 0.0,
            "win_rate": round(wins / n_bets, 4) if n_bets else 0.0,
            "avg_edge_pct": round(sum(bet.edge_pct for bet in bets) / n_bets, 4) if n_bets else 0.0,
        }

    def _prediction_to_signal(
        self, prediction: PoissonPrediction, generated_at: str
    ) -> dict[str, Any]:
        fair_odds = 1.0 / prediction.probability
        return {
            "signal_id": f"poisson_{prediction.event_id}_{prediction.selection}",
            "strategy_id": "poisson_team_model_v1",
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
            "selection_ru": {"home": "П1", "draw": "X", "away": "П2"}[prediction.selection],
            "entry_odds": prediction.odds,
            "reference_fair_odds": round(fair_odds, 4),
            "edge_pct": prediction.edge_pct,
            "model_probability": prediction.probability,
            "expected_home_goals": prediction.expected_home_goals,
            "expected_away_goals": prediction.expected_away_goals,
            "confidence": "high" if prediction.probability >= 0.6 else "medium",
            "timestamp_utc": generated_at,
            "explain_formatted": (
                f"Poisson xG {prediction.expected_home_goals:.2f}:"
                f"{prediction.expected_away_goals:.2f}; вероятность {prediction.probability:.1%}."
            ),
            "status": "paper",
        }

    def _expected_goals(self, home_team: str, away_team: str) -> tuple[float, float]:
        home = self.team_stats_.get(home_team, {})
        away = self.team_stats_.get(away_team, {})
        home_attack = home.get("home_for", self.global_home_goals_) / self.global_home_goals_
        home_defense = home.get("home_against", self.global_away_goals_) / self.global_away_goals_
        away_attack = away.get("away_for", self.global_away_goals_) / self.global_away_goals_
        away_defense = away.get("away_against", self.global_home_goals_) / self.global_home_goals_
        home_lambda = self.global_home_goals_ * home_attack * away_defense
        away_lambda = self.global_away_goals_ * away_attack * home_defense
        return (min(max(home_lambda, 0.2), 4.5), min(max(away_lambda, 0.2), 4.5))

    def _match_probabilities(
        self, home_lambda: float, away_lambda: float
    ) -> dict[Selection, float]:
        home_win = draw = away_win = 0.0
        for home_goals in range(self.config.max_goals + 1):
            home_prob = self._poisson_pmf(home_goals, home_lambda)
            for away_goals in range(self.config.max_goals + 1):
                probability = home_prob * self._poisson_pmf(away_goals, away_lambda)
                if home_goals > away_goals:
                    home_win += probability
                elif home_goals == away_goals:
                    draw += probability
                else:
                    away_win += probability
        total = home_win + draw + away_win
        return {
            "home": home_win / total,
            "draw": draw / total,
            "away": away_win / total,
        }

    def _shrunk_mean(self, series: pd.Series, prior: float) -> float:
        values = pd.to_numeric(series, errors="coerce").dropna()
        n = len(values)
        if n == 0:
            return prior
        alpha = self.config.shrinkage_matches
        return float((values.sum() + alpha * prior) / (n + alpha))

    def _poisson_pmf(self, goals: int, expected_goals: float) -> float:
        return math.exp(-expected_goals) * expected_goals**goals / math.factorial(goals)

    def _extract_odds(self, row: pd.Series) -> tuple[float, float, float] | None:
        cols = self._odds_columns()
        try:
            odds = (float(row[cols[0]]), float(row[cols[1]]), float(row[cols[2]]))
        except (KeyError, TypeError, ValueError):
            return None
        if any(value <= 1.0 for value in odds):
            return None
        return odds

    def _odds_allowed(self, odds: float) -> bool:
        return self.config.max_signal_odds is None or odds <= self.config.max_signal_odds

    def _odds_columns(self) -> tuple[str, str, str]:
        prefix = self.config.bookmaker_prefix
        return (f"{prefix}H", f"{prefix}D", f"{prefix}A")

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": asdict(self.config),
            "global_home_goals": self.global_home_goals_,
            "global_away_goals": self.global_away_goals_,
            "teams": self.team_stats_,
        }
