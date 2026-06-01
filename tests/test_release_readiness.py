"""Release-readiness tests.

Covers the two critical bugs fixed for release plus provider and import checks.
All tests are pure-unit (no network, no disk writes, no external APIs).
"""

from __future__ import annotations

import importlib
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# 1. run_trainer.py — DailyTrainer called with correct signature
# ---------------------------------------------------------------------------


class TestTrainerCall:
    """Verify that run_trainer._train_league uses run_on_dataframe, not run(df, ...)."""

    def test_trainer_uses_run_on_dataframe(self, tmp_path):
        """run_on_dataframe(league, cutoff, df) must be called, not run(df, ...)."""
        # Build a minimal staged CSV so loader finds data
        staging = tmp_path / "staging"
        staging.mkdir()
        csv = staging / "EPL_latest.csv"
        csv.write_text(
            "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n" "01/01/2024,Arsenal,Chelsea,2,1,H\n",
            encoding="utf-8",
        )

        model_dir = tmp_path / "models"
        model_dir.mkdir()

        called_with: dict = {}

        def fake_run_on_dataframe(league, cutoff_date, matches):
            called_with["league"] = league
            called_with["cutoff_date"] = cutoff_date
            called_with["n_rows"] = len(matches)
            return SimpleNamespace(
                model_id="dc_EPL_test",
                promoted=True,
                brier_score=0.55,
                log_loss=0.95,
            )

        fake_trainer = MagicMock()
        fake_trainer.run_on_dataframe.side_effect = fake_run_on_dataframe

        fake_loader_result = SimpleNamespace(
            dataframe=pd.DataFrame(
                {
                    "Date": ["01/01/2024"],
                    "HomeTeam": ["Arsenal"],
                    "AwayTeam": ["Chelsea"],
                }
            )
        )
        fake_loader = MagicMock()
        fake_loader.build.return_value = fake_loader_result
        fake_loader.save_combined.return_value = str(csv)

        with (
            patch("src.ingest.openfootball.OpenFootballLoader", return_value=fake_loader),
            patch("src.models.dixon_coles.DixonColesConfig"),
            patch("src.models.model_registry.ModelRegistry"),
            patch("src.models.trainer.DailyTrainer", return_value=fake_trainer),
        ):
            import src.cron.run_trainer as rt
            import importlib

            importlib.reload(rt)

            result = rt._train_league(
                league="EPL",
                seasons=["2023-24"],
                model_dir=model_dir,
                staging_dir=staging,
                cutoff=date(2024, 6, 1),
            )

        # If run(df, ...) were called instead, the mock would fail differently.
        assert fake_trainer.run_on_dataframe.called, "run_on_dataframe was not called"
        assert not fake_trainer.run.called, "run() must NOT be called (wrong signature)"
        assert called_with["league"] == "EPL"


# ---------------------------------------------------------------------------
# 2. run_signal_scan.py — generate_signals_for_league exists and is importable
# ---------------------------------------------------------------------------


class TestGenerateSignalsForLeague:
    """Verify generate_signals_for_league is importable and behaves correctly."""

    def test_function_is_importable(self):
        from src.signals.run_signal_scan import generate_signals_for_league

        assert callable(generate_signals_for_league)

    def test_returns_empty_when_no_data_source(self, tmp_path):
        """With no odds key and no staged CSV, should return [] gracefully."""
        from src.signals.run_signal_scan import generate_signals_for_league

        model = MagicMock()
        result = generate_signals_for_league(
            model=model,
            league="EPL",
            scan_date=date(2026, 5, 29),
            staging_dir=tmp_path,
            odds_api_key="",
        )
        assert result == [], "Expected empty list when no data source available"

    def test_uses_staged_csv_when_present(self, tmp_path):
        """Should pick up upcoming rows from staged CSV when odds API not configured."""
        from src.signals.run_signal_scan import generate_signals_for_league
        from src.models.production_signal_engine import ProductionDixonColesSignalEngine

        # Create a staged CSV with future matches that have odds columns
        future = date(2099, 1, 1)
        csv_content = (
            "Date,HomeTeam,AwayTeam,B365H,B365D,B365A\n"
            f"{future.strftime('%d/%m/%Y')},Arsenal,Chelsea,2.10,3.40,3.60\n"
        )
        (tmp_path / "EPL_latest.csv").write_text(csv_content, encoding="utf-8")

        signals_returned = [{"signal_id": "s1", "edge_pct": 3.0, "dataset_hash": "sha256:abc"}]
        model = MagicMock()

        with patch.object(
            ProductionDixonColesSignalEngine, "generate_signals", return_value=signals_returned
        ):
            result = generate_signals_for_league(
                model=model,
                league="EPL",
                scan_date=future,
                staging_dir=tmp_path,
                odds_api_key="",
            )

        assert len(result) == 1
        assert result[0]["signal_id"] == "s1"
        # dataset_hash must be present
        assert "dataset_hash" in result[0]

    def test_signals_have_dataset_hash(self, tmp_path):
        """Every returned signal must have a dataset_hash field."""
        from src.signals.run_signal_scan import generate_signals_for_league
        from src.models.production_signal_engine import ProductionDixonColesSignalEngine

        future = date(2099, 1, 1)
        csv_content = (
            "Date,HomeTeam,AwayTeam,B365H,B365D,B365A\n"
            f"{future.strftime('%d/%m/%Y')},Arsenal,Chelsea,2.10,3.40,3.60\n"
        )
        (tmp_path / "EPL_latest.csv").write_text(csv_content, encoding="utf-8")

        # Signal without dataset_hash — function must inject it
        signals_from_engine = [{"signal_id": "s2", "edge_pct": 3.0}]
        model = MagicMock()

        with patch.object(
            ProductionDixonColesSignalEngine, "generate_signals", return_value=signals_from_engine
        ):
            result = generate_signals_for_league(
                model=model,
                league="EPL",
                scan_date=future,
                staging_dir=tmp_path,
                odds_api_key="",
            )

        assert all("dataset_hash" in s for s in result), "All signals must have dataset_hash"


