from __future__ import annotations

import json

from src.ingest.free_source_feeds import FreeSourceFeedIngestor
from src.ingest.run_free_source_ingest import main as run_free_source_ingest_main


def test_free_source_feed_ingestor_copies_local_exports(tmp_path) -> None:
    source = tmp_path / "telegram.json"
    source.write_text(
        '[{"text": "29/05/2026\\nArsenal vs Chelsea\\n1.90 3.40 4.20"}]', encoding="utf-8"
    )
    config = tmp_path / "free_sources.yaml"
    config.write_text(
        f"""
sources:
  - name: telegram public export
    type: local
    path: "{source}"
    format: json
""",
        encoding="utf-8",
    )
    output_dir = tmp_path / "inbox"

    result = FreeSourceFeedIngestor().ingest(config, output_dir)

    assert not result.skipped_sources
    assert result.downloaded_files == [str(output_dir / "telegram_public_export.json")]
    assert (output_dir / "telegram_public_export.json").read_text(
        encoding="utf-8"
    ) == source.read_text(encoding="utf-8")


def test_free_source_feed_ingestor_skips_disabled_and_missing_config(tmp_path) -> None:
    missing = tmp_path / "missing.yaml"

    result = FreeSourceFeedIngestor().ingest(missing, tmp_path / "inbox")

    assert result.downloaded_files == []
    assert result.skipped_sources == [f"{missing}: missing"]


def test_free_source_feed_ingestor_uses_relative_paths(tmp_path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    source_dir = tmp_path / "exports"
    source_dir.mkdir()
    source = source_dir / "discord.ndjson"
    source.write_text('{"content": "Liverpool vs Everton 1.80 3.60 4.50"}\n', encoding="utf-8")
    config = config_dir / "free_sources.yaml"
    config.write_text(
        """
sources:
  - name: discord
    type: local
    path: "../exports/discord.ndjson"
""",
        encoding="utf-8",
    )

    result = FreeSourceFeedIngestor().ingest(config, tmp_path / "inbox")

    assert result.downloaded_files == [str(tmp_path / "inbox" / "discord.ndjson")]


def test_free_source_ingest_cli_writes_report(tmp_path, monkeypatch) -> None:
    source = tmp_path / "events.txt"
    source.write_text("29/05/2026\nArsenal vs Chelsea\n1.90 3.40 4.20\n", encoding="utf-8")
    config = tmp_path / "free_sources.yaml"
    config.write_text(
        f"""
sources:
  - name: events
    type: local
    path: "{source}"
""",
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_free_source_ingest",
            "--config",
            str(config),
            "--output-dir",
            str(tmp_path / "inbox"),
            "--report-path",
            str(report_path),
        ],
    )

    run_free_source_ingest_main()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert len(report["downloaded_files"]) == 1
    assert report["skipped_sources"] == []
