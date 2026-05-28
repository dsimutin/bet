from __future__ import annotations

import json
from pathlib import Path

from src.models.run_daily_bot_smoke import run_smoke


def test_daily_bot_smoke_runs_end_to_end_with_paper_stake(tmp_path) -> None:
    result = run_smoke(output_dir=tmp_path)

    assert result["passed"] is True
    assert result["signals_count"] >= 1
    assert result["telegram_payload_paths"]
    assert result["settled_previous_signals"] == 1
    assert result["paper_stakes"]
    assert all(stake is not None and stake > 0 for stake in result["paper_stakes"])

    payload = json.loads(Path(result["telegram_payload_paths"][0]).read_text(encoding="utf-8"))
    assert "Paper stake:" in payload["payload"]["message"]
    assert payload["payload"]["send_result"]["dry_run"] is True

    ledger = json.loads(Path(result["ledger_path"]).read_text(encoding="utf-8"))
    assert ledger["entries"]
    assert any(item["ledger_status"] == "settled" for item in ledger["entries"].values())
    assert any(item["delivery_status"] == "dry_run" for item in ledger["entries"].values())
    assert all(item["stake_units"] > 0 for item in ledger["entries"].values())
