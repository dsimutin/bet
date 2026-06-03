from __future__ import annotations

from src.infrastructure.render_db import RenderDB


def test_render_db_persists_quota_usage_and_provider_floor(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db = RenderDB(sqlite_path=tmp_path / "metadata.db")

    db.record_api_quota_request("the_odds_api", 3, "2026-06-03")
    db.record_api_quota_request("the_odds_api", 4, "2026-06-03")

    assert db.fetch_api_quota_monthly_usage("the_odds_api", "2026-06") == 7

    db.record_api_quota_provider_headers(
        {
            "api_name": "the_odds_api",
            "provider": "the_odds_api",
            "endpoint": "/sports/soccer_epl/odds",
            "requested_at_utc": "2026-06-03T12:00:00+00:00",
            "x_requests_used": "480",
            "x_requests_remaining": "20",
            "x_requests_last": "1",
            "source": "unit",
        }
    )

    assert db.fetch_api_quota_monthly_usage("the_odds_api", "2026-06") == 480

    db.record_api_quota_provider_headers(
        {
            "api_name": "the_odds_api",
            "provider": "the_odds_api",
            "requested_at_utc": "2026-06-04T12:00:00+00:00",
            "x_requests_used": "100",
        }
    )

    assert db.fetch_api_quota_monthly_usage("the_odds_api", "2026-06") == 480
