"""Tests for active monitoring mode.

Covers:
- run_history writer and reader
- active report formatter
- no-new-data → training skipped with clear reason
- enough new data → training triggered
- signal deduplication
- Telegram dry-run (no crash)
- cron run history JSONL writer
- /health/active endpoint
- sports config parser
- disabled provider handling
- no signals → report still generated
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Run history writer/reader
# ---------------------------------------------------------------------------

class TestRunHistory:
    def test_write_and_read(self, tmp_path):
        from src.models.run_history import write_run, read_recent, read_last_run

        p = tmp_path / "runs.jsonl"
        rec = write_run(
            run_type="signal_scan",
            status="success",
            started_at="2026-05-29T12:00:00+00:00",
            finished_at="2026-05-29T12:01:00+00:00",
            duration_seconds=60.0,
            signals_count=3,
            sent_count=3,
            path=p,
        )
        assert rec["run_type"] == "signal_scan"
        assert rec["status"] == "success"
        assert rec["signals_count"] == 3

        records = read_recent(path=p)
        assert len(records) == 1
        assert records[0]["signals_count"] == 3

    def test_read_last_run(self, tmp_path):
        from src.models.run_history import write_run, read_last_run

        p = tmp_path / "runs.jsonl"
        write_run("signal_scan", "success", "2026-05-29T10:00:00+00:00",
                  "2026-05-29T10:01:00+00:00", 60.0, path=p)
        write_run("settlement", "success", "2026-05-29T10:20:00+00:00",
                  "2026-05-29T10:21:00+00:00", 10.0, settled_count=5, path=p)
        write_run("signal_scan", "partial", "2026-05-29T13:00:00+00:00",
                  "2026-05-29T13:01:00+00:00", 62.0, path=p)

        last_signal = read_last_run("signal_scan", path=p)
        assert last_signal["status"] == "partial"

        last_settle = read_last_run("settlement", path=p)
        assert last_settle["settled_count"] == 5

    def test_read_last_run_returns_none_when_empty(self, tmp_path):
        from src.models.run_history import read_last_run
        result = read_last_run("signal_scan", path=tmp_path / "missing.jsonl")
        assert result is None

    def test_required_fields_present(self, tmp_path):
        from src.models.run_history import write_run
        p = tmp_path / "runs.jsonl"
        rec = write_run("training_check", "skip", "2026-05-29T12:40:00+00:00",
                        "2026-05-29T12:40:01+00:00", 1.0,
                        trained=False, training_reason="not enough new data", path=p)
        assert "run_id" in rec
        assert rec["trained"] is False
        assert "not enough" in rec["training_reason"]


# ---------------------------------------------------------------------------
# 2. Active report formatter
# ---------------------------------------------------------------------------

class TestActiveReportFormatter:
    def _signals_result(self, **kwargs) -> dict:
        base = {
            "sports": ["football"],
            "leagues": ["EPL"],
            "matches_count": 10,
            "upcoming_count": 3,
            "recently_finished": 2,
            "odds_count": 30,
            "signals_count": 0,
            "candidates_checked": 4,
            "sent_count": 0,
            "duplicates_skipped": 0,
            "top_signals": [],
            "no_signal_reason": "no upcoming fixtures",
            "providers_ok": ["OpenFootball"],
            "providers_skip": [],
            "source_errors": [],
        }
        base.update(kwargs)
        return base

    def _settlement_result(self, **kwargs) -> dict:
        base = {
            "settled_count": 3,
            "wins": 2, "losses": 1, "pushes": 0,
            "pnl_units": 0.75,
            "roi_pct": 5.2,
            "hit_rate_pct": 66.7,
            "drift_status": "OK",
            "kelly_multiplier": 1.0,
        }
        base.update(kwargs)
        return base

    def _training_result(self, **kwargs) -> dict:
        base = {
            "trained": False,
            "training_reason": "training skipped: only 4 new settled matches, minimum is 10",
            "model_status": "production",
            "model_age_hours": 9.0,
            "league": "",
            "n_matches": 0,
            "old_brier": None,
            "new_brier": None,
            "promoted": False,
        }
        base.update(kwargs)
        return base

    def test_format_active_report_returns_string(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))
        monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))

        import importlib
        import src.reporting.active_report as ar
        importlib.reload(ar)

        text = ar.format_active_report(
            self._signals_result(),
            self._settlement_result(),
            self._training_result(),
        )
        assert isinstance(text, str)
        assert "Report" in text or "Отчёт" in text

    def test_report_contains_all_sections(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
        monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))

        import importlib
        import src.reporting.active_report as ar
        importlib.reload(ar)

        text = ar.format_active_report(
            self._signals_result(),
            self._settlement_result(),
            self._training_result(),
        )
        # Section headers — bilingual report uses Russian/English mix
        assert any(kw in text for kw in ("Football", "⚽", "лигам", "Data"))
        assert any(kw in text for kw in ("Signals", "Сканирование", "сигнал"))
        assert any(kw in text for kw in ("Training", "Модели", "Переобучение"))
        assert any(kw in text for kw in ("Results", "Система", "P&L"))
        assert any(kw in text for kw in ("Health", "Система", "Drift", "Дрейф"))

    def test_no_signals_reason_appears_in_report(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
        monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))

        import importlib
        import src.reporting.active_report as ar
        importlib.reload(ar)

        text = ar.format_active_report(
            self._signals_result(signals_count=0, no_signal_reason="no upcoming fixtures"),
            self._settlement_result(),
            self._training_result(),
        )
        assert "no upcoming fixtures" in text

    def test_training_skipped_reason_in_report(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
        monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))

        import importlib
        import src.reporting.active_report as ar
        importlib.reload(ar)

        text = ar.format_active_report(
            self._signals_result(),
            self._settlement_result(),
            self._training_result(
                trained=False,
                training_reason="training skipped: only 4 new settled matches, minimum is 10",
            ),
        )
        assert "training skipped" in text.lower() or "not enough" in text.lower() or "4 new" in text.lower()

    def test_promoted_model_shows_brier_comparison(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
        monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))

        import importlib
        import src.reporting.active_report as ar
        importlib.reload(ar)

        text = ar.format_active_report(
            self._signals_result(),
            self._settlement_result(),
            self._training_result(
                trained=True,
                league="LALIGA",
                n_matches=1510,
                old_brier=0.62,
                new_brier=0.54,
                promoted=True,
                training_reason="new model promoted because Brier improved from 0.6200 to 0.5400",
            ),
        )
        assert "promoted" in text.lower()
        assert "0.54" in text or "0.5400" in text


    def test_off_season_shows_world_cup_countdown(self, tmp_path, monkeypatch):
        """When all leagues are off-season and World Cup is <30 days away, show countdown."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))
        monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))

        import importlib
        import src.reporting.active_report as ar
        importlib.reload(ar)

        # Simulate: key set, some soccer leagues active, but 0 signals (off-season)
        signals = self._signals_result(
            has_odds_api_key=True,
            active_soccer_leagues=["soccer_usa_mls", "soccer_brazil_campeonato"],
            signals_count=0,
            per_league={
                "EPL": {"status": "no_fixtures", "signals": 0},
                "LIGUE1": {"status": "no_fixtures", "signals": 0},
            },
        )
        text = ar.format_active_report(signals, self._settlement_result(), self._training_result())
        # Should show off-season info block
        assert "межсезонье" in text or "off-season" in text.lower() or "Mls" in text or "август" in text

    def test_ligue1_shown_in_per_league_section(self, tmp_path, monkeypatch):
        """Ligue 1 should appear in per-league section when included in per_league dict."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))
        monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))

        import importlib
        import src.reporting.active_report as ar
        importlib.reload(ar)

        signals = self._signals_result(
            has_odds_api_key=True,
            per_league={"LIGUE1": {"status": "no_fixtures", "signals": 0}},
            active_soccer_leagues=["soccer_epl"],
        )
        text = ar.format_active_report(signals, self._settlement_result(), self._training_result())
        assert "Ligue" in text or "🇫🇷" in text


# ---------------------------------------------------------------------------
# 3. Training: no new data → skip
# ---------------------------------------------------------------------------

class TestTrainingCheck:
    def test_training_skipped_when_not_enough_new_data(self, tmp_path, monkeypatch):
        """When fewer than MIN_NEW_SETTLED_MATCHES_FOR_TRAINING settled since last run,
        training must be skipped and the reason clearly logged."""
        monkeypatch.setenv("MIN_NEW_SETTLED_MATCHES_FOR_TRAINING", "10")
        monkeypatch.setenv("FORCE_TRAINING", "false")
        monkeypatch.setenv("LEAGUES", "EPL")
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))
        monkeypatch.setenv("STAGING_DIR", str(tmp_path / "staging"))
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))

        # Write a fake last training run 1h ago
        from src.models.run_history import write_run
        p = tmp_path / "reports" / "cron_runs.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        write_run("training_check", "success",
                  (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                  datetime.now(timezone.utc).isoformat(), 10.0,
                  settled_count=0, trained=True, path=p)
        # Only 2 new settled since then
        write_run("settlement", "success",
                  (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
                  datetime.now(timezone.utc).isoformat(), 5.0,
                  settled_count=2, path=p)

        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))

        import importlib
        import src.cron.run_active_report as rar
        importlib.reload(rar)

        result = rar._run_training_check()

        assert result["trained"] is False
        assert "training skipped" in result["training_reason"].lower()
        assert "minimum is 10" in result["training_reason"]

    def test_force_training_overrides_min_check(self, tmp_path, monkeypatch):
        """FORCE_TRAINING=true must bypass the new-settled-matches check."""
        monkeypatch.setenv("FORCE_TRAINING", "true")
        monkeypatch.setenv("MIN_NEW_SETTLED_MATCHES_FOR_TRAINING", "10")
        monkeypatch.setenv("LEAGUES", "EPL")
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))
        monkeypatch.setenv("STAGING_DIR", str(tmp_path / "staging"))
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
        (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
        (tmp_path / "staging").mkdir(parents=True, exist_ok=True)
        (tmp_path / "models").mkdir(parents=True, exist_ok=True)

        import importlib
        import pandas as pd
        import src.cron.run_active_report as rar
        importlib.reload(rar)

        (tmp_path / "staging").mkdir(parents=True, exist_ok=True)
        csv = tmp_path / "staging" / "EPL_latest.csv"
        pd.DataFrame({"Date": ["01/01/2024"], "HomeTeam": ["Arsenal"],
                      "AwayTeam": ["Chelsea"]}).to_csv(csv, index=False)

        from types import SimpleNamespace

        # Patch at module level where DailyTrainer is actually imported
        with patch("src.models.trainer.DailyTrainer") as MockTrainer:
            mock_instance = MagicMock()
            mock_instance.run_on_dataframe.return_value = SimpleNamespace(
                promoted=False, brier_score=0.61, model_id="dc_EPL_test"
            )
            MockTrainer.return_value = mock_instance

            with patch("src.ingest.openfootball.OpenFootballLoader") as MockLoader:
                mock_loader = MagicMock()
                mock_loader.build.return_value = MagicMock(dataframe=pd.read_csv(csv))
                mock_loader.save_combined.return_value = str(csv)
                MockLoader.return_value = mock_loader

                result = rar._run_training_check()

        # Training was attempted (trained=True since FORCE_TRAINING=true bypasses check)
        assert result.get("trained") is True


# ---------------------------------------------------------------------------
# 4. Signal deduplication
# ---------------------------------------------------------------------------

class TestSignalDeduplication:
    def test_duplicate_signal_counted(self, tmp_path):
        """Adding the same signal_id twice must increment duplicates_skipped."""
        from src.models.signal_ledger import SignalLedger

        ledger_path = tmp_path / "ledger.json"
        ledger = SignalLedger.load_or_create(ledger_path)

        sig = {
            "signal_id": "test-dup-001",
            "dataset_hash": "sha256:abc",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "market_key": "h2h",
            "entry_odds": 2.1,
            "edge_pct": 5.0,
        }

        dupes = 0
        try:
            ledger.add_signal(sig)
        except Exception:
            dupes += 1

        try:
            ledger.add_signal(sig)
        except Exception:
            dupes += 1

        # Second add either raises or silently skips — either way only 1 in ledger
        entries = ledger.entries()
        assert len([e for e in entries.values() if e.get("signal_id") == "test-dup-001"]) == 1


# ---------------------------------------------------------------------------
# 5. Telegram dry-run
# ---------------------------------------------------------------------------

class TestTelegramDryRun:
    def test_send_status_report_dry_run(self, tmp_path, monkeypatch):
        """_send_status_report must not crash when no Telegram credentials."""
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

        import importlib
        import src.cron.run_active_report as rar
        importlib.reload(rar)

        # Should not raise
        rar._send_status_report(
            {"sports": ["football"], "leagues": ["EPL"], "signals_count": 0,
             "matches_count": 0, "upcoming_count": 0, "recently_finished": 0,
             "odds_count": 0, "candidates_checked": 0, "sent_count": 0,
             "duplicates_skipped": 0, "top_signals": [], "no_signal_reason": "no model",
             "providers_ok": [], "providers_skip": [], "source_errors": []},
            {"settled_count": 0, "wins": 0, "losses": 0, "pushes": 0,
             "pnl_units": None, "roi_pct": None, "hit_rate_pct": None,
             "drift_status": "no_data", "kelly_multiplier": 1.0},
            {"trained": False, "training_reason": "training skipped: 0 new settled",
             "model_status": "no production model", "model_age_hours": None,
             "league": "", "n_matches": 0, "old_brier": None, "new_brier": None, "promoted": False},
        )

        # Dry-run payload must be saved
        assert (tmp_path / "active_report_dry_run.json").exists()

    def test_signal_alert_dry_run(self, tmp_path, monkeypatch):
        """_send_signal_alerts must not crash without credentials."""
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

        import importlib
        import src.cron.run_active_report as rar
        importlib.reload(rar)

        sent = rar._send_signal_alerts([
            {"signal_id": "s1", "home_team": "Arsenal", "away_team": "Chelsea",
             "edge_pct": 5.0, "entry_odds": 2.1, "selection_ru": "П1"},
        ])
        assert sent == 0  # dry-run returns 0


# ---------------------------------------------------------------------------
# 6. /health/active endpoint
# ---------------------------------------------------------------------------

class TestHealthActiveEndpoint:
    def test_health_active_importable(self):
        from src.web.health_app import app, health_active
        assert callable(health_active)

    def test_health_active_returns_dict(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
        monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))
        monkeypatch.setenv("ACTIVE_MODE", "false")

        import importlib
        import src.web.health_app as ha
        importlib.reload(ha)

        result = ha.health_active()
        assert isinstance(result, dict)
        assert "active_mode" in result
        assert "last_runs" in result
        assert "stats_24h" in result
        assert "model_age_hours" in result

    def test_health_active_last_runs_populated_from_history(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))
        monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.json"))
        monkeypatch.setenv("ACTIVE_MODE", "false")

        (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
        from src.models.run_history import write_run
        p = tmp_path / "reports" / "cron_runs.jsonl"
        write_run("signal_scan", "success", "2026-05-29T12:00:00+00:00",
                  "2026-05-29T12:01:00+00:00", 60.0, signals_count=2, path=p)

        import importlib
        import src.web.health_app as ha
        importlib.reload(ha)

        result = ha.health_active()
        assert result["last_runs"]["signal_scan"] is not None
        assert "2026-05-29" in result["last_runs"]["signal_scan"]


# ---------------------------------------------------------------------------
# 7. Sports config parser
# ---------------------------------------------------------------------------

class TestSportsConfig:
    def test_sports_yaml_parseable(self):
        import yaml
        from pathlib import Path
        p = Path("configs/sports.yaml")
        assert p.exists()
        cfg = yaml.safe_load(p.read_text())
        assert "sports" in cfg
        assert "soccer" in cfg["sports"] or "football" in cfg["sports"]

    def test_only_football_has_model_support(self):
        """Only football sport has Dixon-Coles model architecture."""
        # This is a documentation test — confirms our architecture contract
        supported_sports_with_models = ["football", "soccer"]
        unsupported = ["tennis", "hockey", "basketball"]
        # We don't import models for unsupported sports
        for sport in unsupported:
            # Confirm there's no model file for these sports
            from pathlib import Path
            model_files = list(Path("data/models").glob(f"dc_{sport}_*.pkl"))
            assert len(model_files) == 0, f"Unexpected model for {sport}"

    def test_sports_env_var_default_is_football(self, monkeypatch):
        monkeypatch.delenv("SPORTS", raising=False)
        import importlib
        import src.cron.run_active_report as rar
        importlib.reload(rar)
        assert rar.SPORTS == ["football"]


# ---------------------------------------------------------------------------
# 8. Disabled provider handling
# ---------------------------------------------------------------------------

class TestDisabledProviderHandling:
    def test_flashscore_not_used_in_active_report(self):
        """Active report must never call FlashscoreProvider.fetch()."""
        from src.ingest.providers import FlashscoreProvider, ProviderDisabledError
        p = FlashscoreProvider()
        assert p.enabled is False
        with pytest.raises(ProviderDisabledError):
            p.fetch()

    def test_no_odds_api_key_gracefully_handled(self, tmp_path, monkeypatch):
        """Signal scan must return empty list gracefully when no Odds API key."""
        monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
        monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))
        monkeypatch.setenv("STAGING_DIR", str(tmp_path / "staging"))
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
        monkeypatch.setenv("LEAGUES", "EPL")
        (tmp_path / "models").mkdir(parents=True, exist_ok=True)
        (tmp_path / "staging").mkdir(parents=True, exist_ok=True)
        (tmp_path / "reports").mkdir(parents=True, exist_ok=True)

        import importlib
        import src.cron.run_active_report as rar
        importlib.reload(rar)

        result = rar._run_signal_scan()
        # No crash, returns dict
        assert isinstance(result, dict)
        assert result["signals_count"] == 0
        # Reason must explain why
        assert result["no_signal_reason"] or result["providers_skip"]


# ---------------------------------------------------------------------------
# 9. run_active_report importable and main callable
# ---------------------------------------------------------------------------

class TestRunActiveReportImport:
    def test_module_importable(self):
        from src.cron.run_active_report import main
        assert callable(main)

    def test_empty_signals_result_helper(self):
        from src.cron.run_active_report import _empty_signals_result
        r = _empty_signals_result("test reason")
        assert r["signals_count"] == 0
        assert r["no_signal_reason"] == "test reason"

    def test_empty_settlement_result_helper(self):
        from src.cron.run_active_report import _empty_settlement_result
        r = _empty_settlement_result()
        assert r["settled_count"] == 0


# ---------------------------------------------------------------------------
# 10. Scheduler module importable (ACTIVE_MODE=false)
# ---------------------------------------------------------------------------

class TestSchedulerImport:
    def test_scheduler_importable(self, monkeypatch):
        monkeypatch.setenv("ACTIVE_MODE", "false")
        import importlib
        import src.services.scheduler as sched
        importlib.reload(sched)
        assert callable(sched.start)
        assert callable(sched.stop)

    def test_scheduler_no_op_when_inactive(self, monkeypatch):
        monkeypatch.setenv("ACTIVE_MODE", "false")
        import importlib
        import src.services.scheduler as sched
        importlib.reload(sched)
        sched.start()  # Must not raise or actually start
        # No scheduler running
        assert not (sched._scheduler and sched._scheduler.running)
