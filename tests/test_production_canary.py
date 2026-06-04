from __future__ import annotations

import json

from fastapi.testclient import TestClient

from src.ops.production_canary import run_production_canary
import src.web.health_app as health_app


def _write_model(model_dir, league: str = "EPL") -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / f"dc_{league}.meta.json").write_text(
        json.dumps({"status": "production", "league": league}),
        encoding="utf-8",
    )


def _write_reports(data_dir) -> None:
    reports = data_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "runtime_signals.json").write_text("{}", encoding="utf-8")


def test_canary_fails_when_production_database_is_unavailable(monkeypatch, tmp_path) -> None:
    _write_model(tmp_path / "models")
    _write_reports(tmp_path)
    monkeypatch.setattr(
        "src.infrastructure.persistent_ledger.ledger_healthcheck",
        lambda path=None: {"backend": "unavailable", "ok": False, "error": "db down"},
    )

    result = run_production_canary(
        data_dir=tmp_path,
        model_dir=tmp_path / "models",
        ledger_path=tmp_path / "ledger.json",
        env={
            "APP_ENV": "production",
            "DATABASE_URL": "postgres://secret@example/db",
            "ADMIN_API_TOKEN": "admin",
            "TELEGRAM_WEBHOOK_SECRET": "hook",
            "TELEGRAM_ALLOWED_CHAT_IDS": "123",
            "PAPER_TRADING_ONLY": "true",
            "LEAGUES": "EPL",
            "SPORTS": "football",
            "THE_ODDS_API_KEY": "odds",
        },
    )

    assert result["status"] == "fail"
    assert "ledger_backend" in result["summary"]["failed_critical"]


def test_canary_passes_core_runtime_without_spending_quota(monkeypatch, tmp_path) -> None:
    _write_model(tmp_path / "models")
    _write_reports(tmp_path)
    monkeypatch.setattr(
        "src.infrastructure.persistent_ledger.ledger_healthcheck",
        lambda path=None: {"backend": "supabase", "ok": True},
    )

    result = run_production_canary(
        data_dir=tmp_path,
        model_dir=tmp_path / "models",
        ledger_path=tmp_path / "ledger.json",
        env={
            "APP_ENV": "production",
            "DATABASE_URL": "postgres://secret@example/db",
            "ADMIN_API_TOKEN": "admin",
            "TELEGRAM_WEBHOOK_SECRET": "hook",
            "TELEGRAM_ALLOWED_CHAT_IDS": "123",
            "TELEGRAM_BOT_TOKEN": "bot",
            "TELEGRAM_CHAT_ID": "123",
            "PAPER_TRADING_ONLY": "true",
            "LEAGUES": "EPL",
            "SPORTS": "football",
            "ACTIVE_MODE": "true",
            "THE_ODDS_API_KEY": "odds",
            "THE_ODDS_API_MIN_REMAINING_HARD_STOP": "25",
            "THE_ODDS_API_MIN_REMAINING_PRIORITY_REFRESH": "50",
        },
    )

    assert result["status"] == "pass"
    assert result["summary"]["quota_spent"] is False
    assert result["summary"]["ledger_mutated"] is False


def test_canary_rejects_local_json_as_production_authority(monkeypatch, tmp_path) -> None:
    _write_model(tmp_path / "models")
    _write_reports(tmp_path)
    monkeypatch.setattr(
        "src.infrastructure.persistent_ledger.ledger_healthcheck",
        lambda path=None: {"backend": "local_json", "ok": True},
    )

    result = run_production_canary(
        data_dir=tmp_path,
        model_dir=tmp_path / "models",
        ledger_path=tmp_path / "ledger.json",
        env={
            "APP_ENV": "production",
            "DATABASE_URL": "postgres://secret@example/db",
            "ADMIN_API_TOKEN": "admin",
            "TELEGRAM_WEBHOOK_SECRET": "hook",
            "TELEGRAM_ALLOWED_CHAT_IDS": "123",
            "PAPER_TRADING_ONLY": "true",
            "LEAGUES": "EPL",
            "SPORTS": "football",
            "THE_ODDS_API_KEY": "odds",
        },
    )

    assert result["status"] == "fail"
    assert result["checks"]["ledger_backend"]["status"] == "fail"


def test_health_canary_endpoint_requires_admin_and_reports_status(monkeypatch, tmp_path) -> None:
    _write_model(tmp_path / "models")
    _write_reports(tmp_path)
    monkeypatch.setenv("ADMIN_API_TOKEN", "admin")
    monkeypatch.setenv("LEAGUES", "EPL")
    monkeypatch.setenv("SPORTS", "football")
    monkeypatch.setattr(health_app, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(health_app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(health_app, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(
        "src.infrastructure.persistent_ledger.ledger_healthcheck",
        lambda path=None: {"backend": "local_json", "ok": True},
    )
    client = TestClient(health_app.app)

    assert client.get("/health/canary").status_code == 401
    response = client.get("/health/canary", headers={"Authorization": "Bearer admin"})

    assert response.status_code == 200
    assert response.json()["summary"]["quota_spent"] is False
