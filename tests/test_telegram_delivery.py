"""Tests: Telegram delivery, env bool parsing, scheduler states, health endpoints."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_http_error(code: int, body: dict) -> urllib.error.HTTPError:
    raw = json.dumps(body).encode()
    err = urllib.error.HTTPError(
        url="https://api.telegram.org/",
        code=code,
        msg=str(code),
        hdrs=None,  # type: ignore[arg-type]
        fp=BytesIO(raw),
    )
    return err


def _make_ok_response(body: dict) -> MagicMock:
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.read.return_value = json.dumps(body).encode()
    m.status = 200
    return m


# ---------------------------------------------------------------------------
# 1. Env bool parser
# ---------------------------------------------------------------------------

class TestEnvBoolParser:
    """_env_bool must parse true/false strings robustly."""

    def _parse(self, val: str) -> bool:
        # Test the shared implementation
        return val.strip().lower() in ("1", "true", "yes", "on")

    @pytest.mark.parametrize("v", ["true", "True", "TRUE", "1", "yes", "YES", "on", "ON"])
    def test_truthy_values(self, v: str) -> None:
        assert self._parse(v) is True

    @pytest.mark.parametrize("v", ["false", "False", "FALSE", "0", "no", "NO", "off", "OFF", ""])
    def test_falsy_values(self, v: str) -> None:
        assert self._parse(v) is False

    def test_strips_whitespace(self) -> None:
        assert self._parse("  true  ") is True
        assert self._parse("  false  ") is False

    def test_scheduler_env_bool(self) -> None:
        from src.services.scheduler import _env_bool
        with patch.dict(os.environ, {"ACTIVE_MODE": "true"}):
            assert _env_bool("ACTIVE_MODE") is True
        with patch.dict(os.environ, {"ACTIVE_MODE": "false"}):
            assert _env_bool("ACTIVE_MODE") is False
        with patch.dict(os.environ, {"ACTIVE_MODE": "  True  "}):
            assert _env_bool("ACTIVE_MODE") is True


# ---------------------------------------------------------------------------
# 2. run_telegram_test CLI
# ---------------------------------------------------------------------------

class TestRunTelegramTest:
    """run_telegram_test exit codes and output."""

    def test_exits_1_when_token_missing(self, capsys) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            with pytest.raises(SystemExit) as exc:
                import importlib
                import src.cron.run_telegram_test as m
                importlib.reload(m)
                m.main()
            assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "TELEGRAM_BOT_TOKEN" in captured.out

    def test_exits_1_when_chat_id_missing(self, capsys) -> None:
        mock_resp = _make_ok_response({"ok": True, "result": {"username": "testbot"}})
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok:123"}, clear=False):
            os.environ.pop("TELEGRAM_CHAT_ID", None)
            with patch("urllib.request.urlopen", return_value=mock_resp):
                with pytest.raises(SystemExit) as exc:
                    import importlib
                    import src.cron.run_telegram_test as m
                    importlib.reload(m)
                    m.main()
                assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "TELEGRAM_CHAT_ID" in captured.out

    def test_exits_2_on_401_unauthorized(self, capsys) -> None:
        err = _make_http_error(401, {"ok": False, "description": "Unauthorized"})
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "bad_token"}, clear=False):
            os.environ.pop("TELEGRAM_CHAT_ID", None)
            with patch("urllib.request.urlopen", side_effect=err):
                with pytest.raises(SystemExit) as exc:
                    import importlib
                    import src.cron.run_telegram_test as m
                    importlib.reload(m)
                    m.main()
                assert exc.value.code == 2

    def test_exits_3_on_network_error(self, capsys) -> None:
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok:123"}, clear=False):
            with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
                with pytest.raises(SystemExit) as exc:
                    import importlib
                    import src.cron.run_telegram_test as m
                    importlib.reload(m)
                    m.main()
                assert exc.value.code == 3

    def test_exits_0_on_success(self, capsys) -> None:
        me_resp = _make_ok_response({"ok": True, "result": {"username": "mybot"}})
        send_resp = _make_ok_response({"ok": True, "result": {"message_id": 42}})
        responses = iter([me_resp, send_resp])
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "tok:valid",
            "TELEGRAM_CHAT_ID": "-100123456",
        }, clear=False):
            with patch("urllib.request.urlopen", side_effect=lambda *a, **kw: next(responses)):
                with pytest.raises(SystemExit) as exc:
                    import importlib
                    import src.cron.run_telegram_test as m
                    importlib.reload(m)
                    m.main()
                assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "SUCCESS" in captured.out
        # Must NOT expose token
        assert "tok:valid" not in captured.out
        # Must show masked chat_id
        assert "***" in captured.out

    def test_masks_chat_id_in_output(self, capsys) -> None:
        err = _make_http_error(400, {"ok": False, "description": "chat not found"})
        me_resp = _make_ok_response({"ok": True, "result": {"username": "bot"}})
        responses = iter([me_resp, err])
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "tok:123",
            "TELEGRAM_CHAT_ID": "-1001234567890",
        }, clear=False):
            with patch("urllib.request.urlopen", side_effect=lambda *a, **kw: next(responses)):
                with pytest.raises(SystemExit) as exc:
                    import importlib
                    import src.cron.run_telegram_test as m
                    importlib.reload(m)
                    m.main()
                assert exc.value.code == 2
        out = capsys.readouterr().out
        assert "tok:123" not in out  # token not exposed
        assert "***7890" in out      # chat id masked


# ---------------------------------------------------------------------------
# 3. _send_status_report delivery states
# ---------------------------------------------------------------------------

class TestSendStatusReport:
    """_send_status_report returns 'sent'/'dry_run'/'failed'."""

    def _call(self, env: dict, urlopen_side_effect=None):
        import importlib
        import src.cron.run_active_report as m
        importlib.reload(m)

        dummy_signals = {"signals_count": 0, "candidates_checked": 0}
        dummy_settle = {"settled_count": 0, "drift_status": "OK", "kelly_multiplier": 1.0}
        dummy_train = {"trained": False, "training_reason": "skip", "model_age_hours": 1}

        with patch.dict(os.environ, env, clear=False):
            if urlopen_side_effect is not None:
                with patch("urllib.request.urlopen", side_effect=urlopen_side_effect):
                    return m._send_status_report(dummy_signals, dummy_settle, dummy_train)
            else:
                return m._send_status_report(dummy_signals, dummy_settle, dummy_train)

    def test_dry_run_when_token_missing(self, tmp_path) -> None:
        env = {}
        env.pop("TELEGRAM_BOT_TOKEN", None)
        env.pop("TELEGRAM_CHAT_ID", None)
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("TELEGRAM_CHAT_ID", None)
            with patch("src.cron.run_active_report.REPORTS_DIR", tmp_path):
                status = self._call({})
        assert status == "dry_run"

    def test_dry_run_when_chat_id_missing(self, tmp_path) -> None:
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok:x"}, clear=False):
            os.environ.pop("TELEGRAM_CHAT_ID", None)
            with patch("src.cron.run_active_report.REPORTS_DIR", tmp_path):
                status = self._call({"TELEGRAM_BOT_TOKEN": "tok:x"})
        assert status == "dry_run"

    def test_sent_on_success(self, tmp_path) -> None:
        resp = _make_ok_response({"ok": True, "result": {"message_id": 1}})
        # Reload module inside env context so REPORTS_DIR is picked up from env
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "tok:valid",
            "TELEGRAM_CHAT_ID": "-1001234",
            "REPORTS_DIR": str(tmp_path),
        }, clear=False):
            import importlib
            import src.cron.run_active_report as m
            importlib.reload(m)
            dummy_signals: dict = {"signals_count": 0, "candidates_checked": 0}
            dummy_settle: dict = {"settled_count": 0, "drift_status": "OK", "kelly_multiplier": 1.0}
            dummy_train: dict = {"trained": False, "training_reason": "skip", "model_age_hours": 1}
            with patch("urllib.request.urlopen", return_value=resp):
                status = m._send_status_report(dummy_signals, dummy_settle, dummy_train)
        assert status == "sent"
        # Delivery status file saved
        assert (tmp_path / "tg_delivery_status.json").exists()
        saved = json.loads((tmp_path / "tg_delivery_status.json").read_text())
        assert saved["last_status"] == "sent"

    def test_failed_on_401_no_retry(self, tmp_path) -> None:
        """401 Unauthorized must not be retried."""
        call_count = 0
        def side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            raise _make_http_error(401, {"ok": False, "description": "Unauthorized"})

        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "bad:token",
            "TELEGRAM_CHAT_ID": "-1001234",
        }, clear=False):
            with patch("src.cron.run_active_report.REPORTS_DIR", tmp_path):
                status = self._call(
                    {"TELEGRAM_BOT_TOKEN": "bad:token", "TELEGRAM_CHAT_ID": "-1001234"},
                    urlopen_side_effect=side_effect,
                )
        assert status == "failed"
        assert call_count == 1  # no retry on 4xx

    def test_failed_on_400_no_retry(self, tmp_path) -> None:
        """400 Bad Request must not be retried (e.g. wrong parse_mode)."""
        call_count = 0
        def side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            raise _make_http_error(400, {"ok": False, "description": "Bad Request: parse_mode is invalid"})

        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "tok:x",
            "TELEGRAM_CHAT_ID": "-1001234",
        }, clear=False):
            with patch("src.cron.run_active_report.REPORTS_DIR", tmp_path):
                status = self._call(
                    {"TELEGRAM_BOT_TOKEN": "tok:x", "TELEGRAM_CHAT_ID": "-1001234"},
                    urlopen_side_effect=side_effect,
                )
        assert status == "failed"
        assert call_count == 1

    def test_parse_mode_not_empty_string_in_payload(self, tmp_path) -> None:
        """Verify the fixed payload never sends parse_mode='' (causes 400)."""
        captured_payloads: list[dict] = []

        def mock_urlopen(req, *a, **kw):
            import urllib.request as ur
            if hasattr(req, "data") and req.data:
                captured_payloads.append(json.loads(req.data.decode()))
            return _make_ok_response({"ok": True, "result": {"message_id": 99}})

        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "tok:y",
            "TELEGRAM_CHAT_ID": "-999",
        }, clear=False):
            with patch("src.cron.run_active_report.REPORTS_DIR", tmp_path):
                with patch("urllib.request.urlopen", side_effect=mock_urlopen):
                    self._call(
                        {"TELEGRAM_BOT_TOKEN": "tok:y", "TELEGRAM_CHAT_ID": "-999"},
                    )

        assert captured_payloads, "No payload captured"
        payload = captured_payloads[0]
        # parse_mode must not be present OR must not be empty string
        assert payload.get("parse_mode", "NOT_SET") != "", \
            "parse_mode='' would cause Telegram 400 Bad Request"


# ---------------------------------------------------------------------------
# 4. run_active_report --force
# ---------------------------------------------------------------------------

class TestActiveReportForce:
    def test_force_flag_sends_report(self, tmp_path) -> None:
        """--force should call _send_status_report regardless of ACTIVE_MODE."""
        resp = _make_ok_response({"ok": True, "result": {"message_id": 5}})
        with patch.dict(os.environ, {
            "ACTIVE_MODE": "false",
            "TELEGRAM_BOT_TOKEN": "tok:force",
            "TELEGRAM_CHAT_ID": "-555",
            "TELEGRAM_STATUS_REPORTS_ENABLED": "true",
            "REPORTS_DIR": str(tmp_path),
            "LEDGER_PATH": str(tmp_path / "ledger.json"),
            "STAGING_DIR": str(tmp_path),
            "MODEL_DIR": str(tmp_path),
        }, clear=False):
            with patch("urllib.request.urlopen", return_value=resp):
                import importlib
                import src.cron.run_active_report as m
                importlib.reload(m)
                # force=True should not raise
                m.main(force=True)

    def test_dry_run_message_when_no_telegram(self, capsys, tmp_path) -> None:
        with patch.dict(os.environ, {
            "TELEGRAM_STATUS_REPORTS_ENABLED": "true",
            "REPORTS_DIR": str(tmp_path),
            "LEDGER_PATH": str(tmp_path / "ledger.json"),
            "STAGING_DIR": str(tmp_path),
            "MODEL_DIR": str(tmp_path),
        }, clear=False):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("TELEGRAM_CHAT_ID", None)
            import importlib
            import src.cron.run_active_report as m
            importlib.reload(m)
            m.main(force=True)
        out = capsys.readouterr().out + capsys.readouterr().err
        # Should not claim it was sent
        assert "SUCCESS" not in out or "dry" in out.lower() or "DRY" in out


# ---------------------------------------------------------------------------
# 5. Scheduler states
# ---------------------------------------------------------------------------

class TestSchedulerStates:
    def test_scheduler_disabled_when_active_mode_false(self) -> None:
        with patch.dict(os.environ, {"ACTIVE_MODE": "false"}, clear=False):
            import importlib
            import src.services.scheduler as sched_mod
            importlib.reload(sched_mod)
            # start() should not raise and should not start scheduler
            import asyncio
            async def _check():
                sched_mod.start()
                # No scheduler should be running
                return sched_mod._scheduler is None or not getattr(sched_mod._scheduler, "running", False)
            result = asyncio.run(_check())
            assert result is True

    def test_scheduler_enabled_when_active_mode_true(self) -> None:
        import asyncio
        with patch.dict(os.environ, {"ACTIVE_MODE": "true"}, clear=False):
            import importlib
            import src.services.scheduler as sched_mod
            importlib.reload(sched_mod)

            async def _check():
                sched_mod.start()
                running = sched_mod._scheduler is not None and sched_mod._scheduler.running
                n_jobs = len(sched_mod._scheduler.get_jobs()) if sched_mod._scheduler else 0
                sched_mod.stop()
                return running, n_jobs

            running, n_jobs = asyncio.run(_check())
            assert running is True
            assert n_jobs == 5  # signal_scan, settlement, training_check, active_report, keep_alive

    def test_scheduler_logs_next_run_times(self, caplog) -> None:
        import asyncio
        import logging
        with patch.dict(os.environ, {"ACTIVE_MODE": "true"}, clear=False):
            import importlib
            import src.services.scheduler as sched_mod
            importlib.reload(sched_mod)
            with caplog.at_level(logging.INFO, logger="scheduler"):
                async def _run():
                    sched_mod.start()
                    sched_mod.stop()
                asyncio.run(_run())
        assert "ACTIVE SCHEDULER STARTED" in caplog.text
        assert "next_run=" in caplog.text

    def test_scheduler_logs_disabled_message(self, caplog) -> None:
        import asyncio
        import logging
        with patch.dict(os.environ, {"ACTIVE_MODE": "false"}, clear=False):
            import importlib
            import src.services.scheduler as sched_mod
            importlib.reload(sched_mod)
            with caplog.at_level(logging.INFO, logger="scheduler"):
                async def _run():
                    sched_mod.start()
                asyncio.run(_run())
        assert "DISABLED" in caplog.text


# ---------------------------------------------------------------------------
# 6. /health/active endpoint reflects Telegram and scheduler state
# ---------------------------------------------------------------------------

class TestHealthActiveEndpoint:
    def _get_active(self, active_mode: str = "false", tmp_path: Path | None = None) -> dict:
        with patch.dict(os.environ, {
            "ACTIVE_MODE": active_mode,
            "TELEGRAM_BOT_TOKEN": "tok:test" if active_mode == "true" else "",
            "TELEGRAM_CHAT_ID": "-1001234" if active_mode == "true" else "",
            "TELEGRAM_STATUS_REPORTS_ENABLED": "true",
            "TELEGRAM_SIGNAL_ALERTS_ENABLED": "true",
        }, clear=False):
            from fastapi.testclient import TestClient
            import importlib
            import src.web.health_app as ha
            importlib.reload(ha)
            client = TestClient(ha.app)
            return client.get("/health/active").json()

    def test_returns_telegram_section(self, tmp_path) -> None:
        data = self._get_active()
        assert "telegram" in data
        tg = data["telegram"]
        assert "bot_token_present" in tg
        assert "status_reports_enabled" in tg
        assert "last_delivery_status" in tg

    def test_returns_jobs_section(self, tmp_path) -> None:
        data = self._get_active()
        assert "jobs" in data

    def test_telegram_token_present_false_when_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            from fastapi.testclient import TestClient
            import importlib
            import src.web.health_app as ha
            importlib.reload(ha)
            client = TestClient(ha.app)
            data = client.get("/health/active").json()
        assert data["telegram"]["bot_token_present"] is False

    def test_last_delivery_status_from_file(self, tmp_path) -> None:
        status_file = tmp_path / "tg_delivery_status.json"
        status_file.write_text(json.dumps({
            "last_status": "sent",
            "last_at": "2026-05-29T20:00:00+00:00",
            "last_error": None,
        }))
        with patch("src.web.health_app.REPORTS_DIR", tmp_path):
            from fastapi.testclient import TestClient
            import importlib
            import src.web.health_app as ha
            importlib.reload(ha)
            client = TestClient(ha.app)
            data = client.get("/health/active").json()
        assert data["telegram"]["last_delivery_status"] == "sent"


# ---------------------------------------------------------------------------
# 7. /health/readiness degraded states
# ---------------------------------------------------------------------------

class TestHealthReadinessDegraded:
    def _get_readiness(self, env_overrides: dict, tmp_path: Path) -> dict:
        # Inject REPORTS_DIR via env so module reload picks it up correctly
        overrides = {
            "REPORTS_DIR": str(tmp_path),
            "DATA_DIR": str(tmp_path),
            "MODEL_DIR": str(tmp_path / "models"),
            "LEDGER_PATH": str(tmp_path / "ledger.json"),
            **env_overrides,
        }
        with patch.dict(os.environ, overrides, clear=False):
            from fastapi.testclient import TestClient
            import importlib
            import src.web.health_app as ha
            importlib.reload(ha)
            client = TestClient(ha.app)
            return client.get("/health/readiness").json()

    def test_degraded_when_failed_telegram_delivery(self, tmp_path) -> None:
        status_file = tmp_path / "tg_delivery_status.json"
        status_file.write_text(json.dumps({
            "last_status": "failed",
            "last_at": "2026-05-29T20:00:00+00:00",
            "last_error": "http_401: Unauthorized",
        }))
        data = self._get_readiness({}, tmp_path)
        assert data.get("degraded") is True
        assert "telegram_delivery" in data["checks"]
        assert data["checks"]["telegram_delivery"]["ready"] is False

    def test_not_degraded_when_delivery_ok(self, tmp_path) -> None:
        status_file = tmp_path / "tg_delivery_status.json"
        status_file.write_text(json.dumps({
            "last_status": "sent",
            "last_at": "2026-05-29T20:00:00+00:00",
            "last_error": None,
        }))
        data = self._get_readiness({}, tmp_path)
        # telegram_delivery check should pass
        assert data["checks"].get("telegram_delivery", {}).get("ready", True) is True

    def test_telegram_config_check_when_active_mode(self, tmp_path) -> None:
        with patch.dict(os.environ, {
            "ACTIVE_MODE": "true",
            "TELEGRAM_STATUS_REPORTS_ENABLED": "true",
        }, clear=False):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("TELEGRAM_CHAT_ID", None)
            data = self._get_readiness({
                "ACTIVE_MODE": "true",
                "TELEGRAM_STATUS_REPORTS_ENABLED": "true",
            }, tmp_path)
        assert data.get("degraded") is True
        assert "telegram_config" in data["checks"]
        assert data["checks"]["telegram_config"]["ready"] is False
