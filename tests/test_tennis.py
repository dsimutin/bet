"""Tests for tennis ELO model and signal scanner."""

from __future__ import annotations

import pickle
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# TennisEloModel tests
# ---------------------------------------------------------------------------

class TestTennisEloModel:
    def _make_matches(self) -> pd.DataFrame:
        """Small synthetic match set for unit testing."""
        rows = [
            {"match_date": date(2024, 1, 10), "winner_name": "Novak Djokovic",
             "loser_name": "Carlos Alcaraz", "surface": "hard"},
            {"match_date": date(2024, 1, 15), "winner_name": "Carlos Alcaraz",
             "loser_name": "Jannik Sinner", "surface": "clay"},
            {"match_date": date(2024, 1, 20), "winner_name": "Jannik Sinner",
             "loser_name": "Novak Djokovic", "surface": "hard"},
            {"match_date": date(2024, 2, 1), "winner_name": "Novak Djokovic",
             "loser_name": "Daniil Medvedev", "surface": "grass"},
            {"match_date": date(2024, 2, 5), "winner_name": "Daniil Medvedev",
             "loser_name": "Carlos Alcaraz", "surface": "hard"},
        ]
        return pd.DataFrame(rows)

    def test_fit_builds_ratings(self):
        from src.models.tennis_elo import TennisEloModel
        model = TennisEloModel()
        df = self._make_matches()
        model.fit(df)

        assert model.params.n_matches == len(df)
        assert model.params.n_players == 4
        assert "Novak Djokovic" in model.known_players()
        assert "Carlos Alcaraz" in model.known_players()

    def test_predict_proba_range(self):
        from src.models.tennis_elo import TennisEloModel
        model = TennisEloModel()
        model.fit(self._make_matches())

        prob = model.predict_proba("Novak Djokovic", "Carlos Alcaraz", "hard")
        assert 0.0 < prob < 1.0

    def test_predict_proba_sums_to_one(self):
        from src.models.tennis_elo import TennisEloModel
        model = TennisEloModel()
        model.fit(self._make_matches())

        p1 = model.predict_proba("Novak Djokovic", "Carlos Alcaraz", "clay")
        p2 = model.predict_proba("Carlos Alcaraz", "Novak Djokovic", "clay")
        assert abs(p1 + p2 - 1.0) < 1e-10

    def test_unknown_player_uses_start_rating(self):
        from src.models.tennis_elo import TennisEloModel, ELO_START
        model = TennisEloModel()
        model.fit(self._make_matches())

        # Unknown vs unknown → 50%
        prob = model.predict_proba("Unknown Player", "Another Unknown", "hard")
        assert abs(prob - 0.5) < 1e-6

    def test_winner_gets_higher_rating(self):
        from src.models.tennis_elo import TennisEloModel
        model = TennisEloModel()
        # Djokovic wins 2 matches, Alcaraz wins 1
        df = pd.DataFrame([
            {"match_date": date(2024, 1, 1), "winner_name": "Djokovic",
             "loser_name": "Alcaraz", "surface": "hard"},
            {"match_date": date(2024, 1, 2), "winner_name": "Djokovic",
             "loser_name": "Alcaraz", "surface": "hard"},
            {"match_date": date(2024, 1, 3), "winner_name": "Alcaraz",
             "loser_name": "Federer", "surface": "hard"},
        ])
        model.fit(df)
        assert model.get_overall_rating("Djokovic") > model.get_overall_rating("Alcaraz")

    def test_has_enough_data(self):
        from src.models.tennis_elo import TennisEloModel
        model = TennisEloModel()
        rows = [
            {"match_date": date(2024, 1, i + 1), "winner_name": "Player A",
             "loser_name": "Player B", "surface": "hard"}
            for i in range(20)
        ]
        model.fit(pd.DataFrame(rows))
        assert model.has_enough_data("Player A", min_matches=15)
        assert not model.has_enough_data("Player B", min_matches=25)

    def test_save_load_roundtrip(self, tmp_path):
        from src.models.tennis_elo import TennisEloModel
        model = TennisEloModel()
        model.fit(self._make_matches())

        path = tmp_path / "test_elo.pkl"
        model.save(path)
        loaded = TennisEloModel.load(path)

        assert loaded.params.n_players == model.params.n_players
        p_orig = model.predict_proba("Novak Djokovic", "Carlos Alcaraz", "hard")
        p_loaded = loaded.predict_proba("Novak Djokovic", "Carlos Alcaraz", "hard")
        assert abs(p_orig - p_loaded) < 1e-10

    def test_surface_specific_ratings_stored_independently(self):
        from src.models.tennis_elo import TennisEloModel, ELO_START
        model = TennisEloModel()
        rows = []
        # A dominates on clay, C dominates on hard (A always loses on hard)
        for i in range(20):
            rows.append({"match_date": date(2024, 1, i + 1), "winner_name": "A",
                          "loser_name": "B", "surface": "clay"})
            rows.append({"match_date": date(2024, 2, i + 1), "winner_name": "C",
                          "loser_name": "A", "surface": "hard"})
        model.fit(pd.DataFrame(rows))

        # A's clay rating should be above start, A's hard rating should be below start
        r_clay = model._surface["clay"].get("A", ELO_START)
        r_hard = model._surface["hard"].get("A", ELO_START)
        assert r_clay > ELO_START
        assert r_hard < ELO_START


