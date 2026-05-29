"""Active monitoring Telegram report formatter.

Produces the 3-hourly status message sent by run_active_report.
All sections are plain-text safe (no HTML parse_mode required).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from glob import glob
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
MODEL_DIR = Path(os.environ.get("MODEL_DIR", DATA_DIR / "models"))
LEDGER_PATH = Path(os.environ.get("LEDGER_PATH", DATA_DIR / "core" / "paper_signal_ledger.json"))
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", DATA_DIR / "reports"))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def format_active_report(
    signals_result: dict[str, Any],
    settlement_result: dict[str, Any],
    training_result: dict[str, Any],
) -> str:
    """Return a ready-to-send Telegram text for the active status report."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts: list[str] = [f"🤖 Active report — {now}", ""]

    parts += _section_data(signals_result)
    parts += _section_signals(signals_result)
    parts += _section_training(training_result)
    parts += _section_results(settlement_result)
    parts += _section_health()

    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _section_data(signals_result: dict[str, Any]) -> list[str]:
    sports = signals_result.get("sports", ["football"])
    leagues = signals_result.get("leagues", [])
    matches = signals_result.get("matches_count", 0)
    upcoming = signals_result.get("upcoming_count", 0)
    finished = signals_result.get("recently_finished", 0)
    odds = signals_result.get("odds_count", 0)
    providers_ok = signals_result.get("providers_ok", [])
    providers_skip = signals_result.get("providers_skip", [])
    src_errors = signals_result.get("source_errors", [])

    lines = ["📊 Data"]
    lines.append(f"Sports: {', '.join(sports)}")
    lines.append(f"Leagues: {', '.join(leagues) if leagues else 'none'}")
    lines.append(f"Matches found: {matches}")
    lines.append(f"Upcoming: {upcoming}")
    lines.append(f"Finished since last run: {finished}")
    lines.append(f"Odds loaded: {odds}")
    if providers_ok:
        lines.append(f"Providers OK: {', '.join(providers_ok)}")
    if providers_skip:
        lines.append(f"Providers skipped: {', '.join(providers_skip)}")
    if src_errors:
        lines.append(f"Source errors: {'; '.join(src_errors[:3])}")
    return lines + [""]


def _section_signals(signals_result: dict[str, Any]) -> list[str]:
    candidates = signals_result.get("candidates_checked", 0)
    new_signals = signals_result.get("signals_count", 0)
    sent = signals_result.get("sent_count", 0)
    dupes = signals_result.get("duplicates_skipped", 0)
    no_signal_reason = signals_result.get("no_signal_reason", "")
    top_signals = signals_result.get("top_signals", [])

    lines = ["📡 Signals"]
    lines.append(f"Candidates checked: {candidates}")
    lines.append(f"New value signals: {new_signals}")
    lines.append(f"Sent to Telegram: {sent}")
    lines.append(f"Duplicates skipped: {dupes}")

    if new_signals == 0 and no_signal_reason:
        lines.append(f"Reason: {no_signal_reason}")
    elif top_signals:
        lines.append("Top signals:")
        for s in top_signals[:3]:
            home = s.get("home_team", "?")
            away = s.get("away_team", "?")
            edge = s.get("edge_pct", "?")
            odds = s.get("entry_odds", "?")
            sel = s.get("selection_ru", s.get("selection", "?"))
            lines.append(f"  • {home} vs {away} | {sel} @ {odds} | edge={edge}%")

    return lines + [""]


def _section_training(training_result: dict[str, Any]) -> list[str]:
    trained = training_result.get("trained", False)
    reason = training_result.get("training_reason", "")
    league = training_result.get("league", "")
    n_matches = training_result.get("n_matches", 0)
    old_brier = training_result.get("old_brier")
    new_brier = training_result.get("new_brier")
    promoted = training_result.get("promoted", False)
    model_age_h = training_result.get("model_age_hours")
    model_status = training_result.get("model_status", "unknown")

    lines = ["🧠 Training"]
    if trained:
        lines.append(f"Model retrained: yes ({league}, {n_matches} matches)")
        if old_brier is not None and new_brier is not None:
            direction = "↓ improved" if new_brier < old_brier else "↑ worsened"
            lines.append(f"Brier: {old_brier:.4f} → {new_brier:.4f} ({direction})")
            if promoted:
                lines.append(f"Decision: promoted (Brier improved {old_brier:.4f}→{new_brier:.4f})")
            else:
                lines.append(f"Decision: rejected (Brier worsened {old_brier:.4f}→{new_brier:.4f})")
        elif promoted:
            lines.append("Decision: promoted (first model, passed absolute gate)")
        else:
            lines.append("Decision: candidate (did not beat production model)")
    else:
        lines.append("Model retrained: no")
        if reason:
            lines.append(f"Reason: {reason}")

    age_str = f"{model_age_h:.0f}h" if model_age_h is not None else "unknown"
    lines.append(f"Model age: {age_str}")
    lines.append(f"Model status: {model_status}")
    return lines + [""]


