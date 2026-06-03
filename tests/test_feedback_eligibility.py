from __future__ import annotations

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


def test_experimental_markets_do_not_affect_h2h_feedback() -> None:
    assert not is_feedback_eligible(_entry(market_key="spreads", experimental_market=True))


def test_void_push_expired_do_not_affect_feedback() -> None:
    assert not is_feedback_eligible(_entry(result="push"))
    assert not is_feedback_eligible(_entry(result="void"))
    assert not is_feedback_eligible(_entry(ledger_status="expired"))


def test_priority_h2h_verified_entry_is_feedback_eligible() -> None:
    assert is_feedback_eligible(_entry())