# ---------------------------------------------------------------------------
# Tennis ingest tests
# ---------------------------------------------------------------------------

class TestTennisATPIngest:
    def test_parse_minimal_csv(self, tmp_path):
        from src.ingest.tennis_atp import _parse
        content = "tourney_date,surface,winner_name,loser_name\n20240101,Clay,Djokovic N.,Alcaraz C."
        df = _parse(content)
        assert len(df) == 1
        assert df.iloc[0]["surface"] == "clay"
        assert df.iloc[0]["winner_name"] == "Djokovic N."

    def test_missing_surface_defaults_to_hard(self, tmp_path):
        from src.ingest.tennis_atp import _parse
        content = "tourney_date,tourney_name,winner_name,loser_name\n20240101,Some Open,Player A,Player B"
        df = _parse(content)
        assert df.iloc[0]["surface"] == "hard"

    def test_infer_surface_from_name(self):
        from src.ingest.tennis_atp import infer_surface
        assert infer_surface("Roland Garros") == "clay"
        assert infer_surface("Wimbledon") == "grass"
        assert infer_surface("US Open") == "hard"
        assert infer_surface("Australian Open") == "hard"


# ---------------------------------------------------------------------------
# Tennis signal scan tests
# ---------------------------------------------------------------------------

class TestTennisSignalScan:
    def _make_model(self, tmp_path: Path) -> Path:
        from src.models.tennis_elo import TennisEloModel
        # Build a model with enough matches for Djokovic and Alcaraz
        rows = []
        base = date(2024, 1, 1)
        from datetime import timedelta
        for i in range(30):
            rows.append({
                "match_date": base + timedelta(days=i * 2),
                "winner_name": "Novak Djokovic",
                "loser_name": "Player X",
                "surface": "hard",
            })
            rows.append({
                "match_date": base + timedelta(days=i * 2 + 1),
                "winner_name": "Carlos Alcaraz",
                "loser_name": "Player Y",
                "surface": "clay",
            })
        model = TennisEloModel()
        model.fit(pd.DataFrame(rows))
        path = tmp_path / "tennis_elo_atp_latest.pkl"
        model.save(path)
        return path

    def _make_event(self, player1: str, player2: str, odds1: float, odds2: float) -> dict:
        return {
            "id": "test123",
            "sport_key": "tennis_atp",
            "home_team": player1,
            "away_team": player2,
            "commence_time": "2026-06-10T10:00:00Z",
            "bookmakers": [{
                "key": "pinnacle",
                "markets": [{
                    "key": "h2h",
                    "outcomes": [
                        {"name": player1, "price": odds1},
                        {"name": player2, "price": odds2},
                    ],
                }],
            }],
        }

    def test_no_signal_when_no_edge(self, tmp_path):
        from src.signals.tennis_signal_scan import scan_tennis_signals
        model_path = self._make_model(tmp_path)

        # Even odds → no edge
        events = [self._make_event("Novak Djokovic", "Carlos Alcaraz", 2.0, 2.0)]

        with patch("src.signals.tennis_signal_scan._fetch_atp_events", return_value=events):
            result = scan_tennis_signals(model_path=model_path, api_key="fake", edge_threshold=3.0)

        assert result["signals_count"] == 0

    def test_signal_emitted_when_clear_edge(self, tmp_path):
        from src.signals.tennis_signal_scan import scan_tennis_signals
        from src.models.tennis_elo import TennisEloModel

        # Override model so Djokovic wins 80% on hard
        model_path = self._make_model(tmp_path)
        model = TennisEloModel.load(model_path)
        # Artificially inflate Djokovic's rating
        model._overall["Novak Djokovic"] = 2000
        model._overall["Carlos Alcaraz"] = 1300
        model.save(model_path)

        # Book still offering 2.0 on Djokovic (undervalued)
        events = [self._make_event("Novak Djokovic", "Carlos Alcaraz", 2.0, 1.9)]

        with patch("src.signals.tennis_signal_scan._fetch_atp_events", return_value=events):
            result = scan_tennis_signals(model_path=model_path, api_key="fake", edge_threshold=1.0)

        assert result["signals_count"] > 0
        sig = result["top_signals"][0]
        assert sig["player"] == "Novak Djokovic"
        assert sig["edge_pct"] > 1.0

    def test_no_model_returns_skip(self, tmp_path):
        from src.signals.tennis_signal_scan import scan_tennis_signals
        result = scan_tennis_signals(
            model_path=tmp_path / "nonexistent.pkl", api_key="fake"
        )
        assert result["status"] == "skip"
        assert result["signals_count"] == 0

    def test_api_error_returns_api_error(self, tmp_path):
        from src.signals.tennis_signal_scan import scan_tennis_signals
        model_path = self._make_model(tmp_path)

        with patch("src.signals.tennis_signal_scan._fetch_atp_events", return_value=None):
            result = scan_tennis_signals(model_path=model_path, api_key="fake")
        assert result["status"] == "skip"
        assert result["no_signal_reason"] == "api_error"


