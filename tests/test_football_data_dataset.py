from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.ingest.football_data_dataset import FootballDataDatasetBuilder


class FailingLoader:
    def download_season(self, league: str, season: str, output_dir: Path) -> Path:
        raise RuntimeError("network disabled")


def _write_csv(root: Path, league: str, season: str, home: str) -> None:
    path = root / "data" / "raw" / "football_data" / league / season / "data.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A\n"
        f"{league},01/05/2026,{home},Away,2,1,H,2.1,3.4,3.5\n",
        encoding="latin-1",
    )


def test_dataset_builder_combines_cached_leagues_and_seasons(tmp_path: Path) -> None:
    _write_csv(tmp_path, "E0", "2526", "Alpha")
    _write_csv(tmp_path, "SP1", "2526", "Beta")
    builder = FootballDataDatasetBuilder(project_root=tmp_path)

    result = builder.build(leagues=["E0", "SP1"], seasons=["2526"], use_cache=True)
    output = builder.save_combined(result.dataframe, tmp_path / "reports")

    assert len(result.dataframe) == 2
    assert set(result.dataframe["source_league"]) == {"E0", "SP1"}
    assert result.skipped == []
    assert output.exists()
    reloaded = pd.read_csv(output)
    assert len(reloaded) == 2


def test_dataset_builder_reports_missing_cached_file(tmp_path: Path) -> None:
    builder = FootballDataDatasetBuilder(loader=FailingLoader(), project_root=tmp_path)

    result = builder.build(leagues=["E0"], seasons=["2526"], use_cache=True)

    assert result.dataframe.empty
    assert result.skipped
