"""CLI for project-wide module audit."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.system.module_audit import run_module_audit, write_module_audit_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit all project modules used by the bot.")
    parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("data/reports/module_audit.json"),
    )
    parser.add_argument("--require-pass", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = run_module_audit()
    write_module_audit_report(report, args.report_path)
    summary = report.summary()
    print(
        f"Wrote {args.report_path} "
        f"(passed={report.passed}, checks={summary['total']}, failed={summary['failed']})"
    )
    if args.require_pass and not report.passed:
        raise SystemExit("Module audit failed")


if __name__ == "__main__":
    main()
