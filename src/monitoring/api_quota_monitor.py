"""Monitor API quota usage to prevent overspending in production.

Hard stop thresholds (requests remaining, not used):
  THE_ODDS_API_MIN_REMAINING_HARD_STOP      — refuse any new call at or below this
  THE_ODDS_API_MIN_REMAINING_PRIORITY_REFRESH — allow only priority refreshes above this
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


class APIQuotaMonitor:
    """Track API call counts and warn if approaching limits."""

    LIMITS = {
        "the_odds_api": {"free": 500, "pro": 100000},
        "apifootball": {"free": 100},
    }

    def __init__(self, quota_file: Path = Path("data/monitoring/api_quota.json")) -> None:
        self.quota_file = quota_file
        self.quota_file.parent.mkdir(parents=True, exist_ok=True)

    def record_request(self, api_name: str, calls: int = 1) -> None:
        """Record API calls made."""
        data = self._load()
        today = datetime.now(timezone.utc).date().isoformat()

        if today not in data:
            data[today] = {}
        if api_name not in data[today]:
            data[today][api_name] = 0

        data[today][api_name] += calls
        self._save(data)

        # Check if approaching limit
        month_total = sum(
            v.get(api_name, 0) for k, v in data.items() if k.startswith(today[:7])
        )
        limit = self.LIMITS.get(api_name, {}).get("free", float("inf"))

        if month_total > limit * 0.8:
            _log.warning(
                "[quota] %s usage %.0f%% of monthly limit (%d/%d)",
                api_name,
                month_total / limit * 100,
                month_total,
                limit,
            )

    def monthly_usage(self, api_name: str, month: str = "") -> tuple[int, int]:
        """Get (used, limit) for month (YYYY-MM)."""
        if not month:
            month = datetime.now(timezone.utc).date().isoformat()[:7]

        data = self._load()
        used = sum(v.get(api_name, 0) for k, v in data.items() if k.startswith(month))
        limit = self.LIMITS.get(api_name, {}).get("free", 0)
        return used, limit

    def usage_summary(self) -> dict[str, Any]:
        """Return summary of current month usage."""
        month = datetime.now(timezone.utc).date().isoformat()[:7]
        result = {}
        for api_name in self.LIMITS:
            used, limit = self.monthly_usage(api_name, month)
            result[api_name] = {
                "used": used,
                "limit": limit,
                "remaining": limit - used,
                "pct_used": round(used / limit * 100, 1) if limit else 0,
            }
        return result

    def update_remaining_from_header(self, api_name: str, remaining: int) -> None:
        """Update authoritative remaining count from API response header.

        The Odds API returns x-requests-remaining in every response.
        Storing this gives a more accurate picture than counting locally.
        """
        data = self._load()
        meta_key = f"_meta_{api_name}"
        if meta_key not in data:
            data[meta_key] = {}
        data[meta_key]["last_remaining"] = remaining
        data[meta_key]["last_remaining_at"] = datetime.now(timezone.utc).isoformat()
        self._save(data)

    def get_last_known_remaining(self, api_name: str) -> int | None:
        """Return the last known remaining count from API response headers, or None."""
        data = self._load()
        meta = data.get(f"_meta_{api_name}", {})
        val = meta.get("last_remaining")
        return int(val) if val is not None else None

    def check_hard_stop(self, api_name: str = "the_odds_api") -> None:
        """Raise RuntimeError if remaining quota is at or below the hard stop threshold.

        Reads THE_ODDS_API_MIN_REMAINING_HARD_STOP (default 25).
        Uses last known remaining from API headers; falls back to local count.
        """
        hard_stop = int(os.environ.get("THE_ODDS_API_MIN_REMAINING_HARD_STOP", "25"))
        if hard_stop <= 0:
            return

        remaining = self.get_last_known_remaining(api_name)
        if remaining is None:
            month = datetime.now(timezone.utc).date().isoformat()[:7]
            used, limit = self.monthly_usage(api_name, month)
            remaining = limit - used

        if remaining <= hard_stop:
            raise RuntimeError(
                f"[quota] Hard stop: {api_name} has only {remaining} requests remaining "
                f"(threshold={hard_stop}). Set THE_ODDS_API_MIN_REMAINING_HARD_STOP=0 to disable."
            )

    def is_priority_refresh_allowed(self, api_name: str = "the_odds_api") -> bool:
        """Return True if enough quota remains for priority refreshes.

        Uses THE_ODDS_API_MIN_REMAINING_PRIORITY_REFRESH (default 80).
        When False, only hard-minimum requests should be made.
        """
        threshold = int(os.environ.get("THE_ODDS_API_MIN_REMAINING_PRIORITY_REFRESH", "80"))
        remaining = self.get_last_known_remaining(api_name)
        if remaining is None:
            month = datetime.now(timezone.utc).date().isoformat()[:7]
            used, limit = self.monthly_usage(api_name, month)
            remaining = limit - used
        return remaining > threshold

    def _load(self) -> dict[str, Any]:
        """Load quota data from file."""
        if not self.quota_file.exists():
            return {}
        try:
            return json.loads(self.quota_file.read_text())
        except Exception:
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        """Save quota data to file."""
        try:
            self.quota_file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, default=str)
            )
        except Exception as exc:
            _log.warning("[quota] failed to save: %s", exc)


# Global instance
_monitor = APIQuotaMonitor()


def record_odds_api_request(calls: int = 1) -> None:
    """Record The Odds API call."""
    _monitor.record_request("the_odds_api", calls)


def record_apifootball_request(calls: int = 1) -> None:
    """Record API-Football call."""
    _monitor.record_request("apifootball", calls)


def get_usage_summary() -> dict[str, Any]:
    """Get monthly usage summary for all APIs."""
    return _monitor.usage_summary()


def check_odds_api_quota() -> None:
    """Raise RuntimeError if The Odds API quota is exhausted. Call before any API request."""
    _monitor.check_hard_stop("the_odds_api")


def update_odds_api_remaining(remaining: int) -> None:
    """Record the x-requests-remaining header value from an Odds API response."""
    _monitor.update_remaining_from_header("the_odds_api", remaining)


def is_odds_api_priority_refresh_allowed() -> bool:
    """Return True if quota allows priority refreshes."""
    return _monitor.is_priority_refresh_allowed("the_odds_api")
