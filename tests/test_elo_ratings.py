"""Tests for EloRatingSystem — correctness, anti-leakage, persistence."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.features.elo_ratings import EloRatingSystem


@pytest.fixture()
def simple_history() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Date": "01/01/2024",
                "HomeTeam": "Arsenal",
                "AwayTeam": "Chelsea",
                "FTHG": 2,
                "FTAG": 1,
            },
            {
                "Date": "08/01/2024",
                "HomeTeam": "Chelsea",
                "AwayTeam": "Arsenal",
                "FTHG": 0,
                "FTAG": 3,
            },
            {
                "Date": "15/01/2024",
                "HomeTeam": "Arsenal",
                "AwayTeam": "Liverpool",
                "FTHG": 1,
                "FTAG": 1,
            },
            {
                "Date": "22/01/2024",
                "HomeTeam": "Liverpool",
                "AwayTeam": "Chelsea",
                "FTHG": 2,
                "FTAG": 0,
            },
        ]
    )


class TestEloBasics:
    def test_initial_rating(self):
        elo = EloRatingSystem(initial_rating=1500.0)
        assert elo.get("UnknownTeam") == 1500.0

    def test_home_win_increases_home_rating(self):
        elo = EloRatingSystem(k_factor=32, home_advantage=0)
        elo.update("TeamA", "TeamB", home_goals=2, away_goals=0)
        assert elo.get("TeamA") > 1500.0
        assert elo.get("TeamB") < 1500.0

    def test_draw_balanced_teams(self):
        elo = EloRatingSystem(k_factor=32, home_advantage=0)
        before_a, before_b = elo.get("A"), elo.get("B")
        elo.update("A", "B", home_goals=1, away_goals=1)
        # With equal ratings, draw should have minimal effect
        assert abs(elo.get("A") - before_a) < 2.0
        assert abs(elo.get("B") - before_b) < 2.0

    def test_elo_diff(self, simple_history):
        elo = EloRatingSystem()
        elo.build_from_matches(simple_history)
        # Arsenal won twice; should be ahead of Chelsea
        assert elo.elo_diff("Arsenal", "Chelsea") > 0

    def test_elo_win_prob_range(self):
        elo = EloRatingSystem()
        prob = elo.elo_win_prob("TeamA", "TeamB")
        assert 0.0 < prob < 1.0


class TestAntiLeakage:
    def test_cutoff_excludes_future_matches(self, simple_history):
        elo_full = EloRatingSystem().build_from_matches(simple_history)
        elo_cut = EloRatingSystem().build_from_matches(
            simple_history, cutoff_date=date(2024, 1, 10)
        )
        # Full uses 4 matches; cutoff only 2
        assert elo_cut._n_matches < elo_full._n_matches
        assert elo_cut._n_matches == 2

    def test_empty_result_when_all_future(self):
        df = pd.DataFrame(
            [{"Date": "01/06/2026", "HomeTeam": "A", "AwayTeam": "B", "FTHG": 1, "FTAG": 0}]
        )
        elo = EloRatingSystem().build_from_matches(df, cutoff_date=date(2024, 1, 1))
        assert elo._n_matches == 0
        assert elo.get("A") == 1500.0


class TestFeatureGeneration:
    def test_add_elo_features(self, simple_history):
        elo = EloRatingSystem().build_from_matches(simple_history)
        candidates = pd.DataFrame(
            [
                {"home_team": "Arsenal", "away_team": "Chelsea"},
                {"home_team": "Liverpool", "away_team": "Arsenal"},
            ]
        )
        result = elo.add_elo_features(candidates)
        assert "elo_home" in result.columns
        assert "elo_away" in result.columns
        assert "elo_diff" in result.columns
        assert "elo_win_prob_home" in result.columns
        assert result["elo_win_prob_home"].between(0, 1).all()

    def test_elo_diff_sign(self, simple_history):
        elo = EloRatingSystem(home_advantage=0).build_from_matches(simple_history)
        df = pd.DataFrame([{"home_team": "Arsenal", "away_team": "Chelsea"}])
        result = elo.add_elo_features(df)
        assert result.loc[0, "elo_diff"] == pytest.approx(
            elo.get("Arsenal") - elo.get("Chelsea"), abs=0.01
        )


class TestPersistence:
    def test_save_and_load(self, simple_history, tmp_path):
        elo = EloRatingSystem(k_factor=24).build_from_matches(simple_history)
        path = tmp_path / "elo.json"
        elo.save(path)
        loaded = EloRatingSystem.load(path)

        assert loaded.k == 24.0
        assert loaded._n_matches == elo._n_matches
        for team in ("Arsenal", "Chelsea", "Liverpool"):
            assert loaded.get(team) == pytest.approx(elo.get(team), abs=0.001)

    def test_snapshot_contains_required_keys(self, simple_history, tmp_path):
        elo = EloRatingSystem().build_from_matches(simple_history)
        path = tmp_path / "elo.json"
        elo.save(path)
        data = json.loads(path.read_text())
        for key in ("ratings", "n_matches", "k_factor", "home_advantage", "initial_rating"):
            assert key in data
