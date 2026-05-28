"""
One-time script: authenticate with Telegram and print a StringSession.

Run this LOCALLY (not on Render). Copy the output string and set it as
TELEGRAM_SESSION_STR env variable on Render.

Usage:
    export TELEGRAM_API_ID=12345678
    export TELEGRAM_API_HASH=your_hash_here
    python scripts/gen_telegram_session.py

Get API credentials at: https://my.telegram.org/apps
  1. Log in → API development tools → Create application
  2. App api_id and App api_hash
"""

from __future__ import annotations

import asyncio
import os


async def main() -> None:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = int(os.environ.get("TELEGRAM_API_ID") or input("Enter api_id: "))
    api_hash = os.environ.get("TELEGRAM_API_HASH") or input("Enter api_hash: ")

    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        print("\n" + "=" * 60)
        print("SESSION STRING (set this as TELEGRAM_SESSION_STR on Render):")
        print("=" * 60)
        print(client.session.save())
        print("=" * 60)
        me = await client.get_me()
        print(f"\nAuthenticated as: {me.first_name} (@{me.username})")


if __name__ == "__main__":
    asyncio.run(main())
