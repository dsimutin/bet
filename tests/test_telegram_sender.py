from __future__ import annotations

from src.integrations.telegram_sender import TelegramConfig, TelegramSender


def test_telegram_message_includes_paper_stake() -> None:
    sender = TelegramSender(
        TelegramConfig(bot_token="dry-run-token", chat_id="dry-run", dry_run=True)
    )

    message = sender.format_signal_message(
        {
            "signal_id": "sig_1",
            "strategy_id": "production_dixon_coles_value_v1",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "bookmaker": "bet365",
            "market_key": "h2h",
            "selection_ru": "П1",
            "entry_odds": 1.8,
            "reference_fair_odds": 1.7,
            "edge_pct": 4.2,
            "paper_stake_units": 1.25,
            "timestamp_utc": "2026-05-28T06:00:00+00:00",
            "explain_formatted": "paper stake test",
        }
    )

    assert "Paper stake: 1.25u" in message