# ---------------------------------------------------------------------------
# 3. Providers — FlashscoreProvider raises on fetch()
# ---------------------------------------------------------------------------


class TestFlashscoreProvider:
    def test_flashscore_disabled_by_default(self):
        from src.ingest.providers import FlashscoreProvider

        p = FlashscoreProvider()
        assert p.enabled is False

    def test_flashscore_fetch_raises(self):
        from src.ingest.providers import FlashscoreProvider, ProviderDisabledError

        p = FlashscoreProvider()
        with pytest.raises(ProviderDisabledError, match="Flashscore ingestion is disabled"):
            p.fetch()

    def test_flashscore_error_mentions_alternatives(self):
        from src.ingest.providers import FlashscoreProvider, ProviderDisabledError

        p = FlashscoreProvider()
        try:
            p.fetch()
        except ProviderDisabledError as exc:
            msg = str(exc)
            assert "football-data.co.uk" in msg
            assert "The Odds API" in msg or "Odds API" in msg


class TestGetProvider:
    def test_get_flashscore_provider(self):
        from src.ingest.providers import get_provider

        p = get_provider("flashscore")
        assert p.name == "flashscore"

    def test_get_unknown_provider_raises(self):
        from src.ingest.providers import get_provider

        with pytest.raises(KeyError, match="Unknown data provider"):
            get_provider("nonexistent-provider")

    def test_list_providers(self):
        from src.ingest.providers import list_providers

        providers = list_providers()
        assert "flashscore" in providers
        assert providers["flashscore"] is False  # always disabled
        assert "openfootball" in providers


# ---------------------------------------------------------------------------
# 4. Health app — imports cleanly
# ---------------------------------------------------------------------------


class TestHealthAppImport:
    def test_health_app_importable(self):
        from src.web.health_app import app, health, health_readiness

        assert callable(health)
        assert callable(health_readiness)

    def test_health_readiness_returns_dict(self, tmp_path, monkeypatch):
        """health_readiness() should always return a dict, even with no data."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))
        monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))

        import importlib
        import src.web.health_app as ha

        importlib.reload(ha)

        result = ha.health_readiness()
        assert isinstance(result, dict)
        assert "ready_for_signals" in result
        assert "checks" in result


# ---------------------------------------------------------------------------
# 5. Telegram collector — importable, is_configured() works
# ---------------------------------------------------------------------------


class TestTelegramCollector:
    def test_importable(self):
        from src.ingest.telegram_collector import is_configured, run_collector

        assert callable(is_configured)
        assert callable(run_collector)

    def test_not_configured_when_no_env(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
        monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
        monkeypatch.delenv("TELEGRAM_SESSION_STR", raising=False)
        # Reload to pick up cleared env
        import importlib
        import src.ingest.telegram_collector as tc

        importlib.reload(tc)
        assert tc.is_configured() is False


# ---------------------------------------------------------------------------
# 6. run_signals.py — no ImportError for generate_signals_for_league
# ---------------------------------------------------------------------------


class TestRunSignalsImports:
    def test_run_signals_module_importable(self):
        import src.cron.run_signals as rs

        assert callable(rs.main)
        assert callable(rs._run_league)

    def test_run_league_imports_generate_signals(self):
        """_run_league must import generate_signals_for_league without ImportError."""
        from src.signals.run_signal_scan import generate_signals_for_league

        # Just confirm it's importable — the actual logic is tested above
        assert callable(generate_signals_for_league)
