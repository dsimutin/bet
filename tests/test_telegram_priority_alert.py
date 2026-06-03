from __future__ import annotations

from src.cron.run_signals import _format_priority_alert


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
