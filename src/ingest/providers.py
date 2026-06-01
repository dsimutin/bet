"""Unified data provider interface for all ingestion sources.

All providers return a normalised pandas DataFrame with at minimum:
    Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR (where available),
    and optional odds columns (B365H, B365D, B365A).

Usage:
    from src.ingest.providers import get_provider
    provider = get_provider("openfootball")
    df = provider.fetch(leagues=["EPL"], seasons=["2023-24"])

Flashscore note:
    FlashscoreProvider is DISABLED by default.
    Direct scraping of flashscorekz.com violates the site's Terms of Service
    and robots.txt. Use licensed APIs or manually-supplied CSV exports instead.
    Set FLASHSCORE_PROVIDER_MODE=disabled (default) to make this explicit.
    See README.md § Data Sources for legal alternatives.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import pandas as pd

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base interface
# ---------------------------------------------------------------------------


class BaseDataProvider(ABC):
    """Abstract base for all data providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""

    @property
    def enabled(self) -> bool:
        """Whether this provider is configured and allowed to run."""
        return True

    @abstractmethod
    def fetch(self, **kwargs: Any) -> pd.DataFrame:
        """Fetch data and return a normalised DataFrame.

        Raises:
            ProviderDisabledError: if provider is disabled.
            ProviderConfigError: if required config/env vars are missing.
        """

    def _require_env(self, key: str) -> str:
        value = os.environ.get(key, "")
        if not value:
            raise ProviderConfigError(
                f"{self.name}: required environment variable {key!r} is not set."
            )
        return value


class ProviderDisabledError(RuntimeError):
    """Raised when a disabled provider is called."""


class ProviderConfigError(RuntimeError):
    """Raised when required provider configuration is missing."""


# ---------------------------------------------------------------------------
# OpenFootball provider
# ---------------------------------------------------------------------------


class OpenFootballProvider(BaseDataProvider):
    """Load historical match results from the OpenFootball GitHub project."""

    @property
    def name(self) -> str:
        return "openfootball"

    def fetch(self, **kwargs: Any) -> pd.DataFrame:
        from src.ingest.openfootball import OpenFootballLoader

        leagues = kwargs.get("leagues", [])
        seasons = kwargs.get("seasons", [])
        use_cache = kwargs.get("use_cache", True)

        loader = OpenFootballLoader()
        result = loader.build(leagues=leagues, seasons=seasons, use_cache=use_cache)
        if result.dataframe.empty:
            _log.warning(
                "[%s] No data returned for leagues=%s seasons=%s", self.name, leagues, seasons
            )
        else:
            _log.info("[%s] %d matches for leagues=%s", self.name, len(result.dataframe), leagues)
        return result.dataframe


# ---------------------------------------------------------------------------
# football-data.co.uk provider
# ---------------------------------------------------------------------------


class FootballDataCoUkProvider(BaseDataProvider):
    """Load historical match + odds data from football-data.co.uk CSV files."""

    @property
    def name(self) -> str:
        return "football-data-co-uk"

    def fetch(self, **kwargs: Any) -> pd.DataFrame:
        from src.ingest.football_data_co_uk import FootballDataLoader

        leagues = kwargs.get("leagues", [])
        seasons = kwargs.get("seasons", [])
        staging_dir = Path(kwargs.get("staging_dir", os.environ.get("STAGING_DIR", "data/staging")))
        loader = FootballDataLoader()
        frames: list[pd.DataFrame] = []
        for league in leagues:
            for season in seasons:
                try:
                    csv_path = loader.download_season(
                        league=league, season=season, output_dir=staging_dir
                    )
                    df = loader.load_and_parse(csv_path)
                    frames.append(df)
                    _log.info("[%s] %s %s: %d rows", self.name, league, season, len(df))
                except Exception as exc:
                    _log.warning("[%s] %s %s: %s", self.name, league, season, exc)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# The Odds API provider
# ---------------------------------------------------------------------------


class OddsApiProvider(BaseDataProvider):
    """Fetch live upcoming-match odds from The Odds API."""

    @property
    def name(self) -> str:
        return "odds-api"

    @property
    def enabled(self) -> bool:
        return bool(os.environ.get("THE_ODDS_API_KEY", ""))

    def fetch(self, **kwargs: Any) -> pd.DataFrame:
        api_key = self._require_env("THE_ODDS_API_KEY")
        from src.ingest.odds_api import OddsAPIClient
        from src.ingest.live_odds_adapter import LiveOddsFootballDataAdapter

        sport_keys = kwargs.get("sport_keys", [])
        regions = kwargs.get("regions", ["eu", "uk"])
        markets = kwargs.get("markets", ["h2h"])
        bookmaker_prefix = kwargs.get("bookmaker_prefix", "B365")

        client = OddsAPIClient(api_key=api_key)
        adapter = LiveOddsFootballDataAdapter(bookmaker_prefix=bookmaker_prefix)

        frames: list[pd.DataFrame] = []
        for sport_key in sport_keys:
            try:
                events = client.get_odds(sport=sport_key, regions=regions, markets=markets)
                if not events:
                    _log.info("[%s] %s: 0 events", self.name, sport_key)
                    continue
                result = adapter.convert(events)
                _log.info(
                    "[%s] %s: %d rows converted", self.name, sport_key, result.converted_events
                )
                frames.append(result.dataframe)
            except Exception as exc:
                _log.warning("[%s] %s: %s", self.name, sport_key, exc)

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Telegram live feed provider
# ---------------------------------------------------------------------------


