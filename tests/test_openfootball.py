from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.ingest.openfootball import OpenFootballLoader
from src.models.dixon_coles import DixonColesModel


def _write_openfootball_json(root: Path, league: str, season: str) -> Path:
    path = root / "data" / "raw" / "openfootball" / league / season / "en.1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "name": "English Premier League 2024/25",
                "matches": [
                    {
                        "date": "2024-08-16",
                        "team1": "Manchester United",
                        "team2": "Fulham",
                        "score": {"ft": [1, 0]},
                    },
                    {
                        "date": "2024-08-17",
                        "team1": {"name": "Arsenal"},
                        "team2": {"name": "Wolverhampton Wanderers"},
                        "score": {"ft": [2, 0]},
                    },
                    {
                        "date": "2025-05-25",
                        "team1": "Incomplete",
                        "team2": "Fixture",
                        "score": {},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_openfootball_loader_reads_cached_json_as_training_rows(tmp_path: Path) -> None:
    _write_openfootball_json(tmp_path, "EPL", "2024-25")
    loader = OpenFootballLoader(project_root=tmp_path)

    result = loader.build(leagues=["EPL"], seasons=["2024-25"], use_cache=True)

    assert result.skipped == []
    assert len(result.dataframe) == 2
    assert list(result.dataframe.columns) == [
        "Div",
        "Date",
        "HomeTeam",
        "AwayTeam",
        "FTHG",
        "FTAG",
        "FTR",
        "source_league",
        "source_season",
        "source_provider",
    ]
    assert result.dataframe.iloc[0]["Date"] == "16/08/2024"
    assert result.dataframe.iloc[0]["FTR"] == "H"
    assert set(result.dataframe["source_provider"]) == {"openfootball"}


def test_openfootball_loader_supports_rounds_shape(tmp_path: Path) -> None:
    path = tmp_path / "season.json"
    path.write_text(
        json.dumps(
            {
                "rounds": [
                    {
                        "name": "Matchday 1",
                        "matches": [
                            {
                                "date": "2024-08-18",
                                "team1": "Chelsea",
                                "team2": "Manchester City",
                                "score": {"ft": [0, 2]},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    loader = OpenFootballLoader(project_root=tmp_path)

    frame = loader.parse_file(path, league="EPL", season="2024-25")

    assert frame.iloc[0]["AwayTeam"] == "Manchester City"
    assert frame.iloc[0]["FTR"] == "A"


def test_openfootball_rows_are_compatible_with_dixon_coles_prepare(tmp_path: Path) -> None:
    _write_openfootball_json(tmp_path, "EPL", "2024-25")
    loader = OpenFootballLoader(project_root=tmp_path)

    frame = loader.build(leagues=["EPL"], seasons=["2024-25"], use_cache=True).dataframe
    prepared = DixonColesModel.prepare_matches(frame)

    assert len(prepared) == 2
    assert set(prepared.columns).issuperset(
        {"match_date", "home_team", "away_team", "home_goals", "away_goals"}
    )
    assert pd.api.types.is_integer_dtype(prepared["home_goals"])
