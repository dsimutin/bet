"""Fetch configured free-source exports into the free-source inbox."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import yaml

SUPPORTED_EXTENSIONS = {".csv", ".json", ".jsonl", ".ndjson", ".txt"}


@dataclass(frozen=True)
class FreeSourceFeed:
    name: str
    source_type: str
    url: str | None = None
    path: Path | None = None
    enabled: bool = True
    extension: str | None = None


@dataclass(frozen=True)
class FreeSourceFeedIngestResult:
    downloaded_files: list[str] = field(default_factory=list)
    skipped_sources: list[str] = field(default_factory=list)
    config_path: str = ""
    output_dir: str = ""


class FreeSourceFeedIngestor:
    """Downloads/copies free-source feed files into data/staging/free_sources."""

    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    def ingest(self, config_path: Path, output_dir: Path) -> FreeSourceFeedIngestResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        feeds, config_skips = self._load_config(config_path)
        downloaded: list[str] = []
        skipped = list(config_skips)

        for feed in feeds:
            if not feed.enabled:
                skipped.append(f"{feed.name}: disabled")
                continue
            try:
                target = output_dir / self._target_filename(feed)
                if feed.source_type == "url":
                    if not feed.url:
                        raise ValueError("missing url")
                    self._download_url(feed.url, target)
                elif feed.source_type == "local":
                    if feed.path is None:
                        raise ValueError("missing path")
                    self._copy_local(feed.path, target)
                else:
                    raise ValueError(f"unsupported source type {feed.source_type!r}")
            except (OSError, ValueError, URLError) as exc:
                skipped.append(f"{feed.name}: {exc}")
                continue
            downloaded.append(str(target))

        return FreeSourceFeedIngestResult(
            downloaded_files=downloaded,
            skipped_sources=skipped,
            config_path=str(config_path),
            output_dir=str(output_dir),
        )

    def _load_config(self, config_path: Path) -> tuple[list[FreeSourceFeed], list[str]]:
        if not config_path.exists():
            return [], [f"{config_path}: missing"]
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        raw_sources = raw.get("sources", [])
        if not isinstance(raw_sources, list):
            return [], ["sources must be a list"]

        feeds: list[FreeSourceFeed] = []
        skipped: list[str] = []
        for index, item in enumerate(raw_sources):
            if not isinstance(item, dict):
                skipped.append(f"source[{index}]: must be a mapping")
                continue
            try:
                feeds.append(self._feed_from_mapping(item, index=index, config_path=config_path))
            except ValueError as exc:
                skipped.append(f"source[{index}]: {exc}")
        return feeds, skipped

    def _feed_from_mapping(
        self, item: dict[str, Any], index: int, config_path: Path
    ) -> FreeSourceFeed:
        name = str(item.get("name") or f"source_{index}").strip()
        source_type = str(item.get("type") or ("url" if item.get("url") else "local")).strip()
        extension = self._extension_from_item(item)
        path = item.get("path")
        resolved_path = Path(os.path.expandvars(str(path))).expanduser() if path else None
        if resolved_path is not None and not resolved_path.is_absolute():
            resolved_path = (config_path.parent / resolved_path).resolve()
        return FreeSourceFeed(
            name=name,
            source_type=source_type,
            url=os.path.expandvars(str(item["url"])) if item.get("url") else None,
            path=resolved_path,
            enabled=bool(item.get("enabled", True)),
            extension=extension,
        )

    def _extension_from_item(self, item: dict[str, Any]) -> str | None:
        raw = item.get("extension") or item.get("format")
        if raw is None:
            return None
        extension = str(raw).strip().lower()
        if not extension.startswith("."):
            extension = f".{extension}"
        if extension not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported extension {extension!r}")
        return extension

    def _target_filename(self, feed: FreeSourceFeed) -> str:
        extension = feed.extension or self._infer_extension(feed)
        if extension not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported extension {extension!r}")
        return f"{_slugify(feed.name)}{extension}"

    def _infer_extension(self, feed: FreeSourceFeed) -> str:
        source = feed.url or str(feed.path or "")
        suffix = Path(source.split("?", 1)[0]).suffix.lower()
        return suffix if suffix else ".txt"

    def _download_url(self, url: str, target: Path) -> None:
        request = Request(url, headers={"User-Agent": "sports-betting-mvp-free-source-ingest/1.0"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            target.write_bytes(response.read())

    def _copy_local(self, source: Path, target: Path) -> None:
        if not source.exists():
            raise FileNotFoundError(source)
        shutil.copyfile(source, target)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip().lower())
    return slug.strip("._-") or "source"
