from __future__ import annotations

import subprocess

import scripts.health_check as health_check


def test_collect_only_mode_prints_not_deployment_gate(monkeypatch, caplog) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0], 0, stdout="tests/test_x.py::test_y\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    caplog.set_level("WARNING")
    assert health_check.check_tests(collect_only=True) is True
    assert "COLLECTION ONLY — NOT A DEPLOYMENT GATE" in caplog.text


def test_default_health_script_runs_real_tests(monkeypatch) -> None:
    seen = {}

    def fake_run(cmd, *args, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setenv("THE_ODDS_API_KEY", "secret-key")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert health_check.check_tests() is True
    assert seen["cmd"] == ["python", "-m", "pytest", "-q"]
    assert "THE_ODDS_API_KEY" not in seen["env"]


def test_health_script_next_steps_do_not_push_directly_to_production(monkeypatch, caplog) -> None:
    monkeypatch.setattr(health_check, "check_environment", lambda: True)
    monkeypatch.setattr(health_check, "check_dependencies", lambda: True)
    monkeypatch.setattr(health_check, "check_data_files", lambda: True)
    monkeypatch.setattr(health_check, "check_models", lambda: True)
    monkeypatch.setattr(health_check, "check_api_quota", lambda: True)
    monkeypatch.setattr(health_check, "check_tests", lambda collect_only=False: True)
    caplog.set_level("INFO")

    assert health_check.main([]) == 0

    assert "push to all-the-best branch" not in caplog.text
    assert "Open a PR into all-the-best" in caplog.text
