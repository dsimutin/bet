"""Persistent paper ledger for model-generated betting signals."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SignalResult = Literal["win", "loss", "void"]
DeliveryStatus = Literal["registered", "dry_run", "sent", "blocked"]


@dataclass(frozen=True)
class LedgerAddResult:
    added: list[dict[str, Any]]
    duplicates: list[dict[str, Any]]


class SignalLedger:
    """Stores sent paper signals, prevents duplicates, and tracks outcomes."""

    VERSION = "1.0"

    def __init__(self, entries: dict[str, dict[str, Any]] | None = None) -> None:
        self._entries = entries or {}

    @classmethod
    def load_or_create(cls, path: Path) -> SignalLedger:
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(entries=raw.get("entries", {}))

    def add_signal(self, signal: dict[str, Any], stake_units: float = 1.0) -> bool:
        signal_id = str(signal.get("signal_id", "")).strip()
        if not signal_id:
            raise ValueError("signal_id is required")
        if not signal.get("dataset_hash"):
            raise ValueError(
                f"dataset_hash is required (signal_id={signal_id}); "
                "signals without a hash are considered invalid per anti-leakage rules"
            )
        if signal_id in self._entries:
            return False

        self._entries[signal_id] = {
            **signal,
            "signal_id": signal_id,
            "ledger_status": "open",
            "delivery_status": "registered",
            "delivery_result": None,
            "delivery_block_reason": None,
            "delivered_at_utc": None,
            "stake_units": stake_units,
            "result": None,
            "closing_odds": None,
            "pnl_units": None,
            "clv_pct": None,
            "ledger_created_at_utc": datetime.now(timezone.utc).isoformat(),
            "ledger_updated_at_utc": None,
        }
        return True

    def add_signals(
        self, signals: list[dict[str, Any]], stake_units: float = 1.0
    ) -> LedgerAddResult:
        added: list[dict[str, Any]] = []
        duplicates: list[dict[str, Any]] = []
        for signal in signals:
            signal_stake = float(
                signal.get("stake_units", signal.get("paper_stake_units", stake_units))
            )
            if self.add_signal(signal, stake_units=signal_stake):
                added.append(signal)
            else:
                duplicates.append(signal)
        return LedgerAddResult(added=added, duplicates=duplicates)

    def mark_delivery(
        self,
        signal_id: str,
        status: DeliveryStatus,
        delivery_result: dict[str, Any] | None = None,
        block_reason: str | None = None,
    ) -> None:
        if signal_id not in self._entries:
            raise KeyError(f"Signal {signal_id} not found in ledger")
        if status not in {"registered", "dry_run", "sent", "blocked"}:
            raise ValueError("status must be one of: registered, dry_run, sent, blocked")
        self._entries[signal_id].update(
            {
                "delivery_status": status,
                "delivery_result": delivery_result,
                "delivery_block_reason": block_reason,
                "delivered_at_utc": (
                    datetime.now(timezone.utc).isoformat()
                    if status in {"dry_run", "sent"}
                    else None
                ),
                "ledger_updated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )

    def update_result(
        self,
        signal_id: str,
        result: SignalResult,
        closing_odds: float | None = None,
    ) -> None:
        if signal_id not in self._entries:
            raise KeyError(f"Signal {signal_id} not found in ledger")
        if result not in {"win", "loss", "void"}:
            raise ValueError("result must be one of: win, loss, void")

        entry = self._entries[signal_id]
        stake = float(entry.get("stake_units", 1.0))
        entry_odds = float(entry.get("entry_odds", 0.0))

        if result == "win":
            pnl = stake * (entry_odds - 1.0)
            status = "settled"
        elif result == "loss":
            pnl = -stake
            status = "settled"
        else:
            pnl = 0.0
            status = "void"

        clv_pct = None
        if closing_odds is not None and closing_odds > 1.0:
            clv_pct = (entry_odds / closing_odds - 1.0) * 100

        entry.update(
            {
                "ledger_status": status,
                "result": result,
                "closing_odds": closing_odds,
                "pnl_units": round(pnl, 4),
                "clv_pct": round(clv_pct, 4) if clv_pct is not None else None,
                "ledger_updated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )

    def has_signal(self, signal_id: str) -> bool:
        return signal_id in self._entries

    def get(self, signal_id: str) -> dict[str, Any]:
        return self._entries[signal_id]

    def entries(self) -> dict[str, dict[str, Any]]:
        return dict(self._entries)

    def summary(self) -> dict[str, Any]:
        entries = list(self._entries.values())
        settled = [item for item in entries if item.get("ledger_status") == "settled"]
        open_entries = [item for item in entries if item.get("ledger_status") == "open"]
        wins = [item for item in settled if item.get("result") == "win"]
        total_pnl = sum(float(item.get("pnl_units") or 0.0) for item in settled)
        turnover = sum(float(item.get("stake_units") or 0.0) for item in settled)
        mean_edge = (
            sum(float(item.get("edge_pct") or 0.0) for item in entries) / len(entries)
            if entries
            else 0.0
        )
        return {
            "total_signals": len(entries),
            "open_signals": len(open_entries),
            "settled_signals": len(settled),
            "void_signals": len([item for item in entries if item.get("ledger_status") == "void"]),
            "delivered_signals": len([item for item in entries if self._is_delivered_status(item)]),
            "blocked_delivery_signals": len(
                [item for item in entries if item.get("delivery_status") == "blocked"]
            ),
            "win_rate": round(len(wins) / len(settled), 4) if settled else 0.0,
            "roi_pct": round(total_pnl / turnover * 100, 4) if turnover else 0.0,
            "pnl_units": round(total_pnl, 4),
            "turnover_units": round(turnover, 4),
            "mean_edge_pct": round(mean_edge, 4),
        }

    def delivery_quality_report(
        self,
        min_settled: int,
        min_win_rate: float,
        min_roi_pct: float,
    ) -> dict[str, Any]:
        delivered_settled = [
            item
            for item in self._entries.values()
            if item.get("ledger_status") == "settled" and self._is_delivered_status(item)
        ]
        wins = [item for item in delivered_settled if item.get("result") == "win"]
        total_pnl = sum(float(item.get("pnl_units") or 0.0) for item in delivered_settled)
        turnover = sum(float(item.get("stake_units") or 0.0) for item in delivered_settled)
        n_settled = len(delivered_settled)
        win_rate = len(wins) / n_settled if n_settled else 0.0
        roi_pct = total_pnl / turnover * 100 if turnover else 0.0

        failures: list[str] = []
        warmup = n_settled < min_settled
        if not warmup:
            if win_rate < min_win_rate:
                failures.append(f"win_rate {win_rate:.2%} < {min_win_rate:.2%}")
            if roi_pct < min_roi_pct:
                failures.append(f"roi_pct {roi_pct:.2f}% < {min_roi_pct:.2f}%")

        return {
            "passed": not failures,
            "warmup": warmup,
            "reason": "warmup" if warmup else ("; ".join(failures) if failures else "passed"),
            "min_settled": min_settled,
            "min_win_rate": min_win_rate,
            "min_roi_pct": min_roi_pct,
            "summary": {
                "settled_delivered_signals": n_settled,
                "win_rate": round(win_rate, 4),
                "roi_pct": round(roi_pct, 4),
                "pnl_units": round(total_pnl, 4),
                "turnover_units": round(turnover, 4),
            },
        }

    def _is_delivered_status(self, entry: dict[str, Any]) -> bool:
        status = entry.get("delivery_status")
        return status in {"dry_run", "sent"}

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            "entries": self._entries,
            "summary": self.summary(),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
