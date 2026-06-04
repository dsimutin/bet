"""Background Render scheduler for the live signal pipeline."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

FREE_SOURCE_PATTERNS = ("*.csv", "*.json", "*.jsonl", "*.ndjson", "*.txt")


@dataclass(frozen=True)
class PipelineStage:
    name: str
    command: list[str]


@dataclass(frozen=True)
class PipelineStageResult:
    name: str
    returncode: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "returncode": self.returncode,
            "message": self.message,
        }


@dataclass
class PipelineRunStatus:
    state: str = "idle"
    current_stage: str | None = None
    last_started_at_utc: str | None = None
    last_finished_at_utc: str | None = None
    last_returncode: int | None = None
    last_message: str = ""
    last_command: list[str] = field(default_factory=list)
    stage_results: list[PipelineStageResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "current_stage": self.current_stage,
            "last_started_at_utc": self.last_started_at_utc,
            "last_finished_at_utc": self.last_finished_at_utc,
            "last_returncode": self.last_returncode,
            "last_message": self.last_message,
            "last_command": self.last_command,
            "stage_results": [result.to_dict() for result in self.stage_results],
        }


def env_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def render_scheduler_enabled(env: dict[str, str] | None = None) -> bool:
    env_map = env if env is not None else os.environ
    return env_truthy(env_map.get("ENABLE_RENDER_DAILY_PIPELINE"))


def build_render_pipeline_command(
    root: Path,
    env: dict[str, str] | None = None,
) -> tuple[list[str] | None, str]:
    return build_render_signal_command(root, env)


def build_free_source_ingest_command(root: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "src.ingest.run_free_source_ingest",
        "--config",
        str(root / "configs" / "free_sources.yaml"),
        "--output-dir",
        str(root / "data" / "staging" / "free_sources"),
        "--report-path",
        str(root / "data" / "reports" / "free_source_ingest_report.json"),
    ]


def build_module_audit_command(root: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "src.system.run_module_audit",
        "--report-path",
        str(root / "data" / "reports" / "module_audit.json"),
        "--require-pass",
    ]


def build_readiness_command(root: Path, require_candidate_sources: bool) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "src.models.run_daily_bot_readiness",
        "--free-source-dir",
        str(root / "data" / "staging" / "free_sources"),
        "--free-source-config",
        str(root / "configs" / "free_sources.yaml"),
        "--model-dir",
        str(root / "data" / "models"),
        "--ledger-path",
        str(root / "data" / "core" / "paper_signal_ledger.json"),
        "--report-path",
        str(root / "data" / "reports" / "daily_bot_readiness.json"),
        "--require-ready",
    ]
    if require_candidate_sources:
        cmd.append("--require-candidate-sources")
    return cmd


def build_smoke_command(root: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "src.models.run_daily_bot_smoke",
        "--output-dir",
        str(root / "data" / "reports" / "smoke"),
    ]


def build_render_signal_command(
    root: Path,
    env: dict[str, str] | None = None,
) -> tuple[list[str] | None, str]:
    env_map = env if env is not None else os.environ
    output_dir = root / "data" / "reports"
    ledger_path = root / "data" / "core" / "paper_signal_ledger.json"
    free_source_dir = root / "data" / "staging" / "free_sources"

    live_args: list[str] = []
    if env_map.get("THE_ODDS_API_KEY"):
        live_args = [
            "--live-odds",
            "--live-sport-keys",
            env_map.get("LIVE_SPORT_KEYS", "soccer_epl"),
            "--odds-regions",
            env_map.get("ODDS_REGIONS", "eu,uk"),
            "--preferred-bookmakers",
            env_map.get("PREFERRED_BOOKMAKERS", "bet365,pinnacle"),
        ]

    free_source_args: list[str] = []
    if _has_free_source_files(free_source_dir):
        free_source_args = ["--free-source-inbox", str(free_source_dir)]

    if not live_args and not free_source_args:
        return None, "No THE_ODDS_API_KEY and no free-source inbox files; pipeline skipped."

    send_enabled = env_truthy(env_map.get("SCHEDULED_SEND_TELEGRAM"))
    if send_enabled and not (env_map.get("TELEGRAM_BOT_TOKEN") and env_map.get("TELEGRAM_CHAT_ID")):
        return None, "SCHEDULED_SEND_TELEGRAM=true but Telegram secrets are missing."

    delivery_arg = "--send-telegram" if send_enabled else "--telegram-payload"
    cmd = [
        sys.executable,
        "-m",
        "src.models.run_signal_pipeline",
        "--leagues",
        env_map.get("LEAGUES", "E0,SP1,D1,I1,F1"),
        "--seasons",
        env_map.get("SEASONS", "2122,2223,2324,2425,2526"),
        "--output-dir",
        str(output_dir),
        "--ledger-path",
        str(ledger_path),
        "--min-ledger-settled",
        env_map.get("MIN_LEDGER_SETTLED", "20"),
        "--min-ledger-win-rate",
        env_map.get("MIN_LEDGER_WIN_RATE", "0.55"),
        "--min-ledger-roi-pct",
        env_map.get("MIN_LEDGER_ROI_PCT", "0.0"),
        "--production-dixon-coles",
        "--production-model-dir",
        str(root / "data" / "models"),
        "--production-league",
        env_map.get("PRODUCTION_LEAGUE", "EPL"),
        "--train-production-model",
        "--production-train-openfootball",
        "--openfootball-leagues",
        env_map.get("OPENFOOTBALL_LEAGUES", "EPL"),
        "--openfootball-seasons",
        env_map.get("OPENFOOTBALL_SEASONS", "2021-22,2022-23,2023-24,2024-25"),
        *live_args,
        *free_source_args,
        delivery_arg,
    ]
    return cmd, "ready"


async def run_once(
    root: Path,
    status: PipelineRunStatus,
    env: dict[str, str] | None = None,
) -> None:
    env_map = env if env is not None else dict(os.environ)
    run_env = {**dict(os.environ), **env_map}
    status.stage_results = []
    status.current_stage = None
    status.last_command = []
    status.state = "running"
    status.last_started_at_utc = _now_utc()
    status.last_message = "running"

    preflight = [
        PipelineStage("module_audit", build_module_audit_command(root)),
        PipelineStage("free_source_ingest", build_free_source_ingest_command(root)),
    ]
    if env_truthy(env_map.get("RUN_RENDER_SMOKE_BEFORE_PIPELINE")):
        preflight.append(PipelineStage("offline_smoke", build_smoke_command(root)))
    preflight.append(
        PipelineStage(
            "daily_bot_readiness",
            build_readiness_command(
                root,
                require_candidate_sources=env_truthy(env_map.get("SCHEDULED_SEND_TELEGRAM")),
            ),
        )
    )

    for stage in preflight:
        result = await _run_command_stage(root, stage, status, run_env)
        status.stage_results.append(result)
        if result.returncode != 0:
            status.state = "failed"
            status.current_stage = None
            status.last_returncode = result.returncode
            status.last_message = result.message
            status.last_finished_at_utc = _now_utc()
            return

    cmd, reason = build_render_signal_command(root, env_map)
    status.last_command = cmd or []
    if cmd is None:
        status.state = "skipped"
        status.last_message = reason
        status.last_returncode = None
        status.last_finished_at_utc = _now_utc()
        return

    pipeline_result = await _run_command_stage(
        root,
        PipelineStage("signal_pipeline", cmd),
        status,
        run_env,
    )
    status.stage_results.append(pipeline_result)
    status.last_returncode = pipeline_result.returncode
    status.last_finished_at_utc = _now_utc()
    status.current_stage = None
    if pipeline_result.returncode == 0:
        status.state = "ok"
        status.last_message = pipeline_result.message or "pipeline completed"
    else:
        status.state = "failed"
        status.last_message = pipeline_result.message or "pipeline failed"


async def _run_command_stage(
    root: Path,
    stage: PipelineStage,
    status: PipelineRunStatus,
    env: dict[str, str],
) -> PipelineStageResult:
    status.current_stage = stage.name
    status.last_command = stage.command
    result = await asyncio.to_thread(
        subprocess.run,
        stage.command,
        cwd=root,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )
    return PipelineStageResult(
        name=stage.name,
        returncode=result.returncode,
        message=_tail(result.stdout) or _tail(result.stderr),
    )


async def scheduler_loop(
    root: Path,
    status: PipelineRunStatus,
    stop_event: asyncio.Event,
    env: dict[str, str] | None = None,
) -> None:
    env_map = env if env is not None else dict(os.environ)
    if env_truthy(env_map.get("RUN_PIPELINE_ON_STARTUP")):
        await run_once(root, status, env_map)

    while not stop_event.is_set():
        delay = _seconds_until_next_run(env_map.get("RENDER_DAILY_SIGNAL_UTC", "08:15"))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            await run_once(root, status, env_map)


def _has_free_source_files(path: Path) -> bool:
    if not path.exists():
        return False
    return any(file.is_file() for pattern in FREE_SOURCE_PATTERNS for file in path.glob(pattern))


def _seconds_until_next_run(value: str) -> float:
    target = _parse_utc_time(value)
    now = datetime.now(timezone.utc)
    next_run = datetime.combine(now.date(), target, tzinfo=timezone.utc)
    if next_run <= now:
        next_run += timedelta(days=1)
    return max((next_run - now).total_seconds(), 1.0)


def _parse_utc_time(value: str) -> time:
    try:
        hour_raw, minute_raw = value.strip().split(":", maxsplit=1)
        return time(hour=int(hour_raw), minute=int(minute_raw))
    except (TypeError, ValueError):
        return time(hour=8, minute=15)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tail(text: str, limit: int = 1200) -> str:
    cleaned = text.strip()
    return cleaned[-limit:] if cleaned else ""
