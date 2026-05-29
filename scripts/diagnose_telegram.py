"""Telegram bot diagnostics — check webhook, bot info, and message delivery.

Usage:
    python scripts/diagnose_telegram.py
    python scripts/diagnose_telegram.py --send-test "Test message from bot"
    python scripts/diagnose_telegram.py --check-webhook
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def _api(method: str, payload: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = json.dumps(payload or {}).encode("utf-8") if payload else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "error_code": e.code, "description": body}
    except Exception as e:
        return {"ok": False, "description": str(e)}


def check_credentials() -> bool:
    print("=== Credentials ===")
    ok = True
    if not BOT_TOKEN:
        print("[FAIL] TELEGRAM_BOT_TOKEN not set")
        ok = False
    else:
        masked = BOT_TOKEN[:8] + "..." + BOT_TOKEN[-4:] if len(BOT_TOKEN) > 12 else "***"
        print(f"[OK]   TELEGRAM_BOT_TOKEN = {masked}")

    if not CHAT_ID:
        print("[FAIL] TELEGRAM_CHAT_ID not set")
        ok = False
    else:
        print(f"[OK]   TELEGRAM_CHAT_ID   = {CHAT_ID}")
    return ok


def check_bot_info() -> bool:
    print("\n=== Bot Info ===")
    result = _api("getMe")
    if result.get("ok"):
        bot = result["result"]
        print(f"[OK]   Bot username : @{bot.get('username')}")
        print(f"[OK]   Bot name     : {bot.get('first_name')}")
        print(f"[OK]   Bot ID       : {bot.get('id')}")
        print(f"[OK]   Can join groups: {bot.get('can_join_groups')}")
        return True
    else:
        print(f"[FAIL] getMe failed: {result.get('description')}")
        return False


def check_webhook() -> None:
    print("\n=== Webhook Status ===")
    result = _api("getWebhookInfo")
    if not result.get("ok"):
        print(f"[FAIL] getWebhookInfo: {result.get('description')}")
        return

    info = result["result"]
    url = info.get("url", "")
    if url:
        print(f"[WARN] Webhook active: {url}")
        print("       This may intercept messages. Delete with /deleteWebhook if using polling.")
    else:
        print("[OK]   No webhook set (polling mode)")

    pending = info.get("pending_update_count", 0)
    if pending:
        print(f"[WARN] Pending updates: {pending} (backlog)")

    last_error = info.get("last_error_message")
    if last_error:
        print(f"[FAIL] Last webhook error: {last_error}")
        print(f"       At: {info.get('last_error_date')}")


def check_updates() -> None:
    print("\n=== Recent Updates ===")
    result = _api("getUpdates", {"limit": 5, "timeout": 0})
    if not result.get("ok"):
        print(f"[FAIL] getUpdates: {result.get('description')}")
        return

    updates = result.get("result", [])
    if not updates:
        print("[INFO] No pending updates (bot is up to date)")
    else:
        print(f"[INFO] {len(updates)} pending update(s):")
        for upd in updates:
            msg = upd.get("message", {})
            chat = msg.get("chat", {})
            print(f"       update_id={upd['update_id']} chat_id={chat.get('id')} "
                  f"type={chat.get('type')} text={msg.get('text', '')[:40]!r}")


def send_test_message(text: str) -> bool:
    print(f"\n=== Sending Test Message ===")
    if not CHAT_ID:
        print("[FAIL] TELEGRAM_CHAT_ID not set — cannot send")
        return False

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }
    result = _api("sendMessage", payload)
    if result.get("ok"):
        msg = result["result"]
        print(f"[OK]   Delivered — message_id={msg.get('message_id')} "
              f"chat={msg.get('chat', {}).get('id')}")
        return True
    else:
        print(f"[FAIL] sendMessage: {result.get('description')}")
        err = result.get("error_code")
        if err == 400:
            print("       Hint: check chat_id format (should be negative for groups, e.g. -100xxxxxxx)")
        elif err == 403:
            print("       Hint: bot was blocked or not added to this chat")
        elif err == 401:
            print("       Hint: bot token is invalid or revoked")
        return False


def delete_webhook() -> None:
    print("\n=== Deleting Webhook ===")
    result = _api("deleteWebhook")
    if result.get("ok"):
        print("[OK]   Webhook deleted (polling mode now active)")
    else:
        print(f"[FAIL] {result.get('description')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram bot diagnostics")
    parser.add_argument("--send-test", metavar="TEXT", help="Send a test message to TELEGRAM_CHAT_ID")
    parser.add_argument("--check-webhook", action="store_true", help="Check and display webhook state")
    parser.add_argument("--delete-webhook", action="store_true", help="Delete active webhook (enable polling)")
    parser.add_argument("--check-updates", action="store_true", help="Show pending getUpdates")
    args = parser.parse_args()

    print(f"Telegram Diagnostics — {datetime.now(timezone.utc).isoformat()}")
    print("=" * 50)

    creds_ok = check_credentials()
    if not creds_ok:
        print("\n[EXIT] Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars and retry.")
        sys.exit(1)

    bot_ok = check_bot_info()
    if not bot_ok:
        print("\n[EXIT] Cannot reach Telegram API. Check token and network.")
        sys.exit(1)

    if args.check_webhook or not any([args.send_test, args.delete_webhook, args.check_updates]):
        check_webhook()

    if args.check_updates:
        check_updates()

    if args.delete_webhook:
        delete_webhook()

    if args.send_test:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        text = f"{args.send_test}\n\n_Sent at {ts}_"
        ok = send_test_message(text)
        sys.exit(0 if ok else 1)

    print("\n=== Summary ===")
    print("Run with --send-test 'Hello' to verify message delivery.")
    print("Run with --delete-webhook to switch to polling mode if webhook is blocking.")


if __name__ == "__main__":
    main()
