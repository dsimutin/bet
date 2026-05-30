"""CLI: verify Telegram connectivity from production environment.

Usage:
    python -m src.cron.run_telegram_test

Exit codes:
    0 — message delivered successfully
    1 — missing configuration (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set)
    2 — Telegram API rejected the request (4xx response)
    3 — network error (timeout, DNS failure, connection refused)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def _mask_chat_id(chat_id: str) -> str:
    if len(chat_id) >= 4:
        return "***" + chat_id[-4:]
    return "***" if chat_id else "(empty)"


def _call_api(token: str, method: str, payload: dict | None = None) -> dict:
    """Call Telegram Bot API. Returns response dict.
    Raises urllib.error.HTTPError on 4xx/5xx, OSError on network failure.
    """
    url = _TELEGRAM_API.format(token=token, method=method)
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    active_mode = os.environ.get("ACTIVE_MODE", "false").strip().lower()

    print("[telegram_test] Checking configuration...")

    # ── Step 1: Token present ──
    if not token:
        print("[telegram_test] FAIL: TELEGRAM_BOT_TOKEN is not set (or empty)")
        print("[telegram_test] Set it in Render Dashboard → Environment → TELEGRAM_BOT_TOKEN")
        sys.exit(1)
    print(f"[telegram_test] TELEGRAM_BOT_TOKEN_PRESENT=true  length={len(token)}")

    # ── Step 2: getMe ──
    print("[telegram_test] Calling getMe...")
    try:
        me = _call_api(token, "getMe")
        if not me.get("ok"):
            print(f"[telegram_test] FAIL: getMe returned ok=false — {me}")
            sys.exit(2)
        bot_name = me.get("result", {}).get("username", "?")
        print(f"[telegram_test] getMe OK — bot=@{bot_name}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        try:
            err = json.loads(body)
            desc = err.get("description", body)
        except Exception:
            desc = body
        print(f"[telegram_test] FAIL: Telegram API error {e.code}: {desc}")
        sys.exit(2)
    except OSError as e:
        print(f"[telegram_test] FAIL: Network error — {e}")
        sys.exit(3)

    # ── Step 3: Chat ID present ──
    if not chat_id:
        print("[telegram_test] FAIL: TELEGRAM_CHAT_ID is not set (or empty)")
        print("[telegram_test] Set it in Render Dashboard → Environment → TELEGRAM_CHAT_ID")
        sys.exit(1)
    print(f"[telegram_test] TELEGRAM_CHAT_ID_PRESENT=true  masked={_mask_chat_id(chat_id)}")

    # ── Step 4: Send test message ──
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    text = (
        "✅ Telegram delivery test\n"
        "Environment: production\n"
        f"Timestamp UTC: {ts}\n"
        f"Active mode: {active_mode}"
    )
    print("[telegram_test] Sending test message...")
    try:
        result = _call_api(token, "sendMessage", {
            "chat_id": chat_id,
            "text": text,
        })
        if result.get("ok"):
            msg_id = result.get("result", {}).get("message_id", "?")
            print(f"[telegram_test] SUCCESS: message_id={msg_id}")
            print(f"[telegram_test] Delivered to chat {_mask_chat_id(chat_id)}")
            sys.exit(0)
        else:
            desc = result.get("description", str(result))
            print(f"[telegram_test] FAIL: Telegram returned ok=false — {desc}")
            sys.exit(2)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        try:
            err = json.loads(body)
            desc = err.get("description", body)
        except Exception:
            desc = body
        print(f"[telegram_test] FAIL: Telegram API error {e.code}: {desc}")
        print(f"[telegram_test] chat_id={_mask_chat_id(chat_id)}")
        sys.exit(2)
    except OSError as e:
        print(f"[telegram_test] FAIL: Network error — {e}")
        sys.exit(3)


if __name__ == "__main__":
    main()
