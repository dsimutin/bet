"""Model calibration analysis — reliability diagram + Brier score."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

# Bin edges: [lower, upper)
_BINS: list[tuple[float, float]] = [
    (0.00, 0.50),
    (0.50, 0.60),
    (0.60, 0.65),
    (0.65, 0.70),
    (0.70, 0.75),
    (0.75, 0.80),
    (0.80, 0.90),
    (0.90, 1.01),  # upper bound slightly > 1 to include prob == 1.0
]

_BIN_LABELS: list[str] = [
    "0-50%",
    "50-60%",
    "60-65%",
    "65-70%",
    "70-75%",
    "75-80%",
    "80-90%",
    "90-100%",
]


def _assign_bin(prob: float) -> int | None:
    """Return bin index for a probability value, or None if out of range."""
    for i, (lo, hi) in enumerate(_BINS):
        if lo <= prob < hi:
            return i
    return None


def _brier_score(entries: list[dict[str, Any]]) -> float:
    """Brier score: mean squared error of probability forecasts."""
    total = 0.0
    for e in entries:
        p = float(e["model_prob"])
        outcome = 1.0 if e["result"] == "win" else 0.0
        total += (p - outcome) ** 2
    return total / len(entries) if entries else float("nan")


def _log_loss(entries: list[dict[str, Any]]) -> float:
    """Binary log-loss (cross-entropy)."""
    total = 0.0
    eps = 1e-15
    for e in entries:
        p = max(eps, min(1.0 - eps, float(e["model_prob"])))
        outcome = 1.0 if e["result"] == "win" else 0.0
        total += -(outcome * math.log(p) + (1.0 - outcome) * math.log(1.0 - p))
    return total / len(entries) if entries else float("nan")


def _accuracy(entries: list[dict[str, Any]]) -> float:
    if not entries:
        return float("nan")
    correct = sum(
        1
        for e in entries
        if (e["model_prob"] >= 0.5 and e["result"] == "win")
        or (e["model_prob"] < 0.5 and e["result"] == "loss")
    )
    return correct / len(entries)


def _bin_stats(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compute per-bin calibration statistics."""
    bins: list[list[dict[str, Any]]] = [[] for _ in _BINS]
    for e in entries:
        idx = _assign_bin(float(e["model_prob"]))
        if idx is not None:
            bins[idx].append(e)

    result = []
    for i, (lo, hi) in enumerate(_BINS):
        bucket = bins[i]
        count = len(bucket)
        if count == 0:
            result.append(
                {
                    "bin_label": _BIN_LABELS[i],
                    "bin_lower": lo,
                    "bin_upper": min(hi, 1.0),
                    "count": 0,
                    "mean_predicted_prob": None,
                    "actual_win_rate": None,
                    "over_under_confidence": None,
                }
            )
            continue

        mean_pred = sum(float(e["model_prob"]) for e in bucket) / count
        wins = sum(1 for e in bucket if e["result"] == "win")
        actual_wr = wins / count
        # Positive = overconfident (model predicts higher than actual),
        # Negative = underconfident.
        over_under = mean_pred - actual_wr

        result.append(
            {
                "bin_label": _BIN_LABELS[i],
                "bin_lower": lo,
                "bin_upper": min(hi, 1.0),
                "count": count,
                "mean_predicted_prob": round(mean_pred, 4),
                "actual_win_rate": round(actual_wr, 4),
                "over_under_confidence": round(over_under, 4),
            }
        )
    return result


def _calibration_quality(bins: list[dict[str, Any]]) -> str:
    """Grade calibration quality based on worst-bin deviation."""
    gaps = [
        abs(b["over_under_confidence"])
        for b in bins
        if b["over_under_confidence"] is not None and b["count"] >= 5
    ]
    if not gaps:
        return "insufficient_data"
    max_gap = max(gaps)
    if max_gap < 0.08:
        return "good"
    if max_gap < 0.15:
        return "ok"
    return "poor"


def _sport_stats(entries: list[dict[str, Any]], sport: str) -> dict[str, Any] | None:
    subset = [e for e in entries if str(e.get("sport", "")).lower() == sport.lower()]
    if not subset:
        return None
    wins = sum(1 for e in subset if e["result"] == "win")
    total_pnl = sum(float(e.get("pnl_units") or 0.0) for e in subset)
    return {
        "count": len(subset),
        "win_rate": round(wins / len(subset), 4),
        "brier_score": round(_brier_score(subset), 4),
        "log_loss": round(_log_loss(subset), 4),
        "accuracy": round(_accuracy(subset), 4),
        "total_pnl_units": round(total_pnl, 4),
        "bins": _bin_stats(subset),
    }


