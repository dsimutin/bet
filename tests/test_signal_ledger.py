from __future__ import annotations

from src.models.signal_ledger import SignalLedger


def _signal(signal_id: str = "sig_1") -> dict:
    return {
        "signal_id": signal_id,
        "strategy_id": "consensus_value_poisson_v1",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "bookmaker": "bet365",
        "market_key": "h2h",
        "selection": "home",
        "entry_odds": 1.8,
        "reference_fair_odds": 1.7,
        "edge_pct": 5.88,
        "timestamp_utc": "2026-05-27T12:00:00+00:00",
        "status": "paper",
        "dataset_hash": "sha256:abc123test",
    }


def test_signal_ledger_adds_signals_and_filters_duplicates(tmp_path) -> None:
    ledger = SignalLedger()

    result = ledger.add_signals([_signal(), _signal()])
    path = ledger.save(tmp_path / "ledger.json")
    reloaded = SignalLedger.load_or_create(path)

    assert len(result.added) == 1
    assert len(result.duplicates) == 1
    assert reloaded.has_signal("sig_1")
    assert reloaded.summary()["total_signals"] == 1
    assert reloaded.summary()["open_signals"] == 1
    assert reloaded.get("sig_1")["delivery_status"] == "registered"


def test_signal_ledger_updates_result_and_metrics() -> None:
    ledger = SignalLedger()
    ledger.add_signal(_signal(), stake_units=2.0)

    ledger.update_result("sig_1", result="win", closing_odds=1.7)

    record = ledger.get("sig_1")
    summary = ledger.summary()
    assert record["ledger_status"] == "settled"
    assert record["pnl_units"] == 1.6
    assert record["clv_pct"] == round((1.8 / 1.7 - 1) * 100, 4)
    assert summary["settled_signals"] == 1
    assert summary["win_rate"] == 1.0
    assert summary["roi_pct"] == 80.0


def test_signal_ledger_uses_signal_stake_units() -> None:
    ledger = SignalLedger()
    signal = _signal()
    signal["paper_stake_units"] = 1.75

    ledger.add_signals([signal])

    assert ledger.get("sig_1")["stake_units"] == 1.75


def test_signal_ledger_marks_void_without_turnover() -> None:
    ledger = SignalLedger()
    ledger.add_signal(_signal())

    ledger.update_result("sig_1", result="void")

    assert ledger.get("sig_1")["ledger_status"] == "void"
    assert ledger.summary()["void_signals"] == 1
    assert ledger.summary()["settled_signals"] == 0


def test_signal_ledger_marks_delivery_and_reports_quality() -> None:
    ledger = SignalLedger()
    ledger.add_signal(_signal("sig_win"))
    ledger.add_signal(_signal("sig_loss"))
    ledger.mark_delivery("sig_win", status="sent", delivery_result={"ok": True})
    ledger.mark_delivery("sig_loss", status="sent", delivery_result={"ok": True})
    ledger.update_result("sig_win", result="win")
    ledger.update_result("sig_loss", result="loss")

    report = ledger.delivery_quality_report(
        min_settled=2,
        min_win_rate=0.55,
        min_roi_pct=0.0,
    )

    assert ledger.summary()["delivered_signals"] == 2
    assert report["passed"] is False
    assert report["summary"]["settled_delivered_signals"] == 2
    assert report["summary"]["win_rate"] == 0.5


def test_signal_ledger_quality_report_allows_warmup() -> None:
    ledger = SignalLedger()

    report = ledger.delivery_quality_report(
        min_settled=20,
        min_win_rate=0.55,
        min_roi_pct=0.0,
    )

    assert report["passed"] is True
    assert report["warmup"] is True
    assert report["reason"] == "warmup"
