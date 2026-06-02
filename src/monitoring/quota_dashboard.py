"""Real-time API quota monitoring and alerts.

Tracks The Odds API usage and warns before hitting the free tier limit (500/month).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

QUOTA_LIMIT = 500  # Free tier limit
QUOTA_WARN_THRESHOLD = 0.80  # Warn at 80%
QUOTA_CRITICAL_THRESHOLD = 0.95  # Critical at 95%


class QuotaTracker:
    """Track API quota usage with thresholds and alerts."""

    def __init__(self, log_file: Path | str | None = None):
        self.log_file = Path(log_file) if log_file else None
        self.current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        self._load()

    def _load(self) -> None:
        """Load quota history from file."""
        if not self.log_file or not self.log_file.exists():
            self.usage = {}
            return
        try:
            data = json.loads(self.log_file.read_text())
            self.usage = data.get("usage", {})
        except Exception as exc:
            _log.warning("[quota] failed to load quota log: %s", exc)
            self.usage = {}

    def _save(self) -> None:
        """Persist quota history."""
        if not self.log_file:
            return
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.log_file.write_text(
                json.dumps(
                    {
                        "last_updated": datetime.now(timezone.utc).isoformat(),
                        "usage": self.usage,
                        "limit": QUOTA_LIMIT,
                    },
                    indent=2,
                )
            )
        except Exception as exc:
            _log.warning("[quota] failed to save quota log: %s", exc)

    def record(self, calls: int, component: str = "general") -> None:
        """Record API calls and check for threshold violations."""
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        if month not in self.usage:
            self.usage[month] = {"total": 0, "by_component": {}}
        self.usage[month]["total"] += calls
        if component not in self.usage[month]["by_component"]:
            self.usage[month]["by_component"][component] = 0
        self.usage[month]["by_component"][component] += calls
        self._save()

        # Check thresholds
        used = self.usage[month]["total"]
        pct = 100 * used / QUOTA_LIMIT

        if pct >= QUOTA_CRITICAL_THRESHOLD:
            _log.error(
                "[quota] 🔴 CRITICAL: {:.0f}% quota used ({}/{} calls). "
                "Approaching limit. Disable extended features.",
                pct,
                used,
                QUOTA_LIMIT,
            )
        elif pct >= QUOTA_WARN_THRESHOLD:
            _log.warning(
                "[quota] 🟡 WARNING: {:.0f}% quota used ({}/{} calls). "
                "Monitor usage closely.",
                pct,
                used,
                QUOTA_LIMIT,
            )
        else:
            _log.info(
                "[quota] {:.0f}% quota used ({}/{} calls, {:.0f} remaining)",
                pct,
                used,
                QUOTA_LIMIT,
                QUOTA_LIMIT - used,
            )

    def get_status(self) -> dict[str, Any]:
        """Return current quota status."""
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        if month not in self.usage:
            return {
                "month": month,
                "used": 0,
                "limit": QUOTA_LIMIT,
                "remaining": QUOTA_LIMIT,
                "pct_used": 0.0,
                "status": "healthy",
                "by_component": {},
            }

        used = self.usage[month]["total"]
        pct = 100 * used / QUOTA_LIMIT
        remaining = max(0, QUOTA_LIMIT - used)

        if pct >= QUOTA_CRITICAL_THRESHOLD:
            status = "critical"
        elif pct >= QUOTA_WARN_THRESHOLD:
            status = "warning"
        else:
            status = "healthy"

        return {
            "month": month,
            "used": used,
            "limit": QUOTA_LIMIT,
            "remaining": remaining,
            "pct_used": round(pct, 1),
            "status": status,
            "by_component": self.usage[month].get("by_component", {}),
        }

    def report(self) -> str:
        """Generate human-readable quota report."""
        status = self.get_status()
        month = status["month"]
        used = status["used"]
        limit = status["limit"]
        remaining = status["remaining"]
        pct = status["pct_used"]

        lines = [
            "=" * 60,
            f"API QUOTA STATUS — {month}",
            "=" * 60,
            f"Used:      {used:>4} / {limit} calls ({pct:.1f}%)",
            f"Remaining: {remaining:>4} calls",
            f"Status:    {status['status'].upper()}",
            "=" * 60,
        ]

        if status["by_component"]:
            lines.append("By component:")
            for component, count in sorted(status["by_component"].items()):
                lines.append(f"  {component:20s}: {count:>4} calls")

        lines.append("=" * 60)
        return "\n".join(lines)


# Global instance
_tracker: QuotaTracker | None = None


def init_quota_tracker(log_file: Path | str | None = None) -> None:
    """Initialize global quota tracker."""
    global _tracker
    _tracker = QuotaTracker(log_file)


def record_quota(calls: int, component: str = "general") -> None:
    """Record API calls globally."""
    global _tracker
    if _tracker is None:
        _tracker = QuotaTracker()
    _tracker.record(calls, component)


def get_quota_status() -> dict[str, Any]:
    """Get current quota status."""
    global _tracker
    if _tracker is None:
        _tracker = QuotaTracker()
    return _tracker.get_status()


def quota_report() -> str:
    """Generate quota report."""
    global _tracker
    if _tracker is None:
        _tracker = QuotaTracker()
    return _tracker.report()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(quota_report())
