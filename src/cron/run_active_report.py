"""Cron entrypoint: 3-hourly active monitoring report.

Runs signal scan, settlement, smart training check, then sends
a comprehensive status report to Telegram. Safe to call manually.

Scheduled via APScheduler (in web service) or as standalone CLI:
    python -m src.cron.run_active_report          # normal run
    python -m src.cron.run_active_report --force  # send immediately (bypass ACTIVE_MODE)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
_log = logging.getLogger("active_report")


# ---------------------------------------------------------------------------
# Env vars (all with safe defaults)
# ---------------------------------------------------------------------------

def _env_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).strip().lower() in ("1", "true", "yes")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


ACTIVE_MODE = _env_bool("ACTIVE_MODE", False)
FORCE_TRAINING = _env_bool("FORCE_TRAINING", False)
MIN_NEW_SETTLED = _env_int("MIN_NEW_SETTLED_MATCHES_FOR_TRAINING", 10)
TELEGRAM_STATUS_REPORTS_ENABLED = _env_bool("TELEGRAM_STATUS_REPORTS_ENABLED", True)
TELEGRAM_SIGNAL_ALERTS_ENABLED = _env_bool("TELEGRAM_SIGNAL_ALERTS_ENABLED", True)
SPORTS = os.environ.get("SPORTS", "football").split(",")

LEAGUES = os.environ.get("LEAGUES", "EPL,BUNDESLIGA,LALIGA,SERIEA,LIGUE1").split(",")
MODEL_DIR = Path(os.environ.get("MODEL_DIR", "data/models"))
LEDGER_PATH = Path(os.environ.get("LEDGER_PATH", "data/core/paper_signal_ledger.json"))
STAGING_DIR = Path(os.environ.get("STAGING_DIR", "data/staging"))
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", "data/reports"))
SEASONS = os.environ.get("OPENFOOTBALL_SEASONS", "2021-22,2022-23,2023-24,2024-25").split(",")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def main(force: bool = False) -> None:
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    _log.info("[active] Starting at %s%s", started.isoformat(), " (--force)" if force else "")
    _log.info("[active] Sports: %s | Leagues: %s", SPORTS, LEAGUES)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    signals_result: dict[str, Any] = {}
    settlement_result: dict[str, Any] = {}
    training_result: dict[str, Any] = {}
    errors: list[str] = []

    # Step 1: Signal scan
    try:
        signals_result = _run_signal_scan()
    except Exception as e:
        _log.error("[active] Signal scan failed: %s", e)
        errors.append(f"signal_scan: {e}")
        signals_result = _empty_signals_result("signal scan error: " + str(e))

    # Step 2: Settlement
    try:
        settlement_result = _run_settlement()
    except Exception as e:
        _log.error("[active] Settlement failed: %s", e)
        errors.append(f"settlement: {e}")
        settlement_result = _empty_settlement_result()

    # Step 3: Smart training check
    try:
        training_result = _run_training_check()
    except Exception as e:
        _log.error("[active] Training check failed: %s", e)
        errors.append(f"training_check: {e}")
        training_result = {"trained": False, "training_reason": f"error: {e}",
                           "model_status": "unknown"}

    # Step 4: Write run history
    elapsed = time.perf_counter() - t0
    finished = datetime.now(timezone.utc)
    _write_run_history("active_report", started, finished, elapsed,
                       signals_result, settlement_result, training_result, errors)

    # Step 5: Format and send report
    delivery_status = "skipped"
    if TELEGRAM_STATUS_REPORTS_ENABLED:
        try:
            delivery_status = _send_status_report(signals_result, settlement_result, training_result)
        except Exception as e:
            _log.error("[active] Status report send failed: %s", e)
            errors.append(f"telegram_report: {e}")
            delivery_status = "failed"
    else:
        _log.info("[active] Telegram status reports disabled (TELEGRAM_STATUS_REPORTS_ENABLED=false)")

    status = "partial" if errors else "success"
    _log.info("[active] Done in %.1fs | status=%s | signals=%d | settled=%d | tg=%s",
              elapsed, status, signals_result.get("signals_count", 0),
              settlement_result.get("settled_count", 0), delivery_status)


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------

def _run_signal_scan() -> dict[str, Any]:
    """Run signal scan across all configured leagues. Returns summary dict."""
    from src.models.model_registry import ModelRegistry
    from src.signals.run_signal_scan import generate_signals_for_league
    from src.models.signal_ledger import SignalLedger

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    api_key = os.environ.get("THE_ODDS_API_KEY", "")
    today = date.today()
    registry = ModelRegistry(MODEL_DIR)

    all_signals: list[dict] = []
    active_soccer_leagues: list[str] = []  # leagues with events right now per Odds API

    # Quick sports probe: find which soccer leagues have events today
    if api_key:
        active_soccer_leagues = _probe_active_soccer_leagues(api_key)
    candidates_checked = 0
    sent_count = 0
    dupes_skipped = 0
    providers_ok: list[str] = []
    providers_skip: list[str] = []
    source_errors: list[str] = []
    no_signal_reason = ""
    per_league: dict[str, dict] = {}  # league → {signals, model_brier, status}

    # Football signals (only sport with model)
    if "football" in SPORTS:
        if api_key:
            providers_ok.append("Odds API")
        else:
            providers_skip.append("Odds API (no key)")
            providers_ok.append("OpenFootball (staged)")

        for league in LEAGUES:
            league_info: dict[str, Any] = {"signals": 0, "model_brier": None, "status": "ok"}
            try:
                model = registry.load_latest(league, production_only=True)
                # Grab brier score from model meta
                try:
                    league_info["model_brier"] = getattr(model, "brier_score", None)
                except Exception:
                    pass
            except FileNotFoundError:
                _log.info("[active] %s: no production model — skipping", league)
                providers_skip.append(f"{league} model")
                league_info["status"] = "no_model"
                per_league[league] = league_info
                continue
            except Exception as e:
                source_errors.append(f"{league}: {e}")
                league_info["status"] = "error"
                per_league[league] = league_info
                continue

            try:
                signals = generate_signals_for_league(
                    model=model, league=league, scan_date=today,
                    staging_dir=STAGING_DIR, odds_api_key=api_key,
                )
                candidates_checked += 1
                league_info["signals"] = len(signals)
                league_info["status"] = "ok" if signals else "no_fixtures"
            except Exception as e:
                _log.error("[active] %s: signal generation error: %s", league, e)
                source_errors.append(f"{league}: {e}")
                league_info["status"] = "error"
                league_info["error"] = str(e)[:120]
            per_league[league] = league_info
            all_signals.extend(signals if league_info["status"] != "error" else [])

        # Detect Odds API key errors by checking if api_key was set but all leagues got no_fixtures
        if api_key and not all_signals and not source_errors:
            # Try a quick validation call to distinguish "no matches today" from "bad key"
            _validate_odds_api_key_in_background(api_key, providers_ok, providers_skip)

        if not all_signals and not source_errors:
            no_signal_reason = _determine_no_signal_reason(api_key, providers_skip)

    # Non-football sports: monitor only, no model
    for sport in SPORTS:
        if sport != "football":
            _log.info("[active] %s: multi-sport architecture prepared, model unavailable", sport)
            providers_skip.append(f"{sport} (model unavailable — football only)")

    # Save new signals to ledger and Telegram
    if all_signals:
        try:
            ledger = SignalLedger.load_or_create(LEDGER_PATH)
            for sig in all_signals:
                try:
                    ledger.add_signal(sig)
                except Exception:
                    dupes_skipped += 1
            ledger.save(LEDGER_PATH)
        except Exception as e:
            source_errors.append(f"ledger_save: {e}")

        if TELEGRAM_SIGNAL_ALERTS_ENABLED:
            sent_count = _send_signal_alerts(all_signals)

        # Save daily signals file
        out = REPORTS_DIR / f"{today.isoformat()}_signals.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps({"date": str(today), "signals": all_signals}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    elapsed = time.perf_counter() - t0
    finished = datetime.now(timezone.utc)

    result = {
        "sports": SPORTS,
        "leagues": LEAGUES,
        "matches_count": candidates_checked,
        "upcoming_count": len(all_signals),
        "recently_finished": 0,
        "odds_count": len(all_signals) * 3 if all_signals else 0,
        "signals_count": len(all_signals),
        "candidates_checked": candidates_checked,
        "sent_count": sent_count,
        "duplicates_skipped": dupes_skipped,
        "top_signals": sorted(all_signals, key=lambda s: s.get("edge_pct", 0), reverse=True)[:3],
        "no_signal_reason": no_signal_reason,
        "providers_ok": providers_ok,
        "providers_skip": providers_skip,
        "source_errors": source_errors,
        "duration_s": round(elapsed, 1),
        "per_league": per_league,
        "has_odds_api_key": bool(api_key),
        "active_soccer_leagues": active_soccer_leagues,
    }

    _write_run_history_simple(
        "signal_scan", started, finished, elapsed,
        status="success" if not source_errors else "partial",
        signals_count=len(all_signals), sent_count=sent_count,
        leagues=LEAGUES, errors=source_errors,
    )

    _log.info("[active] Signal scan: %d signals from %d leagues in %.1fs",
              len(all_signals), candidates_checked, elapsed)
    return result


def _run_settlement() -> dict[str, Any]:
    """Run settlement and drift check. Returns summary dict."""
    from src.ingest.openfootball import OpenFootballLoader
    from src.models.settle_signal_ledger import settle_ledger_from_results
    from src.models.signal_ledger import SignalLedger
    from src.monitoring.drift_detector import CUSUMDriftDetector
    import pandas as pd

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    seasons = os.environ.get("OPENFOOTBALL_SEASONS", "2023-24,2024-25").split(",")
    result: dict[str, Any] = {
        "settled_count": 0, "wins": 0, "losses": 0, "pushes": 0,
        "pnl_units": None, "roi_pct": None, "hit_rate_pct": None,
        "drift_status": "no_data", "kelly_multiplier": 1.0,
    }
    errors: list[str] = []

    try:
        loader = OpenFootballLoader()
        res = loader.build(leagues=LEAGUES, seasons=seasons, use_cache=False)
        if res.dataframe.empty:
            _log.info("[active] Settlement: no results downloaded")
            _write_run_history_simple("settlement", started, datetime.now(timezone.utc),
                                      time.perf_counter() - t0, status="skip", settled_count=0)
            return result

        csv_path = loader.save_combined(res.dataframe, STAGING_DIR, "latest_results.csv")
        _log.info("[active] Downloaded %d matches → %s", len(res.dataframe), csv_path)
    except Exception as e:
        _log.error("[active] Settlement download failed: %s", e)
        errors.append(str(e))
        _write_run_history_simple("settlement", started, datetime.now(timezone.utc),
                                  time.perf_counter() - t0, status="failed", errors=errors)
        return result

    try:
        results_csv = STAGING_DIR / "latest_results.csv"
        ledger = SignalLedger.load_or_create(LEDGER_PATH)
        results_df = pd.read_csv(results_csv, encoding="latin-1")
        report = settle_ledger_from_results(ledger, results_df)
        ledger.save(LEDGER_PATH)

        today_iso = date.today().isoformat()
        (REPORTS_DIR / f"settlement_{today_iso}.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )

        settled = report.get("settled_count", 0)
        wins = sum(1 for e in report.get("settled_signals", []) if e.get("result") == "win")
        losses = sum(1 for e in report.get("settled_signals", []) if e.get("result") == "loss")
        result.update({
            "settled_count": settled,
            "wins": wins,
            "losses": losses,
        })
        _log.info("[active] Settled %d bets (W=%d L=%d)", settled, wins, losses)
    except Exception as e:
        _log.error("[active] Settlement failed: %s", e)
        errors.append(str(e))

    try:
        detector = CUSUMDriftDetector(threshold=0.15, drift_window=14, min_window=10)
        drift = detector.evaluate_ledger_path(LEDGER_PATH)
        (REPORTS_DIR / "drift_report.json").write_text(
            json.dumps(drift.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        result["drift_status"] = "DRIFT" if drift.drift_detected else "OK"
        result["kelly_multiplier"] = drift.kelly_multiplier
        _log.info("[active] Drift: %s | kelly=%s", result["drift_status"], drift.kelly_multiplier)
    except Exception as e:
        _log.warning("[active] Drift check failed (non-critical): %s", e)

    elapsed = time.perf_counter() - t0
    _write_run_history_simple(
        "settlement", started, datetime.now(timezone.utc), elapsed,
        status="failed" if errors else "success",
        settled_count=result["settled_count"], errors=errors,
    )
    return result


def _run_training_check() -> dict[str, Any]:
    """Check if enough new data exists; retrain only if warranted."""
    from src.models.run_history import read_last_run
    from src.models.signal_ledger import SignalLedger
    from src.models.model_registry import ModelRegistry

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    result: dict[str, Any] = {
        "trained": False,
        "training_reason": "",
        "model_status": "unknown",
        "model_age_hours": None,
        "league": "",
        "n_matches": 0,
        "old_brier": None,
        "new_brier": None,
        "promoted": False,
    }

    # Get model age for report
    registry = ModelRegistry(MODEL_DIR)
    prod_models = []
    for f in sorted(MODEL_DIR.glob("dc_*.meta.json")):
        try:
            m = json.loads(f.read_text())
            if m.get("status") == "production":
                prod_models.append(m)
        except Exception:
            pass

    if prod_models:
        latest = max(prod_models, key=lambda m: m.get("created_at_utc", ""))
        try:
            dt = datetime.fromisoformat(latest["created_at_utc"].replace("Z", "+00:00"))
            result["model_age_hours"] = round(
                (datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1
            )
        except Exception:
            pass
        result["model_status"] = "production"
    else:
        result["model_status"] = "no production model"

    # Count new settled matches since last training
    last_train = read_last_run("training_check")
    new_settled = 0

    if last_train:
        last_ts = last_train.get("started_at", "")
        if last_ts:
            try:
                from src.models.run_history import runs_since
                since_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                settle_runs = runs_since(since_dt, run_type="settlement")
                new_settled = sum(r.get("settled_count", 0) for r in settle_runs)
            except Exception:
                pass

    # Decision
    if not FORCE_TRAINING and new_settled < MIN_NEW_SETTLED:
        reason = (
            f"only {new_settled} new settled matches since last check, "
            f"minimum is {MIN_NEW_SETTLED}"
        )
        _log.info("[active] Training skipped: %s", reason)
        result["training_reason"] = f"training skipped: {reason}"
        _write_run_history_simple(
            "training_check", started, datetime.now(timezone.utc),
            time.perf_counter() - t0, status="skip", trained=False,
            training_reason=result["training_reason"],
        )
        return result

    if FORCE_TRAINING:
        _log.info("[active] Training forced via FORCE_TRAINING=true")
    else:
        _log.info("[active] Training triggered: %d new settled matches >= %d", new_settled, MIN_NEW_SETTLED)

    # Run training for all leagues
    from src.ingest.openfootball import OpenFootballLoader
    from src.models.dixon_coles import DixonColesConfig
    from src.models.trainer import DailyTrainer
    import pandas as pd

    cutoff = date.today()
    promoted_count = 0
    best_result: dict[str, Any] = {}

    for league in LEAGUES:
        try:
            loader = OpenFootballLoader()
            res = loader.build(leagues=[league], seasons=SEASONS, use_cache=True)
            if res.dataframe.empty:
                _log.info("[active] %s: no data", league)
                continue

            csv_path = loader.save_combined(res.dataframe, STAGING_DIR, f"{league}_latest.csv")
            df = pd.read_csv(csv_path, encoding="latin-1")

            # Get old model brier for comparison
            old_brier = None
            try:
                old_model_meta = [
                    json.loads(f.read_text())
                    for f in MODEL_DIR.glob(f"dc_{league}_*.meta.json")
                    if json.loads(f.read_text()).get("status") == "production"
                ]
                if old_model_meta:
                    old_brier = min(m.get("brier_score", 999) for m in old_model_meta)
            except Exception:
                pass

            # Ligue 1 has higher natural variance (PSG effect) — relax gate to 0.70
            _league_brier_gate = 0.70 if league in ("LIGUE1", "RPL") else 0.65
            trainer = DailyTrainer(
                registry=registry,
                staging_dir=STAGING_DIR,
                config=DixonColesConfig(),
                max_brier_score=_league_brier_gate,
            )
            train_result = trainer.run_on_dataframe(league, cutoff, df)

            promoted = getattr(train_result, "promoted", False)
            new_brier = getattr(train_result, "brier_score", None)
            model_id = getattr(train_result, "model_id", "")
            if promoted:
                promoted_count += 1

            reason_parts = []
            if promoted:
                if old_brier is not None and new_brier is not None:
                    reason_parts.append(
                        f"new model promoted because Brier improved from {old_brier:.4f} to {new_brier:.4f}"
                    )
                else:
                    reason_parts.append("new model promoted (first model, passed absolute Brier gate)")
            else:
                if old_brier is not None and new_brier is not None:
                    reason_parts.append(
                        f"new model rejected because Brier worsened from {old_brier:.4f} to {new_brier:.4f}"
                    )
                else:
                    reason_parts.append("new model rejected (did not beat production model)")

            _log.info("[active] %s: %s | brier=%s", league,
                      "promoted" if promoted else "candidate", new_brier)

            if promoted or not best_result:
                best_result = {
                    "league": league,
                    "n_matches": len(df),
                    "old_brier": old_brier,
                    "new_brier": new_brier,
                    "promoted": promoted,
                    "training_reason": "; ".join(reason_parts),
                }

        except Exception as e:
            _log.error("[active] Training %s failed: %s", league, e)

    elapsed = time.perf_counter() - t0
    result.update({
        "trained": True,
        **best_result,
    })
    _write_run_history_simple(
        "training_check", started, datetime.now(timezone.utc), elapsed,
        status="success", trained=True,
        training_reason=best_result.get("training_reason", ""),
        leagues=LEAGUES,
    )
    return result


def _send_signal_alerts(signals: list[dict]) -> int:
    """Send individual signal alerts to Telegram. Returns count sent."""
    from src.integrations.telegram_sender import TelegramConfig, TelegramSender

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    dry_run = not (token and chat_id)

    if dry_run:
        _log.info("[active] Telegram not configured — dry-run signal alerts")
        for sig in signals[:3]:
            _log.info("  [DRY-RUN] %s vs %s | edge=%s%% @ %s",
                      sig.get("home_team"), sig.get("away_team"),
                      sig.get("edge_pct"), sig.get("entry_odds"))
        return 0

    config = TelegramConfig(bot_token=token, chat_id=chat_id, dry_run=False)
    sender = TelegramSender(config)
    sent = 0
    for sig in signals:
        try:
            res = sender.send_signal(sig)
            if res.get("ok"):
                sent += 1
        except Exception as e:
            _log.error("[active] Signal alert send failed for %s: %s", sig.get("signal_id"), e)
    return sent


def _mask_chat_id(chat_id: str) -> str:
    if len(chat_id) >= 4:
        return "***" + chat_id[-4:]
    return "***" if chat_id else "(empty)"


def _save_tg_delivery_status(status: str, error: str | None = None) -> None:
    """Persist last Telegram delivery status for /health/active."""
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / "tg_delivery_status.json").write_text(
            json.dumps({
                "last_status": status,
                "last_at": datetime.now(timezone.utc).isoformat(),
                "last_error": error,
            }, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        _log.warning("[active] Could not save tg_delivery_status: %s", e)


def _send_status_report(
    signals_result: dict[str, Any],
    settlement_result: dict[str, Any],
    training_result: dict[str, Any],
) -> str:
    """Format and send the 3-hour active status report to Telegram.

    Returns delivery status: "sent" | "dry_run" | "failed".
    """
    import urllib.request
    import urllib.error
    from src.reporting.active_report import format_active_report

    text = format_active_report(signals_result, settlement_result, training_result)

    # Always print to stdout for logs
    print("\n" + "=" * 60)
    print(text)
    print("=" * 60 + "\n")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        missing = []
        if not token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not chat_id:
            missing.append("TELEGRAM_CHAT_ID")
        _log.info("[active] DRY RUN: Telegram config incomplete — missing: %s", ", ".join(missing))
        out = REPORTS_DIR / "active_report_dry_run.json"
        out.write_text(
            json.dumps({"text": text, "ts": datetime.now(timezone.utc).isoformat(),
                        "missing": missing}, indent=2),
            encoding="utf-8",
        )
        _save_tg_delivery_status("dry_run")
        return "dry_run"

    # Send via raw urllib — plain text, NO parse_mode (empty string causes 400 Bad Request)
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }).encode("utf-8")

    last_error: str | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
                if body.get("ok"):
                    _log.info("[active] Status report sent to Telegram (chat=%s)",
                              _mask_chat_id(chat_id))
                    _save_tg_delivery_status("sent")
                    return "sent"
                else:
                    desc = body.get("description", str(body))
                    _log.error("[active] Telegram API not-ok: %s", desc)
                    last_error = f"api_error: {desc}"
                    _save_tg_delivery_status("failed", last_error)
                    return "failed"
        except urllib.error.HTTPError as e:
            body_raw = ""
            try:
                body_raw = e.read().decode("utf-8", errors="replace")
                err_desc = json.loads(body_raw).get("description", body_raw)
            except Exception:
                err_desc = body_raw or str(e)
            last_error = f"http_{e.code}: {err_desc}"
            _log.error("[active] Telegram HTTP %d: %s (attempt %d)", e.code, err_desc, attempt + 1)
            # 4xx are permanent errors — do not retry
            if 400 <= e.code < 500:
                _save_tg_delivery_status("failed", last_error)
                return "failed"
            if attempt < 2:
                time.sleep(2 ** attempt)
        except OSError as e:
            last_error = f"network: {e}"
            _log.warning("[active] Telegram network error (attempt %d): %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(2 ** attempt)

    _save_tg_delivery_status("failed", last_error)
    return "failed"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _probe_active_soccer_leagues(api_key: str) -> list[str]:
    """Query /sports to find soccer competitions that currently have events.

    Returns a list of active sport_key strings (e.g. ['soccer_epl', 'soccer_usa_mls']).
    Returns [] on any error (non-critical — used only for reporting).
    """
    try:
        import urllib.request as _urllib, json as _json
        req = _urllib.Request(
            f"https://api.the-odds-api.com/v4/sports?apiKey={api_key}&all=false",
            headers={"User-Agent": "bet-analytics/1.0"},
        )
        with _urllib.urlopen(req, timeout=10) as resp:
            sports = _json.loads(resp.read())
        active = [
            s["key"] for s in sports
            if s.get("group", "").lower() == "soccer" and s.get("active", False)
        ]
        _log.info("[active] Odds API active soccer leagues: %d found — %s", len(active), active[:8])
        return active
    except Exception as exc:
        _log.warning("[active] Could not probe active leagues: %s", exc)
        return []


def _validate_odds_api_key_in_background(
    api_key: str,
    providers_ok: list[str],
    providers_skip: list[str],
) -> None:
    """Quick check: verify the Odds API key is valid by fetching available sports."""
    try:
        import urllib.request as _urllib
        req = _urllib.Request(
            f"https://api.the-odds-api.com/v4/sports?apiKey={api_key}",
            headers={"User-Agent": "bet-analytics/1.0"},
        )
        with _urllib.urlopen(req, timeout=8) as resp:
            if resp.status == 200:
                _log.info("[active] Odds API key is VALID — no upcoming matches today")
                # Update providers list in-place
                if "Odds API" not in providers_ok:
                    providers_ok.append("Odds API ✅ (ключ верный)")
            else:
                _log.warning("[active] Odds API key check returned %s", resp.status)
    except Exception as exc:
        code = getattr(getattr(exc, "code", None), "__str__", lambda: str(exc))()
        _log.warning("[active] Odds API key validation failed: %s", exc)
        # Replace "Odds API" with error entry
        if "Odds API" in providers_ok:
            providers_ok.remove("Odds API")
        providers_skip.append(f"Odds API ❌ ошибка ключа ({code})")


def _determine_no_signal_reason(api_key: str, providers_skip: list[str]) -> str:
    if not api_key and not (STAGING_DIR / f"{LEAGUES[0]}_latest.csv").exists():
        return "no odds source available (no Odds API key, no staged CSV)"
    if not api_key:
        return "using staged data only; no upcoming fixtures found"
    # Key is set — check if any provider errored
    odds_errors = [p for p in providers_skip if "Odds API" in p and "ошибка" in p]
    if odds_errors:
        return f"Odds API error: check THE_ODDS_API_KEY in Render Dashboard"
    return "no value signals found (no matches meet edge threshold)"


def _empty_signals_result(reason: str) -> dict[str, Any]:
    return {
        "sports": SPORTS, "leagues": LEAGUES,
        "matches_count": 0, "upcoming_count": 0, "recently_finished": 0,
        "odds_count": 0, "signals_count": 0, "candidates_checked": 0,
        "sent_count": 0, "duplicates_skipped": 0, "top_signals": [],
        "no_signal_reason": reason, "providers_ok": [], "providers_skip": [],
        "source_errors": [reason], "duration_s": 0,
    }


def _empty_settlement_result() -> dict[str, Any]:
    return {
        "settled_count": 0, "wins": 0, "losses": 0, "pushes": 0,
        "pnl_units": None, "roi_pct": None, "hit_rate_pct": None,
        "drift_status": "error", "kelly_multiplier": 1.0,
    }


def _write_run_history_simple(
    run_type: str,
    started: datetime,
    finished: datetime,
    elapsed: float,
    status: str,
    **kwargs: Any,
) -> None:
    try:
        from src.models.run_history import write_run
        # Allow callers to override sports/leagues via kwargs
        sports = kwargs.pop("sports", SPORTS)
        leagues = kwargs.pop("leagues", LEAGUES)
        write_run(
            run_type=run_type,
            status=status,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            duration_seconds=elapsed,
            sports=sports,
            leagues=leagues,
            **kwargs,
        )
    except Exception as e:
        _log.warning("[active] Failed to write run history: %s", e)


def _write_run_history(
    run_type: str,
    started: datetime,
    finished: datetime,
    elapsed: float,
    signals_result: dict[str, Any],
    settlement_result: dict[str, Any],
    training_result: dict[str, Any],
    errors: list[str],
) -> None:
    try:
        from src.models.run_history import write_run
        write_run(
            run_type=run_type,
            status="partial" if errors else "success",
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            duration_seconds=elapsed,
            sports=SPORTS,
            leagues=LEAGUES,
            matches_count=signals_result.get("matches_count", 0),
            odds_count=signals_result.get("odds_count", 0),
            signals_count=signals_result.get("signals_count", 0),
            sent_count=signals_result.get("sent_count", 0),
            settled_count=settlement_result.get("settled_count", 0),
            trained=training_result.get("trained", False),
            training_reason=training_result.get("training_reason", ""),
            errors=errors,
        )
    except Exception as e:
        _log.warning("[active] Failed to write run history: %s", e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="3-hour active monitoring report")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Send report immediately, bypassing ACTIVE_MODE check",
    )
    args = parser.parse_args()
    main(force=args.force)
