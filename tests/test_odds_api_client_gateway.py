from __future__ import annotations

from src.ingest.odds_api import OddsAPIClient
from src.services.odds_gateway import OddsGatewayResponse


def test_odds_api_client_delegates_to_gateway(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_gateway(path: str, **kwargs):
        calls.append({"path": path, **kwargs})
        return OddsGatewayResponse(
            payload=[{"id": "evt1"}],
            headers={"remaining": "10", "used": "1", "last": "1"},
            redacted_url="https://example.test?apiKey=%5BREDACTED%5D",
        )

    monkeypatch.setattr("src.services.odds_gateway.fetch_the_odds_api_json", fake_gateway)
    client = OddsAPIClient(api_key="secret", request_delay_sec=0)
    data = client.get_odds("soccer_epl", ["eu"], ["h2h"])

    assert data == [{"id": "evt1"}]
    assert calls[0]["path"] == "/sports/soccer_epl/odds"
    assert calls[0]["api_key"] == "secret"
    assert calls[0]["source"] == "ingest.odds_api"
    assert calls[0]["sport_key"] == "soccer_epl"
    assert calls[0]["markets"] == ["h2h"]
    assert calls[0]["regions"] == ["eu"]
