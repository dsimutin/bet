"""Load upcoming match odds from free/manual source inbox files.

The inbox is deliberately simple: CSV or JSON files exported from Telegram,
Discord, public event calendars, or manual sheets.  Rows are normalized to the
football-data-style columns consumed by the signal pipeline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class FreeSourceInboxResult:
    dataframe: pd.DataFrame
    loaded_files: list[str]
    skipped_files: list[str]


class FreeSourceInboxLoader:
    """Loads free-source upcoming odds from CSV/JSON files."""

    def __init__(self, bookmaker_prefix: str = "B365") -> None:
        self.bookmaker_prefix = bookmaker_prefix

    def load(self, inbox_dir: Path) -> FreeSourceInboxResult:
        if not inbox_dir.exists():
            return FreeSourceInboxResult(pd.DataFrame(), [], [f"{inbox_dir}: missing"])
        frames: list[pd.DataFrame] = []
        loaded: list[str] = []
        skipped: list[str] = []
        for path in sorted(inbox_dir.glob("*")):
            if path.suffix.lower() not in {".csv", ".json", ".jsonl", ".ndjson", ".txt"}:
                continue
            try:
                frame = self._load_file(path)
                normalized = self._normalize(frame, source_path=path)
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                skipped.append(f"{path}: {exc}")
                continue
            if normalized.empty:
                skipped.append(f"{path}: no valid rows")
                continue
            frames.append(normalized)
            loaded.append(str(path))
        if not frames:
            return FreeSourceInboxResult(pd.DataFrame(), loaded, skipped)
        combined = pd.concat(frames, ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["Date", "HomeTeam", "AwayTeam", f"{self.bookmaker_prefix}H"], keep="last"
        )
        return FreeSourceInboxResult(combined.reset_index(drop=True), loaded, skipped)

    def _load_file(self, path: Path) -> pd.DataFrame:
        if path.suffix.lower() == ".csv":
            return pd.read_csv(path, encoding="utf-8")
        if path.suffix.lower() == ".txt":
            return FreeSourceMessageParser(bookmaker_prefix=self.bookmaker_prefix).parse_text_file(
                path
            )
        if path.suffix.lower() in {".jsonl", ".ndjson"}:
            return FreeSourceMessageParser(bookmaker_prefix=self.bookmaker_prefix).parse_jsonl_file(
                path
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = (
            raw
            if isinstance(raw, list)
            else raw.get("matches", raw.get("events", raw.get("messages", [])))
        )
        if not isinstance(rows, list):
            raise ValueError("JSON must be a list or contain matches/events list")
        if rows and _looks_like_message_export(rows):
            return FreeSourceMessageParser(bookmaker_prefix=self.bookmaker_prefix).parse_records(
                rows, source_path=path
            )
        return pd.DataFrame(rows)

    def _normalize(self, frame: pd.DataFrame, source_path: Path) -> pd.DataFrame:
        if frame.empty:
            return frame
        df = frame.rename(columns={col: _canonical_column(col) for col in frame.columns})
        required = {"Date", "HomeTeam", "AwayTeam", "HomeOdds", "DrawOdds", "AwayOdds"}
        missing = required - set(df.columns)
        if missing:
            raise KeyError(f"missing columns {sorted(missing)}")

        output = pd.DataFrame(
            {
                "Date": pd.to_datetime(df["Date"], dayfirst=True, errors="coerce").dt.strftime(
                    "%d/%m/%Y"
                ),
                "HomeTeam": df["HomeTeam"].astype(str).str.strip(),
                "AwayTeam": df["AwayTeam"].astype(str).str.strip(),
                f"{self.bookmaker_prefix}H": pd.to_numeric(df["HomeOdds"], errors="coerce"),
                f"{self.bookmaker_prefix}D": pd.to_numeric(df["DrawOdds"], errors="coerce"),
                f"{self.bookmaker_prefix}A": pd.to_numeric(df["AwayOdds"], errors="coerce"),
                "source_event_id": (
                    df.get("SourceEventId", "").astype(str) if "SourceEventId" in df else ""
                ),
                "source_bookmaker_key": self._series_or_default(
                    df, "Bookmaker", self.bookmaker_prefix
                ),
                "source_bookmaker_title": self._series_or_default(
                    df, "BookmakerTitle", self.bookmaker_prefix
                ),
                "source_channel": self._series_or_default(df, "SourceChannel", ""),
                "source_type": self._series_or_default(df, "SourceType", "free_source_inbox"),
                "source_path": str(source_path),
            }
        )
        odds_cols = [
            f"{self.bookmaker_prefix}H",
            f"{self.bookmaker_prefix}D",
            f"{self.bookmaker_prefix}A",
        ]
        output = output.dropna(subset=["Date", "HomeTeam", "AwayTeam", *odds_cols])
        output = output[(output[odds_cols] > 1.0).all(axis=1)]
        return output

    def _series_or_default(self, df: pd.DataFrame, column: str, default: str) -> pd.Series:
        if column in df:
            return df[column].astype(str).str.strip()
        return pd.Series([default] * len(df), index=df.index)


def _canonical_column(column: Any) -> str:
    key = str(column).strip().lower().replace(" ", "_")
    aliases = {
        "date": "Date",
        "match_date": "Date",
        "event_date": "Date",
        "home": "HomeTeam",
        "home_team": "HomeTeam",
        "hometeam": "HomeTeam",
        "away": "AwayTeam",
        "away_team": "AwayTeam",
        "awayteam": "AwayTeam",
        "home_odds": "HomeOdds",
        "odd_home": "HomeOdds",
        "b365h": "HomeOdds",
        "draw_odds": "DrawOdds",
        "odd_draw": "DrawOdds",
        "b365d": "DrawOdds",
        "away_odds": "AwayOdds",
        "odd_away": "AwayOdds",
        "b365a": "AwayOdds",
        "source_event_id": "SourceEventId",
        "event_id": "SourceEventId",
        "bookmaker": "Bookmaker",
        "source_bookmaker_key": "Bookmaker",
        "bookmaker_title": "BookmakerTitle",
        "source_bookmaker_title": "BookmakerTitle",
        "source_channel": "SourceChannel",
        "channel": "SourceChannel",
        "source_type": "SourceType",
    }
    return aliases.get(key, str(column))


class FreeSourceMessageParser:
    """Extracts upcoming 1X2 odds from raw Telegram/Discord/message exports."""

    _DATE_RE = re.compile(
        r"\b(?P<date>(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4})|(?:\d{4}-\d{2}-\d{2}))\b"
    )
    _ODDS_RE = re.compile(r"(?<!\d)(?P<odds>[1-9]\d?(?:[.,]\d{1,3})?)(?!\d)")
    _MATCH_RE = re.compile(
        r"(?P<home>[A-Za-zА-Яа-яЁё0-9 .'\-&]+?)\s+(?:vs?|v\.?|—|-|@)\s+"
        r"(?P<away>[A-Za-zА-Яа-яЁё0-9 .'\-&]+)"
    )

    def __init__(self, bookmaker_prefix: str = "B365") -> None:
        self.bookmaker_prefix = bookmaker_prefix

    def parse_text_file(self, path: Path) -> pd.DataFrame:
        return self.parse_records(
            [
                {
                    "id": path.stem,
                    "date": "",
                    "text": path.read_text(encoding="utf-8"),
                    "source_type": "text_export",
                }
            ],
            source_path=path,
        )

    def parse_jsonl_file(self, path: Path) -> pd.DataFrame:
        records: list[dict[str, Any]] = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {line_no}: {exc}") from exc
            if isinstance(raw, dict):
                records.append(raw)
        return self.parse_records(records, source_path=path)

    def parse_records(self, records: list[dict[str, Any]], source_path: Path) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for record in records:
            text = _message_text(record)
            if not text:
                continue
            message_ts = _record_timestamp(record)
            fallback_date = _date_for_football_data(message_ts)
            event_id = str(record.get("id", record.get("message_id", "")))
            channel = str(record.get("channel", record.get("chat", record.get("server", ""))))
            source_type = str(record.get("source_type", record.get("type", "message_export")))
            for block_index, block in enumerate(_message_blocks(text)):
                parsed = self._parse_block(block, fallback_date=fallback_date)
                if parsed is None:
                    continue
                rows.append(
                    {
                        **parsed,
                        "source_event_id": f"{source_path.stem}_{event_id}_{block_index}",
                        "source_bookmaker_key": self.bookmaker_prefix,
                        "source_bookmaker_title": self.bookmaker_prefix,
                        "source_channel": channel,
                        "source_type": source_type,
                        "source_path": str(source_path),
                    }
                )
        return pd.DataFrame(rows)

    def _parse_block(self, block: str, fallback_date: str) -> dict[str, Any] | None:
        match = self._find_match(block)
        odds = self._find_odds(block)
        if match is None or odds is None:
            return None
        date_value = self._find_date(block) or fallback_date
        if not date_value:
            return None
        return {
            "Date": date_value,
            "HomeTeam": match[0],
            "AwayTeam": match[1],
            f"{self.bookmaker_prefix}H": odds[0],
            f"{self.bookmaker_prefix}D": odds[1],
            f"{self.bookmaker_prefix}A": odds[2],
        }

    def _find_match(self, text: str) -> tuple[str, str] | None:
        for line in text.splitlines():
            candidate = self._MATCH_RE.search(line)
            if candidate is None:
                continue
            home = _clean_team_name(candidate.group("home"))
            away = _clean_team_name(candidate.group("away"))
            if home and away and not _contains_odds(home) and not _contains_odds(away):
                return home, away
        candidate = self._MATCH_RE.search(text)
        if candidate is None:
            return None
        home = _clean_team_name(candidate.group("home"))
        away = _clean_team_name(candidate.group("away"))
        if not home or not away:
            return None
        return home, away

    def _find_odds(self, text: str) -> tuple[float, float, float] | None:
        values = [
            float(match.group("odds").replace(",", ".")) for match in self._ODDS_RE.finditer(text)
        ]
        odds = [value for value in values if 1.01 <= value <= 100.0]
        if len(odds) < 3:
            return None
        return odds[-3], odds[-2], odds[-1]

    def _find_date(self, text: str) -> str:
        match = self._DATE_RE.search(text)
        if match is None:
            return ""
        raw = match.group("date")
        parsed = pd.to_datetime(raw, dayfirst="/" in raw or "." in raw, errors="coerce")
        if pd.isna(parsed):
            return ""
        return parsed.strftime("%d/%m/%Y")


def _looks_like_message_export(rows: list[Any]) -> bool:
    sample = [row for row in rows[:5] if isinstance(row, dict)]
    if not sample:
        return False
    message_keys = {"text", "content", "message", "body"}
    structured_keys = {"home_team", "HomeTeam", "home", "away_team", "AwayTeam", "away"}
    return any(message_keys & set(row) for row in sample) and not any(
        structured_keys & set(row) for row in sample
    )


def _message_text(record: dict[str, Any]) -> str:
    raw = record.get("text", record.get("content", record.get("message", record.get("body", ""))))
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return str(raw) if raw else ""


def _record_timestamp(record: dict[str, Any]) -> str:
    return str(
        record.get(
            "date",
            record.get("timestamp", record.get("created_at", record.get("datetime", ""))),
        )
    )


def _date_for_football_data(value: str) -> str:
    if not value:
        return ""
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%d/%m/%Y")


def _message_blocks(text: str) -> list[str]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if len(blocks) > 1:
        return blocks
    stripped = text.strip()
    return [stripped] if stripped else []


def _clean_team_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" .:-—|")).strip()


def _contains_odds(value: str) -> bool:
    return bool(re.search(r"\b[1-9]\d?[.,]\d{1,3}\b", value))
