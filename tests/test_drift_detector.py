"""Tests for CUSUMDriftDetector."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.monitoring.drift_detector import CUSUMDriftDetector, DriftReport


def _make_entries(n_wins: int, n_losses: int, base_ts: datetime | None = None) -> dict:
    """Build a minimal ledger entries dict for testing."""
    base = base_ts or datetime(2024, 1, 1, tzinfo=timezone.utc)
    entries = {}
    idx = 0
    for _ in range(n_wins):
        ts = (base + timedelta(hours=idx)).isoformat()
        entries[f"sig_{idx}"] = {
            "ledger_status": "settled", "result": "win",
            "pnl_units": 1.0, "stake_units": 1.0,
            "ledger_updated_at_utc": ts,
        }
        idx += 1
    for _ in range(n_losses):
        ts = (base + timedelta(hours=idx)).isoformat()
        entries[f"sig_{idx}"] = {
            "ledger_status": "settled", "result": "loss",
            "pnl_units": -1.0, "stake_units": 1.0,
            "ledger_updated_at_utc": ts,
        }
        idx += 1
    return entries


class FakeLedger:
    def __init__(self, entries: dict):
        self._entries = entries

    def entries(self) -> dict:
        return self._entries


def _make_uniform_entries(n_total: int, win_every_n: int) -> dict:
    """Build entries alternating win/loss at a fixed cadence (stable accuracy)."""
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    entries = {}
    for i in range(n_total):
        result = "win" if i % win_every_n != 0 else "loss"
        ts = (base + timedelta(hours=i)).isoformat()
        entries[f"s_{i}"] = {
            "ledger_status": "settled", "result": result,
            "pnl_units": 1.0 if result == "win" else -1.0,
            "stake_units": 1.0,
            "ledger_updated_at_utc": ts,
        }
    return entries


class TestNoDriftCase:
    def test_stable_accuracy_no_drift(self):
        # Alternating pattern: loss every 3rd → ~67% win rate, stable across all windows
        entries = _make_uniform_entries(n_total=60, win_every_n=3)
        ledger = FakeLedger(entries)
        detector = CUSUMDriftDetector(threshold=0.20, drift_window=14, min_window=10)
        report = detector.evaluate_ledger(ledger)
        assert isinstance(report, DriftReport)
        assert not report.drift_detected
        assert report.kelly_multiplier == pytest.approx(1.0)

    def test_returns_no_drift_on_insufficient_data(self):
        entries = _make_entries(n_wins=3, n_losses=2)
        ledger = FakeLedger(entries)
        detector = CUSUMDriftDetector(threshold=0.15, drift_window=14, min_window=10)
        report = detector.evaluate_ledger(ledger)
        assert not report.drift_detected
        assert "insufficient" in report.reason


class TestDriftDetection:
    def test_detects_drift_when_accuracy_drops(self):
        # Build baseline with many wins, then recent with many losses
        base_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)

        entries = {}
        # 30 wins (baseline window)
        for i in range(30):
            ts = (base_ts + timedelta(hours=i)).isoformat()
            entries[f"baseline_{i}"] = {
                "ledger_status": "settled", "result": "win",
                "pnl_units": 1.0, "stake_units": 1.0,
                "ledger_updated_at_utc": ts,
            }
        # 14 losses (recent window)
        for i in range(14):
            ts = (base_ts + timedelta(hours=30 + i)).isoformat()
            entries[f"recent_{i}"] = {
                "ledger_status": "settled", "result": "loss",
                "pnl_units": -1.0, "stake_units": 1.0,
                "ledger_updated_at_utc": ts,
            }

        ledger = FakeLedger(entries)
        detector = CUSUMDriftDetector(threshold=0.15, drift_window=14, min_window=10)
        report = detector.evaluate_ledger(ledger)

        assert report.drift_detected
        assert report.kelly_multiplier == pytest.approx(0.5)
        assert report.retrain_recommended
        assert report.recent_accuracy == pytest.approx(0.0, abs=0.01)
        assert report.baseline_accuracy == pytest.approx(1.0, abs=0.05)

    def test_kelly_multiplier_is_05_on_drift(self):
        entries = {}
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        for i in range(30):
            entries[f"w{i}"] = {
                "ledger_status": "settled", "result": "win",
                "pnl_units": 1.0, "stake_units": 1.0,
                "ledger_updated_at_utc": (base + timedelta(hours=i)).isoformat(),
            }
        for i in range(14):
            entries[f"l{i}"] = {
                "ledger_status": "settled", "result": "loss",
                "pnl_units": -1.0, "stake_units": 1.0,
                "ledger_updated_at_utc": (base + timedelta(hours=30 + i)).isoformat(),
            }
        detector = CUSUMDriftDetector(drift_window=14, min_window=10, kelly_on_drift=0.5)
        report = detector.evaluate_ledger(FakeLedger(entries))
        assert report.kelly_multiplier == pytest.approx(0.5)


class TestLedgerPath:
    def test_missing_ledger_returns_no_drift(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        detector = CUSUMDriftDetector()
        report = detector.evaluate_ledger_path(path)
        assert not report.drift_detected
        assert "ledger_not_found" in report.reason

    def test_file_based_evaluation(self, tmp_path):
        entries = _make_entries(n_wins=30, n_losses=5)
        path = tmp_path / "ledger.json"
        path.write_text(
            json.dumps({"entries": entries}), encoding="utf-8"
        )
        detector = CUSUMDriftDetector(threshold=0.15, drift_window=10, min_window=5)
        report = detector.evaluate_ledger_path(path)
        assert isinstance(report, DriftReport)


class TestRollingAccuracy:
    def test_rolling_accuracy_output(self):
        now = datetime.now(timezone.utc)
        entries = {}
        for i in range(10):
            ts = (now - timedelta(days=i)).isoformat()
            entries[f"s{i}"] = {
                "ledger_status": "settled",
                "result": "win" if i % 2 == 0 else "loss",
                "pnl_units": 1.0 if i % 2 == 0 else -1.0,
                "stake_units": 1.0,
                "ledger_updated_at_utc": ts,
            }
        detector = CUSUMDriftDetector()
        result = detector.rolling_accuracy(FakeLedger(entries), window_days=14)
        assert result["n_bets"] == 10
        assert 0.0 <= result["accuracy"] <= 1.0

    def test_rolling_accuracy_zero_bets(self):
        now = datetime.now(timezone.utc)
        entries = {}
        for i in range(5):
            ts = (now - timedelta(days=30 + i)).isoformat()
            entries[f"old_{i}"] = {
                "ledger_status": "settled", "result": "win",
                "pnl_units": 1.0, "stake_units": 1.0,
                "ledger_updated_at_utc": ts,
            }
        detector = CUSUMDriftDetector()
        result = detector.rolling_accuracy(FakeLedger(entries), window_days=7)
        assert result["n_bets"] == 0
        assert result["accuracy"] is None


class TestPersistence:
    def test_save_report(self, tmp_path):
        entries = _make_entries(n_wins=30, n_losses=14)
        detector = CUSUMDriftDetector(drift_window=14, min_window=10)
        report = detector.evaluate_ledger(FakeLedger(entries))
        path = tmp_path / "drift_report.json"
        detector.save_report(report, path)
        data = json.loads(path.read_text())
        assert "drift_detected" in data
        assert "kelly_multiplier" in data
        assert "generated_at_utc" in data
