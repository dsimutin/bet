"""Build multi-league football-data.co.uk datasets for model validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from src.ingest.football_data_co_uk import FootballDataLoader


@dataclass(frozen=True)
class FootballDataDatasetResult:
    dataframe: pd.DataFrame
    downloaded_files: list[Path]
    skipped: list[str]


class FootballDataDatasetBuilder:
    """Download/read and combine football-data.co.uk CSVs across leagues/seasons."""

    def __init__(
        self, loader: FootballDataLoader | None = None, project_root: Path | None = None
    ) -> None:
        self.loader = loader or FootballDataLoader()
        self.project_root = Path(project_root or ".")

    def build(
        self,
        leagues: Iterable[str],
        seasons: Iterable[str],
        use_cache: bool = True,
    ) -> FootballDataDatasetResult:
        frames: list[pd.DataFrame] = []
        files: list[Path] = []
        skipped: list[str] = []

        for league in leagues:
            for season in seasons:
                csv_path = self._csv_path(league, season)
                try:
                    if not use_cache or not csv_path.exists():
                        csv_path = self.loader.download_season(league, season, self.project_root)
                    frame = pd.read_csv(csv_path, encoding="latin-1")
                except Exception as exc:
                    skipped.append(f"{league}/{season}: {exc}")
                    continue

                if frame.empty or not {"Date", "HomeTeam", "AwayTeam", "FTR"}.issubset(
                    frame.columns
                ):
                    skipped.append(f"{league}/{season}: empty_or_invalid")
                    continue

                frame = frame.copy()
                frame["source_league"] = league
                frame["source_season"] = season
                frames.append(frame)
                files.append(csv_path)

        combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
        if not combined.empty:
            combined = self._sort_combined(combined)

        return FootballDataDatasetResult(
            dataframe=combined,
            downloaded_files=files,
            skipped=skipped,
        )

    def save_combined(
        self,
        dataframe: pd.DataFrame,
        output_dir: Path,
        filename: str = "football_data_combined.csv",
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename
        dataframe.to_csv(output_path, index=False)
        return output_path

    def _csv_path(self, league: str, season: str) -> Path:
        return self.project_root / "data" / "raw" / "football_data" / league / season / "data.csv"

    def _sort_combined(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        result = dataframe.copy()
        parsed_dates = pd.to_datetime(result["Date"], dayfirst=True, errors="coerce")
        result["_sort_date"] = parsed_dates
        result = result.sort_values(["_sort_date", "source_league", "HomeTeam", "AwayTeam"])
        return result.drop(columns=["_sort_date"]).reset_index(drop=True)
