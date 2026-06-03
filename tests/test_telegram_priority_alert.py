from __future__ import annotations

from src.cron.run_signals import _format_priority_alert
from src.models.production_signal_engine import recompute_signal_metrics
from src.web.today_picks import _format_pick


def test_priority_alert_escapes_html_external_fields() -> None:
    message = _format_priority_alert(
        {
            "sport": "football",
            "home_team": "A <script>",
            "away_team": "B & Co",
            "selection_ru": "П1 <bad>",
            "entry_odds": "2.10<script>",
            "edge_pct": "3.5&",
            "model_probability": 0.61,
            "model_id": "dc<EPL>",
        }
    )

    assert "A &lt;script&gt;" in message
    assert "B &amp; Co" in message
    assert "П1 &lt;bad&gt;" in message
    assert "2.10&lt;script&gt;" in message
    assert "3.5&amp;" in message
    assert "dc&lt;EPL&gt;" in message
    assert "<script>" not in message


def test_tennis_priority_alert_escapes_html_external_fields() -> None:
    message = _format_priority_alert(
        {
            "sport": "tennis",
            "player": "Alice <One>",
            "opponent": "Bob & Two",
            "surface": "hard<script>",
            "recent_form": "W&W",
            "days_since_last_match": "2<3",
            "entry_odds": 1.9,
            "edge_pct": 4.2,
            "model_probability": 0.58,
        }
    )

    assert "Alice &lt;One&gt;" in message
    assert "Bob &amp; Two" in message
    assert "hard&lt;script&gt;" in message
    assert "W&amp;W" in message
    assert "2&lt;3" in message


def test_telegram_card_metrics_are_internally_consistent() -> None:
    signal = recompute_signal_metrics(
        {
            "sport": "football",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "selection": "home",
            "entry_odds": 2.0,
            "market_probability": 0.5,
            "event_time_utc": "2026-06-10T18:30:00+00:00",
        },
        adjusted_probability=0.55,
    )

    text = "\n".join(_format_pick(signal, priority=True))

    assert "Вероятность модели: <b>55.0%</b>" in text
    assert "Справедливый кэф: 1.8182" in text
    assert "Edge: <b>10.0%</b>" in text
    assert f"Ставка: {signal['stake_units']}u" in text
