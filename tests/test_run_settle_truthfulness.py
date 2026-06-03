from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.cron.run_settle import run_settlement_job
from src.models.signal_ledger import SignalLedger


def _signal(signal_id: str = "sig_1") -> dict:
    return {
        "signal_id": signal_id,
        "strategy_id": "test_strategy",
        "sport": "football",
        "event_date": "2026-06-01",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "bookmaker": "bet365",
        "market_key": "h2h",
        "selection": "home",
        "entry_odds": 1.8,
        "reference_fair_odds": 1.7,
        "edge_pct": 5.0,
        "timestamp_utc": "2026-05-31T12:00:00+00:00",
        "dataset_hash": "sha256:test",
    }


class _Loader:
    def __init__(self) -> None:
        self.dataframe = pd.DataFrame(
            [{"Date": "01/06/2026", "HomeTeam": "Arsenal", "AwayTeam": "Chelsea", "FTR": "H"}]
        )

    def build(self, **kwargs):
        return self

    def save_combined(self, dataframe, staging_dir: Path, filename: str) -> Path:
        staging_dir.mkdir(parents=True, exist_ok=True)
        path = staging_dir / filename
        dataframe.to_csv(path, index=False)
        return path


def _ledger_with_open_signal() -> SignalLedger:
    ledger = SignalLedger()
    ledger.add_signal(_signal())
    return ledger


def test_settlement_partial_failure_reports_partial(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("STAGING_DIR", str(tmp_path / "staging"))
    ledger = _ledger_with_open_signal()

    def football_fails(*args):
        raise RuntimeError("football failed with secret-token")

    result = run_settlement_job(
        load_ledger_func=lambda _path: ledger,
        save_ledger_func=lambda _ledger, _path: _path,
        openfootball_loader_factory=_Loader,
        football_settle_func=football_fails,
        tennis_settle_func=lambda _ledger, _api_key: {"settled": 1, "unmatched": 0},
    )

    assert result["status"] == "partial"
    assert result["steps"]["football_settlement"]["status"] == "failed"
    assert result["steps"]["ledger_save"]["status"] == "success"


def test_settlement_save_failure_reports_failed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("STAGING_DIR", str(tmp_path / "staging"))
    ledger = _ledger_with_open_signal()

    def save_fails(*args):
        raise RuntimeError("database unavailable")

    result = run_settlement_job(
        load_ledger_func=lambda _path: ledger,
        save_ledger_func=save_fails,
        openfootball_loader_factory=_Loader,
        football_settle_func=lambda *_args: {"settled_count": 1, "settled_signals": []},
        tennis_settle_func=lambda _ledger, _api_key: {"settled": 0, "unmatched": 0},
    )

    assert result["status"] == "failed"
    assert result["steps"]["ledger_save"]["status"] == "failed"


def test_settlement_reports_failed_when_authoritative_save_fails(monkeypatch, tmp_path) -> None:
    test_settlement_save_failure_reports_failed(monkeypatch, tmp_path)


def test_settlement_skipped_when_no_open_signals(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("STAGING_DIR", str(tmp_path / "staging"))

    result = run_settlement_job(
        load_ledger_func=lambda _path: SignalLedger(),
        save_ledger_func=lambda _ledger, _path: _path,
        openfootball_loader_factory=_Loader,
        tennis_settle_func=lambda _ledger, _api_key: {"settled": 0, "unmatched": 0},
    )

    assert result["status"] == "skipped"
    assert result["total_settled"] == 0
