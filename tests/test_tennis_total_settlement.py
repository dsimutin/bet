from __future__ import annotations

from src.models.settle_tennis_signals import _settle_total


def test_total_threshold_roundtrip_from_scan_to_settlement() -> None:
    row = {"score": "6-4 6-4"}
    assert _settle_total(row, {"selection": "under", "total_threshold": 22.5}) == "win"


def test_over_total_settlement() -> None:
    row = {"score": "7-6(4) 6-7(5) 7-5"}
    assert _settle_total(row, {"selection": "over", "total_threshold": 30.5}) == "win"


def test_under_total_settlement() -> None:
    row = {"score": "6-3 6-4"}
    assert _settle_total(row, {"selection": "under", "total_threshold": 20.5}) == "win"


def test_integer_total_push() -> None:
    row = {"score": "6-4 6-4"}
    assert _settle_total(row, {"selection": "over", "total_threshold": 20.0}) == "push"


def test_retirement_total_is_void() -> None:
    row = {"score": "6-4 RET"}
    assert _settle_total(row, {"selection": "under", "total_threshold": 20.5}) == "void"


def test_tennis_retirement_is_void() -> None:
    row = {"score": "6-4 RET"}
    assert _settle_total(row, {"selection": "over", "total_threshold": 20.5}) == "void"


def test_tennis_walkover_is_void() -> None:
    row = {"score": "W/O"}
    assert _settle_total(row, {"selection": "under", "total_threshold": 20.5}) == "void"
