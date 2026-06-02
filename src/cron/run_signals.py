"""Unified quota-efficient paper signal runtime for football and tennis."""

from __future__ import annotations
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> None:
    started = time.perf_counter()
    model_dir = Path(os.environ.get("MODEL_DIR", "data/models"))
    ledger_path = Path(os.environ.get("LEDGER_PATH", "data/core/paper_signal_ledger.json"))
    staging_dir = Path(os.environ.get("STAGING_DIR", "data/staging"))
    leagues = [
        x.strip()
        for x in os.environ.get("LEAGUES", "EPL,BUNDESLIGA,LALIGA,SERIEA,LIGUE1").split(",")
        if x.strip()
    ]
    today = date.today()
    candidates: list[dict[str, Any]] = []
    print(f"[signals] unified scan started {datetime.now(timezone.utc).isoformat()}")

    for league in leagues:
        try:
            rows = _run_league(league, model_dir, staging_dir, today)
            candidates.extend(rows)
            print(f"[signals] football {league}: {len(rows)} candidate(s)")
        except Exception as exc:
            print(f"[signals] football {league}: FAILED {exc}", file=sys.stderr)

    try:
        tennis = _run_tennis(model_dir)
        candidates.extend(tennis)
        print(f"[signals] tennis: {len(tennis)} candidate(s)")
    except Exception as exc:
        print(f"[signals] tennis: FAILED {exc}", file=sys.stderr)

    try:
        exotic = _run_exotic()
        candidates.extend(exotic)
        print(f"[signals] exotic: {len(exotic)} candidate(s)")
    except Exception as exc:
        print(f"[signals] exotic: FAILED {exc}", file=sys.stderr)

    from src.infrastructure.persistent_ledger import load_ledger, save_ledger
    from src.models.feedback_policy import FeedbackPolicy

    ledger = load_ledger(ledger_path)

    # Expire signals whose event passed >24h ago without settlement result.
    # Runs before policy evaluation so stale signals don't skew segment stats.
    expired_ids = ledger.expire_stale_signals(hours_past_event=24.0)
    if expired_ids:
        print(f"[signals] expired {len(expired_ids)} stale open signal(s)")

    policy = FeedbackPolicy(ledger.entries())

    verified, timestamp_blocked = _partition_by_timestamp_policy(candidates)
    priority: list[dict[str, Any]] = []
    watchlist: list[dict[str, Any]] = []
    blocked: list[tuple[dict[str, Any], str]] = list(timestamp_blocked)

    for signal in verified:
        decision = policy.evaluate(signal)
        signal["recommendation_tier"] = decision.tier
        signal["recommendation_reason"] = decision.reason
        signal["feedback_policy"] = decision.details
        if decision.tier == "priority":
            priority.append(signal)
        elif decision.tier == "watchlist":
            watchlist.append(signal)
        else:
            blocked.append((signal, decision.reason))

    visible_result = ledger.add_signals(priority + watchlist)
    new_ids = {str(item["signal_id"]) for item in visible_result.added}
    new_priority = [item for item in priority if str(item["signal_id"]) in new_ids]
    for signal, reason in blocked:
        try:
            if ledger.add_signal(signal, stake_units=float(signal.get("stake_units", 1.0))):
                ledger.mark_delivery(str(signal["signal_id"]), "blocked", block_reason=reason)
        except Exception as exc:
            print(f"[signals] blocked registration failed: {exc}", file=sys.stderr)

    save_ledger(ledger, ledger_path)
    sent, failed, dry_run = _notify_priority(new_priority, ledger)
    save_ledger(ledger, ledger_path)

    elapsed = round(time.perf_counter() - started, 1)
    print(
        f"[signals] done {elapsed}s candidates={len(candidates)} priority={len(priority)} watchlist={len(watchlist)} blocked={len(blocked)} new_priority={len(new_priority)} sent={sent} failed={failed}"
    )
    _log_run(
        "signal-pipeline",
        "success" if failed == 0 else "partial",
        elapsed,
        f"priority={len(priority)} watchlist={len(watchlist)} blocked={len(blocked)} sent={sent} failed={failed}",
        {
            "candidates": len(candidates),
            "priority": len(priority),
            "watchlist": len(watchlist),
            "blocked": len(blocked),
            "new_priority": len(new_priority),
            "sent": sent,
            "failed": failed,
            "dry_run": dry_run,
        },
    )


def _run_league(
    league: str, model_dir: Path, staging_dir: Path, today: date
) -> list[dict[str, Any]]:
    from src.models.model_registry import ModelRegistry
    from src.signals.football_runtime_scan import generate_football_signals_runtime

    registry = ModelRegistry(model_dir)
    try:
        model = registry.load_latest(league, production_only=True)
    except FileNotFoundError:
        return []
    calibrator = None
    try:
        if model.model_id:
            calibrator = registry.load_calibrator(model.model_id)
    except Exception:
        pass
    return generate_football_signals_runtime(
        model, league, today, staging_dir, os.environ.get("THE_ODDS_API_KEY", ""), calibrator
    )


def _run_exotic() -> list[dict[str, Any]]:
    api_key = os.environ.get("THE_ODDS_API_KEY", "")
    if not api_key:
        return []
    from src.signals.exotic_zero_shot_scan import scan_exotic_leagues
    enabled_raw = os.environ.get("EXOTIC_LEAGUES", "")
    enabled = [s.strip() for s in enabled_raw.split(",") if s.strip()] if enabled_raw else None
    return scan_exotic_leagues(api_key, leagues=enabled)