def _section_results(settlement_result: dict[str, Any]) -> list[str]:
    settled = settlement_result.get("settled_count", 0)
    wins = settlement_result.get("wins", 0)
    losses = settlement_result.get("losses", 0)
    pushes = settlement_result.get("pushes", 0)
    pnl = settlement_result.get("pnl_units")
    roi = settlement_result.get("roi_pct")
    hit_rate = settlement_result.get("hit_rate_pct")
    drift_status = settlement_result.get("drift_status", "no_data")
    kelly = settlement_result.get("kelly_multiplier", 1.0)

    lines = ["📈 Results"]
    lines.append(f"Settled since last report: {settled}")
    lines.append(f"W/L/P: {wins}/{losses}/{pushes}")
    if pnl is not None:
        sign = "+" if pnl >= 0 else ""
        lines.append(f"PnL: {sign}{pnl:.2f}u")
    if roi is not None:
        sign = "+" if roi >= 0 else ""
        lines.append(f"ROI: {sign}{roi:.1f}%")
    if hit_rate is not None:
        lines.append(f"Hit rate: {hit_rate:.1f}%")
    lines.append(f"Drift: {drift_status}")
    if kelly != 1.0:
        lines.append(f"Kelly multiplier: {kelly:.2f}")
    return lines + [""]


def _section_health() -> list[str]:
    lines = ["🏥 Health"]

    # Ledger
    ledger_status = "missing"
    if LEDGER_PATH.exists():
        try:
            data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
            total = len(data.get("entries", {}))
            ledger_status = f"OK ({total} entries)"
        except Exception:
            ledger_status = "unreadable"
    lines.append(f"Ledger: {ledger_status}")

    # Model freshness
    meta_files = sorted(glob(str(MODEL_DIR / "dc_*.meta.json")))
    prod_metas = []
    for mf in meta_files:
        try:
            m = json.loads(Path(mf).read_text())
            if m.get("status") == "production":
                prod_metas.append(m)
        except Exception:
            pass

    if prod_metas:
        latest = max(prod_metas, key=lambda m: m.get("created_at_utc", ""))
        created = latest.get("created_at_utc", "")
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_h = round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)
            lines.append(f"Model: OK ({latest.get('league')} {age_h}h old)")
        except Exception:
            lines.append(f"Model: OK ({latest.get('league')})")
    else:
        lines.append("Model: no production model")

    # Disk
    def _mb(p: Path) -> float:
        if not p.exists():
            return 0.0
        return round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1_048_576, 2)

    total_mb = _mb(DATA_DIR)
    lines.append(f"Disk: {total_mb:.1f} MB used")

    # Last runs from run history
    try:
        from src.models.run_history import read_last_run
        for job, label in [
            ("signal_scan", "Last signal scan"),
            ("settlement", "Last settlement"),
            ("training_check", "Last training check"),
        ]:
            rec = read_last_run(job)
            if rec:
                ts = rec.get("started_at", "?")[:16].replace("T", " ")
                status = rec.get("status", "?")
                lines.append(f"{label}: {ts} UTC ({status})")
            else:
                lines.append(f"{label}: never")
    except Exception:
        pass

    # Providers
    provider_parts = ["OpenFootball OK", "Odds API " + (
        "OK" if os.environ.get("THE_ODDS_API_KEY") else "no key"
    ), "Flashscore disabled"]
    lines.append("Providers: " + ", ".join(provider_parts))

    # Errors from last runs
    try:
        from src.models.run_history import read_recent
        recent = read_recent(50)
        recent_errors = [
            e for rec in recent for e in rec.get("errors", [])
        ][-3:]
        if recent_errors:
            lines.append(f"Recent errors: {'; '.join(recent_errors)}")
        else:
            lines.append("Errors: none")
    except Exception:
        lines.append("Errors: unknown")

    return lines
