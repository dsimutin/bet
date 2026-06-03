from __future__ import annotations

import pytest

from src.monitoring.api_quota_monitor import APIQuotaMonitor


class _FakeQuotaDB:
    def __init__(self) -> None:
        self.requests: list[tuple[str, int, str]] = []
        self.provider_records: list[dict] = []
        self.monthly_used = 0

    def record_api_quota_request(self, api_name: str, calls: int, usage_date: str) -> None:
        self.requests.append((api_name, calls, usage_date))

    def record_api_quota_provider_headers(self, record: dict) -> None:
        self.provider_records.append(record)

    def fetch_api_quota_monthly_usage(self, api_name: str, month: str) -> int:
        return self.monthly_used


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


def test_quota_monitor_uses_database_backend(monkeypatch) -> None:
    fake_db = _FakeQuotaDB()
    fake_db.monthly_used = 42
    monkeypatch.setattr("src.monitoring.api_quota_monitor._get_db", lambda: fake_db)

    monitor = APIQuotaMonitor(use_database=True)
    monitor.record_request("the_odds_api", calls=2)
    monitor.record_provider_headers(
        {
            "provider": "the_odds_api",
            "requested_at_utc": "2026-06-03T12:00:00+00:00",
            "x_requests_used": "44",
        }
    )

    assert fake_db.requests[0][0] == "the_odds_api"
    assert fake_db.requests[0][1] == 2
    assert fake_db.provider_records[0]["api_name"] == "the_odds_api"
    assert monitor.monthly_usage("the_odds_api", month="2026-06") == (42, 500)


def test_quota_monitor_fails_closed_on_database_read_error(monkeypatch) -> None:
    class BrokenDB:
        def fetch_api_quota_monthly_usage(self, api_name: str, month: str) -> int:
            raise RuntimeError("database unavailable")

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr("src.monitoring.api_quota_monitor._get_db", lambda: BrokenDB())

    monitor = APIQuotaMonitor(use_database=True)

    assert monitor.monthly_usage("the_odds_api", month="2026-06") == (500, 500)
    assert monitor.can_request("the_odds_api", min_remaining=25) is False


def test_quota_monitor_raises_on_production_database_write_error(monkeypatch) -> None:
    class BrokenDB:
        def record_api_quota_request(self, api_name: str, calls: int, usage_date: str) -> None:
            raise RuntimeError("database unavailable")

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr("src.monitoring.api_quota_monitor._get_db", lambda: BrokenDB())

    monitor = APIQuotaMonitor(use_database=True)

    with pytest.raises(RuntimeError, match="database unavailable"):
        monitor.record_request("the_odds_api", calls=1)
