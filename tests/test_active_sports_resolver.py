from __future__ import annotations

from pathlib import Path

import yaml

from src.ingest.active_sports_resolver import ActiveSportsResolver


class FakeOddsProvider:
    def __init__(self, odds_by_sport: dict[str, list[dict]]) -> None:
        self.odds_by_sport = odds_by_sport
        self.probed: list[str] = []

    def get_sports(self) -> list[dict]:
        return [
            {"key": "soccer_epl", "title": "EPL", "active": True, "has_outrights": False},
            {"key": "tennis_atp", "title": "ATP", "active": True, "has_outrights": False},
            {"key": "basketball_nba", "title": "NBA", "active": True, "has_outrights": False},
        ]

    def get_odds(self, sport: str, regions: list[str], markets: list[str]) -> list[dict]:
        self.probed.append(sport)
        return self.odds_by_sport.get(sport, [])


def _event_with_h2h() -> dict:
    return {
        "id": "evt_1",
        "bookmakers": [
            {
                "key": "book",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "A", "price": 1.9},
                            {"name": "B", "price": 2.0},
                        ],
                    }
                ],
            }
        ],
    }


def _write_config(tmp_path: Path) -> Path:
    config = {
        "sports": {
            "soccer": {
                "active": True,
                "priority": 1,
                "api_sport_keys": ["soccer_epl"],
            },
            "tennis": {
                "active": True,
                "priority": 2,
                "api_sport_keys": ["tennis_atp"],
            },
            "basketball": {
                "active": True,
                "priority": 3,
                "api_sport_keys": ["basketball_nba"],
            },
        },
        "settings": {
            "enabled": ["soccer", "tennis", "basketball"],
            "fallback_if_no_events": True,
            "min_events_required": 1,
            "max_sports_per_run": 2,
            "exclude_sports": [],
        },
    }
    path = tmp_path / "sports.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_resolver_falls_back_when_soccer_has_no_events(tmp_path: Path) -> None:
    provider = FakeOddsProvider(
        {
            "soccer_epl": [],
            "tennis_atp": [_event_with_h2h()],
        }
    )
    resolver = ActiveSportsResolver(provider, _write_config(tmp_path), markets=["h2h"])

    report = resolver.resolve()

    assert [sport.sport_key for sport in report.active_sports] == ["tennis_atp"]
    assert provider.probed[:2] == ["soccer_epl", "tennis_atp"]
    assert report.no_data_reason is None


def test_resolver_writes_no_data_report_when_all_sports_empty(tmp_path: Path) -> None:
    provider = FakeOddsProvider({"soccer_epl": [], "tennis_atp": [], "basketball_nba": []})
    resolver = ActiveSportsResolver(provider, _write_config(tmp_path), markets=["h2h"])

    report = resolver.resolve()
    report_path = resolver.write_no_data_report(tmp_path, report)

    assert report.active_sports == []
    assert report.no_data_reason is not None
    assert report_path.exists()
    assert "no_data_reason" in report_path.read_text(encoding="utf-8")


def test_resolver_limits_number_of_active_sports(tmp_path: Path) -> None:
    provider = FakeOddsProvider(
        {
            "soccer_epl": [_event_with_h2h()],
            "tennis_atp": [_event_with_h2h()],
            "basketball_nba": [_event_with_h2h()],
        }
    )
    resolver = ActiveSportsResolver(provider, _write_config(tmp_path), markets=["h2h"])

    report = resolver.resolve()

    assert [sport.sport_key for sport in report.active_sports] == ["soccer_epl", "tennis_atp"]
