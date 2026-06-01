"""Production signal scan entrypoint for Render APScheduler.

The job is intentionally idempotent:
- generated signals are registered in the persistent ledger first;
- only newly registered signals are delivered to Telegram;
- delivery result is persisted after every attempt;
- football signals with unverified pre-match timestamps are blocked by default.

Paper trading only.  No bookmaker transactions are performed.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    started = time.perf_counter()
    leagues = [x.strip() for x in os.environ.get("LEAGUES", "EPL,BUNDESLIGA,LALIGA,SERIEA").split(",") if x.strip()]
    model_dir = Path(os.environ.get("MODEL_DIR", "data/models"))
    ledger_path = Path(os.environ.get("LEDGER_PATH", "data/core/paper_signal_ledger.json"))
    staging_dir = Path(os.environ.get("STAGING_DIR", "data/staging"))
    today = date.today()

    print(f"[signals] Starting at {datetime.now(timezone.utc).isoformat()}")
    print(f"[signals] Leagues: {leagues} | Date: {today}")

    candidates: list[dict[str, Any]] = []
    for league in leagues:
        try:
            league_signals = _run_league(league, model_dir, staging_dir, today)
            candidates.extend(league_signals)
            print(f"[signals] {league}: {len(league_signals)} candidate signal(s)")
        except Exception as exc:
            print(f"[signals] {league}: FAILED — {exc}", file=sys.stderr)

    tennis_signals = _run_tennis(model_dir)
    candidates.extend(tennis_signals)
    print(f"[signals] tennis: {len(tennis_signals)} h2h candidate signal(s)")

    if not candidates:
        elapsed = round(time.perf_counter() - started, 1)
        print("[signals] No signals generated.")
        _log_run("signal-pipeline", "no_signals", elapsed, "0 signals")
        return

    from src.infrastructure.persistent_ledger import load_ledger, save_ledger

    ledger = load_ledger(ledger_path)
    deliverable, blocked = _partition_by_timestamp_policy(candidates)
    for signal, reason in blocked:
        if ledger.add_signal(signal, stake_units=float(signal.get("stake_units", 1.0))):
            ledger.mark_delivery(signal["signal_id"], "blocked", block_reason=reason)

    add_result = ledger.add_signals(deliverable)
    new_signals = add_result.added
    duplicate_count = len(add_result.duplicates)
    save_ledger(ledger, ledger_path)

    print(
        f"[signals] Registered new={len(new_signals)} duplicates={duplicate_count} "
        f"blocked={len(blocked)}"
    )

    sent, failed, dry_run = _notify_telegram(new_signals, ledger)
    save_ledger(ledger, ledger_path)

    elapsed = round(time.perf_counter() - started, 1)
    print(
        f"[signals] Done in {elapsed}s | candidates={len(candidates)} new={len(new_signals)} "
        f"sent={sent} failed={failed} dry_run={dry_run}"
    )
    _log_run(
        "signal-pipeline",
        "success" if failed == 0 else "partial",
        elapsed,
        f"new={len(new_signals)} sent={sent} failed={failed} duplicates={duplicate_count} blocked={len(blocked)}",
        {
            "candidates": len(candidates),
            "new_signals": len(new_signals),
            "duplicates": duplicate_count,
            "blocked": len(blocked),
            "sent": sent,
            "failed": failed,
            "dry_run": dry_run,
            "leagues": leagues,
        },
    )


def _run_league(league: str, model_dir: Path, staging_dir: Path, today: date) -> list[dict[str, Any]]:
    from src.models.model_registry import ModelRegistry
    from src.signals.run_signal_scan import generate_signals_for_league

    registry = ModelRegistry(model_dir)
    try:
        model = registry.load_latest(league, production_only=True)
    except FileNotFoundError:
        print(f"[signals] {league}: no production model — skipping")
        return []

    calibrator = None
    try:
        calibrator = registry.load_calibrator(model.model_id)
    except Exception:
        pass

    return generate_signals_for_league(
        model=model,
        league=league,
        scan_date=today,
        staging_dir=staging_dir,
        odds_api_key=os.environ.get("THE_ODDS_API_KEY", ""),
        calibrator=calibrator,
    )


def _run_tennis(model_dir: Path) -> list[dict[str, Any]]:
    """Generate only h2h tennis signals until market-specific settlement is implemented."""
    api_key = os.environ.get("THE_ODDS_API_KEY", "")
    if not api_key:
        return []

    from src.signals.tennis_signal_scan import scan_tennis_signals

    result = scan_tennis_signals(
        model_path=model_dir / "tennis_elo_atp_latest.pkl",
        api_key=api_key,
    )
    signals = result.get("all_signals", [])
    return [signal for signal in signals if signal.get("market", "h2h") == "h2h"]


def _partition_by_timestamp_policy(
    signals: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    """Block football odds that cannot be proven to pre-date kick-off."""
    if _env_bool("ALLOW_UNVERIFIED_ODDS", False):
        return signals, []

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
            blocked.append((signal, "invalid pre-match timestamp format"))
            continue
        if snapshot >= event_time:
            blocked.append((signal, "odds snapshot is not earlier than kick-off"))
            continue
        allowed.append(signal)
    return allowed, blocked


def _notify_telegram(signals: list[dict[str, Any]], ledger) -> tuple[int, int, int]:
    """Deliver only newly registered signals and persist delivery outcome."""
    from src.integrations.telegram_sender import TelegramConfig, TelegramSender

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    dry_run_mode = not (token and chat_id)
    sender = TelegramSender(TelegramConfig(bot_token=token or "dry-run", chat_id=chat_id or "dry-run", dry_run=dry_run_mode))

    sent = failed = dry_runs = 0
    for signal in signals:
        signal_id = str(signal["signal_id"])
        try:
            if signal.get("sport") == "tennis":
                result = sender.send_message(_format_tennis_h2h(signal))
            else:
                result = sender.send_signal(signal)
            if result.get("ok"):
                status = "dry_run" if result.get("dry_run") else "sent"
                ledger.mark_delivery(signal_id, status, delivery_result=_safe_delivery_result(result))
                if status == "sent":
                    sent += 1
                else:
                    dry_runs += 1
            else:
                ledger.mark_delivery(signal_id, "failed", delivery_result=_safe_delivery_result(result))
                failed += 1
        except Exception as exc:
            ledger.mark_delivery(signal_id, "failed", delivery_result={"error": str(exc)})
            failed += 1
            print(f"[signals] Telegram failed for {signal_id}: {exc}", file=sys.stderr)
    return sent, failed, dry_runs


def _safe_delivery_result(result: dict[str, Any]) -> dict[str, Any]:
    """Avoid persisting large Telegram payloads and chat identifiers."""
    return {
        "ok": bool(result.get("ok")),
        "dry_run": bool(result.get("dry_run")),
        "error": result.get("error"),
        "description": result.get("description"),
    }


def _format_tennis_h2h(signal: dict[str, Any]) -> str:
    player = signal.get("player", "?")
    opponent = signal.get("opponent", "?")
    odds = signal.get("entry_odds", "?")
    edge = signal.get("edge_pct", "?")
    probability = signal.get("model_prob", 0)
    prob_text = f"{float(probability):.1%}" if isinstance(probability, (int, float)) else str(probability)
    return (
        "🎾 <b>PAPER SIGNAL — теннис</b>\n"
        f"Победа: <b>{player}</b>\n"
        f"Соперник: {opponent}\n"
        f"Коэффициент: <b>{odds}</b>\n"
        f"Вероятность модели: {prob_text}\n"
        f"Edge: <b>{edge}%</b>\n"
        "📄 Бумажная ставка — реальные деньги не используются"
    )


def _log_run(job: str, status: str, duration_s: float, message: str, meta: dict[str, Any] | None = None) -> None:
    try:
        from src.infrastructure.render_db import get_db

        get_db().log_cron_run(job, status, duration_s, message, meta)
    except Exception:
        pass


if __name__ == "__main__":
    main()
