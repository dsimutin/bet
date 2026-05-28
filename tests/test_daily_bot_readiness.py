from __future__ import annotations

import json

from src.models.daily_bot_readiness import evaluate_readiness
from src.models.run_daily_bot_readiness import main as run_readiness_main


def test_readiness_passes_with_free_source_inbox_file(tmp_path) -> None:
    inbox = tmp_path / "free_sources"
    inbox.mkdir()
    (inbox / "events.txt").write_text(
        "29/05/2026\nArsenal vs Chelsea\n1.90 3.40 4.20\n",
        encoding="utf-8",
    )

    report = evaluate_readiness(
        free_source_dir=inbox,
        free_source_config=tmp_path / "missing.yaml",
        model_dir=tmp_path / "models",
        ledger_path=tmp_path / "core" / "paper_signal_ledger.json",
        env={},
    )

    assert report.passed is True
    assert report.to_dict()["checks"][0]["name"] == "candidate_sources"


def test_readiness_blocks_scheduled_send_without_telegram_secrets(tmp_path) -> None:
    inbox = tmp_path / "free_sources"
    inbox.mkdir()
    (inbox / "events.txt").write_text("Arsenal vs Chelsea 1.90 3.40 4.20\n", encoding="utf-8")

    report = evaluate_readiness(
        free_source_dir=inbox,
        free_source_config=tmp_path / "missing.yaml",
        model_dir=tmp_path / "models",
        ledger_path=tmp_path / "core" / "paper_signal_ledger.json",
        scheduled_send_telegram=True,
        env={},
    )

    assert report.passed is False
    telegram_check = [check for check in report.checks if check.name == "telegram_delivery"][0]
    assert telegram_check.passed is False
    assert "TELEGRAM_BOT_TOKEN" in telegram_check.details


def test_readiness_passes_with_enabled_free_source_config(tmp_path) -> None:
    config = tmp_path / "free_sources.yaml"
    config.write_text(
        """
sources:
  - name: public
    enabled: true
    type: url
    url: https://example.com/odds.txt
    format: txt
""",
        encoding="utf-8",
    )

    report = evaluate_readiness(
        free_source_dir=tmp_path / "empty",
        free_source_config=config,
        model_dir=tmp_path / "models",
        ledger_path=tmp_path / "core" / "paper_signal_ledger.json",
        env={},
    )

    assert report.passed is True


def test_readiness_cli_writes_report(tmp_path, monkeypatch) -> None:
    inbox = tmp_path / "free_sources"
    inbox.mkdir()
    (inbox / "events.txt").write_text("Arsenal vs Chelsea 1.90 3.40 4.20\n", encoding="utf-8")
    report_path = tmp_path / "readiness.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_daily_bot_readiness",
            "--free-source-dir",
            str(inbox),
            "--free-source-config",
            str(tmp_path / "missing.yaml"),
            "--model-dir",
            str(tmp_path / "models"),
            "--ledger-path",
            str(tmp_path / "core" / "paper_signal_ledger.json"),
            "--report-path",
            str(report_path),
        ],
    )

    run_readiness_main()

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["passed"] is True
