from __future__ import annotations

from src.models.settle_tennis_signals import _settle_spread


def test_bo3_favorite_2_0_minus_1_5_wins() -> None:
    row = {"score": "6-4 6-4"}
    assert _settle_spread(row, "Player A", True, {"handicap": -1.5}) == "win"


def test_bo3_favorite_2_1_minus_1_5_loses() -> None:
    row = {"score": "6-4 4-6 6-4"}
    assert _settle_spread(row, "Player A", True, {"handicap": -1.5}) == "loss"


def test_bo3_underdog_loses_1_2_plus_1_5_wins() -> None:
    row = {"score": "6-4 4-6 6-4"}
    assert _settle_spread(row, "Player B", False, {"handicap": 1.5}) == "win"


def test_bo3_player_2_0_minus_2_pushes() -> None:
    row = {"score": "6-4 6-4"}
    assert _settle_spread(row, "Player A", True, {"handicap": -2.0}) == "push"


def test_bo5_favorite_3_1_minus_1_5_wins() -> None:
    row = {"score": "6-4 6-4 4-6 6-4"}
    assert _settle_spread(row, "Player A", True, {"handicap": -1.5}) == "win"
