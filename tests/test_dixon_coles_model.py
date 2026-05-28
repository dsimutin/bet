from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.models.dixon_coles_model import DixonColesConfig, DixonColesModel


def _matches(n: int = 90) -> pd.DataFrame:
    start = date(2025, 1, 1)
    teams = ["Alpha", "Beta", "Gamma", "Delta"]
    rows = []
    for idx in range(n):
        home = teams[idx % len(teams)]
        away = teams[(idx + 1) % len(teams)]
        if home == "Alpha":
            fthg, ftag, ftr = 3, 0, "H"
        elif away == "Alpha":
            fthg, ftag, ftr = 0, 2, "A"
        elif idx % 5 == 0:
            fthg, ftag, ftr = 1, 1, "D"
        else:
            fthg, ftag, ftr = 2, 1, "H"
        rows.append(
            {
                "Date": (start + timedelta(days=idx)).strftime("%d/%m/%Y"),
                "HomeTeam": home,
                "AwayTeam": away,
                "FTHG": fthg,
                "FTAG": ftag,
                "FTR": ftr,
            }
        )
    return pd.DataFrame(rows)


def test_dixon_coles_predicts_1x2_probabilities() -> None:
    model = DixonColesModel(
        DixonColesConfig(
            min_train_matches=30,
            max_iterations=50,
        )
    )
    prepared = model.prepare_matches(_matches())
    model.fit(prepared)

    predictions = model.predict_match(prepared.iloc[-1])

    assert len(predictions) == 3
    assert abs(sum(item.probability for item in predictions) - 1.0) < 1e-4
    assert {item.selection for item in predictions} == {"home", "draw", "away"}
    assert all(item.expected_home_goals > 0 for item in predictions)


def test_dixon_coles_uses_time_decay_without_lookahead() -> None:
    model = DixonColesModel(
        DixonColesConfig(
            min_train_matches=30,
            time_decay=0.01,
            max_iterations=30,
        )
    )
    prepared = model.prepare_matches(_matches())
    train = prepared[prepared["match_date"] < prepared["match_date"].max()]

    model.fit(train)
    predictions = model.predict_match(prepared.iloc[-1])

    assert predictions
    assert prepared.iloc[-1]["match_date"] > train["match_date"].max()
