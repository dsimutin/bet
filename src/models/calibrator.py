"""Probability calibration for 1X2 model outputs."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize


@dataclass(frozen=True)
class CalibrationParams:
    temperature: float
    bias: tuple[float, float, float]


class ProbabilityCalibrator:
    """Multiclass Platt-style calibration using temperature and class biases."""

    def __init__(self) -> None:
        self.params = CalibrationParams(temperature=1.0, bias=(0.0, 0.0, 0.0))

    def fit(self, predicted_probs: np.ndarray, outcomes: np.ndarray) -> None:
        probs = _validate_probs(predicted_probs)
        y = np.asarray(outcomes, dtype=int)
        if probs.shape[0] != y.shape[0]:
            raise ValueError("predicted_probs and outcomes length mismatch")
        if probs.shape[0] == 0:
            raise ValueError("Cannot fit calibrator on empty data")

        logits = np.log(np.clip(probs, 1e-9, 1.0))

        def objective(vector: np.ndarray) -> float:
            temperature = 0.1 + np.exp(vector[0])
            bias = vector[1:4]
            calibrated = _softmax(logits / temperature + bias)
            selected = calibrated[np.arange(len(y)), y]
            return float(-np.mean(np.log(np.clip(selected, 1e-12, 1.0))))

        result = minimize(objective, np.zeros(4, dtype=float), method="L-BFGS-B")
        vector = result.x if result.success else np.zeros(4, dtype=float)
        temperature = float(0.1 + np.exp(vector[0]))
        bias = (float(vector[1]), float(vector[2]), float(vector[3]))
        self.params = CalibrationParams(temperature=temperature, bias=bias)

    def calibrate(self, raw_probs: np.ndarray) -> np.ndarray:
        probs = _validate_probs(raw_probs)
        logits = np.log(np.clip(probs, 1e-9, 1.0))
        bias = np.asarray(self.params.bias, dtype=float)
        return _softmax(logits / self.params.temperature + bias)

    def brier_score(self, predicted: np.ndarray, outcomes: np.ndarray) -> float:
        probs = _validate_probs(predicted)
        y = np.asarray(outcomes, dtype=int)
        if probs.shape[0] != y.shape[0]:
            raise ValueError("predicted and outcomes length mismatch")
        actual = np.zeros_like(probs)
        actual[np.arange(len(y)), y] = 1.0
        return float(np.mean(np.sum((probs - actual) ** 2, axis=1)))

    def log_loss(self, predicted: np.ndarray, outcomes: np.ndarray) -> float:
        probs = _validate_probs(predicted)
        y = np.asarray(outcomes, dtype=int)
        if probs.shape[0] != y.shape[0]:
            raise ValueError("predicted and outcomes length mismatch")
        selected = probs[np.arange(len(y)), y]
        return float(-np.mean(np.log(np.clip(selected, 1e-12, 1.0))))


def _validate_probs(probs: np.ndarray) -> np.ndarray:
    arr = np.asarray(probs, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError("Expected probabilities with shape (N, 3)")
    arr = np.clip(arr, 1e-9, 1.0)
    return arr / arr.sum(axis=1, keepdims=True)


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - values.max(axis=1, keepdims=True)
    exp_values = np.exp(shifted)
    return exp_values / exp_values.sum(axis=1, keepdims=True)
