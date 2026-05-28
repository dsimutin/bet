"""
Resolve active sports for the current scan.

The resolver keeps the pipeline from stopping when the preferred sport has no
events. It asks the odds provider which sports are available, probes configured
sports in priority order, and returns only sports that have events with usable
bookmaker markets.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import yaml

logger = logging.getLogger(__name__)


class OddsProvider(Protocol):
    """Small protocol implemented by OddsAPIClient and test doubles."""

    def get_sports(self) -> list[dict]:
        """Return provider sports metadata."""

    def get_odds(self, sport: str, regions: list[str], markets: list[str]) -> list[dict]:
        """Return odds for one provider sport key."""


@dataclass(frozen=True)
class SportProbeResult:
    sport_key: str
    configured_sport: str
    title: str
    priority: int
    event_count: int
    supported_markets: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SportProbeFailure:
    sport_key: str
    configured_sport: str
    reason: str


@dataclass(frozen=True)
class ActiveSportsReport:
    generated_at_utc: str
    active_sports: list[SportProbeResult]
    failures: list[SportProbeFailure]
    no_data_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at_utc": self.generated_at_utc,
            "active_sports": [asdict(item) for item in self.active_sports],
            "failures": [asdict(item) for item in self.failures],
            "no_data_reason": self.no_data_reason,
        }


class ActiveSportsResolver:
    """Find configured sports that have events and odds available today."""

    DEFAULT_REGIONS = ["eu", "uk"]
    DEFAULT_MARKETS = ["h2h"]

    def __init__(
        self,
        odds_provider: OddsProvider,
        config_path: Path,
        regions: list[str] | None = None,
        markets: list[str] | None = None,
    ) -> None:
        self.odds_provider = odds_provider
        self.config_path = Path(config_path)
        self.regions = regions or self.DEFAULT_REGIONS
        self.markets = markets or self.DEFAULT_MARKETS
        self.config = self._load_config()

    def resolve(self) -> ActiveSportsReport:
        settings = self.config.get("settings", {})
        fallback_enabled = bool(settings.get("fallback_if_no_events", True))
        max_sports = int(settings.get("max_sports_per_run", 5))
        min_events = int(settings.get("min_events_required", 1))

        provider_sports = self._get_provider_sports()
        provider_by_key = {str(item.get("key", "")): item for item in provider_sports}
        provider_active_keys = {
            key
            for key, item in provider_by_key.items()
            if item.get("active", True) and not item.get("has_outrights", False)
        }

        active: list[SportProbeResult] = []
        failures: list[SportProbeFailure] = []

        for candidate in self._candidate_sport_keys():
            sport_key = candidate["sport_key"]
            configured_sport = candidate["configured_sport"]

            if provider_active_keys and sport_key not in provider_active_keys:
                failures.append(
                    SportProbeFailure(sport_key, configured_sport, "not_active_in_provider")
                )
                continue

            try:
                events = self.odds_provider.get_odds(
                    sport=sport_key,
                    regions=self.regions,
                    markets=self.markets,
                )
            except Exception as exc:  # pragma: no cover - covered through behavior, message varies
                logger.warning("Odds probe failed for %s: %s", sport_key, exc)
                failures.append(
                    SportProbeFailure(sport_key, configured_sport, f"provider_error: {exc}")
                )
                if not fallback_enabled:
                    break
                continue

            usable_events = [event for event in events if self._event_has_markets(event)]
            if len(usable_events) < min_events:
                failures.append(
                    SportProbeFailure(sport_key, configured_sport, "no_events_with_odds")
                )
                if not fallback_enabled:
                    break
                continue

            title = str(provider_by_key.get(sport_key, {}).get("title") or candidate["title"])
            active.append(
                SportProbeResult(
                    sport_key=sport_key,
                    configured_sport=configured_sport,
                    title=title,
                    priority=int(candidate["priority"]),
                    event_count=len(usable_events),
                    supported_markets=self._collect_markets(usable_events),
                )
            )
            if len(active) >= max_sports:
                break

        no_data_reason = None
        if not active:
            no_data_reason = (
                "No configured sports had enough events with bookmaker markets for this scan."
            )

        return ActiveSportsReport(
            generated_at_utc=datetime.now(timezone.utc).isoformat(),
            active_sports=active,
            failures=failures,
            no_data_reason=no_data_reason,
        )

    def write_report(self, output_dir: Path, report: ActiveSportsReport) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "active_sports_report.json"
        output_path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return output_path

    def write_no_data_report(self, output_dir: Path, report: ActiveSportsReport) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "no_data_report.json"
        output_path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return output_path

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Sports config not found: {self.config_path}")
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        return raw

    def _get_provider_sports(self) -> list[dict]:
        try:
            return self.odds_provider.get_sports()
        except Exception as exc:
            logger.warning("Could not load provider sports list: %s", exc)
            return []

    def _candidate_sport_keys(self) -> list[dict[str, Any]]:
        settings = self.config.get("settings", {})
        enabled = set(settings.get("enabled", []))
        excluded = set(settings.get("exclude_sports", []))
        sports = self.config.get("sports", {})

        candidates: list[dict[str, Any]] = []
        for sport_name, sport_cfg in sports.items():
            if enabled and sport_name not in enabled:
                continue
            if sport_name in excluded or not sport_cfg.get("active", False):
                continue

            keys = sport_cfg.get("api_sport_keys") or self._keys_from_legacy_config(
                sport_name, sport_cfg
            )
            for sport_key in keys:
                if sport_key in excluded:
                    continue
                candidates.append(
                    {
                        "configured_sport": sport_name,
                        "sport_key": sport_key,
                        "priority": int(sport_cfg.get("priority", 99)),
                        "title": str(sport_cfg.get("name_ru") or sport_name),
                    }
                )

        return sorted(candidates, key=lambda item: (item["priority"], item["sport_key"]))

    def _keys_from_legacy_config(self, sport_name: str, sport_cfg: dict[str, Any]) -> list[str]:
        if sport_name == "soccer":
            return [
                "soccer_epl",
                "soccer_spain_la_liga",
                "soccer_germany_bundesliga",
                "soccer_italy_serie_a",
                "soccer_france_ligue_one",
                "soccer_uefa_champs_league",
            ]
        if sport_name == "tennis":
            return ["tennis_atp", "tennis_wta"]
        if sport_name in {"hockey", "ice_hockey"}:
            return ["icehockey_nhl"]
        return list(sport_cfg.get("sports_supported", []))

    def _event_has_markets(self, event: dict[str, Any]) -> bool:
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                outcomes = market.get("outcomes", [])
                if market.get("key") in self.markets and len(outcomes) >= 2:
                    return True
        return False

    def _collect_markets(self, events: list[dict[str, Any]]) -> list[str]:
        markets: set[str] = set()
        for event in events:
            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market.get("outcomes"):
                        markets.add(str(market.get("key", "")))
        return sorted(m for m in markets if m)
