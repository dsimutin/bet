"""CLI for fetching configured free-source exports into the inbox."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from src.ingest.free_source_feeds import FreeSourceFeedIngestor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch free-source exports into staging inbox.")
    parser.add_argument("--config", type=Path, default=Path("configs/free_sources.yaml"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/staging/free_sources"))
    parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("data/reports/free_source_ingest_report.json"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = FreeSourceFeedIngestor(timeout_seconds=args.timeout_seconds).ingest(
        config_path=args.config,
        output_dir=args.output_dir,
    )
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.write_text(
        json.dumps(asdict(result), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(
        f"Wrote {args.report_path} "
        f"(downloaded={len(result.downloaded_files)}, skipped={len(result.skipped_sources)})"
    )


if __name__ == "__main__":
    main()
