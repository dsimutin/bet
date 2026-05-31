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

    def test_inject_live_serve_stats(self):
        from src.models.tennis_markov import TennisMarkovModel
        model = TennisMarkovModel()
        model.fit(self._make_matches())
        # Inject live stats — Player A has stronger serve
        live = {"Player A": {"hard": 0.72}, "Player B": {"hard": 0.60}}
        model.inject_live_serve_stats(live)
        # With serve advantage injected, Player A should win more
        p_a = model.predict_proba("Player A", "Player B", "hard")
        assert p_a > 0.5, "Player A with higher serve % should win more often"

    def test_live_stats_name_fallback(self):
        from src.models.tennis_markov import TennisMarkovModel
        model = TennisMarkovModel()
        model.fit(self._make_matches())
        # Stats keyed by last name pattern should still match
        live = {"Player A": {"hard": 0.75}, "Player B": {"hard": 0.55}}
        model.inject_live_serve_stats(live)
        spw = model.get_serve_prob("Player A", "hard")
        assert spw == 0.75


# ---------------------------------------------------------------------------
# TennisAbstract scraper (unit — no HTTP)
# ---------------------------------------------------------------------------

class TestTennisAbstractScraper:
    def test_parse_pct(self):
        from src.ingest.tennis_abstract import _parse_pct
        assert abs(_parse_pct("68.4") - 0.684) < 1e-6
        assert abs(_parse_pct("68.4%") - 0.684) < 1e-6
        assert abs(_parse_pct("0.684") - 0.684) < 1e-6
        assert _parse_pct("abc") is None
        assert _parse_pct("") is None

    def test_parse_leaders_table(self):
        from src.ingest.tennis_abstract import _parse_leaders_table
        html = """
        <table>
          <tr><th>#</th><th>Player</th><th>SPW</th><th>RPW</th></tr>
          <tr><td>1</td><td>Sinner J.</td><td>72.8%</td><td>41.2%</td></tr>
          <tr><td>2</td><td>Alcaraz C.</td><td>69.1%</td><td>42.0%</td></tr>
        </table>
        """
        result = _parse_leaders_table(html)
        assert "Sinner J." in result
        assert abs(result["Sinner J."] - 0.728) < 1e-6
        assert "Alcaraz C." in result
        assert abs(result["Alcaraz C."] - 0.691) < 1e-6

    def test_get_player_serve_prob_exact(self):
        from src.ingest.tennis_abstract import get_player_serve_prob
        stats = {"Jannik Sinner": {"hard": 0.728, "clay": 0.712}}
        p = get_player_serve_prob("Jannik Sinner", "hard", stats=stats)
        assert abs(p - 0.728) < 1e-6

    def test_get_player_serve_prob_lastname_fallback(self):
        from src.ingest.tennis_abstract import get_player_serve_prob
        stats = {"Jannik Sinner": {"hard": 0.728}}
        # Abbreviated name "J. Sinner" → fallback by last name "Sinner"
        p = get_player_serve_prob("J. Sinner", "hard", stats=stats)
        assert p is not None
        assert abs(p - 0.728) < 1e-6

    def test_get_player_serve_prob_missing(self):
        from src.ingest.tennis_abstract import get_player_serve_prob
        p = get_player_serve_prob("Unknown Player", "hard", stats={})
        assert p is None

    def test_cache_freshness(self, tmp_path):
        from src.ingest.tennis_abstract import _is_cache_fresh, _save_cache
        path = tmp_path / "stats.json"
        assert not _is_cache_fresh(path, 24)  # file doesn't exist
        _save_cache(path, {"Sinner": {"hard": 0.72}})
        assert _is_cache_fresh(path, 24)
        assert not _is_cache_fresh(path, 0)  # max_age=0 → always stale


# ---------------------------------------------------------------------------
# ATP Rankings + name resolver (unit — no HTTP)
# ---------------------------------------------------------------------------

class TestATPRankings:
    def _mock_players_csv(self) -> str:
        return "player_id,name_first,name_last,hand,dob,ioc,height,wikidata_id\n207989,Jannik,Sinner,R,20010816,ITA,188,\n206173,Carlos,Alcaraz,R,20030505,ESP,185,\n100644,Alexander,Zverev,R,19970420,GER,198,\n"

    def _mock_rankings_csv(self) -> str:
        return "ranking_date,rank,player,points\n20260105,1,207989,14750\n20260105,2,206173,11960\n20260105,3,100644,5705\n"

    def test_download_players_parses_correctly(self):
        from src.ingest.atp_rankings import _download_players
        from unittest.mock import patch
        with patch("src.ingest.atp_rankings._get", return_value=self._mock_players_csv()):
            result = _download_players("http://fake")
        assert "207989" in result
        assert result["207989"]["name_first"] == "Jannik"
        assert result["206173"]["name_last"] == "Alcaraz"

    def test_download_rankings_parses_correctly(self):
        from src.ingest.atp_rankings import _download_rankings
        from unittest.mock import patch
        with patch("src.ingest.atp_rankings._get", return_value=self._mock_rankings_csv()):
            result = _download_rankings("http://fake")
        assert len(result) == 3
        assert result[0]["rank"] == 1
        assert result[0]["player_id"] == "207989"

    def test_get_rankings_combines_players_and_rankings(self, tmp_path):
        from src.ingest.atp_rankings import get_rankings
        from unittest.mock import patch
        with patch("src.ingest.atp_rankings._download_players",
                   return_value={"207989": {"name_first": "Jannik", "name_last": "Sinner"},
                                 "206173": {"name_first": "Carlos", "name_last": "Alcaraz"}}), \
             patch("src.ingest.atp_rankings._download_rankings",
                   return_value=[{"rank": 1, "player_id": "207989", "points": 14750},
                                 {"rank": 2, "player_id": "206173", "points": 11960}]):
            rows = get_rankings(top_n=10, tour="atp", cache_dir=tmp_path)
        assert rows[0]["full_name"] == "Jannik Sinner"
        assert rows[1]["full_name"] == "Carlos Alcaraz"
        assert rows[0]["rank"] == 1

    def test_build_name_resolver_abbreviations(self, tmp_path):
        from src.ingest.atp_rankings import build_name_resolver
        from unittest.mock import patch
        mock_rankings = [{"rank": 1, "player_id": "1", "name_first": "Jannik",
                          "name_last": "Sinner", "full_name": "Jannik Sinner", "points": 14000},
                         {"rank": 2, "player_id": "2", "name_first": "Carlos",
                          "name_last": "Alcaraz", "full_name": "Carlos Alcaraz", "points": 12000}]
        with patch("src.ingest.atp_rankings.get_rankings", return_value=mock_rankings):
            resolver = build_name_resolver(top_n=10, cache_dir=tmp_path)

        assert resolver.get("J. Sinner") == "Jannik Sinner"
        assert resolver.get("Sinner") == "Jannik Sinner"
        assert resolver.get("Jannik Sinner") == "Jannik Sinner"
        assert resolver.get("C. Alcaraz") == "Carlos Alcaraz"

    def test_resolve_name_passthrough(self, tmp_path):
        from src.ingest.atp_rankings import resolve_name
        from unittest.mock import patch
        resolver = {"Jannik Sinner": "Jannik Sinner", "j. sinner": "Jannik Sinner"}
        result = resolve_name("J. Sinner", resolver=resolver)
        assert result == "Jannik Sinner"
        result2 = resolve_name("Unknown Player", resolver=resolver)
        assert result2 == "Unknown Player"
