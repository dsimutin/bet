from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.services.odds_gateway import fetch_the_odds_api_json, redact_url


class _Resp:
    status = 200
    headers = {
        "x-requests-remaining": "123",
        "x-requests-used": "7",
        "x-requests-last": "1",
    }

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return json.dumps([{"key": "soccer_epl"}]).encode("utf-8")


def test_odds_gateway_records_provider_headers(monkeypatch) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        "src.monitoring.api_quota_monitor.can_make_odds_api_request", lambda **_: True
    )
    monkeypatch.setattr(
        "src.monitoring.api_quota_monitor.record_odds_api_provider_headers",
        lambda record: captured.append(record),
    )
    with patch("urllib.request.urlopen", return_value=_Resp()):
        response = fetch_the_odds_api_json(
            "/sports/soccer_epl/odds",
            api_key="secret",
            query={"regions": "eu", "markets": "h2h"},
            source="unit",
            sport_key="soccer_epl",
            markets=["h2h"],
            regions=["eu"],
        )

    assert response.payload == [{"key": "soccer_epl"}]
    assert captured[0]["provider"] == "the_odds_api"
    assert captured[0]["x_requests_remaining"] == "123"
    assert captured[0]["source"] == "unit"


def test_odds_gateway_redacts_api_key() -> None:
    assert (
        redact_url("https://api.example.test/path?apiKey=secret&regions=eu")
        == "https://api.example.test/path?apiKey=%5BREDACTED%5D&regions=eu"
    )


def test_quota_hard_stop_blocks_optional_request(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.monitoring.api_quota_monitor.can_make_odds_api_request", lambda **_: False
    )
    with pytest.raises(RuntimeError, match="quota hard stop"):
        fetch_the_odds_api_json(
            "/sports",
            api_key="secret",
            query={},
            source="unit",
        )
