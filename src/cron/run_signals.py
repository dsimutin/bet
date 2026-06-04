"""Unified quota-efficient paper signal runtime for football and tennis."""

from __future__ import annotations
import os
import sys
import time
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> dict[str, Any]:
    return run_signal_job()


def run_signal_job(
    *,
    run_league_func=None,
    run_tennis_func=None,
    run_exotic_func=None,
    load_ledger_func=None,
    save_ledger_func=None,
    notify_priority_func=None,
) -> dict[str, Any]:
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
    steps: dict[str, dict[str, Any]] = {}
    print(f"[signals] unified scan started {datetime.now(timezone.utc).isoformat()}")
    run_league_func = run_league_func or _run_league
    run_tennis_func = run_tennis_func or _run_tennis
    run_exotic_func = run_exotic_func or _run_exotic

    for league in leagues:
        step_name = f"football_{league}"
        try:
            rows = run_league_func(league, model_dir, staging_dir, today)
            candidates.extend(rows)
            steps[step_name] = {"status": "success", "candidates": len(rows)}
            print(f"[signals] football {league}: {len(rows)} candidate(s)")
        except Exception as exc:
            steps[step_name] = {"status": "failed", "error": _sanitize_error(exc)}
            print(f"[signals] football {league}: FAILED {_sanitize_error(exc)}", file=sys.stderr)

    try:
        tennis = run_tennis_func(model_dir)
        candidates.extend(tennis)
        steps["tennis"] = {"status": "success", "candidates": len(tennis)}
        print(f"[signals] tennis: {len(tennis)} candidate(s)")
    except Exception as exc:
        steps["tennis"] = {"status": "failed", "error": _sanitize_error(exc)}
        print(f"[signals] tennis: FAILED {_sanitize_error(exc)}", file=sys.stderr)

    try:
        exotic = run_exotic_func()
        candidates.extend(exotic)
        steps["exotic"] = {"status": "success", "candidates": len(exotic)}
        print(f"[signals] exotic: {len(exotic)} candidate(s)")
    except Exception as exc:
        steps["exotic"] = {"status": "failed", "error": _sanitize_error(exc)}
        print(f"[signals] exotic: FAILED {_sanitize_error(exc)}", file=sys.stderr)

    from src.infrastructure.persistent_ledger import load_ledger, save_ledger
    from src.models.feedback_policy import FeedbackPolicy

    load_ledger_func = load_ledger_func or load_ledger
    save_ledger_func = save_ledger_func or save_ledger
    notify_priority_func = notify_priority_func or _notify_priority

    try:
        ledger = load_ledger_func(ledger_path)
        steps["ledger_load"] = {"status": "success"}
    except Exception as exc:
        elapsed = round(time.perf_counter() - started, 1)
        steps["ledger_load"] = {"status": "failed", "error": _sanitize_error(exc)}
        result = _signal_job_result(
            status="failed",
            elapsed=elapsed,
            candidates=len(candidates),
            priority=0,
            watchlist=0,
            blocked=0,
            new_priority=0,
            sent=0,
            failed=0,
            dry_run=0,
            steps=steps,
        )
        _record_signal_job_result(result)
        return result

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
        if signal.get("odds_freshness_tier") == "watchlist" and decision.tier == "priority":
            signal["recommendation_tier"] = "watchlist"
            signal["recommendation_reason"] = "odds snapshot is watchlist-fresh, not priority-fresh"
            watchlist.append(signal)
            continue
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

    try:
        save_ledger_func(ledger, ledger_path)
        steps["ledger_save_before_delivery"] = {"status": "success"}
    except Exception as exc:
        elapsed = round(time.perf_counter() - started, 1)
        steps["ledger_save_before_delivery"] = {"status": "failed", "error": _sanitize_error(exc)}
        result = _signal_job_result(
            status="failed",
            elapsed=elapsed,
            candidates=len(candidates),
            priority=len(priority),
            watchlist=len(watchlist),
            blocked=len(blocked),
            new_priority=len(new_priority),
            sent=0,
            failed=0,
            dry_run=0,
            steps=steps,
        )
        _record_signal_job_result(result)
        return result
    try:
        sent, failed, dry_run = notify_priority_func(new_priority, ledger)
        steps["telegram"] = {
            "status": "success" if failed == 0 else "partial",
            "sent": sent,
            "failed": failed,
            "dry_run": dry_run,
        }
    except Exception as exc:
        sent, failed, dry_run = 0, len(new_priority), 0
        steps["telegram"] = {"status": "failed", "error": _sanitize_error(exc)}
    try:
        save_ledger_func(ledger, ledger_path)
        steps["ledger_save_after_delivery"] = {"status": "success"}
    except Exception as exc:
        elapsed = round(time.perf_counter() - started, 1)
        steps["ledger_save_after_delivery"] = {"status": "failed", "error": _sanitize_error(exc)}
        result = _signal_job_result(
            status="failed",
            elapsed=elapsed,
            candidates=len(candidates),
            priority=len(priority),
            watchlist=len(watchlist),
            blocked=len(blocked),
            new_priority=len(new_priority),
            sent=sent,
            failed=failed,
            dry_run=dry_run,
            steps=steps,
        )
        _record_signal_job_result(result)
        return result

    elapsed = round(time.perf_counter() - started, 1)
    print(
        f"[signals] done {elapsed}s candidates={len(candidates)} priority={len(priority)} watchlist={len(watchlist)} blocked={len(blocked)} new_priority={len(new_priority)} sent={sent} failed={failed}"
    )
    status = _derive_signal_job_status(steps)
    if failed:
        status = "partial"
    result = _signal_job_result(
        status,
        elapsed,
        len(candidates),
        len(priority),
        len(watchlist),
        len(blocked),
        len(new_priority),
        sent,
        failed,
        dry_run,
        steps,
    )
    _record_signal_job_result(result)
    return result


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
    # Run if either odds-api.io OR The Odds API key is configured
    odds_io_configured = bool(os.environ.get("ODDS_API_IO_KEY", "").strip())
    if not api_key and not odds_io_configured:
        print("[signals] tennis: skipped (no THE_ODDS_API_KEY or ODDS_API_IO_KEY configured)")
        return []
    from src.signals.tennis_runtime_scan import scan_tennis_h2h_runtime

    result = scan_tennis_h2h_runtime(model_dir / "tennis_elo_atp_latest.pkl", api_key)
    return list(result.get("all_signals", []))


