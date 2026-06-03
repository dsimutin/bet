"""Convert The Odds API live h2h odds into model-ready football rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class LiveOddsConversionResult:
    dataframe: pd.DataFrame
    converted_events: int
    skipped_events: list[str] = field(default_factory=list)


class LiveOddsFootballDataAdapter:
    """Maps live 1X2 odds to the football-data column shape used by models."""

    DRAW_NAMES = {"draw", "tie", "x"}

    def __init__(
        self,
        bookmaker_prefix: str = "B365",
        preferred_bookmakers: list[str] | None = None,
        allow_bookmaker_fallback: bool = True,
    ) -> None:
        self.bookmaker_prefix = bookmaker_prefix
        self.preferred_bookmakers = preferred_bookmakers or ["bet365", "pinnacle"]
        self.allow_bookmaker_fallback = allow_bookmaker_fallback

    def convert(self, events: list[dict[str, Any]]) -> LiveOddsConversionResult:
        rows: list[dict[str, Any]] = []
        skipped: list[str] = []
        snapshot_ts = datetime.now(timezone.utc).isoformat()

        for event in events:
            event_time = self._parse_event_time(event.get("commence_time"))
            event_id = event.get("id", "unknown")
            if event_time is None:
                skipped.append(f"{event_id}: invalid_commence_time")
                continue

            selected = self._select_bookmaker(event)
            if selected is None:
                skipped.append(f"{event_id}: no_complete_h2h_bookmaker")
                continue

            bookmaker, market = selected
            odds = self._extract_1x2_prices(event, market)
            if odds is None:
                skipped.append(f"{event_id}: no_complete_1x2_prices")
                continue

            source_last_update = market.get("last_update") or bookmaker.get("last_update") or ""
            rows.append(
                {
                    "Date": event_time.strftime("%d/%m/%Y"),
                    "HomeTeam": event.get("home_team"),
                    "AwayTeam": event.get("away_team"),
                    f"{self.bookmaker_prefix}H": odds["home"],
                    f"{self.bookmaker_prefix}D": odds["draw"],
                    f"{self.bookmaker_prefix}A": odds["away"],
                    "event_time_utc": event_time.isoformat(),
                    "snapshot_ts_utc": str(event.get("_odds_snapshot_ts_utc") or snapshot_ts),
                    "source_last_update_utc": source_last_update,
                    "source_event_id": event.get("id"),
                    "source_sport_key": event.get("sport_key"),
                    "source_bookmaker_key": bookmaker.get("key"),
                    "source_bookmaker_title": bookmaker.get("title"),
                    "source_market_key": market.get("key") or "h2h",
                }
            )

        return LiveOddsConversionResult(
            dataframe=pd.DataFrame(rows),
            converted_events=len(rows),
            skipped_events=skipped,
        )

    def _select_bookmaker(
        self, event: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        complete: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for bookmaker in event.get("bookmakers", []) or []:
            for market in bookmaker.get("markets", []) or []:
                if market.get("key") != "h2h":
                    continue
                if self._extract_1x2_prices(event, market) is not None:
                    complete.append((bookmaker, market))
                    break

        if not complete:
            return None

        by_key = {
            str(bookmaker.get("key", "")).lower(): (bookmaker, market)
            for bookmaker, market in complete
        }
        for preferred in self.preferred_bookmakers:
            selected = by_key.get(preferred.lower())
            if selected is not None:
                return selected

        if self.allow_bookmaker_fallback:
            return complete[0]
        return None

    def _extract_1x2_prices(
        self,
        event: dict[str, Any],
        market: dict[str, Any],
    ) -> dict[str, float] | None:
        home_team = str(event.get("home_team", "")).strip().lower()
        away_team = str(event.get("away_team", "")).strip().lower()
        if not home_team or not away_team:
            return None

        prices: dict[str, float] = {}
        for outcome in market.get("outcomes", []) or []:
            name = str(outcome.get("name", "")).strip().lower()
            price = self._safe_price(outcome.get("price"))
            if price is None:
                continue
            if name == home_team:
                prices["home"] = price
            elif name == away_team:
                prices["away"] = price
            elif name in self.DRAW_NAMES:
                prices["draw"] = price

        if {"home", "draw", "away"} <= set(prices):
            return prices
        return None

    def _safe_price(self, value: Any) -> float | None:
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None
        if price <= 1.0:
            return None
        return round(price, 4)

    def _parse_event_time(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            dt = value
        else:
            raw = str(value or "").replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