def _run_tennis(model_dir: Path) -> list[dict[str, Any]]:
    api_key = os.environ.get("THE_ODDS_API_KEY", "")
    if not api_key:
        return []
    from src.signals.tennis_runtime_scan import scan_tennis_h2h_runtime

    result = scan_tennis_h2h_runtime(model_dir / "tennis_elo_atp_latest.pkl", api_key)
    return list(result.get("all_signals", []))


def _partition_by_timestamp_policy(
    signals: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    allowed: list[dict[str, Any]] = []
    blocked: list[tuple[dict[str, Any], str]] = []
    for signal in signals:
        if signal.get("sport") == "tennis":
            allowed.append(signal)
            continue
        snapshot_raw = str(signal.get("snapshot_ts_utc") or "").strip()
        event_raw = str(signal.get("event_time_utc") or "").strip()
        if not snapshot_raw or not event_raw:
            blocked.append((signal, "missing verified pre-match timestamps"))
            continue
        try:
            snapshot = datetime.fromisoformat(snapshot_raw.replace("Z", "+00:00"))
            event_time = datetime.fromisoformat(event_raw.replace("Z", "+00:00"))
        except ValueError:
            blocked.append((signal, "invalid pre-match timestamp"))
            continue
        if snapshot >= event_time:
            blocked.append((signal, "odds snapshot is not earlier than event start"))
            continue
        allowed.append(signal)
    return allowed, blocked


def _notify_priority(signals: list[dict[str, Any]], ledger) -> tuple[int, int, int]:
    from src.integrations.telegram_sender import TelegramConfig, TelegramSender

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    dry_mode = not (token and chat_id)
    sender = TelegramSender(
        TelegramConfig(bot_token=token or "dry-run", chat_id=chat_id or "dry-run", dry_run=dry_mode)
    )
    sent = failed = dry_runs = 0
    for signal in signals:
        signal_id = str(signal["signal_id"])
        result = sender.send_message(_format_priority_alert(signal))
        if result.get("ok"):
            status = "dry_run" if result.get("dry_run") else "sent"
            ledger.mark_delivery(
                signal_id,
                status,
                delivery_result={"ok": True, "dry_run": bool(result.get("dry_run"))},
            )
            dry_runs += int(status == "dry_run")
            sent += int(status == "sent")
        else:
            ledger.mark_delivery(
                signal_id, "failed", delivery_result={"ok": False, "error": result.get("error")}
            )
            failed += 1
    return sent, failed, dry_runs


def _format_priority_alert(signal: dict[str, Any]) -> str:
    sport = signal.get("sport", "football")
    odds = signal.get("entry_odds", "?")
    edge = signal.get("edge_pct", signal.get("edge_vs_fair_pct", "?"))
    prob = signal.get("model_probability", signal.get("model_prob"))
    prob_text = f"{float(prob):.1%}" if isinstance(prob, (int, float)) else "?"
    is_exotic = signal.get("model_source") == "bayesian_zero_shot"

    if sport == "tennis":
        title = f"🎾 <b>ПРИОРИТЕТНЫЙ PAPER-СИГНАЛ</b>\nПобеда: <b>{signal.get('player', '?')}</b>\nСоперник: {signal.get('opponent', '?')}"
        extras = f"\nПокрытие: {signal.get('surface', '?')}\nФорма: {signal.get('recent_form', '?')} | Отдых: {signal.get('days_since_last_match', '?')} дн."
        disclaimer = "📄 Бумажный сигнал. Проверьте линию перед любым самостоятельным решением."
    elif is_exotic:
        league_name = signal.get("league_name", signal.get("league", "?"))
        title = f"🌍 <b>WATCHLIST (экзотика)</b> — {league_name}\nМатч: <b>{signal.get('home_team', '?')} — {signal.get('away_team', '?')}</b>\nИсход: <b>{signal.get('selection_ru', signal.get('selection', '?'))}</b>"
        extras = f"\nМаржа БК: {signal.get('margin_pct', '?')}%"
        disclaimer = "⚠️ Байесовская модель без истории лиги. Только Watchlist. Проверьте вручную."
    else:
        title = f"⚽ <b>ПРИОРИТЕТНЫЙ PAPER-СИГНАЛ</b>\nМатч: <b>{signal.get('home_team', '?')} — {signal.get('away_team', '?')}</b>\nИсход: <b>{signal.get('selection_ru', signal.get('selection', '?'))}</b>"
        extras = f"\nМодель: {signal.get('model_id', '?')}"
        disclaimer = "📄 Бумажный сигнал. Проверьте линию перед любым самостоятельным решением."
    return f"{title}\nКоэффициент: <b>{odds}</b>\nВероятность модели: <b>{prob_text}</b>\nEdge: <b>{edge}%</b>{extras}\n\n{disclaimer}"


def _log_run(
    job: str, status: str, duration_s: float, message: str, meta: dict[str, Any] | None = None
) -> None:
    try:
        from src.infrastructure.render_db import get_db

        get_db().log_cron_run(job, status, duration_s, message, meta)
    except Exception:
        pass


if __name__ == "__main__":
    main()
