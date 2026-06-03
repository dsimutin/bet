from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.models.signal_ledger import SignalLedger


def _signal(signal_id: str = "sig_1", event_time_utc: str | None = None) -> dict:
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
        "event_time_utc": event_time_utc or "2026-12-31T15:00:00Z",
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


def test_ledger_pnl_for_win_loss_push_void() -> None:
    ledger = SignalLedger()
    for signal_id in ("win", "loss", "push", "void"):
        ledger.add_signal(_signal(signal_id, event_time_utc="2026-12-31T15:00:00Z"))

    ledger.update_result("win", result="win")
    ledger.update_result("loss", result="loss")
    ledger.update_result("push", result="push")
    ledger.update_result("void", result="void")

    assert ledger.get("win")["pnl_units"] == 0.8
    assert ledger.get("loss")["pnl_units"] == -1.0
    assert ledger.get("push")["pnl_units"] == 0.0
    assert ledger.get("void")["pnl_units"] == 0.0
    summary = ledger.summary()
    assert summary["settled_signals"] == 3
    assert summary["void_signals"] == 1
    assert summary["turnover_units"] == 2.0


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


def test_signal_ledger_marks_failed_delivery_as_not_delivered() -> None:
    ledger = SignalLedger()
    ledger.add_signal(_signal())

    ledger.mark_delivery("sig_1", status="failed", delivery_result={"ok": False})

    assert ledger.summary()["delivered_signals"] == 0
    assert ledger.get("sig_1")["delivery_status"] == "failed"


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


def test_expire_stale_signals_marks_old_open_as_expired() -> None:
    """Open signals whose event passed >24h ago should be auto-expired."""
    past = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    ledger = SignalLedger()
    ledger.add_signal(_signal("old_sig", event_time_utc=past))

    expired = ledger.expire_stale_signals(hours_past_event=24.0)

    assert "old_sig" in expired
    assert ledger.get("old_sig")["ledger_status"] == "expired"
    assert ledger.summary()["expired_signals"] == 1
    assert ledger.summary()["open_signals"] == 0


def test_expire_stale_signals_leaves_future_events_open() -> None:
    """Signals for future events must not be expired."""
    future = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    ledger = SignalLedger()
    ledger.add_signal(_signal("future_sig", event_time_utc=future))

    expired = ledger.expire_stale_signals(hours_past_event=24.0)

    assert expired == []
    assert ledger.get("future_sig")["ledger_status"] == "open"


def test_expire_stale_signals_does_not_touch_settled() -> None:
    """Already settled signals must not be re-expired."""
    past = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    ledger = SignalLedger()
    ledger.add_signal(_signal("settled_sig", event_time_utc=past))
    ledger.update_result("settled_sig", result="win")

    expired = ledger.expire_stale_signals(hours_past_event=24.0)

    assert expired == []
    assert ledger.get("settled_sig")["ledger_status"] == "settled"


def test_expire_stale_signals_just_past_threshold_not_expired() -> None:
    """Signal 23h past event (below 24h threshold) must stay open."""
    recent_past = (datetime.now(timezone.utc) - timedelta(hours=23)).isoformat()
    ledger = SignalLedger()
    ledger.add_signal(_signal("recent_sig", event_time_utc=recent_past))

    expired = ledger.expire_stale_signals(hours_past_event=24.0)

    assert expired == []
    assert ledger.get("recent_sig")["ledger_status"] == "open"
