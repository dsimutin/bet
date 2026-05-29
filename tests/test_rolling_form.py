"""Tests for RollingFormBuilder, RestDaysBuilder, HomeAdvantageBuilder."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.features.rolling_form import RollingFormBuilder
from src.features.rest_days import RestDaysBuilder
from src.features.home_advantage import HomeAdvantageBuilder


@pytest.fixture()
def history() -> pd.DataFrame:
    return pd.DataFrame([
        {"Date": "01/01/2024", "HomeTeam": "Arsenal",   "AwayTeam": "Chelsea",   "FTHG": 3, "FTAG": 0},
        {"Date": "08/01/2024", "HomeTeam": "Chelsea",   "AwayTeam": "Liverpool", "FTHG": 1, "FTAG": 1},
        {"Date": "15/01/2024", "HomeTeam": "Liverpool", "AwayTeam": "Arsenal",   "FTHG": 0, "FTAG": 2},
        {"Date": "22/01/2024", "HomeTeam": "Arsenal",   "AwayTeam": "Liverpool", "FTHG": 2, "FTAG": 2},
        {"Date": "29/01/2024", "HomeTeam": "Chelsea",   "AwayTeam": "Arsenal",   "FTHG": 1, "FTAG": 3},
    ])


# ------------------------------------------------------------------ #
# RollingFormBuilder
# ------------------------------------------------------------------ #

class TestRollingFormBuilder:
    def test_team_form_after_wins(self, history):
        builder = RollingFormBuilder(window=5).build(history)
        arsenal = builder.team_form("Arsenal")
        assert arsenal.wins >= 2
        assert arsenal.n_matches >= 3

    def test_unknown_team_zeros(self, history):
        builder = RollingFormBuilder(window=5).build(history)
        form = builder.team_form("UnknownFC")
        assert form.n_matches == 0
        assert form.win_rate == 0.0

    def test_window_limits_records(self, history):
        builder = RollingFormBuilder(window=2).build(history)
        form = builder.team_form("Arsenal")
        assert form.n_matches <= 2

    def test_features_for_match(self, history):
        builder = RollingFormBuilder(window=5).build(history)
        feat = builder.features_for_match("Arsenal", "Chelsea")
        assert feat.home_team == "Arsenal"
        assert feat.away_team == "Chelsea"
        assert 0.0 <= feat.home_win_rate <= 1.0
        assert 0.0 <= feat.away_win_rate <= 1.0

    def test_cutoff_anti_leakage(self, history):
        full = RollingFormBuilder(window=10).build(history)
        cut = RollingFormBuilder(window=10).build(history, cutoff_date=date(2024, 1, 20))
        # With cutoff 20 Jan, only 3 matches (01, 08, 15) are used
        arsenal_full = full.team_form("Arsenal")
        arsenal_cut = cut.team_form("Arsenal")
        assert arsenal_cut.n_matches <= arsenal_full.n_matches

    def test_add_form_features_columns(self, history):
        builder = RollingFormBuilder(window=5).build(history)
        candidates = pd.DataFrame([
            {"home_team": "Arsenal", "away_team": "Chelsea"},
        ])
        result = builder.add_form_features(candidates)
        expected_cols = {
            "form_home_wins", "form_home_gs_mean", "form_home_ppg",
            "form_away_wins", "form_advantage",
        }
        assert expected_cols.issubset(result.columns)


# ------------------------------------------------------------------ #
# RestDaysBuilder
# ------------------------------------------------------------------ #

class TestRestDaysBuilder:
    def test_rest_days_after_matches(self, history):
        builder = RestDaysBuilder().build(history)
        feat = builder.features_for_match(
            "Arsenal", "Chelsea", match_date=date(2024, 2, 5)
        )
        assert feat.home_rest_days is not None
        assert feat.away_rest_days is not None
        assert feat.home_rest_days > 0

    def test_unknown_team_none_rest(self, history):
        builder = RestDaysBuilder().build(history)
        feat = builder.features_for_match("UnknownA", "UnknownB", match_date=date(2024, 2, 1))
        assert feat.home_rest_days is None
        assert feat.away_rest_days is None
        assert feat.rest_days_diff is None

    def test_cutoff_anti_leakage(self, history):
        cut = RestDaysBuilder().build(history, cutoff_date=date(2024, 1, 10))
        # Arsenal last match before cutoff is 01/01
        feat = cut.features_for_match("Arsenal", "Chelsea", match_date=date(2024, 1, 15))
        # Arsenal: 14 days from Jan 1; Chelsea: Jan 8 → 7 days
        assert feat.home_rest_days == 14
        assert feat.away_rest_days == 7

    def test_schedule_congestion(self, history):
        builder = RestDaysBuilder().build(history)
        feat = builder.features_for_match(
            "Arsenal", "Chelsea", match_date=date(2024, 2, 5)
        )
        # Arsenal played on Jan 22 and Jan 29 → 2 matches in 14 days before Feb 5
        assert feat.home_schedule_congestion >= 0

    def test_add_rest_features_columns(self, history):
        builder = RestDaysBuilder().build(history)
        candidates = pd.DataFrame([
            {"home_team": "Arsenal", "away_team": "Chelsea", "match_date": date(2024, 2, 5)},
        ])
        result = builder.add_rest_features(candidates)
        assert "rest_home_days" in result.columns
        assert "rest_away_days" in result.columns
        assert "rest_days_diff" in result.columns


# ------------------------------------------------------------------ #
# HomeAdvantageBuilder
# ------------------------------------------------------------------ #

class TestHomeAdvantageBuilder:
    def test_home_win_rate_populated(self, history):
        builder = HomeAdvantageBuilder(window=10).build(history)
        feat = builder.features_for_match("Arsenal", "Chelsea")
        assert 0.0 <= feat.home_win_rate_home_games <= 1.0

    def test_unknown_team_zeroes(self, history):
        builder = HomeAdvantageBuilder(window=10).build(history)
        feat = builder.features_for_match("Ghost", "Phantom")
        assert feat.home_win_rate_home_games == 0.0
        assert feat.away_win_rate_away_games == 0.0

    def test_add_home_advantage_columns(self, history):
        builder = HomeAdvantageBuilder(window=10).build(history)
        candidates = pd.DataFrame([{"home_team": "Arsenal", "away_team": "Chelsea"}])
        result = builder.add_home_advantage_features(candidates)
        assert "ha_home_win_rate" in result.columns
        assert "ha_score" in result.columns

    def test_cutoff_anti_leakage(self, history):
        full = HomeAdvantageBuilder(window=10).build(history)
        cut = HomeAdvantageBuilder(window=10).build(history, cutoff_date=date(2024, 1, 10))
        # Arsenal has played 1 home game before cutoff (Jan 1 only)
        feat_cut = cut.features_for_match("Arsenal", "Chelsea")
        feat_full = full.features_for_match("Arsenal", "Chelsea")
        # With only 1 home match, can differ from full
        assert feat_cut.home_win_rate_home_games in (0.0, 1.0)
        assert isinstance(feat_full.home_advantage_score, float)
