from pathlib import Path

WORKFLOW_PATH = Path(".github/workflows/signal-pipeline.yml")


def test_signal_workflow_allows_opt_in_scheduled_telegram_delivery() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "schedule:" in workflow
    assert "SCHEDULED_SEND_TELEGRAM" in workflow
    assert 'GITHUB_EVENT_NAME:-}" = "schedule"' in workflow
    assert 'delivery_arg="--send-telegram"' in workflow


def test_signal_workflow_accepts_free_source_text_exports() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "-name '*.jsonl'" in workflow
    assert "-name '*.ndjson'" in workflow
    assert "-name '*.txt'" in workflow
    assert "--free-source-inbox data/staging/free_sources" in workflow


def test_signal_workflow_ingests_configured_free_sources_before_scan() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "Audit project modules" in workflow
    assert "src.system.run_module_audit" in workflow
    assert "data/reports/module_audit.json" in workflow
    assert "Ingest configured free sources" in workflow
    assert "src.ingest.run_free_source_ingest" in workflow
    assert "--config configs/free_sources.yaml" in workflow
    assert "--output-dir data/staging/free_sources" in workflow
    assert "data/reports/free_source_ingest_report.json" in workflow


def test_signal_workflow_writes_daily_bot_readiness_report() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "Audit daily bot readiness" in workflow
    assert "src.models.run_daily_bot_readiness" in workflow
    assert "data/reports/daily_bot_readiness.json" in workflow
    assert "REQUIRE_CANDIDATE_SOURCES" in workflow


def test_signal_workflow_runs_offline_smoke_before_live_pipeline() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "Run offline bot smoke" in workflow
    assert "src.models.run_daily_bot_smoke" in workflow
    assert "data/reports/smoke" in workflow


def test_signal_workflow_can_train_production_model_from_openfootball() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "PRODUCTION_TRAIN_OPENFOOTBALL" in workflow
    assert "--production-train-openfootball" in workflow
    assert "--openfootball-leagues EPL" in workflow


def test_signal_workflow_persists_models_and_ledger_between_runs() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "contents: write" in workflow
    assert "Persist bot state" in workflow
    assert "data/core/paper_signal_ledger.json" in workflow
    assert "data/models/*.pkl" in workflow
    assert "data/models/*.meta.json" in workflow
    assert "data/models/calibration_*.pkl" in workflow
    assert 'git commit -m "chore: persist daily bot state"' in workflow
    assert "git push" in workflow


def test_gitignore_allows_persisted_bot_state_and_public_free_sources() -> None:
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert "!data/core/paper_signal_ledger.json" in gitignore
    assert "!data/models/*.pkl" in gitignore
    assert "!data/models/*.meta.json" in gitignore
    assert "!data/models/calibration_*.pkl" in gitignore
    assert "!data/staging/free_sources/*.jsonl" in gitignore
    assert "!data/staging/free_sources/*.txt" in gitignore
