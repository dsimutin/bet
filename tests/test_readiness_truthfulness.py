from __future__ import annotations

import json

from fastapi.testclient import TestClient

import src.web.health_app as health_app


def _write_model(model_dir, league: str = "EPL") -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / f"dc_{league}.meta.json").write_text(
        json.dumps({"status": "production", "league": league}),
        encoding="utf-8",
    )


def _write_staging(data_dir) -> None:
    staging = data_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "EPL_latest.csv").write_text("Date,HomeTeam,AwayTeam\n", encoding="utf-8")


def test_ready_returns_503_when_database_required_but_unavailable(monkeypatch, tmp_path) -> None:
    _write_model(tmp_path / "models", league="EPL")
    _write_staging(tmp_path)
    monkeypatch.setenv("LEAGUES", "EPL")
    monkeypatch.setattr(health_app, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(health_app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(health_app, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(
        "src.infrastructure.persistent_ledger.ledger_healthcheck",
        lambda path=None: {"backend": "unavailable", "ok": False},
    )

    response = TestClient(health_app.app).get("/ready")

    assert response.status_code == 503
    assert response.json()["ledger"]["ok"] is False


def test_health_reports_database_unavailable(monkeypatch, tmp_path) -> None:
    _write_model(tmp_path / "models", league="EPL")
    _write_staging(tmp_path)
    monkeypatch.setenv("LEAGUES", "EPL")
    monkeypatch.setattr(health_app, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(health_app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(health_app, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(
        "src.infrastructure.persistent_ledger.ledger_healthcheck",
        lambda path=None: {"backend": "unavailable", "ok": False},
    )

    response = TestClient(health_app.app).get("/ready")

    assert response.status_code == 503
    assert response.json()["ledger"] == {"backend": "unavailable", "ok": False}


def test_ready_returns_503_when_required_model_missing(monkeypatch, tmp_path) -> None:
    _write_model(tmp_path / "models", league="EPL")
    _write_staging(tmp_path)
    monkeypatch.setenv("LEAGUES", "EPL,LALIGA")
    monkeypatch.setattr(health_app, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(health_app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(health_app, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(
        "src.infrastructure.persistent_ledger.ledger_healthcheck",
        lambda path=None: {"backend": "local_json", "ok": True},
    )

    response = TestClient(health_app.app).get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["model"]["ready"] is False


def test_missing_required_model_marks_readiness_failed(monkeypatch, tmp_path) -> None:
    _write_model(tmp_path / "models", league="EPL")
    _write_staging(tmp_path)
    monkeypatch.setenv("LEAGUES", "EPL,LALIGA")
    monkeypatch.setattr(health_app, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(health_app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(health_app, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(
        "src.infrastructure.persistent_ledger.ledger_healthcheck",
        lambda path=None: {"backend": "supabase", "ok": True},
    )

    body = TestClient(health_app.app).get("/ready").json()

    assert body["status"] == "not_ready"
    assert body["checks"]["model"]["ready"] is False


def test_ready_returns_503_when_production_config_missing(monkeypatch, tmp_path) -> None:
    _write_model(tmp_path / "models", league="EPL")
    _write_staging(tmp_path)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LEAGUES", "EPL")
    monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(health_app, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(health_app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(health_app, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(
        "src.infrastructure.persistent_ledger.ledger_healthcheck",
        lambda path=None: {"backend": "supabase", "ok": True},
    )

    response = TestClient(health_app.app).get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["production_config"]["ready"] is False


def test_ready_output_redacts_secrets(monkeypatch, tmp_path) -> None:
    _write_model(tmp_path / "models", league="EPL")
    _write_staging(tmp_path)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LEAGUES", "EPL")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:password@example/db")
    monkeypatch.setenv("ADMIN_API_TOKEN", "admin-secret")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setattr(health_app, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(health_app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(health_app, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(
        "src.infrastructure.persistent_ledger.ledger_healthcheck",
        lambda path=None: {"backend": "supabase", "ok": True},
    )

    body = TestClient(health_app.app).get("/ready").text

    assert "admin-secret" not in body
    assert "webhook-secret" not in body
    assert "password" not in body


def test_health_output_redacts_secrets(monkeypatch, tmp_path) -> None:
    test_ready_output_redacts_secrets(monkeypatch, tmp_path)
