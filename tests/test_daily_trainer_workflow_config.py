from pathlib import Path

WORKFLOW_PATH = Path(".github/workflows/daily-trainer.yml")


def test_daily_trainer_workflow_persists_model_state() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "contents: write" in workflow
    assert "Persist model state" in workflow
    # Multi-league matrix: patterns are league-scoped (dc_${league}_*.pkl etc.)
    assert ".pkl" in workflow
    assert ".meta.json" in workflow
    assert "calibration_" in workflow
    assert "data/reports/daily_training_" in workflow
    assert "--download-openfootball" in workflow
    assert "persist daily model state" in workflow
    assert "git push" in workflow
    # Strategy matrix covers all supported leagues
    assert "EPL" in workflow
    assert "BUNDESLIGA" in workflow
    assert "LALIGA" in workflow
    assert "SERIEA" in workflow
