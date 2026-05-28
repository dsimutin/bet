from __future__ import annotations

import argparse
from pathlib import Path

from src.models.run_signal_pipeline import (
    build_benchmark_command,
    build_parser,
    build_settle_command,
    build_signal_command,
    build_train_command,
)


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        output_dir=Path("data/reports"),
        download_football_data=True,
        input=None,
        leagues="E0,SP1",
        seasons="2526",
        no_cache=False,
        bookmaker_prefix="B365",
        min_train_matches=120,
        test_window_days=30,
        max_windows=2,
        dixon_coles_max_iterations=20,
        upcoming_input=Path("data/manual/upcoming.csv"),
        free_source_inbox=None,
        live_odds=False,
        live_sport_keys="soccer_epl",
        odds_regions="eu,uk",
        preferred_bookmakers="bet365,pinnacle",
        no_bookmaker_fallback=False,
        ledger_path=Path("data/core/paper_signal_ledger.json"),
        settle_ledger=True,
        settle_results_input=None,
        max_signals=10,
        production_dixon_coles=False,
        production_model_dir=Path("data/models"),
        production_league="EPL",
        train_production_model=True,
        production_trainer_min_matches=20,
        production_trainer_max_iterations=120,
        production_trainer_max_brier_score=0.60,
        production_trainer_max_log_loss=1.20,
        production_train_openfootball=False,
        openfootball_leagues="EPL",
        openfootball_seasons="2023-24,2024-25",
        telegram_payload=True,
        send_telegram=False,
        allow_duplicate_signals=False,
        min_edge_pct=2.0,
        min_signal_probability=0.45,
        max_entry_odds=None,
        high_hit_mode=True,
        auto_high_hit_profile=True,
        require_auto_high_hit_profile=True,
        target_hit_rate=0.55,
        min_profile_bets=30,
        disable_quality_gate=False,
        min_quality_win_rate=0.45,
        min_quality_roi_pct=0.0,
        consensus=True,
        require_ledger_quality=True,
        min_ledger_settled=20,
        min_ledger_win_rate=0.55,
        min_ledger_roi_pct=0.0,
        model_quality_scope="latest_window",
        model_quality_candidates=None,
        model_quality_mode="all",
        min_benchmark_predictions=100,
        min_brier_improvement=0.0,
        min_log_loss_improvement=0.0,
    )


def test_pipeline_builds_benchmark_command_with_downloaded_history() -> None:
    cmd = build_benchmark_command(_args())

    assert "src.models.run_model_benchmark" in cmd
    assert "--download-football-data" in cmd
    assert cmd[cmd.index("--leagues") + 1] == "E0,SP1"
    assert cmd[cmd.index("--max-windows") + 1] == "2"


def test_pipeline_builds_gated_signal_command() -> None:
    cmd = build_signal_command(_args())

    assert "src.models.run_historical_value_model" in cmd
    assert "--require-model-quality" in cmd
    assert (
        cmd[cmd.index("--benchmark-report") + 1] == "data/reports/model_probability_benchmark.json"
    )
    assert cmd[cmd.index("--model-quality-scope") + 1] == "latest_window"
    assert cmd[cmd.index("--model-quality-mode") + 1] == "all"
    assert "--require-ledger-quality" in cmd
    assert "--consensus" in cmd
    assert "--telegram-payload" in cmd
    assert "--auto-high-hit-profile" in cmd
    assert "--require-auto-high-hit-profile" in cmd
    assert cmd[cmd.index("--target-hit-rate") + 1] == "0.55"
    assert cmd[cmd.index("--upcoming-input") + 1] == "data/manual/upcoming.csv"


def test_pipeline_passes_free_source_inbox() -> None:
    args = _args()
    args.free_source_inbox = Path("data/staging/free_sources")

    cmd = build_signal_command(args)

    assert cmd[cmd.index("--free-source-inbox") + 1] == "data/staging/free_sources"


def test_pipeline_builds_settle_command_from_downloaded_history() -> None:
    cmd = build_settle_command(_args())

    assert cmd is not None
    assert "src.models.settle_signal_ledger" in cmd
    assert cmd[cmd.index("--ledger-path") + 1] == "data/core/paper_signal_ledger.json"
    assert cmd[cmd.index("--results-input") + 1] == "data/reports/football_data_combined.csv"
    assert (
        cmd[cmd.index("--report-path") + 1] == "data/reports/signal_ledger_settlement_report.json"
    )


def test_pipeline_can_skip_settlement() -> None:
    args = _args()
    args.settle_ledger = False

    assert build_settle_command(args) is None


def test_pipeline_can_enable_production_dixon_coles_signals() -> None:
    args = _args()
    args.production_dixon_coles = True

    cmd = build_signal_command(args)

    assert "--production-dixon-coles" in cmd
    assert cmd[cmd.index("--production-model-dir") + 1] == "data/models"
    assert cmd[cmd.index("--production-league") + 1] == "EPL"
    assert "--consensus" not in cmd


def test_pipeline_builds_train_command_for_production_dixon_coles() -> None:
    args = _args()
    args.production_dixon_coles = True

    cmd = build_train_command(args)

    assert cmd is not None
    assert "src.models.run_daily_trainer" in cmd
    assert cmd[cmd.index("--league") + 1] == "EPL"
    assert cmd[cmd.index("--input") + 1] == "data/reports/football_data_combined.csv"
    assert cmd[cmd.index("--model-dir") + 1] == "data/models"
    assert cmd[cmd.index("--max-brier-score") + 1] == "0.6"
    assert cmd[cmd.index("--max-log-loss") + 1] == "1.2"


def test_pipeline_can_train_production_model_from_openfootball() -> None:
    args = _args()
    args.production_dixon_coles = True
    args.production_train_openfootball = True

    cmd = build_train_command(args)

    assert cmd is not None
    assert "--download-openfootball" in cmd
    assert "--input" not in cmd
    assert cmd[cmd.index("--openfootball-leagues") + 1] == "EPL"
    assert cmd[cmd.index("--openfootball-seasons") + 1] == "2023-24,2024-25"


def test_pipeline_can_skip_production_training() -> None:
    args = _args()
    args.production_dixon_coles = True
    args.train_production_model = False

    assert build_train_command(args) is None


def test_pipeline_parser_allows_research_mode_switches() -> None:
    args = build_parser().parse_args(
        [
            "--no-download-football-data",
            "--no-high-hit-mode",
            "--no-consensus",
            "--no-require-ledger-quality",
            "--no-settle-ledger",
            "--no-auto-high-hit-profile",
            "--no-require-auto-high-hit-profile",
            "--no-train-production-model",
        ]
    )

    assert args.download_football_data is False
    assert args.high_hit_mode is False
    assert args.consensus is False
    assert args.require_ledger_quality is False
    assert args.settle_ledger is False
    assert args.auto_high_hit_profile is False
    assert args.require_auto_high_hit_profile is False
    assert args.train_production_model is False


def test_pipeline_parser_defaults_to_multi_season_history() -> None:
    args = build_parser().parse_args([])

    assert args.seasons == "2122,2223,2324,2425,2526"
