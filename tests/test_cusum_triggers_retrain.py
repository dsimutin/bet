"""Integration test: CUSUM detects accuracy decline and triggers retrain signal."""

import pytest
from src.monitoring.drift_detector import CUSUMDriftDetector


def test_cusum_detects_decline_and_triggers_retrain():
    detector = CUSUMDriftDetector(threshold=0.15, drift_window=14, min_window=10)

    # Phase 1: 28 correct predictions → stable baseline (accuracy ~100%)
    for _ in range(28):
        detector.add_prediction(predicted_prob=0.6, actual_outcome=1)

    assert detector.drift_detected is False, "No drift expected during good streak"
    assert detector.get_kelly_multiplier() == 1.0

    # Phase 2: 14 incorrect predictions → accuracy collapses to 0%
    for _ in range(14):
        detector.add_prediction(predicted_prob=0.6, actual_outcome=0)

    assert detector.drift_detected is True, "Drift must be detected after accuracy collapse"
    assert detector.get_kelly_multiplier() == pytest.approx(0.5)
    assert detector.retrain_recommended is True


def test_cusum_no_false_positive_stable_model():
    """Model with consistent ~60% accuracy should not trigger drift."""
    detector = CUSUMDriftDetector(threshold=0.20, drift_window=14, min_window=10)

    # Alternating: win/win/loss → ~67% win rate throughout
    for i in range(60):
        outcome = 0 if i % 3 == 2 else 1
        detector.add_prediction(predicted_prob=0.6, actual_outcome=outcome)

    assert detector.drift_detected is False
    assert detector.get_kelly_multiplier() == pytest.approx(1.0)


def test_cusum_reset_clears_state():
    detector = CUSUMDriftDetector(threshold=0.15, drift_window=14, min_window=10)

    # Force drift
    for _ in range(28):
        detector.add_prediction(predicted_prob=0.6, actual_outcome=1)
    for _ in range(14):
        detector.add_prediction(predicted_prob=0.6, actual_outcome=0)

    assert detector.drift_detected is True

    # Reset simulates post-retrain state
    detector.reset_stream()
    assert detector.drift_detected is False
    assert detector.get_kelly_multiplier() == pytest.approx(1.0)


def test_stream_report_matches_properties():
    detector = CUSUMDriftDetector(threshold=0.15, drift_window=14, min_window=10)

    for _ in range(28):
        detector.add_prediction(predicted_prob=0.7, actual_outcome=1)
    for _ in range(14):
        detector.add_prediction(predicted_prob=0.7, actual_outcome=0)

    report = detector.stream_report()
    assert report.drift_detected == detector.drift_detected
    assert report.kelly_multiplier == detector.get_kelly_multiplier()
    assert report.retrain_recommended == detector.retrain_recommended
    assert report.recent_accuracy == pytest.approx(0.0, abs=0.01)
    assert report.baseline_accuracy == pytest.approx(1.0, abs=0.05)