def _partition_by_timestamp_policy(
    signals: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    from src.models.timestamp_policy import verify_pre_match_timestamps

    allowed: list[dict[str, Any]] = []
    blocked: list[tuple[dict[str, Any], str]] = []
    for signal in signals:
        status = verify_pre_match_timestamps(signal)
        signal["timestamp_verification_status"] = status
        if status == "missing_timestamp":
            blocked.append((signal, "missing verified pre-match timestamps"))
            continue
        if status == "invalid_timestamp":
            blocked.append((signal, "invalid pre-match timestamp"))
            continue
        if status == "post_start":
            blocked.append((signal, "odds snapshot is not earlier than event start"))
            continue
        if signal.get("odds_freshness_tier") == "stale_blocked":
            signal["timestamp_verification_status"] = "stale_snapshot"
            blocked.append((signal, "odds snapshot is too old for paper signal"))
            continue
        allowed.append(signal)
    return allowed, blocked


def _notify_priority(signals: list[dict[str, Any]], ledger) -> tuple[int, int, int]:
    from src.integrations.telegram_sender import TelegramConfig, TelegramSender

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    dry_mode = not (token and chat_id)
    sender = TelegramSender(
        TelegramConfig(
            bot_token=token or "dry-run",
            chat_id=chat_id or "dry-run",
            dry_run=dry_mode,
            max_message_length=4096,
        )
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
    odds = _html_text(signal.get("entry_odds", "?"))
    edge = _html_text(signal.get("edge_pct", signal.get("edge_vs_fair_pct", "?")))
    prob = signal.get("model_probability", signal.get("model_prob"))
    prob_text = f"{float(prob):.1%}" if isinstance(prob, (int, float)) else "?"
    is_exotic = signal.get("model_source") == "bayesian_zero_shot"

    if sport == "tennis":
        title = f"🎾 <b>ПРИОРИТЕТНЫЙ PAPER-СИГНАЛ</b>\nПобеда: <b>{_html_text(signal.get('player', '?'))}</b>\nСоперник: {_html_text(signal.get('opponent', '?'))}"
        extras = f"\nПокрытие: {_html_text(signal.get('surface', '?'))}\nФорма: {_html_text(signal.get('recent_form', '?'))} | Отдых: {_html_text(signal.get('days_since_last_match', '?'))} дн."
        disclaimer = "📄 Бумажный сигнал. Проверьте линию перед любым самостоятельным решением."
    elif is_exotic:
        league_name = _html_text(signal.get("league_name", signal.get("league", "?")))
        title = f"🌍 <b>WATCHLIST (экзотика)</b> — {league_name}\nМатч: <b>{_html_text(signal.get('home_team', '?'))} — {_html_text(signal.get('away_team', '?'))}</b>\nИсход: <b>{_html_text(signal.get('selection_ru', signal.get('selection', '?')))}</b>"
        extras = f"\nМаржа БК: {_html_text(signal.get('margin_pct', '?'))}%"
        disclaimer = "⚠️ Байесовская модель без истории лиги. Только Watchlist. Проверьте вручную."
    else:
        title = f"⚽ <b>ПРИОРИТЕТНЫЙ PAPER-СИГНАЛ</b>\nМатч: <b>{_html_text(signal.get('home_team', '?'))} — {_html_text(signal.get('away_team', '?'))}</b>\nИсход: <b>{_html_text(signal.get('selection_ru', signal.get('selection', '?')))}</b>"
        extras = f"\nМодель: {_html_text(signal.get('model_id', '?'))}"
        disclaimer = "📄 Бумажный сигнал. Проверьте линию перед любым самостоятельным решением."
    return f"{title}\nКоэффициент: <b>{odds}</b>\nВероятность модели: <b>{prob_text}</b>\nEdge: <b>{edge}%</b>{extras}\n\n{disclaimer}"


def _html_text(value: Any) -> str:
    return escape(str(value), quote=False)


def _signal_job_result(
    status: str,
    elapsed: float,
    candidates: int,
    priority: int,
    watchlist: int,
    blocked: int,
    new_priority: int,
    sent: int,
    failed: int,
    dry_run: int,
    steps: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "job_name": "signal-pipeline",
        "status": status,
        "finished_at_utc": now,
        "duration_s": elapsed,
        "candidates": candidates,
        "priority": priority,
        "watchlist": watchlist,
        "blocked": blocked,
        "new_priority": new_priority,
        "sent": sent,
        "failed": failed,
        "dry_run": dry_run,
        "steps": steps,
    }


def _derive_signal_job_status(steps: dict[str, dict[str, Any]]) -> str:
    statuses = [str(step.get("status", "")) for step in steps.values()]
    if not statuses:
        return "skipped"
    if any(status == "failed" for status in statuses):
        source_statuses = [
            str(step.get("status", ""))
            for name, step in steps.items()
            if name.startswith("football_") or name in {"tennis", "exotic"}
        ]
        if source_statuses and all(status == "failed" for status in source_statuses):
            return "failed"
        return "partial"
    if any(status == "partial" for status in statuses):
        return "partial"
    return "success"


def _record_signal_job_result(result: dict[str, Any]) -> None:
    _log_run(
        str(result["job_name"]),
        str(result["status"]),
        float(result["duration_s"]),
        (
            f"priority={result['priority']} watchlist={result['watchlist']} "
            f"blocked={result['blocked']} sent={result['sent']} failed={result['failed']}"
        ),
        result,
    )


def _sanitize_error(exc: Exception) -> str:
    text = str(exc)
    for key, value in os.environ.items():
        if value and ("KEY" in key or "TOKEN" in key or "SECRET" in key or "DATABASE_URL" == key):
            text = text.replace(value, f"[REDACTED_{key}]")
    return text


def _log_run(
    job: str, status: str, duration_s: float, message: str, meta: dict[str, Any] | None = None
) -> None:
    try:
        from src.infrastructure.render_db import get_db

        get_db().log_cron_run(job, status, duration_s, message, meta)
    except Exception as exc:
        print(f"[signals] audit log failed (non-critical): {exc}", file=sys.stderr)


if __name__ == "__main__":
    result = main()
    if result.get("status") == "failed":
        raise SystemExit(1)
