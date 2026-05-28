from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.models.model_benchmark import BenchmarkConfig, ProbabilityBenchmark


def _benchmark_matches(n: int = 150) -> pd.DataFrame:
    start = date(2025, 1, 1)
    teams = ["Alpha", "Beta", "Gamma", "Delta"]
    rows = []
    for idx in range(n):
        home = teams[idx % len(teams)]
        away = teams[(idx + 1) % len(teams)]
        home_win = home == "Alpha" or idx % 4 == 0
        draw = idx % 7 == 0
        if draw:
            fthg, ftag, ftr = 1, 1, "D"
        elif home_win:
            fthg, ftag, ftr = 2, 0, "H"
        else:
            fthg, ftag, ftr = 0, 2, "A"
        rows.append(
            {
                "Date": (start + timedelta(days=idx)).strftime("%d/%m/%Y"),
                "HomeTeam": home,
                "AwayTeam": away,
                "FTHG": fthg,
                "FTAG": ftag,
                "FTR": ftr,
                "B365H": 1.80 if home_win else 2.80,
                "B365D": 3.40,
                "B365A": 4.20 if home_win else 1.90,
            }
        )
    return pd.DataFrame(rows)


def test_probability_benchmark_builds_metrics_without_lookahead() -> None:
    benchmark = ProbabilityBenchmark(
        BenchmarkConfig(
            min_train_matches=30,
            test_window_days=30,
            max_windows=2,
            dixon_coles_max_iterations=20,
        )
    )

    report = benchmark.run(_benchmark_matches())

    assert len(report.windows) == 2
    assert {item.model_name for item in report.overall_metrics} == {
        "market_implied",
        "historical_calibration",
        "poisson_team_strength",
        "dixon_coles_time_decay",
    }
    assert all(item.n_predictions > 0 for item in report.overall_metrics)
    assert report.windows[0].train_end < report.windows[0].test_start


def test_probability_benchmark_writes_json_and_markdown(tmp_path) -> None:
    benchmark = ProbabilityBenchmark(
        BenchmarkConfig(
            min_train_matches=30,
            test_window_days=30,
            max_windows=1,
            dixon_coles_max_iterations=20,
        )
    )
    report = benchmark.run(_benchmark_matches())

    json_path, md_path = benchmark.write_report(report, tmp_path)

    assert json_path.exists()
    assert md_path.exists()
    assert "Model Probability Benchmark" in md_path.read_text(encoding="utf-8")
