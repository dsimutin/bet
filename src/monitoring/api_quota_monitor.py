"""Monitor API quota usage to prevent overspending in production."""

from __future__ import annotations

import json
import logging
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
        month_total = sum(v.get(api_name, 0) for k, v in data.items() if k.startswith(today[:7]))
        limit = self.LIMITS.get(api_name, {}).get("free", float("inf"))

        if month_total > limit * 0.8:
            _log.warning(
                "[quota] %s usage %.0f%% of monthly limit (%d/%d)",
                api_name,
                month_total / limit * 100,
                month_total,
                limit,
            )

    def can_request(self, api_name: str, min_remaining: int) -> bool:
        used, limit = self.monthly_usage(api_name)
        return (limit - used) >= min_remaining

    def record_provider_headers(self, record: dict[str, Any]) -> None:
        data = self._load()
        records = data.setdefault("_provider_records", [])
        if isinstance(records, list):
            records.append(record)
            data["_provider_records"] = records[-200:]
        api_name = _provider_api_name(str(record.get("provider", "")))
        used = _parse_int(record.get("x_requests_used"))
        if api_name and used is not None:
            month = str(record.get("requested_at_utc") or datetime.now(timezone.utc).isoformat())[
                :7
            ]
            provider_usage = data.setdefault("_provider_monthly_usage", {})
            if isinstance(provider_usage, dict):
                month_usage = provider_usage.setdefault(month, {})
                if isinstance(month_usage, dict):
                    month_usage[api_name] = max(int(month_usage.get(api_name, 0) or 0), used)
                    provider_usage[month] = month_usage
                data["_provider_monthly_usage"] = provider_usage
        if isinstance(records, list) or api_name:
            self._save(data)

    def monthly_usage(self, api_name: str, month: str = "") -> tuple[int, int]:
        """Get (used, limit) for month (YYYY-MM)."""
        if not month:
            month = datetime.now(timezone.utc).date().isoformat()[:7]

        data = self._load()
        local_used = sum(
            v.get(api_name, 0)
            for k, v in data.items()
            if k.startswith(month) and isinstance(v, dict)
        )
        provider_usage = data.get("_provider_monthly_usage", {})
        provider_used = 0
        if isinstance(provider_usage, dict):
            month_usage = provider_usage.get(month, {})
            if isinstance(month_usage, dict):
                provider_used = int(month_usage.get(api_name, 0) or 0)
        used = max(local_used, provider_used)
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
            self.quota_file.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        except Exception as exc:
            _log.warning("[quota] failed to save: %s", exc)


# Global instance
_monitor = APIQuotaMonitor()


def record_odds_api_request(calls: int = 1) -> None:
    """Record The Odds API call."""
    _monitor.record_request("the_odds_api", calls)


def can_make_odds_api_request(*, priority_refresh: bool = False) -> bool:
    import os

    threshold_name = (
        "THE_ODDS_API_MIN_REMAINING_PRIORITY_REFRESH"
        if priority_refresh
        else "THE_ODDS_API_MIN_REMAINING_HARD_STOP"
    )
    min_remaining = int(os.environ.get(threshold_name, "50" if priority_refresh else "25"))
    return _monitor.can_request("the_odds_api", min_remaining)


def record_odds_api_provider_headers(record: dict[str, Any]) -> None:
    _monitor.record_provider_headers(record)


def record_apifootball_request(calls: int = 1) -> None:
    """Record API-Football call."""
    _monitor.record_request("apifootball", calls)


def get_usage_summary() -> dict[str, Any]:
    """Get monthly usage summary for all APIs."""
    return _monitor.usage_summary()


def _provider_api_name(provider: str) -> str:
    aliases = {
        "the_odds_api": "the_odds_api",
        "oddsapi": "the_odds_api",
        "api-football": "apifootball",
        "apifootball": "apifootball",
    }
    return aliases.get(provider.strip().lower(), "")


def _parse_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
