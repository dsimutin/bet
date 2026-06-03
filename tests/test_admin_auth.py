from __future__ import annotations

from fastapi.testclient import TestClient


def test_admin_route_rejects_missing_token(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", "secret")
    from src.web.health_app import app

    client = TestClient(app)
    assert client.get("/health/active").status_code == 401


def test_admin_route_rejects_wrong_token(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", "secret")
    from src.web.health_app import app

    client = TestClient(app)
    assert client.get("/health/active", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_admin_route_accepts_valid_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", "secret")
    from src.web.health_app import app

    client = TestClient(app)
    assert (
        client.get("/health/active", headers={"Authorization": "Bearer secret"}).status_code == 200
    )


def test_debug_routes_disabled_in_production(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", "secret")
    monkeypatch.setenv("ENABLE_DEBUG_ROUTES", "false")
    from src.web.health_app import app

    client = TestClient(app)
    response = client.get("/debug/tennis", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 404


def test_public_health_does_not_expose_secrets(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", "secret")
    from src.web.health_app import app

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "secret" not in response.text


def test_deep_readiness_requires_admin_token(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", "secret")
    from src.web.health_app import app

    client = TestClient(app)
    assert client.get("/health/readiness").status_code == 401
