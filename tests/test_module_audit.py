from __future__ import annotations

import json

from src.system.module_audit import CORE_PACKAGES, run_module_audit, write_module_audit_report
from src.system.run_module_audit import main as run_module_audit_main


def test_module_audit_passes_for_all_core_packages() -> None:
    report = run_module_audit()
    payload = report.to_dict()

    assert report.passed is True
    checked_packages = {check["package"] for check in payload["checks"]}
    for package in CORE_PACKAGES:
        assert package in checked_packages
    assert payload["summary"]["failed"] == 0


def test_module_audit_writes_json_report(tmp_path) -> None:
    report = run_module_audit()
    output_path = tmp_path / "module_audit.json"

    write_module_audit_report(report, output_path)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["summary"]["total"] == len(payload["checks"])


def test_module_audit_cli_writes_report(tmp_path, monkeypatch) -> None:
    output_path = tmp_path / "module_audit.json"
    monkeypatch.setattr(
        "sys.argv",
        ["run_module_audit", "--report-path", str(output_path), "--require-pass"],
    )

    run_module_audit_main()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["passed"] is True


def test_web_modules_endpoint_returns_audit_payload() -> None:
    from fastapi.testclient import TestClient

    from src.web.app import app

    with TestClient(app) as client:
        response = client.get("/api/modules")

    assert response.status_code == 200
    payload = response.json()
    assert payload["passed"] is True
    assert payload["summary"]["failed"] == 0
