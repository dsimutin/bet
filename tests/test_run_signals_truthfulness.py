from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.cron.run_signals import run_signal_job
from src.models.signal_ledger import SignalLedger


def _signal(signal_id: str = "sig_1") -> dict:
    event_time = datetime.now(timezone.utc) + timedelta(hours=2)
    snapshot = datetime.now(timezone.utc)
    return {
        "signal_id": signal_id,
        "strategy_id": "unit",
        "sport": "football",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "bookmaker": "bet365",
        "market_key": "h2h",
        "selection": "home",
        "entry_odds": 2.0,
        "reference_fair_odds": 1.8,
        "model_probability": 0.57,
        "edge_pct": 5.0,
        "confidence": "medium",
        "timestamp_utc": snapshot.isoformat(),
        "snapshot_ts_utc": snapshot.isoformat(),
        "event_time_utc": event_time.isoformat(),
        "odds_freshness_tier": "priority",
        "dataset_hash": "sha256:unit",
    }


def test_signal_scan_reports_partial_when_one_source_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LEAGUES", "EPL")
    monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))
    ledger = SignalLedger()

    def league_fails(*args):
        raise RuntimeError("football failed with secret-token")

    result = run_signal_job(
        run_league_func=league_fails,
        run_tennis_func=lambda _model_dir: [],
        run_exotic_func=lambda: [],
        load_ledger_func=lambda _path: ledger,
        save_ledger_func=lambda _ledger, _path: _path,
        notify_priority_func=lambda _signals, _ledger: (0, 0, 0),
    )

    assert result["status"] == "partial"
    assert result["steps"]["football_EPL"]["status"] == "failed"
    assert result["steps"]["tennis"]["status"] == "success"
    assert result["steps"]["ledger_save_after_delivery"]["status"] == "success"


def test_signal_scan_reports_failed_when_authoritative_save_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LEAGUES", "EPL")
    monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))

    def save_fails(*args):
        raise RuntimeError("database unavailable")

    result = run_signal_job(
        run_league_func=lambda *_args: [_signal()],
        run_tennis_func=lambda _model_dir: [],
        run_exotic_func=lambda: [],
        load_ledger_func=lambda _path: SignalLedger(),
        save_ledger_func=save_fails,
        notify_priority_func=lambda _signals, _ledger: (0, 0, 0),
    )

    assert result["status"] == "failed"
    assert result["steps"]["ledger_save_before_delivery"]["status"] == "failed"


def test_signal_scan_reports_failed_when_all_sources_fail(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LEAGUES", "EPL")
    monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))

    def fails(*args):
        raise RuntimeError("source down")

    result = run_signal_job(
        run_league_func=fails,
        run_tennis_func=fails,
        run_exotic_func=fails,
        load_ledger_func=lambda _path: SignalLedger(),
        save_ledger_func=lambda _ledger, _path: _path,
        notify_priority_func=lambda _signals, _ledger: (0, 0, 0),
    )

    assert result["status"] == "failed"
    assert result["steps"]["football_EPL"]["status"] == "failed"
    assert result["steps"]["tennis"]["status"] == "failed"
    assert result["steps"]["exotic"]["status"] == "failed"
