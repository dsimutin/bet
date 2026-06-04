"""Cron entrypoint: settle paper ledger + drift check."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> None:
    result = run_settlement_job()
    if result.get("status") == "failed":
        raise SystemExit(1)


def run_settlement_job(
    *,
    load_ledger_func: Callable[[Path], Any] | None = None,
    save_ledger_func: Callable[[Any, Path], Any] | None = None,
    openfootball_loader_factory: Callable[[], Any] | None = None,
    football_settle_func: Callable[[Any, Any, str], dict[str, Any]] | None = None,
    tennis_settle_func: Callable[[Any, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    leagues = [
        x.strip()
        for x in os.environ.get("LEAGUES", "EPL,BUNDESLIGA,LALIGA,SERIEA,LIGUE1").split(",")
        if x.strip()
    ]
    seasons = [
        x.strip()
        for x in os.environ.get("OPENFOOTBALL_SEASONS", "2023-24,2024-25,2025-26").split(",")
        if x.strip()
    ]
    staging_dir = Path(os.environ.get("STAGING_DIR", "data/staging"))
    ledger_path = Path(os.environ.get("LEDGER_PATH", "data/core/paper_signal_ledger.json"))
    reports_dir = Path(os.environ.get("REPORTS_DIR", "data/reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)

    print(f"[settle] Starting at {datetime.now(timezone.utc).isoformat()}")
    print(f"[settle] Leagues: {leagues} | Seasons: {seasons}")
    steps: dict[str, dict[str, Any]] = {
        "source_fetch": {"status": "skipped", "error": ""},
        "football_settlement": {"status": "skipped", "error": ""},
        "tennis_settlement": {"status": "skipped", "error": ""},
        "ledger_save": {"status": "skipped", "error": ""},
        "report_write": {"status": "skipped", "error": ""},
        "drift": {"status": "skipped", "error": ""},
    }

    try:
        from src.ingest.openfootball import OpenFootballLoader

        loader = (
            openfootball_loader_factory() if openfootball_loader_factory else OpenFootballLoader()
        )
        result = loader.build(leagues=leagues, seasons=seasons, use_cache=False)
        if result.dataframe.empty:
            print("[settle] No football results downloaded — continuing with tennis settlement.")
            football_df = None
            steps["source_fetch"] = {"status": "skipped", "error": "no_football_results"}
        else:
            csv_path = loader.save_combined(result.dataframe, staging_dir, "latest_results.csv")
            print(f"[settle] Downloaded {len(result.dataframe)} matches → {csv_path}")
            import pandas as pd

            football_df = pd.read_csv(csv_path, encoding="latin-1")
            steps["source_fetch"] = {"status": "success", "error": ""}
    except Exception as exc:
        print(f"[settle] Football result download failed: {exc}", file=sys.stderr)
        football_df = None
        steps["source_fetch"] = {"status": "failed", "error": _sanitize_error(exc)}

    from src.infrastructure.persistent_ledger import load_ledger, save_ledger

    load_ledger_func = load_ledger_func or load_ledger
    save_ledger_func = save_ledger_func or save_ledger
    api_key = os.environ.get("THE_ODDS_API_KEY", "").strip()
    try:
        ledger = load_ledger_func(ledger_path)
    except Exception as exc:
        elapsed = round(time.perf_counter() - t0, 1)
        steps["ledger_save"] = {"status": "failed", "error": _sanitize_error(exc)}
        return _finish_settlement_job(
            status="failed",
            elapsed=elapsed,
            total_settled=0,
            steps=steps,
            reports_dir=reports_dir,
            report_payload={"error": "ledger_load_failed"},
        )

    if not _has_open_settle_candidates(ledger):
        elapsed = round(time.perf_counter() - t0, 1)
        return _finish_settlement_job(
            status="skipped",
            elapsed=elapsed,
            total_settled=0,
            steps=steps,
            reports_dir=reports_dir,
            report_payload={"summary": ledger.summary(), "reason": "no_open_signals"},
        )

    football_report: dict = {"settled_count": 0, "settled_signals": [], "summary": ledger.summary()}
    if football_df is not None:
        try:
            from src.models.settle_signal_ledger import settle_ledger_from_results

            football_settle_func = football_settle_func or settle_ledger_from_results
            football_report = football_settle_func(ledger, football_df, api_key)
            print(f"[settle] Football: {football_report.get('settled_count', 0)} settled")
            steps["football_settlement"] = {"status": "success", "error": ""}
        except Exception as exc:
            print(f"[settle] Football settlement failed: {exc}", file=sys.stderr)
            steps["football_settlement"] = {"status": "failed", "error": _sanitize_error(exc)}

    tennis_report = (
        tennis_settle_func(ledger, api_key)
        if tennis_settle_func
        else _settle_tennis(ledger, api_key)
    )
    steps["tennis_settlement"] = (
        {"status": "failed", "error": _sanitize_text(str(tennis_report.get("error", "")))}
        if tennis_report.get("error")
        else {"status": "success", "error": ""}
    )

    # Auto-expire open signals whose event passed >24h ago without a matching result.
    # This prevents stale signals from accumulating as permanent "open" entries.
    expired_ids = ledger.expire_stale_signals(hours_past_event=24.0)
    if expired_ids:
        print(f"[settle] Expired {len(expired_ids)} stale unresolved signal(s): {expired_ids}")

    try:
        save_ledger_func(ledger, ledger_path)
        steps["ledger_save"] = {"status": "success", "error": ""}
        _notify_telegram_settlement(football_report, tennis_report, ledger)
    except Exception as exc:
        print(f"[settle] Ledger save failed: {exc}", file=sys.stderr)
        elapsed = round(time.perf_counter() - t0, 1)
        steps["ledger_save"] = {"status": "failed", "error": _sanitize_error(exc)}
        return _finish_settlement_job(
            status="failed",
            elapsed=elapsed,
            total_settled=0,
            steps=steps,
            reports_dir=reports_dir,
            report_payload={
                "football": football_report,
                "tennis": tennis_report,
                "summary": ledger.summary(),
            },
        )

    report = {
        "date": date.today().isoformat(),
        "football": football_report,
        "tennis": tennis_report,
        "summary": ledger.summary(),
        "steps": steps,
    }
    report_path = reports_dir / f"settlement_{date.today().isoformat()}.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    steps["report_write"] = {"status": "success", "error": ""}
    print(f"[settle] Report → {report_path}")

    try:
        from src.monitoring.drift_detector import CUSUMDriftDetector

        detector = CUSUMDriftDetector(threshold=0.15, drift_window=14, min_window=10)
        drift_report = detector.evaluate_ledger_path(ledger_path)
        drift_path = reports_dir / "drift_report.json"
        drift_path.write_text(
            json.dumps(drift_report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(
            f"[settle] Drift: {'DRIFT DETECTED' if drift_report.drift_detected else 'no drift'} "
            f"| kelly={drift_report.kelly_multiplier}"
        )
        steps["drift"] = {"status": "success", "error": ""}
    except Exception as exc:
        print(f"[settle] Drift check failed (non-critical): {exc}", file=sys.stderr)
        steps["drift"] = {"status": "partial", "error": _sanitize_error(exc)}

    elapsed = round(time.perf_counter() - t0, 1)
    total_settled = int(football_report.get("settled_count", 0)) + int(
        tennis_report.get("settled", 0)
    )
    status = _aggregate_settlement_status(steps, total_settled)
    print(f"[settle] Done in {elapsed}s | total settled={total_settled}")
    _log_run(
        "settle-ledger",
        status,
        elapsed,
        f"settled={total_settled}",
        {"total_settled": total_settled, "steps": steps},
    )
    return {
        "job_name": "settle-ledger",
        "status": status,
        "started_at_utc": report.get("date"),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "duration_s": elapsed,
        "total_settled": total_settled,
        "steps": steps,
        "report_path": str(report_path),
    }


def _settle_tennis(ledger, api_key: str = "") -> dict:
    """Settle only supported tennis h2h signals."""
    try:
        from src.models.settle_tennis_signals import settle_tennis_from_sackmann

        cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "raw" / "tennis_atp"
        report = settle_tennis_from_sackmann(ledger, cache_dir, api_key=api_key)
        print(
            f"[settle] Tennis: {report.get('settled', 0)} settled, "
            f"{report.get('unmatched', 0)} pending"
        )
        return report
    except Exception as exc:
        print(f"[settle] Tennis settlement failed (non-critical): {exc}", file=sys.stderr)
        return {"settled": 0, "unmatched": 0, "error": str(exc)}


def _has_open_settle_candidates(ledger: Any) -> bool:
    return any(entry.get("ledger_status") == "open" for entry in ledger.entries().values())


def _aggregate_settlement_status(steps: dict[str, dict[str, Any]], total_settled: int) -> str:
    if steps.get("ledger_save", {}).get("status") == "failed":
        return "failed"
    required = [
        steps.get("source_fetch", {}).get("status"),
        steps.get("football_settlement", {}).get("status"),
        steps.get("tennis_settlement", {}).get("status"),
        steps.get("ledger_save", {}).get("status"),
    ]
    if all(status in {"skipped", None} for status in required) and total_settled == 0:
        return "skipped"
    if any(status == "failed" for status in required):
        return "partial"
    return "success"


def _finish_settlement_job(
    *,
    status: str,
    elapsed: float,
    total_settled: int,
    steps: dict[str, dict[str, Any]],
    reports_dir: Path,
    report_payload: dict[str, Any],
) -> dict[str, Any]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": date.today().isoformat(),
        "status": status,
        "steps": steps,
        **report_payload,
    }
    report_path = reports_dir / f"settlement_{date.today().isoformat()}.json"
    try:
        report_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        steps["report_write"] = {"status": "success", "error": ""}
    except Exception as exc:
        steps["report_write"] = {"status": "failed", "error": _sanitize_error(exc)}
    _log_run(
        "settle-ledger",
        status,
        elapsed,
        f"settled={total_settled}",
        {"total_settled": total_settled, "steps": steps},
    )
    return {
        "job_name": "settle-ledger",
        "status": status,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "duration_s": elapsed,
        "total_settled": total_settled,
        "steps": steps,
        "report_path": str(report_path),
    }


def _sanitize_error(exc: BaseException) -> str:
    return _sanitize_text(str(exc))


def _sanitize_text(text: str) -> str:
    for env_name in (
        "DATABASE_URL",
        "THE_ODDS_API_KEY",
        "ODDS_API_IO_KEY",
        "API_FOOTBALL_KEY",
        "TELEGRAM_BOT_TOKEN",
        "ADMIN_API_TOKEN",
        "TELEGRAM_WEBHOOK_SECRET",
    ):
        value = os.environ.get(env_name, "")
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def _notify_telegram_settlement(
    football_report: dict, tennis_report: dict, ledger: Any
) -> None:
    try:
        all_settled = (
            football_report.get("settled_signals", []) +
            tennis_report.get("settled_signals", [])
        )
        if not all_settled:
            return
        from src.web.telegram_bot import notify_settlement_results

        notify_settlement_results(all_settled, ledger.summary())
    except Exception as exc:
        print(f"[settle] Telegram notification failed (non-critical): {exc}", file=sys.stderr)


def _log_run(
    job: str, status: str, duration_s: float, message: str, meta: dict | None = None
) -> None:
    try:
        from src.infrastructure.render_db import get_db

        get_db().log_cron_run(job, status, duration_s, message, meta)
    except Exception as exc:
        print(f"[settle] audit log failed (non-critical): {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
