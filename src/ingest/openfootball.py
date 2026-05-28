"""OpenFootball GitHub raw loader for completed football results.

OpenFootball has no bookmaker odds, so this loader is meant for model training
and result settlement. It normalizes JSON seasons into the same match columns
used by football-data.co.uk: Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

OPENFOOTBALL_RAW_URL = (
    "https://raw.githubusercontent.com/openfootball/football.json/master/{season}/{code}.json"
)

LEAGUE_CODES: dict[str, str] = {
    "EPL": "en.1",
    "E0": "en.1",
    "BUNDESLIGA": "de.1",
    "D1": "de.1",
    "LALIGA": "es.1",
    "SP1": "es.1",
    "SERIEA": "it.1",
    "I1": "it.1",
    "LIGUE1": "fr.1",
    "F1": "fr.1",
    "PORTUGAL": "pt.1",
    "NETHERLANDS": "nl.1",
}


@dataclass(frozen=True)
class OpenFootballDatasetResult:
    dataframe: pd.DataFrame
    downloaded_files: list[Path]
    skipped: list[str]


class OpenFootballLoader:
    """Download/cache OpenFootball seasons and expose football-data-style rows."""

    def __init__(
        self,
        project_root: Path | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.project_root = Path(project_root or ".")
        self.timeout_seconds = timeout_seconds

    def build(
        self,
        leagues: Iterable[str],
        seasons: Iterable[str],
        use_cache: bool = True,
    ) -> OpenFootballDatasetResult:
        frames: list[pd.DataFrame] = []
        files: list[Path] = []
        skipped: list[str] = []

        for league in leagues:
            for season in seasons:
                try:
                    json_path = self.fetch_season(league, season, use_cache=use_cache)
                    frame = self.parse_file(json_path, league=league, season=season)
                except Exception as exc:
                    skipped.append(f"{league}/{season}: {exc}")
                    continue

                if frame.empty:
                    skipped.append(f"{league}/{season}: no_completed_matches")
                    continue

                frames.append(frame)
                files.append(json_path)

        combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
        if not combined.empty:
            combined = self._sort_combined(combined)

        return OpenFootballDatasetResult(
            dataframe=combined,
            downloaded_files=files,
            skipped=skipped,
        )

    def fetch_season(self, league: str, season: str, use_cache: bool = True) -> Path:
        code = self._league_code(league)
        json_path = self._json_path(league, season, code)
        if use_cache and json_path.exists():
            return json_path

        json_path.parent.mkdir(parents=True, exist_ok=True)
        url = OPENFOOTBALL_RAW_URL.format(season=season, code=code)
        request = Request(url, headers={"User-Agent": "bet-openfootball-loader/1.0"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read()
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} for {url}") from exc
        except URLError as exc:
            raise RuntimeError(f"network error for {url}: {exc.reason}") from exc

        json_path.write_bytes(payload)
        return json_path

    def parse_file(self, path: Path, league: str, season: str) -> pd.DataFrame:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows: list[dict[str, Any]] = []
        for match in self._iter_matches(payload):
            parsed = self._parse_match(match, league=league, season=season)
            if parsed is not None:
                rows.append(parsed)

        return pd.DataFrame(rows)

    def save_combined(
        self,
        dataframe: pd.DataFrame,
        output_dir: Path,
        filename: str = "openfootball_combined.csv",
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename
        dataframe.to_csv(output_path, index=False)
        return output_path

    def _json_path(self, league: str, season: str, code: str) -> Path:
        safe_league = self._normalize_league_key(league)
        return (
            self.project_root
            / "data"
            / "raw"
            / "openfootball"
            / safe_league
            / season
            / f"{code}.json"
        )

    def _parse_match(
        self, match: dict[str, Any], league: str, season: str
    ) -> dict[str, Any] | None:
        home_team = self._team_name(match.get("team1") or match.get("home_team"))
        away_team = self._team_name(match.get("team2") or match.get("away_team"))
        match_date = match.get("date")
        score = self._full_time_score(match.get("score"))
        parsed_date = pd.to_datetime(match_date, errors="coerce")
        if not home_team or not away_team or pd.isna(parsed_date) or score is None:
            return None

        home_goals, away_goals = score
        return {
            "Div": self._normalize_league_key(league),
            "Date": parsed_date.strftime("%d/%m/%Y"),
            "HomeTeam": home_team,
            "AwayTeam": away_team,
            "FTHG": home_goals,
            "FTAG": away_goals,
            "FTR": "H" if home_goals > away_goals else "A" if away_goals > home_goals else "D",
            "source_league": self._normalize_league_key(league),
            "source_season": season,
            "source_provider": "openfootball",
        }

    def _iter_matches(self, payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
        if isinstance(payload.get("matches"), list):
            yield from payload["matches"]
            return

        rounds = payload.get("rounds")
        if isinstance(rounds, list):
            for round_item in rounds:
                matches = round_item.get("matches", [])
                if isinstance(matches, list):
                    yield from matches

    def _full_time_score(self, score: Any) -> tuple[int, int] | None:
        if not isinstance(score, dict):
            return None
        full_time = score.get("ft") or score.get("fullTime") or score.get("full_time")
        if not isinstance(full_time, list | tuple) or len(full_time) != 2:
            return None
        try:
            return int(full_time[0]), int(full_time[1])
        except (TypeError, ValueError):
            return None

    def _team_name(self, value: Any) -> str | None:
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, dict):
            for key in ("name", "club", "title"):
                name = value.get(key)
                if isinstance(name, str) and name.strip():
                    return name.strip()
        return None

    def _league_code(self, league: str) -> str:
        key = self._normalize_league_key(league)
        try:
            return LEAGUE_CODES[key]
        except KeyError as exc:
            known = ", ".join(sorted(LEAGUE_CODES))
            raise ValueError(f"Unsupported OpenFootball league={league!r}. Known: {known}") from exc

    def _normalize_league_key(self, league: str) -> str:
        return league.strip().replace(" ", "").replace("-", "").upper()

    def _sort_combined(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        result = dataframe.copy()
        result["_sort_date"] = pd.to_datetime(result["Date"], dayfirst=True, errors="coerce")
        result = result.sort_values(["_sort_date", "source_league", "HomeTeam", "AwayTeam"])
        return result.drop(columns=["_sort_date"]).reset_index(drop=True)