class TelegramProvider(BaseDataProvider):
    """Read accumulated live odds messages from the Telegram collector JSONL."""

    @property
    def name(self) -> str:
        return "telegram-live"

    @property
    def enabled(self) -> bool:
        staging = Path(os.environ.get("STAGING_DIR", "data/staging"))
        return (staging / "free_sources" / "telegram_live.jsonl").exists()

    def fetch(self, **kwargs: Any) -> pd.DataFrame:
        from src.ingest.free_source_inbox import FreeSourceMessageParser

        staging = Path(kwargs.get("staging_dir", os.environ.get("STAGING_DIR", "data/staging")))
        path = staging / "free_sources" / "telegram_live.jsonl"
        if not path.exists():
            _log.warning("[%s] No telegram_live.jsonl found at %s", self.name, path)
            return pd.DataFrame()

        parser = FreeSourceMessageParser()
        rows: list[dict] = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    import json

                    record = json.loads(line)
                    parsed = parser._parse_block(
                        record.get("text", ""), fallback_date=record.get("date", "")
                    )
                    if parsed:
                        rows.append(parsed)
                except Exception:
                    pass

        _log.info("[%s] %d parsed rows from telegram_live.jsonl", self.name, len(rows))
        return pd.DataFrame(rows) if rows else pd.DataFrame()


# ---------------------------------------------------------------------------
# Manual CSV provider
# ---------------------------------------------------------------------------


class ManualCsvProvider(BaseDataProvider):
    """Load a manually supplied CSV export (e.g. from Flashscore manual export)."""

    @property
    def name(self) -> str:
        return "manual-csv"

    def fetch(self, **kwargs: Any) -> pd.DataFrame:
        path = kwargs.get("path", "")
        csv_path = Path(path)
        if not csv_path.exists():
            raise FileNotFoundError(f"[{self.name}] CSV not found: {csv_path}")

        encoding = kwargs.get("encoding", "utf-8")
        try:
            df = pd.read_csv(csv_path, encoding=encoding)
        except UnicodeDecodeError:
            df = pd.read_csv(csv_path, encoding="latin-1")

        _log.info("[%s] Loaded %d rows from %s", self.name, len(df), csv_path)
        return df


# ---------------------------------------------------------------------------
# Flashscore provider — DISABLED
# ---------------------------------------------------------------------------


class FlashscoreProvider(BaseDataProvider):
    """Flashscore data provider — DISABLED by policy.

    Direct scraping of flashscorekz.com (or any Flashscore regional site) is
    prohibited by the site's Terms of Service and robots.txt. This provider
    exists as a named stub so configuration can explicitly reference it and
    receive a clear error instead of a silent no-op.

    How to get Flashscore-style data legally:
        1. football-data.co.uk — free historical results + odds CSV.
        2. The Odds API       — live and historical odds, commercial licence.
        3. OpenFootball       — open-source historical results (GitHub).
        4. ManualCsvProvider  — import a CSV you exported manually from the site.
        5. Flashscore does not offer a licensed public API as of 2026; if they
           introduce one, implement a proper authenticated client here.

    To suppress this error and keep this provider disabled silently, set:
        FLASHSCORE_PROVIDER_MODE=disabled   (default, raises on .fetch())
        FLASHSCORE_ENABLED=false            (read by is_enabled check)
    """

    @property
    def name(self) -> str:
        return "flashscore"

    @property
    def enabled(self) -> bool:
        return False

    def fetch(self, **kwargs: Any) -> pd.DataFrame:
        raise ProviderDisabledError(
            "Flashscore ingestion is disabled because direct scraping violates "
            "the site's Terms of Service and robots.txt. "
            "Use a licensed data source instead: football-data.co.uk, "
            "The Odds API, OpenFootball, or ManualCsvProvider for manual CSV exports. "
            "See README.md § Data Sources for setup instructions. "
            "Set FLASHSCORE_ENABLED=false (default) to acknowledge this policy."
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[BaseDataProvider]] = {
    "openfootball": OpenFootballProvider,
    "football-data-co-uk": FootballDataCoUkProvider,
    "odds-api": OddsApiProvider,
    "telegram-live": TelegramProvider,
    "manual-csv": ManualCsvProvider,
    "flashscore": FlashscoreProvider,
}


def get_provider(name: str) -> BaseDataProvider:
    """Return a provider instance by name.

    Args:
        name: Provider key (openfootball, football-data-co-uk, odds-api,
              telegram-live, manual-csv, flashscore).

    Raises:
        KeyError: If the provider name is unknown.
    """
    cls = _REGISTRY.get(name.lower())
    if cls is None:
        available = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"Unknown data provider {name!r}. Available: {available}")
    return cls()


def list_providers() -> dict[str, bool]:
    """Return all registered providers and their enabled status."""
    return {name: cls().enabled for name, cls in _REGISTRY.items()}
