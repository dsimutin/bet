"""Tests for live results fallback for settlement."""

from __future__ import annotations

from datetime import date

import pytest

from src.ingest.live_results import get_live_match_result, _teams_match


class TestLiveResults:
    """Test live results lookup fallback."""

    def test_teams_match_exact(self) -> None:
        """Exact match should work."""
        assert _teams_match("Manchester United", "manchester united")
        assert _teams_match("Manchester United FC", "manchester united fc")

    def test_teams_match_partial(self) -> None:
        """Partial name overlap (contains) should work."""
        assert _teams_match("Manchester United FC", "Manchester United")
        assert _teams_match("Manchester United", "manchester united fc")
        assert _teams_match("Arsenal", "arsenal fc")
        assert _teams_match("Liverpool FC", "Liverpool")

    def test_teams_no_match(self) -> None:
        """Clearly different teams."""
        assert not _teams_match("Arsenal", "Liverpool")
        assert not _teams_match("Manchester United", "Manchester City")

    def test_get_live_match_result_no_api_key(self) -> None:
        """Should return None without API key."""
        result = get_live_match_result(
            home_team="Arsenal",
            away_team="Liverpool",
            match_date=date(2026, 6, 1),
            league="EPL",
            api_key="",
        )
        assert result is None

    def test_get_live_match_result_invalid_league(self) -> None:
        """Should return None for unsupported league."""
        result = get_live_match_result(
            home_team="Arsenal",
            away_team="Liverpool",
            match_date=date(2026, 6, 1),
            league="SUPER_LEAGUE",
            api_key="test_key",
        )
        assert result is None
