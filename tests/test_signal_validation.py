"""Tests for production SignalLedger and signal validation logic.

Previously tested stub classes — now tests the real production code.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.models.signal_ledger import SignalLedger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signal(
    signal_id: str = "sig_20250615_000001",
    entry_odds: float = 2.10,
    stake: float = 1.0,
    selection: str = "H",
) -> dict:
    return {
        "signal_id": signal_id,
        "dataset_hash": "sha256:aabbccdd",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "selection": selection,
        "entry_odds": entry_odds,
        "stake_units": stake,
    }


# ---------------------------------------------------------------------------
# add_signal
# ---------------------------------------------------------------------------


def test_add_signal_requires_signal_id() -> None:
    ledger = SignalLedger()
    with pytest.raises(ValueError, match="signal_id is required"):
        ledger.add_signal({"dataset_hash": "sha256:xx"})


def test_add_signal_requires_dataset_hash() -> None:
    ledger = SignalLedger()
    with pytest.raises(ValueError, match="dataset_hash is required"):
        ledger.add_signal({"signal_id": "sig_20250615_000001"})


def test_add_signal_succeeds_and_status_open() -> None:
    ledger = SignalLedger()
    added = ledger.add_signal(_signal())
    assert added is True
    entry = ledger.get("sig_20250615_000001")
    assert entry["ledger_status"] == "open"
    assert entry["result"] is None
    assert entry["pnl_units"] is None


def test_add_signal_deduplicates() -> None:
    ledger = SignalLedger()
    ledger.add_signal(_signal())
    again = ledger.add_signal(_signal())
    assert again is False
    assert len(ledger.entries()) == 1


# ---------------------------------------------------------------------------
# update_result
# ---------------------------------------------------------------------------


def test_update_result_win_computes_pnl_and_clv() -> None:
    ledger = SignalLedger()
    ledger.add_signal(_signal(entry_odds=2.10, stake=1.0))
    ledger.update_result("sig_20250615_000001", result="win", closing_odds=1.95)

    entry = ledger.get("sig_20250615_000001")
    assert entry["ledger_status"] == "settled"
    assert entry["result"] == "win"
    # P&L = stake * (entry_odds - 1)
    assert abs(entry["pnl_units"] - 1.0 * (2.10 - 1.0)) < 1e-6
    # CLV = (entry / closing - 1) * 100
    expected_clv = (2.10 / 1.95 - 1.0) * 100
    assert abs(entry["clv_pct"] - expected_clv) < 0.01


def test_update_result_loss_computes_pnl() -> None:
    ledger = SignalLedger()
    # Pass stake_units=2.0 via add_signal second argument (signal dict stake_units takes priority)
    sig = _signal(entry_odds=2.10, stake=1.0)
    ledger.add_signal(sig, stake_units=1.0)
    ledger.update_result("sig_20250615_000001", result="loss")

    entry = ledger.get("sig_20250615_000001")
    assert entry["ledger_status"] == "settled"
    assert entry["result"] == "loss"
    assert abs(entry["pnl_units"] - (-1.0)) < 1e-6
    assert entry["clv_pct"] is None


def test_update_result_void_zeroes_pnl() -> None:
    ledger = SignalLedger()
    ledger.add_signal(_signal())
    ledger.update_result("sig_20250615_000001", result="void")

    entry = ledger.get("sig_20250615_000001")
    assert entry["ledger_status"] == "void"
    assert entry["pnl_units"] == 0.0


def test_update_result_unknown_signal_raises() -> None:
    ledger = SignalLedger()
    with pytest.raises(KeyError):
        ledger.update_result("nonexistent", result="win")


def test_update_result_invalid_result_raises() -> None:
    ledger = SignalLedger()
    ledger.add_signal(_signal())
    with pytest.raises(ValueError):
        ledger.update_result("sig_20250615_000001", result="push")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# summary / ROI
# ---------------------------------------------------------------------------


def test_summary_roi_one_win_one_loss() -> None:
    ledger = SignalLedger()
    ledger.add_signal(_signal("sig_20250615_000001", entry_odds=2.0, stake=1.0))
    ledger.add_signal(_signal("sig_20250615_000002", entry_odds=2.0, stake=1.0))
    ledger.update_result("sig_20250615_000001", result="win")
    ledger.update_result("sig_20250615_000002", result="loss")

    summary = ledger.summary()
    # +1.0 (win) − 1.0 (loss) = 0 pnl → 0% ROI
    assert abs(summary["roi_pct"]) < 0.01
    assert summary["settled_signals"] == 2
    assert summary["win_rate"] == 0.5


# ---------------------------------------------------------------------------
# save / load_or_create (atomic write)
# ---------------------------------------------------------------------------


def test_save_and_reload_roundtrip() -> None:
    ledger = SignalLedger()
    ledger.add_signal(_signal())
    ledger.update_result("sig_20250615_000001", result="win", closing_odds=1.90)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ledger.json"
        ledger.save(path)
        assert path.exists()

        reloaded = SignalLedger.load_or_create(path)
        assert reloaded.has_signal("sig_20250615_000001")
        entry = reloaded.get("sig_20250615_000001")
        assert entry["result"] == "win"
        assert entry["ledger_status"] == "settled"


def test_save_is_valid_json() -> None:
    ledger = SignalLedger()
    ledger.add_signal(_signal())

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ledger.json"
        ledger.save(path)
        data = json.loads(path.read_text())
        assert "entries" in data
        assert "version" in data
        assert "summary" in data


def test_load_or_create_returns_empty_for_missing_file() -> None:
    ledger = SignalLedger.load_or_create(Path("/nonexistent/ledger.json"))
    assert len(ledger.entries()) == 0


# ---------------------------------------------------------------------------
# mark_delivery
# ---------------------------------------------------------------------------


def test_mark_delivery_dry_run() -> None:
    ledger = SignalLedger()
    ledger.add_signal(_signal())
    ledger.mark_delivery("sig_20250615_000001", status="dry_run")

    entry = ledger.get("sig_20250615_000001")
    assert entry["delivery_status"] == "dry_run"
    assert entry["delivered_at_utc"] is not None


def test_mark_delivery_invalid_status_raises() -> None:
    ledger = SignalLedger()
    ledger.add_signal(_signal())
    with pytest.raises(ValueError):
        ledger.mark_delivery("sig_20250615_000001", status="unknown")  # type: ignore[arg-type]
