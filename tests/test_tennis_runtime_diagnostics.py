from __future__ import annotations

from pathlib import Path

from src.signals import tennis_runtime_scan


def test_runtime_debug_uses_cached_runtime_fetch(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_fetch(api_key: str):
        calls.append(api_key)
        return []

    def fake_debug(model_path: Path, api_key: str):
        events = tennis_runtime_scan.research_scanner._fetch_atp_events(api_key)
        return {
            "events_checked": len(events),
            "signals_count": 0,
            "api_status": "no_events",
            "no_signal_reason": "no_upcoming_events",
        }

    monkeypatch.setattr(tennis_runtime_scan, "get_tennis_h2h_events", fake_fetch)
    monkeypatch.setattr(tennis_runtime_scan.research_scanner, "scan_tennis_with_debug", fake_debug)

    result = tennis_runtime_scan.scan_tennis_h2h_runtime_debug(tmp_path / "model.pkl", "key")

    assert calls == ["key"]
    assert result["runtime_mode"] == "multimarket_cached_debug"
    assert result["diagnostic"] is True
    assert "предстоящих теннисных матчей" in result["tennis_next_action"]


def test_tennis_next_action_points_to_player_coverage() -> None:
    result = tennis_runtime_scan._tennis_next_action(
        {"events_checked": 3, "skipped_no_data": 2, "signals_count": 0}
    )

    assert "игроки не покрыты" in result
