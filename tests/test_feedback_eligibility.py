from __future__ import annotations

import json

from src.models.feedback_policy import FeedbackPolicy, is_feedback_eligible


def _entry(**overrides):
    base = {
        "sport": "football",
        "ledger_status": "settled",
        "result": "win",
        "delivery_status": "sent",
        "recommendation_tier": "priority",
        "timestamp_verification_status": "verified_pre_match",
        "market_key": "h2h",
        "entry_odds": 2.0,
        "pnl_units": 1.0,
        "stake_units": 1.0,
    }
    base.update(overrides)
    return base


def test_blocked_entries_do_not_affect_feedback_policy() -> None:
    policy = FeedbackPolicy({"a": _entry(delivery_status="blocked")})
    assert policy._stats("football", "balanced")["n"] == 0


def test_watchlist_does_not_affect_priority_threshold() -> None:
    entries = {
        f"watch_{i}": _entry(recommendation_tier="watchlist", result="loss", pnl_units=-1.0)
        for i in range(40)
    }
    policy = FeedbackPolicy(entries)

    assert policy._priority_prob("football") == 0.50
    assert policy._stats("football", "balanced")["n"] == 0


def test_experimental_markets_do_not_affect_h2h_feedback() -> None:
    assert not is_feedback_eligible(_entry(market_key="spreads", experimental_market=True))


def test_void_push_expired_do_not_affect_feedback() -> None:
    assert not is_feedback_eligible(_entry(result="push"))
    assert not is_feedback_eligible(_entry(result="void"))
    assert not is_feedback_eligible(_entry(ledger_status="expired"))


def test_priority_h2h_verified_entry_is_feedback_eligible() -> None:
    assert is_feedback_eligible(_entry())


def test_cleanup_script_preserves_original_entries(monkeypatch, tmp_path, capsys) -> None:
    ledger_path = tmp_path / "data" / "core" / "paper_signal_ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps(
            {
                "entries": {
                    "legacy_total": _entry(market="totals", total_threshold=22.5),
                    "valid_h2h": _entry(market="h2h"),
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    from scripts.rebuild_clean_feedback_metrics import main

    main()
    captured = json.loads(capsys.readouterr().out)
    migrated = json.loads(ledger_path.read_text(encoding="utf-8"))

    assert set(migrated["entries"]) == {"legacy_total", "valid_h2h"}
    assert migrated["entries"]["legacy_total"]["legacy_invalid"] is True
    assert captured["total_entries"] == 2
    assert captured["excluded_legacy_invalid_totals_spreads"] == 1
    assert list((tmp_path / "data" / "reports").glob("clean_feedback_metrics_*.json"))
