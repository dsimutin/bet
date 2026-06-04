"""Read-only production canary for the paper-trading runtime.

The canary answers a narrow question: is this deployed process assembled well
enough to run the paper loop right now? It intentionally avoids provider calls
that spend quota and does not write to the ledger by default.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from glob import glob
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CanaryCheck:
    name: str
    status: str
    detail: str
    critical: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "critical": self.critical,
        }


def run_production_canary(
    *,
    data_dir: Path | None = None,
    model_dir: Path | None = None,
    ledger_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = env or dict(os.environ)
    data_dir = data_dir or Path(env.get("DATA_DIR", "data"))
    model_dir = model_dir or Path(env.get("MODEL_DIR", data_dir / "models"))
    ledger_path = ledger_path or Path(
        env.get("LEDGER_PATH", str(data_dir / "core" / "paper_signal_ledger.json"))
    )

    checks = [
        _check_runtime_imports(),
        _check_production_env(env),
        _check_ledger_backend(ledger_path, env),
        _check_model_artifacts(model_dir, env),
        _check_telegram(env),
        _check_quota_config(env),
        _check_scheduler_contract(env),
        _check_last_reports(data_dir),
    ]
    failed = [c for c in checks if c.status == "fail" and c.critical]
    warnings = [c for c in checks if c.status == "warn"]
    status = "fail" if failed else ("warn" if warnings else "pass")
    next_actions = _next_actions(checks)
    return {
        "status": status,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "checks": {check.name: check.as_dict() for check in checks},
        "next_actions": next_actions,
        "summary": {
            "failed_critical": [c.name for c in failed],
            "warnings": [c.name for c in warnings],
            "next_action_count": len(next_actions),
            "quota_spent": False,
            "ledger_mutated": False,
        },
    }


def _check_runtime_imports() -> CanaryCheck:
    modules = [
        "tenacity",
        "src.services.runtime_odds",
        "src.ingest.apifootball_injuries",
        "src.web.telegram_bot",
        "src.cron.run_signals",
    ]
    missing: list[str] = []
    for module in modules:
        try:
            importlib.import_module(module)
        except Exception as exc:
            missing.append(f"{module}: {_sanitize(str(exc))}")
    if missing:
        return CanaryCheck("runtime_imports", "fail", "; ".join(missing))
    return CanaryCheck("runtime_imports", "pass", "runtime imports are available")


def _check_production_env(env: dict[str, str]) -> CanaryCheck:
    app_env = env.get("APP_ENV", "development").strip().lower()
    if app_env != "production":
        return CanaryCheck(
            "production_env",
            "warn",
            f"APP_ENV={app_env}; canary is most meaningful in production",
            critical=False,
        )
    required = [
        "DATABASE_URL",
        "ADMIN_API_TOKEN",
        "TELEGRAM_WEBHOOK_SECRET",
        "TELEGRAM_ALLOWED_CHAT_IDS",
    ]
    missing = [key for key in required if not env.get(key, "").strip()]
    if missing:
        return CanaryCheck("production_env", "fail", "missing: " + ", ".join(missing))
    if env.get("PAPER_TRADING_ONLY", "true").strip().lower() != "true":
        return CanaryCheck("production_env", "fail", "PAPER_TRADING_ONLY must be true")
    return CanaryCheck("production_env", "pass", "required production safety config present")


def _check_ledger_backend(ledger_path: Path, env: dict[str, str]) -> CanaryCheck:
    try:
        from src.infrastructure.persistent_ledger import ledger_healthcheck

        health = ledger_healthcheck(ledger_path)
    except Exception as exc:
        return CanaryCheck("ledger_backend", "fail", _sanitize(str(exc)))
    if not health.get("ok"):
        return CanaryCheck(
            "ledger_backend",
            "fail",
            f"{health.get('backend', 'unknown')}: {_sanitize(str(health.get('error', 'not ok')))}",
        )
    backend = str(health.get("backend", "unknown"))
    if env.get("APP_ENV", "development").strip().lower() == "production" and backend != "supabase":
        return CanaryCheck(
            "ledger_backend",
            "fail",
            f"production ledger authority must be supabase, got {backend}",
        )
    status = "pass" if backend in {"supabase", "local_json"} else "warn"
    critical = backend == "supabase"
    return CanaryCheck("ledger_backend", status, f"backend={backend}", critical=critical)


def _check_model_artifacts(model_dir: Path, env: dict[str, str]) -> CanaryCheck:
    required = [
        item.strip().upper() for item in env.get("LEAGUES", "EPL").split(",") if item.strip()
    ]
    present: set[str] = set()
    for path in glob(str(model_dir / "dc_*.meta.json")):
        try:
            import json

            meta = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            continue
        if meta.get("status") == "production" and meta.get("league"):
            present.add(str(meta["league"]).upper())
    missing = sorted(set(required) - present)
    tennis_enabled = "tennis" in env.get("SPORTS", "football,tennis").lower()
    tennis_model = model_dir / "tennis_elo_atp_latest.pkl"
    if missing:
        return CanaryCheck(
            "model_artifacts", "fail", "missing football models: " + ", ".join(missing)
        )
    if tennis_enabled and not tennis_model.exists():
        return CanaryCheck("model_artifacts", "warn", "tennis enabled but tennis ELO model missing")
    return CanaryCheck("model_artifacts", "pass", f"football models ready: {', '.join(required)}")


def _check_telegram(env: dict[str, str]) -> CanaryCheck:
    missing = [
        key
        for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_ALLOWED_CHAT_IDS")
        if not env.get(key, "").strip()
    ]
    if missing:
        return CanaryCheck("telegram", "warn", "missing: " + ", ".join(missing), critical=False)
    return CanaryCheck("telegram", "pass", "Telegram delivery config present", critical=False)


def _check_quota_config(env: dict[str, str]) -> CanaryCheck:
    if not env.get("THE_ODDS_API_KEY") and not env.get("ODDS_API_IO_KEY"):
        return CanaryCheck(
            "quota_config", "warn", "no odds provider key configured", critical=False
        )
    hard_stop = _int_env(env, "THE_ODDS_API_MIN_REMAINING_HARD_STOP", 25)
    priority = _int_env(env, "THE_ODDS_API_MIN_REMAINING_PRIORITY_REFRESH", 50)
    if priority < hard_stop:
        return CanaryCheck("quota_config", "fail", "priority refresh threshold below hard stop")
    return CanaryCheck(
        "quota_config",
        "pass",
        f"hard_stop={hard_stop}, priority_refresh={priority}",
        critical=False,
    )


def _check_scheduler_contract(env: dict[str, str]) -> CanaryCheck:
    if env.get("ACTIVE_MODE", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return CanaryCheck("scheduler_contract", "warn", "ACTIVE_MODE is disabled", critical=False)
    return CanaryCheck("scheduler_contract", "pass", "ACTIVE_MODE enabled", critical=False)


def _check_last_reports(data_dir: Path) -> CanaryCheck:
    reports_dir = data_dir / "reports"
    if not reports_dir.exists():
        return CanaryCheck("last_reports", "warn", "reports directory missing", critical=False)
    signal_reports = list(reports_dir.glob("*signals*.json"))
    settlement_reports = list(reports_dir.glob("settlement_*.json"))
    if not signal_reports and not settlement_reports:
        return CanaryCheck(
            "last_reports", "warn", "no signal or settlement reports yet", critical=False
        )
    return CanaryCheck(
        "last_reports",
        "pass",
        f"signal_reports={len(signal_reports)}, settlement_reports={len(settlement_reports)}",
        critical=False,
    )


def _next_actions(checks: list[CanaryCheck]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    check_map = {check.name: check for check in checks}
    _append_action(
        actions,
        check_map,
        "runtime_imports",
        "Redeploy from all-the-best and verify requirements-render.txt includes runtime dependencies.",
    )
    _append_action(
        actions,
        check_map,
        "production_env",
        "Set required Render environment variables and keep PAPER_TRADING_ONLY=true.",
    )
    _append_action(
        actions,
        check_map,
        "ledger_backend",
        "Fix DATABASE_URL/SUPABASE_LEDGER_ENABLED; production ledger authority must be Supabase.",
    )
    _append_action(
        actions,
        check_map,
        "model_artifacts",
        "Run bootstrap training or restore durable model artifacts before enabling scans.",
    )
    _append_action(
        actions,
        check_map,
        "telegram",
        "Set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID and TELEGRAM_ALLOWED_CHAT_IDS, then test delivery.",
    )
    _append_action(
        actions,
        check_map,
        "quota_config",
        "Set THE_ODDS_API_KEY or ODDS_API_IO_KEY and keep priority refresh threshold above hard stop.",
    )
    _append_action(
        actions,
        check_map,
        "scheduler_contract",
        "Enable ACTIVE_MODE=true only after canary/readiness are acceptable.",
    )
    _append_action(
        actions,
        check_map,
        "last_reports",
        "Run the first protected /trigger, then confirm signal and settlement reports appear.",
    )
    return actions


def _append_action(
    actions: list[dict[str, Any]],
    checks: dict[str, CanaryCheck],
    name: str,
    action: str,
) -> None:
    check = checks.get(name)
    if not check or check.status == "pass":
        return
    actions.append(
        {
            "check": name,
            "severity": "critical" if check.status == "fail" and check.critical else "warning",
            "action": action,
            "detail": check.detail,
        }
    )


def _int_env(env: dict[str, str], key: str, default: int) -> int:
    try:
        return int(env.get(key, str(default)))
    except ValueError:
        return default


def _sanitize(text: str) -> str:
    safe = text
    for key, value in os.environ.items():
        if (
            value
            and len(value) >= 4
            and any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET", "DATABASE_URL"))
        ):
            safe = safe.replace(value, "[REDACTED]")
    return safe