# ---------------------------------------------------------------------------
# Tennis section in active report
# ---------------------------------------------------------------------------

class TestTennisActiveReport:
    def test_tennis_section_no_signals(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))
        monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))

        import importlib
        import src.reporting.active_report as ar
        importlib.reload(ar)

        tennis = {"sport": "tennis", "tour": "ATP", "signals_count": 0,
                  "events_checked": 5, "skipped_no_data": 1,
                  "top_signals": [], "status": "ok", "no_signal_reason": "no_edge_found"}
        text = ar.format_active_report(
            {"leagues": ["EPL"], "signals_count": 0, "providers_ok": [], "providers_skip": [],
             "source_errors": [], "duration_s": 1.0},
            {"settled_count": 0, "wins": 0, "losses": 0, "drift_status": "OK", "kelly_multiplier": 1.0},
            {"trained": False, "training_reason": "skip", "model_age_hours": 10},
            tennis,
        )
        assert "🎾" in text or "Теннис" in text or "Tennis" in text

    def test_tennis_section_with_signals(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))
        monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))

        import importlib
        import src.reporting.active_report as ar
        importlib.reload(ar)

        tennis = {
            "sport": "tennis", "tour": "ATP", "signals_count": 2, "events_checked": 10,
            "skipped_no_data": 0, "status": "ok", "no_signal_reason": "",
            "top_signals": [
                {"player": "Djokovic", "opponent": "Alcaraz",
                 "entry_odds": 2.1, "edge_pct": 5.2, "model_prob": 0.55},
            ],
        }
        text = ar.format_active_report(
            {"leagues": ["EPL"], "signals_count": 0, "providers_ok": [], "providers_skip": [],
             "source_errors": [], "duration_s": 1.0},
            {"settled_count": 0, "wins": 0, "losses": 0, "drift_status": "OK", "kelly_multiplier": 1.0},
            {"trained": False, "training_reason": "skip", "model_age_hours": 10},
            tennis,
        )
        assert "Djokovic" in text
        assert "5.2" in text


# ---------------------------------------------------------------------------
# TennisMarkovModel tests
# ---------------------------------------------------------------------------

class TestTennisMarkovModel:
    def _make_matches(self) -> pd.DataFrame:
        rows = []
        from datetime import date, timedelta
        base = date(2024, 1, 1)
        for i in range(40):
            rows.append({
                "match_date": base + timedelta(days=i),
                "winner_name": "Player A",
                "loser_name": "Player B",
                "surface": "hard",
                "w_svpt": 80, "w_1stIn": 55, "w_1stWon": 42, "w_2ndWon": 14,
                "l_svpt": 80, "l_1stIn": 50, "l_1stWon": 35, "l_2ndWon": 12,
                "score": "6-3 6-4",
            })
        return pd.DataFrame(rows)

    def test_fit_trains(self):
        from src.models.tennis_markov import TennisMarkovModel
        model = TennisMarkovModel()
        model.fit(self._make_matches())
        assert model.params.n_matches == 40
        assert model.params.n_players == 2

    def test_predict_proba_range(self):
        from src.models.tennis_markov import TennisMarkovModel
        model = TennisMarkovModel()
        model.fit(self._make_matches())
        p = model.predict_proba("Player A", "Player B", "hard")
        assert 0.0 < p < 1.0

    def test_symmetry(self):
        from src.models.tennis_markov import TennisMarkovModel
        model = TennisMarkovModel()
        model.fit(self._make_matches())
        p1 = model.predict_proba("Player A", "Player B", "hard")
        p2 = model.predict_proba("Player B", "Player A", "hard")
        assert abs(p1 + p2 - 1.0) < 1e-6

    def test_p_win_game_sanity(self):
        from src.models.tennis_markov import _p_win_game
        assert abs(_p_win_game(0.5) - 0.5) < 1e-9
        assert _p_win_game(0.7) > _p_win_game(0.6) > _p_win_game(0.5)
        assert 0.0 < _p_win_game(0.65) < 1.0

    def test_save_load_roundtrip(self, tmp_path):
        from src.models.tennis_markov import TennisMarkovModel
        model = TennisMarkovModel()
        model.fit(self._make_matches())
        path = tmp_path / "markov.pkl"
        model.save(path)
        loaded = TennisMarkovModel.load(path)
        p1 = model.predict_proba("Player A", "Player B", "hard")
        p2 = loaded.predict_proba("Player A", "Player B", "hard")
        assert abs(p1 - p2) < 1e-6
