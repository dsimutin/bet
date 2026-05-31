"""
Telegram channel collector — reads messages in real time via Telethon MTProto client.

Football: Messages containing 1X2 odds are parsed by FreeSourceMessageParser
          and appended to data/staging/free_sources/telegram_live.jsonl

Tennis:  Messages from tennis capper channels are parsed by TennisCappersParser
         and appended to data/staging/free_sources/tennis_capper_tips.jsonl
         Consensus signals are used to boost ELO/Markov predictions.

Setup (one-time, run locally):
    python scripts/gen_telegram_session.py

Then set these env vars on Render:
    TELEGRAM_API_ID      - from https://my.telegram.org/apps
    TELEGRAM_API_HASH    - from https://my.telegram.org/apps
    TELEGRAM_SESSION_STR - output of gen_telegram_session.py
    TELEGRAM_CHANNELS    - comma-separated channel usernames
    TELEGRAM_TENNIS_CHANNELS - tennis-specific channels (parsed differently)
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

# Default tennis capper channels — override via TELEGRAM_TENNIS_CHANNELS env var
_DEFAULT_TENNIS_CHANNELS = [
    "@tennis_tips_free",        # 13.7k members, EN, ATP/WTA free tips
    "@tenniswinbet_picks",      # AI-based ATP predictions, free
    "@tennisbettingprofree",    # 4.1k members, ATP/WTA/Challenger
    "@PredixSportOfficial",     # AI sports predictions with serve metrics
    "@tennisbettingfreetipss",  # Daily free analysis, professional traders
]


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
    env_tennis = os.environ.get("TELEGRAM_TENNIS_CHANNELS", "")

    if channels is None:
        channels = [c.strip() for c in env_channels.split(",") if c.strip()]

    # Tennis channels from dedicated env var OR auto-detect from combined list
    tennis_channels_raw = [c.strip() for c in env_tennis.split(",") if c.strip()]
    # Always include default tennis channels if not overridden
    if not tennis_channels_raw:
        tennis_channels_raw = _DEFAULT_TENNIS_CHANNELS

    all_channels = list(set(channels + tennis_channels_raw))
    if not all_channels:
        _log.warning("TELEGRAM_CHANNELS is empty — collector idle.")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tennis_output = output_path.parent / "tennis_capper_tips.jsonl"

    football_parser = FreeSourceMessageParser()
    from src.ingest.tennis_capper_parser import parse_tennis_tip

    client = TelegramClient(StringSession(session_str), api_id, api_hash)
    await client.start()
    _log.info("Telegram collector connected as %s", await client.get_me())

    valid_entities: list[Any] = []
    tennis_entity_ids: set[int] = set()

    for ch in all_channels:
        try:
            entity = await client.get_entity(ch)
            valid_entities.append(entity)
            if ch in tennis_channels_raw:
                tennis_entity_ids.add(entity.id)
            _log.info("Resolved Telegram channel: %r (tennis=%s)", ch, ch in tennis_channels_raw)
        except Exception as exc:
            _log.warning("Skipping unresolvable Telegram channel %r: %s", ch, exc)

    if not valid_entities:
        _log.warning("No valid Telegram channels after resolution — collector idle.")
        await client.disconnect()
        return

    @client.on(events.NewMessage(chats=valid_entities))
    async def handler(event: Any) -> None:
        text = event.raw_text or ""
        if not text.strip():
            return

        chat = getattr(event.chat, "username", None) or str(event.chat_id)
        ts = datetime.now(timezone.utc).isoformat()
        chat_id = getattr(event.chat, "id", 0)

        # Route to tennis or football parser
        is_tennis_channel = chat_id in tennis_entity_ids

        if is_tennis_channel:
            tip = parse_tennis_tip(text, channel=f"@{chat}")
            if tip is None:
                return
            record = {
                "id": str(event.id),
                "channel": f"@{chat}",
                "date": ts,
                "player_picked": tip.player_picked,
                "opponent": tip.opponent,
                "odds": tip.odds,
                "confidence": tip.confidence,
                "text": text[:300],
                "source_type": "tennis_capper",
            }
            _log.info(
                "Tennis tip from @%s: %s WIN @ %s (conf=%.2f)",
                chat, tip.player_picked, tip.odds, tip.confidence,
            )
            with tennis_output.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        else:
            # Football 1X2 parser
            digits = sum(c.isdigit() for c in text)
            if digits < 3:
                return
            parsed = football_parser._parse_block(text, fallback_date="")
            if parsed is None:
                return
            record = {
                "id": str(event.id),
                "channel": f"@{chat}",
                "date": ts,
                "text": text,
                "source_type": "telegram_live",
            }
            _log.info(
                "Football odds from @%s: %s vs %s",
                chat, parsed.get("HomeTeam", "?"), parsed.get("AwayTeam", "?"),
            )
            with output_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    _log.info(
        "Telegram collector watching %d valid channel(s)",
        len(valid_entities),
    )

    if stop_event:
        await stop_event.wait()
    else:
        await client.run_until_disconnected()

    await client.disconnect()
    _log.info("Telegram collector stopped.")
