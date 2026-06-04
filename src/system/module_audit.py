"""Project-wide module audit for bot runtime readiness."""

from __future__ import annotations

import importlib
import json
import pkgutil
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

CORE_PACKAGES = (
    "backtest",
    "features",
    "ingest",
    "integrations",
    "models",
    "normalize",
    "reporting",
    "signals",
    "web",
)


@dataclass(frozen=True)
class ModuleAuditCheck:
    name: str
    passed: bool
    details: str
    package: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "details": self.details,
            "package": self.package,
        }


@dataclass(frozen=True)
class ModuleAuditReport:
    passed: bool
    generated_at_utc: str
    checks: list[ModuleAuditCheck] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "generated_at_utc": self.generated_at_utc,
            "checks": [check.to_dict() for check in self.checks],
            "summary": self.summary(),
        }

    def summary(self) -> dict[str, Any]:
        by_package: dict[str, dict[str, int]] = {}
        for check in self.checks:
            item = by_package.setdefault(check.package, {"passed": 0, "failed": 0})
            item["passed" if check.passed else "failed"] += 1
        return {
            "total": len(self.checks),
            "passed": sum(1 for check in self.checks if check.passed),
            "failed": sum(1 for check in self.checks if not check.passed),
            "by_package": by_package,
        }


def run_module_audit() -> ModuleAuditReport:
    checks = [_import_check(module_name) for module_name in _iter_src_modules()]
    checks.extend(_smoke_checks())
    return ModuleAuditReport(
        passed=all(check.passed for check in checks),
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        checks=checks,
    )


def write_module_audit_report(report: ModuleAuditReport, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output_path


def _iter_src_modules() -> list[str]:
    import src

    module_names = [
        name
        for _, name, _ in pkgutil.walk_packages(src.__path__, prefix="src.")
        if _package_name(name) in CORE_PACKAGES
    ]
    return sorted(module_names)


def _import_check(module_name: str) -> ModuleAuditCheck:
    try:
        importlib.import_module(module_name)
        return ModuleAuditCheck(
            name=f"import:{module_name}",
            passed=True,
            details="import ok",
            package=_package_name(module_name),
        )
    except Exception as exc:
        return ModuleAuditCheck(
            name=f"import:{module_name}",
            passed=False,
            details=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}",
            package=_package_name(module_name),
        )


def _smoke_checks() -> list[ModuleAuditCheck]:
    checks: list[tuple[str, str, Callable[[], str]]] = [
        ("ingest", "smoke:telegram_collector_config", _smoke_telegram_config),
        ("normalize", "smoke:devig_power", _smoke_devig_power),
        ("features", "smoke:clv_compute", _smoke_clv),
        ("models", "smoke:ledger_add_signal", _smoke_ledger),
        ("signals", "smoke:signal_record_validation", _smoke_signal_record),
        ("backtest", "smoke:backtest_config_paper_only", _smoke_backtest_config),
        ("reporting", "smoke:daily_report_builder", _smoke_reporting_builder),
        ("integrations", "smoke:telegram_dry_run_format", _smoke_telegram_sender),
        ("web", "smoke:render_scheduler_disabled_default", _smoke_render_scheduler),
    ]
    return [_run_smoke_check(package, name, func) for package, name, func in checks]


def _run_smoke_check(
    package: str,
    name: str,
    func: Callable[[], str],
) -> ModuleAuditCheck:
    try:
        details = func()
        return ModuleAuditCheck(name=name, passed=True, details=details, package=package)
    except Exception as exc:
        return ModuleAuditCheck(
            name=name,
            passed=False,
            details=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}",
            package=package,
        )


def _smoke_telegram_config() -> str:
    from src.ingest.telegram_collector import is_configured

    return f"configured={is_configured()}"


def _smoke_devig_power() -> str:
    from src.normalize.odds_normalizer import OddsNormalizer

    fair_odds = OddsNormalizer().devig_power([2.0, 3.5, 4.0])
    if len(fair_odds) != 3 or any(value <= 1.0 for value in fair_odds):
        raise ValueError(f"unexpected fair odds: {fair_odds}")
    return "power devig ok"


def _smoke_clv() -> str:
    from src.features.clv_features import CLVAnalyzer

    clv = CLVAnalyzer().compute_clv(2.10, 2.00)
    if clv <= 0:
        raise ValueError(f"expected positive CLV, got {clv}")
    return f"clv={clv:.2f}"


def _smoke_ledger() -> str:
    from src.models.signal_ledger import SignalLedger

    ledger = SignalLedger()
    added = ledger.add_signal(
        {
            "signal_id": "module_audit_signal",
            "dataset_hash": "sha256:module-audit",
            "entry_odds": 2.0,
        }
    )
    if not added:
        raise ValueError("expected signal to be added")
    return "ledger add ok"


def _smoke_signal_record() -> str:
    from src.signals.run_signal_scan import SignalRecord

    signal = SignalRecord(
        signal_id="sig_20260530_000001",
        strategy_id="module_audit",
        normalized_event_id="audit_match",
        bookmaker="paper",
        market_key="h2h",
        selection="home",
        entry_odds=2.05,
        reference_fair_odds=1.95,
        edge_pct=5.13,
        clv_proxy_expected="unknown",
        status="paper",
        confidence="medium",
        timestamp_utc=datetime.now(timezone.utc),
    )
    return f"status={signal.status}"


def _smoke_backtest_config() -> str:
    from datetime import date

    from src.backtest.run_backtest import BacktestConfig

    config = BacktestConfig(
        strategy_id="module_audit",
        sport="football",
        league="EPL",
        market_key="h2h",
        start_date=date(2024, 8, 1),
        end_date=date(2024, 9, 1),
        fold_size_days=30,
        min_edge_pct=2.0,
        max_margin_pct=5.0,
        paper_trading_only=True,
    )
    if not config.paper_trading_only:
        raise ValueError("backtest must remain paper-only")
    return "paper-only config ok"


def _smoke_reporting_builder() -> str:
    from tempfile import TemporaryDirectory

    from src.reporting.build_daily_report import DailyReportBuilder, DailyReportConfig

    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        signals_dir = tmp_path / "signals"
        signals_dir.mkdir()
        builder = DailyReportBuilder(DailyReportConfig(), tmp_path)
        signals = builder.load_signals(signals_dir)
    if signals:
        raise ValueError("missing signal directory should be empty")
    return "report builder ok"


def _smoke_telegram_sender() -> str:
    from src.integrations.telegram_sender import TelegramConfig, TelegramSender

    sender = TelegramSender(
        TelegramConfig(
            bot_token="dry-run-token",
            chat_id="dry-run",
            dry_run=True,
            max_message_length=4096,
        )
    )
    message = sender.format_signal_message(
        {
            "strategy_id": "module_audit",
            "home_team": "Alpha",
            "away_team": "Beta",
            "bookmaker": "paper",
            "market_key": "h2h",
            "entry_odds": 2.0,
            "reference_fair_odds": 1.9,
            "edge_pct": 5.0,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    if "PAPER SIGNAL" not in message:
        raise ValueError("unexpected Telegram message format")
    return "telegram dry-run format ok"


def _smoke_render_scheduler() -> str:
    from src.web.render_scheduler import render_scheduler_enabled

    if render_scheduler_enabled({}):
        raise ValueError("scheduler should be disabled by default")
    return "render scheduler default ok"


def _package_name(module_name: str) -> str:
    parts = module_name.split(".")
    return parts[1] if len(parts) > 1 else module_name
