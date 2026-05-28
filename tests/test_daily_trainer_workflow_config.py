from pathlib import Path

WORKFLOW_PATH = Path(".github/workflows/daily-trainer.yml")


def test_daily_trainer_workflow_persists_model_state() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "contents: write" in workflow
    assert "Persist model state" in workflow
    assert "data/models/*.pkl" in workflow
    assert "data/models/*.meta.json" in workflow
    assert "data/models/calibration_*.pkl" in workflow
    assert "data/reports/daily_training_*.json" in workflow
    assert "HISTORY_SOURCE" in workflow
    assert "--download-openfootball" in workflow
    assert 'git commit -m "chore: persist daily model state"' in workflow
    assert "git push" in workflow
