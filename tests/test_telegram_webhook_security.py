from __future__ import annotations

import contextlib

from fastapi.testclient import TestClient


def test_webhook_rejects_missing_secret(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook-secret")
    from src.web.health_app import app

    client = TestClient(app)
    assert client.post("/webhook/telegram", json={"update_id": 1}).status_code == 401


def test_webhook_rejects_wrong_secret(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook-secret")
    from src.web.health_app import app

    client = TestClient(app)
    response = client.post(
        "/webhook/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        json={"update_id": 1},
    )
    assert response.status_code == 401


def test_webhook_accepts_valid_secret(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook-secret")
    from src.web.health_app import app

    client = TestClient(app)
    response = client.post(
        "/webhook/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "hook-secret"},
        json={"update_id": 1},
    )
    assert response.status_code == 200


def test_bot_ignores_non_allowlisted_chat(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    import src.web.telegram_bot as bot

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        bot, "_send", lambda chat_id, text, reply_markup=None: sent.append((chat_id, text))
    )

    bot.handle_update({"update_id": 101, "message": {"chat": {"id": 999}, "text": "/today"}})

    assert sent == []


def test_duplicate_update_id_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    import src.web.telegram_bot as bot

    bot._seen_updates.clear()
    calls: list[str] = []
    monkeypatch.setattr(
        bot, "_send_main_menu", lambda chat_id, first_name="": calls.append(chat_id)
    )

    update = {"update_id": 202, "message": {"chat": {"id": 123}, "text": "/start"}}
    bot.handle_update(update)
    bot.handle_update(update)

    assert calls == ["123"]


def test_manual_refresh_respects_cooldown(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_MANUAL_REFRESH_COOLDOWN_SECONDS", "300")
    import src.web.telegram_bot as bot

    bot._refresh_last_by_chat.clear()
    sent: list[str] = []
    monkeypatch.setattr(bot, "_send", lambda chat_id, text, reply_markup=None: sent.append(text))
    monkeypatch.setattr(bot.threading.Thread, "start", lambda self: None)
    monkeypatch.setattr(bot.time, "monotonic", lambda: 1000.0)

    bot._refresh("123")
    bot._refresh("123")

    assert any("Повторите через" in text for text in sent)


def test_parallel_manual_refresh_does_not_start_two_scans(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_MANUAL_REFRESH_COOLDOWN_SECONDS", "0")
    import src.web.telegram_bot as bot

    class InlineThread:
        def __init__(self, target, daemon=False):
            self.target = target

        def start(self):
            self.target()

    @contextlib.contextmanager
    def busy_guard(_name):
        yield False

    sent: list[str] = []
    monkeypatch.setattr(bot.threading, "Thread", InlineThread)
    monkeypatch.setattr(bot, "_send", lambda chat_id, text, reply_markup=None: sent.append(text))
    monkeypatch.setattr("src.services.job_guard.job_guard", busy_guard)

    bot._refresh("123")

    assert any("Скан уже выполняется" in text for text in sent)


def test_manual_refresh_error_is_sanitized(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_MANUAL_REFRESH_COOLDOWN_SECONDS", "0")
    import src.web.telegram_bot as bot

    class InlineThread:
        def __init__(self, target, daemon=False):
            self.target = target

        def start(self):
            self.target()

    @contextlib.contextmanager
    def acquired_guard(_name):
        yield True

    def run_signals_fails():
        raise RuntimeError("secret-token leaked")

    sent: list[str] = []
    monkeypatch.setattr(bot.threading, "Thread", InlineThread)
    monkeypatch.setattr(bot, "_send", lambda chat_id, text, reply_markup=None: sent.append(text))
    monkeypatch.setattr("src.services.job_guard.job_guard", acquired_guard)
    monkeypatch.setattr("src.cron.run_signals.main", run_signals_fails)

    bot._refresh("123")

    assert any("Подробности скрыты" in text for text in sent)
    assert all("secret-token" not in text for text in sent)


def test_tennis_diagnostics_error_is_sanitized(monkeypatch) -> None:
    import src.web.telegram_bot as bot

    class InlineThread:
        def __init__(self, target, daemon=False):
            self.target = target

        def start(self):
            self.target()

    def scan_fails(*args, **kwargs):
        raise ModuleNotFoundError("No module named 'tenacity'")

    sent: list[str] = []
    monkeypatch.setattr(bot.threading, "Thread", InlineThread)
    monkeypatch.setattr(bot, "_send", lambda chat_id, text, reply_markup=None: sent.append(text))
    monkeypatch.setattr("src.signals.tennis_runtime_scan.scan_tennis_h2h_runtime", scan_fails)

    bot._debug_tennis("123")

    assert any("Подробности скрыты" in text for text in sent)
    assert all("tenacity" not in text for text in sent)
    assert all("No module named" not in text for text in sent)
