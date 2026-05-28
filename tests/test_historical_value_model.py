from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.models.historical_value_model import HistoricalValueModel, HistoricalValueModelConfig


def _synthetic_matches(n: int = 130) -> pd.DataFrame:
    start = date(2025, 1, 1)
    rows = []
    for idx in range(n):
        is_home_pattern = idx % 3 != 0
        rows.append(
            {
                "Date": (start + timedelta(days=idx)).strftime("%d/%m/%Y"),
                "HomeTeam": f"Home {idx}",
                "AwayTeam": f"Away {idx}",
                "FTR": "H" if is_home_pattern else "A",
                "B365H": 2.10 if is_home_pattern else 2.40,
                "B365D": 3.30,
                "B365A": 3.60 if is_home_pattern else 2.10,
            }
        )
    return pd.DataFrame(rows)


def test_historical_model_builds_recent_validation_report() -> None:
    config = HistoricalValueModelConfig(
        min_train_matches=30,
        min_edge_pct=1.0,
        test_window_days=30,
        recent_windows_days=(7, 30),
        smoothing_alpha=5.0,
    )
    model = HistoricalValueModel(config)

    report = model.build_report(_synthetic_matches())

    assert len(report.recent_summaries) == 2
    assert report.recent_summaries[0].window_name == "last_7_days"
    assert report.fold_summaries
    assert report.calibration


def test_historical_model_never_uses_recent_window_for_training() -> None:
    config = HistoricalValueModelConfig(
        min_train_matches=30,
        min_edge_pct=1.0,
        recent_windows_days=(30,),
        smoothing_alpha=5.0,
    )
    model = HistoricalValueModel(config)

    summary = model.recent_validation(_synthetic_matches())[0]

    assert summary.train_end < summary.test_start
    assert summary.n_train_matches > 0
    assert summary.n_test_matches == 30


def test_historical_model_writes_json_and_markdown_reports(tmp_path) -> None:
    config = HistoricalValueModelConfig(min_train_matches=30, min_edge_pct=1.0)
    model = HistoricalValueModel(config)
    report = model.build_report(_synthetic_matches())

    json_path, md_path = model.write_report(report, tmp_path)

    assert json_path.exists()
    assert md_path.exists()
    assert "Historical Value Model Report" in md_path.read_text(encoding="utf-8")


def test_historical_model_generates_telegram_ready_signals(tmp_path) -> None:
    config = HistoricalValueModelConfig(
        min_train_matches=30,
        min_edge_pct=1.0,
        min_signal_probability=0.45,
        require_recent_quality=False,
        smoothing_alpha=5.0,
    )
    model = HistoricalValueModel(config)
    history = _synthetic_matches()
    upcoming = pd.DataFrame(
        [
            {
                "Date": "15/05/2025",
                "HomeTeam": "Strong Home",
                "AwayTeam": "Weak Away",
                "B365H": 2.20,
                "B365D": 3.40,
                "B365A": 3.50,
            }
        ]
    )

    signals = model.generate_signals(history, upcoming)
    output_path = model.save_signals(signals, tmp_path)

    assert signals
    assert signals[0]["status"] == "paper"
    assert signals[0]["model_probability"] >= 0.45
    assert signals[0]["edge_pct"] >= 1.0
    assert {"strategy_id", "home_team", "away_team", "entry_odds", "reference_fair_odds"} <= set(
        signals[0]
    )
    assert output_path.exists()


def test_historical_model_quality_gate_can_block_signals() -> None:
    config = HistoricalValueModelConfig(
        min_train_matches=30,
        min_edge_pct=1.0,
        min_signal_probability=0.45,
        require_recent_quality=True,
        min_quality_bets=999,
        min_quality_win_rate=0.95,
        min_quality_roi_pct=100.0,
        smoothing_alpha=5.0,
    )
    model = HistoricalValueModel(config)
    upcoming = pd.DataFrame(
        [
            {
                "Date": "15/05/2025",
                "HomeTeam": "Strong Home",
                "AwayTeam": "Weak Away",
                "B365H": 2.20,
                "B365D": 3.40,
                "B365A": 3.50,
            }
        ]
    )

    gate = model.quality_gate_report(_synthetic_matches())
    signals = model.generate_signals(_synthetic_matches(), upcoming)

    assert gate["passed"] is False
    assert signals == []


def test_historical_model_respects_max_signal_odds() -> None:
    config = HistoricalValueModelConfig(
        min_train_matches=30,
        min_edge_pct=1.0,
        min_signal_probability=0.45,
        max_signal_odds=1.85,
        require_recent_quality=False,
        smoothing_alpha=5.0,
    )
    model = HistoricalValueModel(config)
    upcoming = pd.DataFrame(
        [
            {
                "Date": "15/05/2025",
                "HomeTeam": "Strong Home",
                "AwayTeam": "Weak Away",
                "B365H": 2.20,
                "B365D": 3.40,
                "B365A": 3.50,
            }
        ]
    )

    signals = model.generate_signals(_synthetic_matches(), upcoming)

    assert signals == []


def test_historical_backtest_uses_same_probability_floor_as_live_signals() -> None:
    config = HistoricalValueModelConfig(
        min_train_matches=30,
        min_edge_pct=1.0,
        min_signal_probability=1.0,
        require_recent_quality=False,
        smoothing_alpha=5.0,
    )
    model = HistoricalValueModel(config)

    bets, summaries = model.walk_forward_backtest(_synthetic_matches())

    assert bets == []
    assert summaries
    assert all(summary.n_bets == 0 for summary in summaries)


def test_historical_model_keeps_live_bookmaker_metadata_in_signal() -> None:
    config = HistoricalValueModelConfig(
        min_train_matches=30,
        min_edge_pct=1.0,
        min_signal_probability=0.45,
        require_recent_quality=False,
        smoothing_alpha=5.0,
    )
    model = HistoricalValueModel(config)
    upcoming = pd.DataFrame(
        [
            {
                "Date": "15/05/2025",
                "HomeTeam": "Strong Home",
                "AwayTeam": "Weak Away",
                "B365H": 2.20,
                "B365D": 3.40,
                "B365A": 3.50,
                "source_event_id": "live_event_123",
                "source_bookmaker_key": "bet365",
                "source_bookmaker_title": "Bet365",
            }
        ]
    )

    signals = model.generate_signals(_synthetic_matches(), upcoming)

    assert signals
    assert signals[0]["event_id"] == "live_event_123"
    assert signals[0]["bookmaker"] == "bet365"
    assert signals[0]["bookmaker_title"] == "Bet365"
