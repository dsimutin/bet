"""Readiness audit for the scheduled daily paper-signal bot."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

FREE_SOURCE_PATTERNS = ("*.csv", "*.json", "*.jsonl", "*.ndjson", "*.txt")


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    passed: bool
    details: str


@dataclass(frozen=True)
class DailyBotReadinessReport:
    passed: bool
    checks: list[ReadinessCheck] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [
                {"name": check.name, "passed": check.passed, "details": check.details}
                for check in self.checks
            ],
        }


def evaluate_readiness(
    free_source_dir: Path = Path("data/staging/free_sources"),
    free_source_config: Path = Path("configs/free_sources.yaml"),
    model_dir: Path = Path("data/models"),
    ledger_path: Path = Path("data/core/paper_signal_ledger.json"),
    scheduled_send_telegram: bool | None = None,
    require_candidate_sources: bool | None = None,
    env: dict[str, str] | None = None,
) -> DailyBotReadinessReport:
    env_map = env if env is not None else dict(os.environ)
    send_enabled = (
        _truthy(env_map.get("SCHEDULED_SEND_TELEGRAM", "false"))
        if scheduled_send_telegram is None
        else scheduled_send_telegram
    )
    sources_required = (
        send_enabled or _truthy(env_map.get("REQUIRE_CANDIDATE_SOURCES", "false"))
        if require_candidate_sources is None
        else require_candidate_sources
    )

    checks = [
        _source_check(free_source_dir, free_source_config, env_map, sources_required),
        _telegram_check(send_enabled, env_map),
        _path_check("model_state_path", model_dir, expect_file=False),
        _path_check("ledger_state_path", ledger_path.parent, expect_file=False),
        _workflow_check(Path(".github/workflows/signal-pipeline.yml")),
    ]
    return DailyBotReadinessReport(
        passed=all(check.passed for check in checks),
        checks=checks,
    )


def _source_check(
    free_source_dir: Path,
    free_source_config: Path,
    env: dict[str, str],
    require_candidate_sources: bool,
) -> ReadinessCheck:
    if env.get("THE_ODDS_API_KEY"):
        return ReadinessCheck("candidate_sources", True, "THE_ODDS_API_KEY is configured")

    inbox_files = _free_source_files(free_source_dir)
    if inbox_files:
        return ReadinessCheck(
            "candidate_sources",
            True,
            f"{len(inbox_files)} free-source inbox file(s) available",
        )

    configured = _enabled_free_source_count(free_source_config)
    if configured > 0:
        return ReadinessCheck(
            "candidate_sources",
            True,
            f"{configured} enabled free-source feed(s) configured",
        )

    if not require_candidate_sources:
        return ReadinessCheck(
            "candidate_sources",
            True,
            "No live candidate sources configured; dry-run/no-op mode is ready",
        )

    return ReadinessCheck(
        "candidate_sources",
        False,
        "No THE_ODDS_API_KEY, no free-source inbox files, and no enabled free-source feeds",
    )


def _telegram_check(send_enabled: bool, env: dict[str, str]) -> ReadinessCheck:
    if not send_enabled:
        return ReadinessCheck(
            "telegram_delivery",
            True,
            "Scheduled Telegram delivery disabled; dry-run payload mode is ready",
        )
    missing = [key for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not env.get(key)]
    if missing:
        return ReadinessCheck(
            "telegram_delivery",
            False,
            f"Scheduled delivery enabled but missing {', '.join(missing)}",
        )
    return ReadinessCheck("telegram_delivery", True, "Telegram secrets configured")


def _path_check(name: str, path: Path, expect_file: bool) -> ReadinessCheck:
    if expect_file:
        passed = path.exists() and path.is_file()
    else:
        path.mkdir(parents=True, exist_ok=True)
        passed = path.exists() and path.is_dir()
    return ReadinessCheck(name, passed, str(path))


def _workflow_check(path: Path) -> ReadinessCheck:
    if not path.exists():
        return ReadinessCheck("scheduled_workflow", False, f"{path} missing")
    text = path.read_text(encoding="utf-8")
    required = ["schedule:", "src.ingest.run_free_source_ingest", "src.models.run_signal_pipeline"]
    missing = [item for item in required if item not in text]
    if missing:
        return ReadinessCheck("scheduled_workflow", False, f"missing {missing}")
    return ReadinessCheck("scheduled_workflow", True, str(path))


def _free_source_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    files: list[Path] = []
    for pattern in FREE_SOURCE_PATTERNS:
        files.extend(path.glob(pattern))
    return sorted(file for file in files if file.is_file())


def _enabled_free_source_count(config_path: Path) -> int:
    if not config_path.exists():
        return 0
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    sources = raw.get("sources", [])
    if not isinstance(sources, list):
        return 0
    return sum(1 for source in sources if isinstance(source, dict) and source.get("enabled", True))


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