def calibration_stats(ledger_path: Path) -> dict[str, Any]:
    """Load ledger and compute model calibration statistics.

    Args:
        ledger_path: Path to the JSON paper signal ledger.

    Returns:
        A dict with per-bin calibration data, overall metrics, per-sport
        breakdowns, and a calibration quality grade.
    """
    raw = json.loads(ledger_path.read_text(encoding="utf-8"))
    all_entries = list((raw.get("entries") or {}).values())

    # Keep only settled entries with valid model_prob and result
    settled: list[dict[str, Any]] = []
    for e in all_entries:
        if e.get("ledger_status") != "settled":
            continue
        if e.get("result") not in {"win", "loss"}:
            continue
        try:
            prob = float(e["model_prob"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0.0 <= prob <= 1.0):
            continue
        settled.append(e)

    if not settled:
        return {
            "total_settled": 0,
            "bins": [],
            "brier_score": None,
            "log_loss": None,
            "accuracy": None,
            "total_pnl_units": None,
            "calibration_quality": "insufficient_data",
            "by_sport": {},
        }

    wins = sum(1 for e in settled if e["result"] == "win")
    total_pnl = sum(float(e.get("pnl_units") or 0.0) for e in settled)
    bins = _bin_stats(settled)
    quality = _calibration_quality(bins)

    by_sport: dict[str, Any] = {}
    for sport in ("football", "tennis"):
        sport_data = _sport_stats(settled, sport)
        if sport_data is not None:
            by_sport[sport] = sport_data

    return {
        "total_settled": len(settled),
        "total_wins": wins,
        "overall_win_rate": round(wins / len(settled), 4),
        "brier_score": round(_brier_score(settled), 4),
        "log_loss": round(_log_loss(settled), 4),
        "accuracy": round(_accuracy(settled), 4),
        "total_pnl_units": round(total_pnl, 4),
        "calibration_quality": quality,
        "bins": bins,
        "by_sport": by_sport,
    }


# ---------------------------------------------------------------------------
# Telegram formatter
# ---------------------------------------------------------------------------

def _verdict(stats: dict[str, Any]) -> str:
    """Determine overall calibration bias verdict."""
    bins = [
        b for b in stats.get("bins", [])
        if b["over_under_confidence"] is not None and b["count"] >= 5
    ]
    if not bins:
        return "недостаточно данных"

    weighted_gap = sum(b["over_under_confidence"] * b["count"] for b in bins)
    total_weight = sum(b["count"] for b in bins)
    mean_gap = weighted_gap / total_weight if total_weight else 0.0

    if abs(mean_gap) < 0.03:
        return "хорошо калибрована"
    if mean_gap > 0:
        return "переоценивает вероятности"
    return "недооценивает вероятности"


def format_calibration_telegram(stats: dict[str, Any]) -> str:
    """Format calibration stats as an HTML Telegram message.

    Args:
        stats: Dict returned by :func:`calibration_stats`.

    Returns:
        HTML-formatted string suitable for Telegram (parse_mode=HTML).
    """
    lines: list[str] = []
    lines.append("<b>📊 Калибровка модели</b>")
    lines.append("")

    total = stats.get("total_settled", 0)
    if total == 0:
        lines.append("⚠️ Нет завершённых ставок для анализа.")
        return "\n".join(lines)

    # --- Per-bin table ---
    lines.append("<b>Бины вероятностей:</b>")
    for b in stats.get("bins", []):
        count = b["count"]
        if count == 0:
            continue
        label = b["bin_label"]
        pred = b["mean_predicted_prob"]
        actual = b["actual_win_rate"]
        gap = b["over_under_confidence"]

        # Display label with nicer formatting
        pred_pct = f"{pred * 100:.0f}%"
        actual_pct = f"{actual * 100:.0f}%"
        gap_abs = abs(gap)
        icon = "✅" if gap_abs <= 0.08 else "❌"

        lines.append(
            f"{label}: прогноз {pred_pct} → реально {actual_pct} ({count} ставок) {icon}"
        )

    lines.append("")

    # --- Brier score ---
    brier = stats.get("brier_score")
    if brier is not None:
        if brier < 0.15:
            brier_grade = "отлично"
        elif brier < 0.20:
            brier_grade = "хорошо"
        elif brier < 0.25:
            brier_grade = "удовлетворительно"
        else:
            brier_grade = "плохо"
        lines.append(
            f"<b>Brier Score:</b> {brier:.4f} ({brier_grade}; 0.25 = случайная модель)"
        )

    ll = stats.get("log_loss")
    if ll is not None:
        lines.append(f"<b>Log-Loss:</b> {ll:.4f}")

    acc = stats.get("accuracy")
    if acc is not None:
        lines.append(f"<b>Точность (accuracy):</b> {acc * 100:.1f}%")

    pnl = stats.get("total_pnl_units")
    if pnl is not None:
        pnl_sign = "+" if pnl >= 0 else ""
        lines.append(f"<b>P&amp;L (paper):</b> {pnl_sign}{pnl:.2f} ед.")

    wr = stats.get("overall_win_rate")
    if wr is not None:
        lines.append(
            f"<b>Win rate:</b> {wr * 100:.1f}% из {total} завершённых ставок"
        )

    lines.append("")

    # --- By-sport breakdown ---
    by_sport = stats.get("by_sport", {})
    if by_sport:
        lines.append("<b>По видам спорта:</b>")
        sport_labels = {"football": "Футбол ⚽", "tennis": "Теннис 🎾"}
        for sport, sdata in by_sport.items():
            label = sport_labels.get(sport, sport.capitalize())
            s_wr = sdata.get("win_rate", 0)
            s_cnt = sdata.get("count", 0)
            s_brier = sdata.get("brier_score")
            s_pnl = sdata.get("total_pnl_units")
            pnl_sign = "+" if (s_pnl or 0) >= 0 else ""
            brier_str = f", Brier {s_brier:.4f}" if s_brier is not None else ""
            pnl_str = (
                f", P&amp;L {pnl_sign}{s_pnl:.2f} ед."
                if s_pnl is not None
                else ""
            )
            lines.append(
                f"  {label}: WR {s_wr * 100:.1f}% ({s_cnt} ставок){brier_str}{pnl_str}"
            )
        lines.append("")

    # --- Quality badge ---
    quality = stats.get("calibration_quality", "unknown")
    quality_labels = {
        "good": "✅ Хорошая",
        "ok": "⚠️ Удовлетворительная",
        "poor": "❌ Плохая",
        "insufficient_data": "❓ Недостаточно данных",
    }
    quality_str = quality_labels.get(quality, quality)
    lines.append(f"<b>Качество калибровки:</b> {quality_str}")

    # --- Verdict ---
    verdict_text = _verdict(stats)
    lines.append(f"<b>Вывод:</b> модель <i>{verdict_text}</i>")

    return "\n".join(lines)
