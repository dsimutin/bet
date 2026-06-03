from __future__ import annotations

from src.monitoring.api_quota_monitor import APIQuotaMonitor


def test_provider_headers_set_monthly_usage_floor(tmp_path) -> None:
    monitor = APIQuotaMonitor(tmp_path / "quota.json")

    monitor.record_provider_headers(
        {
            "provider": "the_odds_api",
            "requested_at_utc": "2026-06-03T12:00:00+00:00",
            "x_requests_used": "480",
        }
    )

    used, limit = monitor.monthly_usage("the_odds_api", month="2026-06")
    assert used == 480
    assert limit == 500
    assert monitor.can_request("the_odds_api", min_remaining=25) is False


def test_provider_usage_never_lowers_local_usage(tmp_path) -> None:
    monitor = APIQuotaMonitor(tmp_path / "quota.json")

    monitor.record_request("the_odds_api", calls=10)
    monitor.record_provider_headers(
        {
            "provider": "the_odds_api",
            "requested_at_utc": "2026-06-03T12:00:00+00:00",
            "x_requests_used": "3",
        }
    )

    used, _ = monitor.monthly_usage("the_odds_api")
    assert used == 10


def test_provider_usage_ignores_non_numeric_headers(tmp_path) -> None:
    monitor = APIQuotaMonitor(tmp_path / "quota.json")

    monitor.record_provider_headers(
        {
            "provider": "the_odds_api",
            "requested_at_utc": "2026-06-03T12:00:00+00:00",
            "x_requests_used": "",
        }
    )

    used, _ = monitor.monthly_usage("the_odds_api", month="2026-06")
    assert used == 0
