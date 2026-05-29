"""CUSUM-based concept drift detector for sports prediction model.

Two APIs:
  Batch API  — reads from SignalLedger after settlement (production use).
  Stream API — add_prediction() per result (testing / simulation).

Usage (batch):
    from src.monitoring.drift_detector import CUSUMDriftDetector
    detector = CUSUMDriftDetector()
    report = detector.evaluate_ledger(ledger)

Usage (stream):
    detector = CUSUMDriftDetector(threshold=0.15, drift_window=14)
    for result in results:
        detector.add_prediction(predicted_prob=0.6, actual_outcome=result)
    if detector.drift_detected:
        kelly = detector.get_kelly_multiplier()  # 0.5
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DriftReport:
    drift_detected: bool
    kelly_multiplier: float
    reason: str
    recent_accuracy: float | None
    baseline_accuracy: float | None
    cusum_value: float
    threshold: float
    n_recent: int
    n_baseline: int
    retrain_recommended: bool
    generated_at_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CUSUMDriftDetector:
    """
    Sliding-window CUSUM detector with both batch (ledger) and stream APIs.

    Drift is declared when baseline_accuracy − recent_accuracy > threshold.
    """

    def __init__(
        self,
        threshold: float = 0.15,
        drift_window: int = 14,
        baseline_multiplier: int = 2,
        min_window: int = 10,
        kelly_on_drift: float = 0.5,
    ) -> None:
        self.threshold = threshold
        self.drift_window = drift_window
        self.baseline_size = drift_window * baseline_multiplier
        self.min_window = min_window
        self.kelly_on_drift = kelly_on_drift

        # Stream API state
        self._stream_errors: list[int] = []   # 0=correct, 1=error
        self._stream_drift: bool = False

    # ------------------------------------------------------------------
    # Stream API (add_prediction / drift_detected / get_kelly_multiplier)
    # ------------------------------------------------------------------

    def add_prediction(self, predicted_prob: float, actual_outcome: int) -> bool:
        """
        Stream API: record one prediction result and check for drift.

        Args:
            predicted_prob: Model's predicted probability for the positive outcome.
            actual_outcome: 1 if prediction was correct, 0 if wrong.

        Returns:
            True if drift was detected after this update.
        """
        error = 0 if actual_outcome == 1 else 1
        self._stream_errors.append(error)
        self._stream_drift = self._check_stream_drift()
        return self._stream_drift

    @property
    def drift_detected(self) -> bool:
        """True if stream drift has been detected (stream API)."""
        return self._stream_drift

    @property
    def retrain_recommended(self) -> bool:
        """True when drift is detected — recommend model retrain."""
        return self._stream_drift

    def get_kelly_multiplier(self) -> float:
        """Return recommended Kelly multiplier: 0.5 on drift, 1.0 otherwise."""
        return self.kelly_on_drift if self._stream_drift else 1.0

    def reset_stream(self) -> None:
        """Clear stream state after a retrain or manual reset."""
        self._stream_errors = []
        self._stream_drift = False

    def _check_stream_drift(self) -> bool:
        errors = self._stream_errors
        total = len(errors)
        need = self.min_window * 2
        if total < need:
            return False
        recent = errors[-self.drift_window :]
        baseline = errors[-self.drift_window - self.baseline_size : -self.drift_window]
        if len(recent) < self.min_window or len(baseline) < self.min_window:
            return False
        recent_acc = 1.0 - sum(recent) / len(recent)
        baseline_acc = 1.0 - sum(baseline) / len(baseline)
        return (baseline_acc - recent_acc) > self.threshold

    def stream_report(self) -> DriftReport:
        """Return a DriftReport from current stream state."""
        errors = self._stream_errors
        total = len(errors)
        need = self.min_window * 2
        if total < need:
            return self._no_data_report(f"insufficient_data: need {need}, got {total}")

        recent = errors[-self.drift_window :]
        baseline = errors[-self.drift_window - self.baseline_size : -self.drift_window]
        if len(recent) < self.min_window or len(baseline) < self.min_window:
            return self._no_data_report("insufficient_window_data")

        recent_acc = 1.0 - sum(recent) / len(recent)
        baseline_acc = 1.0 - sum(baseline) / len(baseline)
        cusum = sum(e - (1.0 - baseline_acc) for e in recent) / len(recent)
        drift = (baseline_acc - recent_acc) > self.threshold

        return DriftReport(
            drift_detected=drift,
            kelly_multiplier=self.kelly_on_drift if drift else 1.0,
            reason=(
                f"accuracy dropped {baseline_acc:.2%}→{recent_acc:.2%} "
                f"(Δ={baseline_acc - recent_acc:.2%} > threshold={self.threshold:.2%})"
                if drift else
                f"no_drift: baseline={baseline_acc:.2%} recent={recent_acc:.2%}"
            ),
            recent_accuracy=round(recent_acc, 4),
            baseline_accuracy=round(baseline_acc, 4),
            cusum_value=round(cusum, 4),
            threshold=self.threshold,
            n_recent=len(recent),
            n_baseline=len(baseline),
            retrain_recommended=drift,
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def evaluate_ledger(self, ledger: Any) -> DriftReport:
        """Evaluate drift from a SignalLedger instance."""
        entries = _settled_entries_sorted(ledger.entries())
        return self._evaluate_entries(entries)

    def evaluate_ledger_path(self, path: Path) -> DriftReport:
        """Evaluate drift from a ledger JSON file path."""
        if not path.exists():
            return self._no_data_report("ledger_not_found")
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries_raw = raw.get("entries", {})
        entries = _settled_entries_sorted(entries_raw)
        return self._evaluate_entries(entries)

    # ------------------------------------------------------------------
    # Core detection logic
    # ------------------------------------------------------------------

    def _evaluate_entries(self, entries: list[dict[str, Any]]) -> DriftReport:
        if len(entries) < self.min_window * 2:
            return self._no_data_report(
                f"insufficient_data: need {self.min_window * 2}, got {len(entries)}"
            )

        # Take most recent drift_window as "recent" and baseline_size before that
        recent = entries[-self.drift_window :]
        baseline = entries[-self.drift_window - self.baseline_size : -self.drift_window]

        if len(recent) < self.min_window:
            return self._no_data_report(
                f"insufficient_recent: need {self.min_window}, got {len(recent)}"
            )
        if len(baseline) < self.min_window:
            return self._no_data_report(
                f"insufficient_baseline: need {self.min_window}, got {len(baseline)}"
            )

        recent_acc = _accuracy(recent)
        baseline_acc = _accuracy(baseline)

        # CUSUM: accumulate over recent errors vs baseline error rate
        baseline_error_rate = 1.0 - baseline_acc
        cusum = 0.0
        for entry in recent:
            error = 0.0 if entry.get("result") == "win" else 1.0
            cusum += error - baseline_error_rate
        cusum_normalised = cusum / len(recent)

        drift = (baseline_acc - recent_acc) > self.threshold

        if drift:
            reason = (
                f"accuracy dropped {baseline_acc:.2%}→{recent_acc:.2%} "
                f"(Δ={baseline_acc - recent_acc:.2%} > threshold={self.threshold:.2%})"
            )
        else:
            reason = (
                f"no_drift: baseline={baseline_acc:.2%} recent={recent_acc:.2%} "
                f"Δ={baseline_acc - recent_acc:.2%} ≤ {self.threshold:.2%}"
            )

        return DriftReport(
            drift_detected=drift,
            kelly_multiplier=self.kelly_on_drift if drift else 1.0,
            reason=reason,
            recent_accuracy=round(recent_acc, 4),
            baseline_accuracy=round(baseline_acc, 4),
            cusum_value=round(cusum_normalised, 4),
            threshold=self.threshold,
            n_recent=len(recent),
            n_baseline=len(baseline),
            retrain_recommended=drift,
        )

    def _no_data_report(self, reason: str) -> DriftReport:
        return DriftReport(
            drift_detected=False,
            kelly_multiplier=1.0,
            reason=reason,
            recent_accuracy=None,
            baseline_accuracy=None,
            cusum_value=0.0,
            threshold=self.threshold,
            n_recent=0,
            n_baseline=0,
            retrain_recommended=False,
        )

    # ------------------------------------------------------------------
    # Rolling accuracy helper (for external reporting)
    # ------------------------------------------------------------------

    def rolling_accuracy(
        self,
        ledger: Any,
        window_days: int = 14,
    ) -> dict[str, Any]:
        """Compute rolling accuracy over the last ``window_days`` calendar days."""
        entries = _settled_entries_sorted(ledger.entries())
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        recent = [
            e
            for e in entries
            if _entry_ts(e) is not None and _entry_ts(e) >= cutoff
        ]
        if not recent:
            return {"window_days": window_days, "n_bets": 0, "accuracy": None, "roi_pct": None}

        wins = sum(1 for e in recent if e.get("result") == "win")
        pnl = sum(float(e.get("pnl_units") or 0.0) for e in recent)
        turnover = sum(float(e.get("stake_units") or 1.0) for e in recent)
        return {
            "window_days": window_days,
            "n_bets": len(recent),
            "accuracy": round(wins / len(recent), 4),
            "roi_pct": round(pnl / turnover * 100, 4) if turnover else 0.0,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_report(self, report: DriftReport, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _settled_entries_sorted(entries: dict[str, Any]) -> list[dict[str, Any]]:
    settled = [
        e
        for e in entries.values()
        if e.get("ledger_status") == "settled" and e.get("result") in ("win", "loss")
    ]
    settled.sort(key=lambda e: e.get("ledger_updated_at_utc") or "")
    return settled


def _accuracy(entries: list[dict[str, Any]]) -> float:
    if not entries:
        return 0.0
    wins = sum(1 for e in entries if e.get("result") == "win")
    return wins / len(entries)


def _entry_ts(entry: dict[str, Any]) -> datetime | None:
    raw = entry.get("ledger_updated_at_utc") or entry.get("ledger_created_at_utc")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
