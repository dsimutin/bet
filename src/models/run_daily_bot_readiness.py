"""CLI for writing the daily bot readiness report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.models.daily_bot_readiness import evaluate_readiness


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit scheduled daily bot readiness.")
    parser.add_argument("--free-source-dir", type=Path, default=Path("data/staging/free_sources"))
    parser.add_argument(
        "--free-source-config", type=Path, default=Path("configs/free_sources.yaml")
    )
    parser.add_argument("--model-dir", type=Path, default=Path("data/models"))
    parser.add_argument(
        "--ledger-path", type=Path, default=Path("data/core/paper_signal_ledger.json")
    )
    parser.add_argument(
        "--report-path", type=Path, default=Path("data/reports/daily_bot_readiness.json")
    )
    parser.add_argument("--require-ready", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = evaluate_readiness(
        free_source_dir=args.free_source_dir,
        free_source_config=args.free_source_config,
        model_dir=args.model_dir,
        ledger_path=args.ledger_path,
    )
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {args.report_path} (passed={report.passed})")
    if args.require_ready and not report.passed:
        raise SystemExit("Daily bot readiness check failed")


if __name__ == "__main__":
    main()
