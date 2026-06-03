from __future__ import annotations

from fastapi.testclient import TestClient


def test_webhook_rejects_missing_secret(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook-secret")
    from src.web.health_app import app

    client = TestClient(app)
    assert client.post("/webhook/telegram", json={"update_id": 1}).status_code == 401


def test_webhook_rejects_wrong_secret(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook-secret")
    from src.web.health_app import app

    client = TestClient(app)
    response = client.post(
        "/webhook/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        json={"update_id": 1},
    )
    assert response.status_code == 401


def test_webhook_accepts_valid_secret(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook-secret")
    from src.web.health_app import app

    client = TestClient(app)
    response = client.post(
        "/webhook/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"},
        json={"update_id": 1},
    )
    assert response.status_code == 200
