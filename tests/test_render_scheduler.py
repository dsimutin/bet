from __future__ import annotations

from pathlib import Path

from src.web.render_scheduler import (
    PipelineRunStatus,
    build_free_source_ingest_command,
    build_readiness_command,
    build_render_pipeline_command,
    build_smoke_command,
    render_scheduler_enabled,
)


def test_render_scheduler_disabled_by_default() -> None:
    assert render_scheduler_enabled({}) is False


def test_render_scheduler_builds_live_pipeline_command_with_render_keys(tmp_path: Path) -> None:
    cmd, reason = build_render_pipeline_command(
        tmp_path,
        {
            "THE_ODDS_API_KEY": "odds-key",
            "TELEGRAM_BOT_TOKEN": "bot-token",
            "TELEGRAM_CHAT_ID": "chat-id",
            "SCHEDULED_SEND_TELEGRAM": "true",
            "LIVE_SPORT_KEYS": "soccer_epl,soccer_spain_la_liga",
        },
    )

    assert reason == "ready"
    assert cmd is not None
    assert "src.models.run_signal_pipeline" in cmd
    assert "--live-odds" in cmd
    assert cmd[cmd.index("--live-sport-keys") + 1] == "soccer_epl,soccer_spain_la_liga"
    assert "--send-telegram" in cmd
    assert "--telegram-payload" not in cmd


def test_render_scheduler_uses_dry_run_payload_without_send_flag(tmp_path: Path) -> None:
    cmd, reason = build_render_pipeline_command(tmp_path, {"THE_ODDS_API_KEY": "odds-key"})

    assert reason == "ready"
    assert cmd is not None
    assert "--telegram-payload" in cmd
    assert "--send-telegram" not in cmd


def test_render_scheduler_skips_when_no_candidate_sources(tmp_path: Path) -> None:
    cmd, reason = build_render_pipeline_command(tmp_path, {})

    assert cmd is None
    assert "No THE_ODDS_API_KEY" in reason


def test_render_scheduler_refuses_send_without_telegram_secrets(tmp_path: Path) -> None:
    cmd, reason = build_render_pipeline_command(
        tmp_path,
        {"THE_ODDS_API_KEY": "odds-key", "SCHEDULED_SEND_TELEGRAM": "true"},
    )

    assert cmd is None
    assert "Telegram secrets are missing" in reason


def test_render_scheduler_has_preflight_ingest_and_readiness_commands(tmp_path: Path) -> None:
    ingest = build_free_source_ingest_command(tmp_path)
    readiness = build_readiness_command(tmp_path, require_candidate_sources=True)

    assert "src.ingest.run_free_source_ingest" in ingest
    assert str(tmp_path / "configs" / "free_sources.yaml") in ingest
    assert str(tmp_path / "data" / "staging" / "free_sources") in ingest
    assert "src.models.run_daily_bot_readiness" in readiness
    assert "--require-ready" in readiness
    assert "--require-candidate-sources" in readiness


def test_render_scheduler_smoke_command_uses_isolated_report_dir(tmp_path: Path) -> None:
    cmd = build_smoke_command(tmp_path)

    assert "src.models.run_daily_bot_smoke" in cmd
    assert str(tmp_path / "data" / "reports" / "smoke") in cmd


def test_pipeline_status_exposes_stage_results() -> None:
    status = PipelineRunStatus()
    payload = status.to_dict()

    assert payload["current_stage"] is None
    assert payload["stage_results"] == []
