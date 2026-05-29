"""
Telegram channel collector — reads messages in real time via Telethon MTProto client.

Messages containing 1X2 odds are parsed by the existing FreeSourceMessageParser
and appended to data/staging/free_sources/telegram_live.jsonl so the regular
signal pipeline picks them up without any manual export.

Setup (one-time, run locally):
    python scripts/gen_telegram_session.py

Then set these env vars on Render:
    TELEGRAM_API_ID      - from https://my.telegram.org/apps
    TELEGRAM_API_HASH    - from https://my.telegram.org/apps
    TELEGRAM_SESSION_STR - output of gen_telegram_session.py
    TELEGRAM_CHANNELS    - comma-separated channel usernames, e.g. @odds_channel,@bet_tips
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_REQUIRED_ENV = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION_STR")


def is_configured() -> bool:
    return all(os.environ.get(k) for k in _REQUIRED_ENV)


async def run_collector(
    output_path: Path,
    channels: list[str] | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Connect and stream messages; append matched odds records to output_path."""
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession

    from src.ingest.free_source_inbox import FreeSourceMessageParser

    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    session_str = os.environ["TELEGRAM_SESSION_STR"]
    env_channels = os.environ.get("TELEGRAM_CHANNELS", "")

    if channels is None:
        channels = [c.strip() for c in env_channels.split(",") if c.strip()]

    if not channels:
        _log.warning("TELEGRAM_CHANNELS is empty — collector idle.")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    parser = FreeSourceMessageParser()

    client = TelegramClient(StringSession(session_str), api_id, api_hash)

    await client.start()
    _log.info("Telegram collector connected as %s", await client.get_me())

    # Resolve each channel up-front; skip ones that don't exist so Telethon
    # never tries to resolve them again on every incoming update.
    valid_channels: list[Any] = []
    for ch in channels:
        try:
            entity = await client.get_entity(ch)
            valid_channels.append(entity)
            _log.info("Resolved Telegram channel: %s → %s", ch, entity)
        except Exception as exc:
            _log.warning("Skipping unresolvable Telegram channel %r: %s", ch, exc)

    if not valid_channels:
        _log.warning("No valid Telegram channels — collector idle.")
        await client.disconnect()
        return

    @client.on(events.NewMessage(chats=valid_channels))
    async def handler(event: Any) -> None:
        text = event.raw_text or ""
        if not text.strip():
            return

        chat = getattr(event.chat, "username", None) or str(event.chat_id)
        ts = datetime.now(timezone.utc).isoformat()

        record = {
            "id": str(event.id),
            "channel": f"@{chat}",
            "date": ts,
            "text": text,
            "source_type": "telegram_live",
        }

        # Quick pre-filter: skip messages that obviously have no odds
        digits = sum(c.isdigit() for c in text)
        if digits < 3:
            return

        # Try to parse odds; if nothing found, still store for the ledger
        parsed = parser._parse_block(text, fallback_date="")
        if parsed is None:
            _log.debug("No odds in message from %s: %.60s", chat, text.replace("\n", " "))
            return

        _log.info(
            "Odds from @%s: %s vs %s  H=%.2f D=%.2f A=%.2f",
            chat,
            parsed.get("HomeTeam", "?"),
            parsed.get("AwayTeam", "?"),
            parsed.get("B365H", 0),
            parsed.get("B365D", 0),
            parsed.get("B365A", 0),
        )

        with output_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    _log.info(
        "Telegram collector watching %d valid channel(s): %s",
        len(valid_channels),
        [getattr(e, "username", None) or getattr(e, "id", e) for e in valid_channels],
    )

    if stop_event:
        await stop_event.wait()
    else:
        await client.run_until_disconnected()

    await client.disconnect()
    _log.info("Telegram collector stopped.")
