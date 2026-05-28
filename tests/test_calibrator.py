from __future__ import annotations

import numpy as np

from src.models.calibrator import ProbabilityCalibrator


def test_brier_score_below_0_25_for_good_predictions() -> None:
    calibrator = ProbabilityCalibrator()
    probs = np.asarray(
        [
            [0.9, 0.05, 0.05],
            [0.1, 0.8, 0.1],
            [0.1, 0.1, 0.8],
            [0.85, 0.1, 0.05],
        ]
    )
    outcomes = np.asarray([0, 1, 2, 0])

    assert calibrator.brier_score(probs, outcomes) < 0.25


def test_calibrated_probs_sum_to_one() -> None:
    calibrator = ProbabilityCalibrator()
    probs = np.asarray(
        [
            [0.7, 0.2, 0.1],
            [0.2, 0.5, 0.3],
            [0.2, 0.2, 0.6],
            [0.6, 0.25, 0.15],
        ]
    )
    outcomes = np.asarray([0, 1, 2, 0])

    calibrator.fit(probs, outcomes)
    calibrated = calibrator.calibrate(probs)

    assert calibrated.shape == (4, 3)
    assert np.allclose(calibrated.sum(axis=1), 1.0)


def test_log_loss_is_lower_for_good_predictions() -> None:
    calibrator = ProbabilityCalibrator()
    outcomes = np.asarray([0, 1, 2])
    good = np.asarray([[0.9, 0.05, 0.05], [0.1, 0.8, 0.1], [0.1, 0.1, 0.8]])
    bad = np.asarray([[0.05, 0.9, 0.05], [0.8, 0.1, 0.1], [0.8, 0.1, 0.1]])

    assert calibrator.log_loss(good, outcomes) < calibrator.log_loss(bad, outcomes)
