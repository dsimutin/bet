"""CLI entrypoint for the historical value model."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from src.integrations.telegram_sender import TelegramConfig, TelegramSender
from src.ingest.football_data_dataset import FootballDataDatasetBuilder
from src.ingest.free_source_inbox import FreeSourceInboxLoader
from src.ingest.live_odds_adapter import LiveOddsFootballDataAdapter
from src.ingest.odds_api import OddsAPIClient
from src.models.consensus_signal_engine import ConsensusConfig, ConsensusSignalEngine
from src.models.high_hit_profile import (
    HighHitProfileSearchConfig,
    find_high_hit_profile,
    write_high_hit_profile_report,
)
from src.models.historical_value_model import HistoricalValueModel, HistoricalValueModelConfig
from src.models.model_quality_gate import ModelQualityGate
from src.models.model_registry import ModelRegistry
from src.models.poisson_team_model import PoissonTeamModel, PoissonTeamModelConfig
from src.models.production_signal_engine import ProductionDixonColesSignalEngine
from src.models.signal_ledger import SignalLedger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run historical paper-trading value model.")
    parser.add_argument("--input", type=Path, help="football-data.co.uk CSV file")
    parser.add_argument("--output-dir", default=Path("data/reports"), type=Path)
    parser.add_argument("--download-football-data", action="store_true")
    parser.add_argument("--leagues", default="E0,SP1,D1,I1,F1")
    parser.add_argument("--seasons", default="2122,2223,2324,2425,2526")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--bookmaker-prefix", default="B365")
    parser.add_argument("--min-edge-pct", default=2.0, type=float)
    parser.add_argument("--min-signal-probability", default=0.45, type=float)
    parser.add_argument("--max-entry-odds", type=float)
    parser.add_argument("--high-hit-mode", action="store_true")
    parser.add_argument("--auto-high-hit-profile", action="store_true")
    parser.add_argument("--require-auto-high-hit-profile", action="store_true")
    parser.add_argument("--target-hit-rate", default=0.55, type=float)
    parser.add_argument("--min-profile-bets", default=30, type=int)
    parser.add_argument("--disable-quality-gate", action="store_true")
    parser.add_argument("--min-quality-win-rate", default=0.45, type=float)
    parser.add_argument("--min-quality-roi-pct", default=0.0, type=float)
    parser.add_argument("--min-train-matches", default=60, type=int)
    parser.add_argument("--test-window-days", default=30, type=int)
    parser.add_argument("--upcoming-input", type=Path, help="CSV with upcoming/manual odds")
    parser.add_argument("--free-source-inbox", type=Path)
    parser.add_argument(
        "--live-odds", action="store_true", help="Fetch upcoming odds from The Odds API"
    )
    parser.add_argument("--live-sport-keys", default="soccer_epl")
    parser.add_argument("--odds-regions", default="eu,uk")
    parser.add_argument("--preferred-bookmakers", default="bet365,pinnacle")
    parser.add_argument("--no-bookmaker-fallback", action="store_true")
    parser.add_argument("--max-signals", default=10, type=int)
    parser.add_argument("--production-dixon-coles", action="store_true")
    parser.add_argument("--production-model-dir", type=Path, default=Path("data/models"))
    parser.add_argument("--production-league", default="EPL")
    parser.add_argument("--telegram-payload", action="store_true")
    parser.add_argument("--send-telegram", action="store_true")
    parser.add_argument("--ledger-path", type=Path)
    parser.add_argument("--allow-duplicate-signals", action="store_true")
    parser.add_argument("--require-ledger-quality", action="store_true")
    parser.add_argument("--min-ledger-settled", default=20, type=int)
    parser.add_argument("--min-ledger-win-rate", default=0.55, type=float)
    parser.add_argument("--min-ledger-roi-pct", default=0.0, type=float)
    parser.add_argument("--require-model-quality", action="store_true")
    parser.add_argument("--benchmark-report", type=Path)
    parser.add_argument(
        "--model-quality-scope", choices=["latest_window", "overall"], default="latest_window"
    )
    parser.add_argument("--model-quality-candidates")
    parser.add_argument("--model-quality-mode", choices=["all", "any"], default="all")
    parser.add_argument("--min-benchmark-predictions", default=100, type=int)
    parser.add_argument("--min-brier-improvement", default=0.0, type=float)
    parser.add_argument("--min-log-loss-improvement", default=0.0, type=float)
    parser.add_argument(
        "--consensus", action="store_true", help="Require value + Poisson agreement"
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    load_dotenv()
    _validate_runtime_args(args)
    _apply_high_hit_mode(args)
    df = _load_history_dataframe(args)
    config = _build_model_config(args)
    if args.auto_high_hit_profile:
        profile_report = find_high_hit_profile(
            df,
            config,
            HighHitProfileSearchConfig(
                target_win_rate=args.target_hit_rate,
                min_bets=args.min_profile_bets,
            ),
        )
        profile_path = write_high_hit_profile_report(profile_report, args.output_dir)
        print(f"Wrote {profile_path} (passed={profile_report['passed']})")
        if args.require_auto_high_hit_profile and not profile_report["passed"]:
            raise SystemExit("No high-hit profile met target; blocking signal delivery")
        selected = profile_report["selected"]
        args.min_signal_probability = float(selected["min_signal_probability"])
        args.min_edge_pct = float(selected["min_edge_pct"])
        args.max_entry_odds = selected["max_entry_odds"]
        config = _build_model_config(args)

    model = HistoricalValueModel(config)
    report = model.build_report(df)
    json_path, md_path = model.write_report(report, args.output_dir)
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")

    if args.upcoming_input or args.live_odds or args.free_source_inbox:
        upcoming_df = _load_candidate_dataframe(args)
        if args.production_dixon_coles:
            production_engine, production_gate = _production_model_gate(args)
            production_gate_path = args.output_dir / "production_model_quality_gate.json"
            args.output_dir.mkdir(parents=True, exist_ok=True)
            production_gate_path.write_text(
                json.dumps(production_gate, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"Wrote {production_gate_path} (passed={production_gate['passed']})")
            if production_engine is None:
                signals = []
                signals_path = _save_empty_production_signals(args.output_dir)
            else:
                signals = production_engine.generate_signals(upcoming_df)
                signals_path = production_engine.save_signals(signals, args.output_dir)
        elif args.consensus:
            consensus_engine = ConsensusSignalEngine(
                value_model=HistoricalValueModel(config),
                poisson_model=PoissonTeamModel(
                    PoissonTeamModelConfig(
                        bookmaker_prefix=args.bookmaker_prefix,
                        min_train_matches=args.min_train_matches,
                        min_edge_pct=args.min_edge_pct,
                        min_signal_probability=args.min_signal_probability,
                        max_signal_odds=args.max_entry_odds,
                    )
                ),
                config=ConsensusConfig(
                    min_edge_pct=args.min_edge_pct,
                    min_probability=args.min_signal_probability,
                    max_entry_odds=args.max_entry_odds,
                    require_quality_gates=not args.disable_quality_gate,
                    max_signals=args.max_signals,
                ),
            )
            quality_gate = consensus_engine.quality_gate_report(df)
            quality_path = consensus_engine.save_quality_gate(quality_gate, args.output_dir)
            print(f"Wrote {quality_path} (passed={quality_gate['passed']})")
            signals = consensus_engine.generate_signals(df, upcoming_df)
            signals_path = consensus_engine.save_signals(signals, args.output_dir)
        else:
            quality_gate = model.quality_gate_report(df)
            quality_path = args.output_dir / "historical_value_quality_gate.json"
            args.output_dir.mkdir(parents=True, exist_ok=True)
            quality_path.write_text(
                json.dumps(quality_gate, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"Wrote {quality_path} (passed={quality_gate['passed']})")
            signals = model.generate_signals(df, upcoming_df, max_signals=args.max_signals)
            signals_path = model.save_signals(signals, args.output_dir)
        print(f"Wrote {signals_path} ({len(signals)} signals)")
        model_gate = _model_quality_gate(args)
        model_gate_path = args.output_dir / "model_delivery_quality_gate.json"
        model_gate_path.write_text(
            json.dumps(model_gate, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Wrote {model_gate_path} (passed={model_gate['passed']})")
        if not model_gate["passed"]:
            signals = []
        signals_for_delivery, ledger, ledger_path = _record_signals_in_ledger(signals, args)

        if args.telegram_payload or args.send_telegram:
            delivery_gate = _ledger_delivery_gate(ledger, args)
            delivery_gate_path = args.output_dir / "ledger_delivery_quality_gate.json"
            delivery_gate_path.write_text(
                json.dumps(delivery_gate, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"Wrote {delivery_gate_path} (passed={delivery_gate['passed']})")
            if not delivery_gate["passed"]:
                for signal in signals_for_delivery:
                    ledger.mark_delivery(
                        str(signal["signal_id"]),
                        status="blocked",
                        block_reason=delivery_gate["reason"],
                    )
                ledger.save(ledger_path)
                signals_for_delivery = []

            sender = TelegramSender(
                TelegramConfig(
                    bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "dry-run-token"),
                    chat_id=os.getenv("TELEGRAM_CHAT_ID", "dry-run"),
                    dry_run=not args.send_telegram,
                    max_message_length=4096,
                )
            )
            for signal in signals_for_delivery:
                send_result = sender.send_signal(signal)
                payload = {
                    "signal": signal,
                    "message": sender.format_signal_message(signal),
                    "send_result": send_result,
                }
                if args.send_telegram:
                    delivery_status = "sent" if send_result.get("ok") else "failed"
                else:
                    delivery_status = "dry_run"
                ledger.mark_delivery(
                    str(signal["signal_id"]),
                    status=delivery_status,
                    delivery_result=send_result,
                )
                payload_path = sender.save_payload(payload, args.output_dir)
                print(f"Wrote {payload_path}")
            ledger.save(ledger_path)


def _build_model_config(args: argparse.Namespace) -> HistoricalValueModelConfig:
    return HistoricalValueModelConfig(
        bookmaker_prefix=args.bookmaker_prefix,
        min_edge_pct=args.min_edge_pct,
        min_signal_probability=args.min_signal_probability,
        max_signal_odds=args.max_entry_odds,
        require_recent_quality=not args.disable_quality_gate,
        min_quality_win_rate=args.min_quality_win_rate,
        min_quality_roi_pct=args.min_quality_roi_pct,
        min_train_matches=args.min_train_matches,
        test_window_days=args.test_window_days,
    )


def _production_model_gate(
    args: argparse.Namespace,
) -> tuple[ProductionDixonColesSignalEngine | None, dict[str, Any]]:
    registry = ModelRegistry(args.production_model_dir)
    try:
        production_model, production_calibrator = registry.load_latest_with_calibrator(
            args.production_league
        )
    except FileNotFoundError as exc:
        return None, {
            "passed": False,
            "reason": "no_promoted_production_model",
            "details": str(exc),
            "league": args.production_league,
            "model_dir": str(args.production_model_dir),
        }

    return (
        ProductionDixonColesSignalEngine(
            production_model,
            min_edge_pct=args.min_edge_pct,
            min_model_probability=args.min_signal_probability,
            max_entry_odds=args.max_entry_odds,
            max_signals=args.max_signals,
            bookmaker_prefix=args.bookmaker_prefix,
            calibrator=production_calibrator,
        ),
        {
            "passed": True,
            "reason": "production_model_loaded",
            "model_id": production_model.model_id,
            "league": args.production_league,
            "model_dir": str(args.production_model_dir),
            "calibrator_loaded": production_calibrator is not None,
        },
    )


def _save_empty_production_signals(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    output_path = output_dir / f"production_dc_signals_{date_str}.json"
    output_path.write_text("[]\n", encoding="utf-8")
    return output_path


def _load_history_dataframe(args: argparse.Namespace) -> pd.DataFrame:
    if args.download_football_data:
        builder = FootballDataDatasetBuilder(project_root=Path("."))
        result = builder.build(
            leagues=_split_csv_arg(args.leagues),
            seasons=_split_csv_arg(args.seasons),
            use_cache=not args.no_cache,
        )
        if result.dataframe.empty:
            skipped = "; ".join(result.skipped) if result.skipped else "no files"
            raise SystemExit(f"No football-data rows loaded: {skipped}")
        output_path = builder.save_combined(result.dataframe, args.output_dir)
        print(
            f"Wrote {output_path} " f"({len(result.dataframe)} rows, skipped={len(result.skipped)})"
        )
        if result.skipped:
            print("Skipped: " + "; ".join(result.skipped))
        return result.dataframe

    if args.input is None:
        raise SystemExit("--input is required unless --download-football-data is used")
    return pd.read_csv(args.input, encoding="latin-1")


def _validate_runtime_args(args: argparse.Namespace) -> None:
    if args.live_odds and not os.getenv("THE_ODDS_API_KEY"):
        raise SystemExit("THE_ODDS_API_KEY is required for --live-odds")
    if args.send_telegram and (
        not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID")
    ):
        raise SystemExit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required for --send-telegram")


def _record_signals_in_ledger(
    signals: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], SignalLedger, Path]:
    ledger_path = args.ledger_path or (args.output_dir / "paper_signal_ledger.json")
    ledger = SignalLedger.load_or_create(ledger_path)
    add_result = ledger.add_signals(signals)
    saved_path = ledger.save(ledger_path)
    print(
        f"Wrote {saved_path} "
        f"(added={len(add_result.added)}, duplicates={len(add_result.duplicates)})"
    )
    if add_result.duplicates and not args.allow_duplicate_signals:
        print(f"Skipped duplicate delivery for {len(add_result.duplicates)} signal(s)")
    if args.allow_duplicate_signals:
        return signals, ledger, ledger_path
    return add_result.added, ledger, ledger_path


def _ledger_delivery_gate(ledger: SignalLedger, args: argparse.Namespace) -> dict[str, Any]:
    report = ledger.delivery_quality_report(
        min_settled=args.min_ledger_settled,
        min_win_rate=args.min_ledger_win_rate,
        min_roi_pct=args.min_ledger_roi_pct,
    )
    if not args.require_ledger_quality:
        return {**report, "passed": True, "enforced": False}
    return {**report, "enforced": True}


def _model_quality_gate(args: argparse.Namespace) -> dict[str, Any]:
    benchmark_path = args.benchmark_report or (args.output_dir / "model_probability_benchmark.json")
    gate = ModelQualityGate(
        benchmark_path=benchmark_path,
        candidate_models=_model_quality_candidates(args),
        min_predictions=args.min_benchmark_predictions,
        min_brier_improvement=args.min_brier_improvement,
        min_log_loss_improvement=args.min_log_loss_improvement,
        scope=args.model_quality_scope,
        mode=args.model_quality_mode,
    )
    report = gate.evaluate()
    if not args.require_model_quality:
        return {**report, "passed": True, "enforced": False}
    return {**report, "enforced": True}


def _model_quality_candidates(args: argparse.Namespace) -> list[str]:
    if args.model_quality_candidates:
        return _split_csv_arg(args.model_quality_candidates)
    if args.production_dixon_coles:
        return ["dixon_coles_time_decay"]
    if args.consensus:
        return ["historical_calibration", "poisson_team_strength"]
    return ["historical_calibration"]


def _load_candidate_dataframe(args: argparse.Namespace) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    if args.upcoming_input:
        frames.append(pd.read_csv(args.upcoming_input, encoding="latin-1"))
    if args.free_source_inbox:
        result = FreeSourceInboxLoader(bookmaker_prefix=args.bookmaker_prefix).load(
            args.free_source_inbox
        )
        report_path = args.output_dir / "free_source_inbox_report.json"
        args.output_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "loaded_files": result.loaded_files,
                    "skipped_files": result.skipped_files,
                    "rows": len(result.dataframe),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"Wrote {report_path} ({len(result.dataframe)} free-source rows)")
        if not result.dataframe.empty:
            frames.append(result.dataframe)
    if args.live_odds:
        frames.append(_fetch_live_odds_dataframe(args))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _fetch_live_odds_dataframe(args: argparse.Namespace) -> pd.DataFrame:
    client = OddsAPIClient(api_key=str(os.getenv("THE_ODDS_API_KEY")))
    adapter = LiveOddsFootballDataAdapter(
        bookmaker_prefix=args.bookmaker_prefix,
        preferred_bookmakers=_split_csv_arg(args.preferred_bookmakers),
        allow_bookmaker_fallback=not args.no_bookmaker_fallback,
    )

    all_raw_events: list[dict] = []
    for sport_key in _split_csv_arg(args.live_sport_keys):
        raw_events = client.get_odds(
            sport=sport_key,
            regions=_split_csv_arg(args.odds_regions),
            markets=["h2h"],
        )
        all_raw_events.extend(raw_events)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = args.output_dir / "live_odds_raw.json"
    raw_path.write_text(json.dumps(all_raw_events, indent=2, ensure_ascii=False), encoding="utf-8")

    result = adapter.convert(all_raw_events)
    upcoming_path = args.output_dir / "live_odds_upcoming.csv"
    result.dataframe.to_csv(upcoming_path, index=False)

    report_path = args.output_dir / "live_odds_conversion_report.json"
    report_path.write_text(
        json.dumps(
            {
                "converted_events": result.converted_events,
                "skipped_events": result.skipped_events,
                "raw_events": len(all_raw_events),
                "upcoming_path": str(upcoming_path),
                "raw_path": str(raw_path),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        f"Wrote {upcoming_path} "
        f"({result.converted_events}/{len(all_raw_events)} live events converted)"
    )
    print(f"Wrote {report_path}")
    return result.dataframe


def _split_csv_arg(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _apply_high_hit_mode(args: argparse.Namespace) -> None:
    if not args.high_hit_mode:
        return
    args.min_signal_probability = max(args.min_signal_probability, 0.58)
    args.min_quality_win_rate = max(args.min_quality_win_rate, 0.55)
    args.min_edge_pct = min(args.min_edge_pct, 1.0)
    if args.max_entry_odds is None:
        args.max_entry_odds = 1.85
    else:
        args.max_entry_odds = min(args.max_entry_odds, 1.85)


if __name__ == "__main__":
    main()
