"""Settle open tennis paper signals against Jeff Sackmann ATP results."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


def settle_tennis_from_sackmann(
    ledger: "SignalLedger",  # noqa: F821
    cache_dir: Path,
) -> dict[str, Any]:
    """Check open tennis signals against latest ATP results.

    Downloads current-year ATP CSV (cached), looks for matches where
    the signal player won or lost, marks them settled.

    Returns dict with settled/unmatched counts and per-signal results.
    """
    from src.models.signal_ledger import SignalLedger  # noqa: F811
    from src.ingest.tennis_atp import download_atp_season

    current_year = datetime.utcnow().year
    df = download_atp_season(current_year, cache_dir, use_cache=True)
    if df.empty:
        _log.warning("[tennis_settle] No ATP data for %d", current_year)
        return {"settled": 0, "unmatched": 0, "no_data": True}

    settled = 0
    unmatched = 0
    results: list[dict[str, Any]] = []

    for signal_id, entry in ledger.entries().items():
        if entry.get("sport") != "tennis":
            continue
        if entry.get("ledger_status") != "open":
            continue

        player = str(entry.get("player", "")).strip()
        opponent = str(entry.get("opponent", "")).strip()
        generated_at = entry.get("generated_at", entry.get("commence_time", ""))

        try:
            signal_date = datetime.fromisoformat(
                generated_at.replace("Z", "+00:00")
            ).date()
        except Exception:
            signal_date = None

        match_row = _find_match(df, player, opponent, signal_date)
        if match_row is None:
            unmatched += 1
            continue

        winner = str(match_row.get("winner_name", "")).strip()
        player_won = _names_match(player, winner)
        result = "win" if player_won else "loss"

        # Closing odds: use b365w/b365l columns when available
        closing_odds = _pick_closing_odds(match_row, player_won)
        ledger.update_result(signal_id, result=result, closing_odds=closing_odds)
        settled += 1

        results.append({
            "signal_id": signal_id,
            "player": player,
            "opponent": opponent,
            "result": result,
            "entry_odds": entry.get("entry_odds"),
            "closing_odds": closing_odds,
            "pnl_units": ledger.get(signal_id).get("pnl_units"),
        })
        _log.info(
            "[tennis_settle] %s vs %s → %s (odds=%.2f)",
            player, opponent, result, entry.get("entry_odds", 0),
        )

    _log.info(
        "[tennis_settle] Settled=%d unmatched=%d total_open_tennis=%d",
        settled, unmatched, settled + unmatched,
    )
    return {
        "settled": settled,
        "unmatched": unmatched,
        "results": results,
        "summary": ledger.summary(),
    }


def _find_match(
    df: "pd.DataFrame",  # noqa: F821
    player: str,
    opponent: str,
    signal_date: date | None,
    window_days: int = 7,
) -> dict[str, Any] | None:
    """Find a row in df where player played opponent near signal_date."""
    if df.empty:
        return None

    if signal_date is not None:
        cutoff_start = signal_date - timedelta(days=1)
        cutoff_end = signal_date + timedelta(days=window_days)
        mask = (
            (df["match_date"].notna()) &
            (df["match_date"].dt.date >= cutoff_start) &
            (df["match_date"].dt.date <= cutoff_end)
        )
        sub = df[mask]
    else:
        sub = df

    for _, row in sub.iterrows():
        w = str(row.get("winner_name", ""))
        l = str(row.get("loser_name", ""))
        participants = {w, l}
        if _names_match(player, w) or _names_match(player, l):
            if _names_match(opponent, w) or _names_match(opponent, l):
                return row.to_dict()

    return None


def _names_match(a: str, b: str) -> bool:
    """Fuzzy name match: exact or last-name match."""
    a = a.strip().lower()
    b = b.strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    # Last name match
    a_last = a.split()[-1]
    b_last = b.split()[-1]
    if a_last == b_last and len(a_last) > 3:
        return True
    # Abbreviated first name: "N. Djokovic" vs "Novak Djokovic"
    if len(a.split()) == 2 and a.split()[0].endswith("."):
        initial = a.split()[0][0]
        last = a.split()[1]
        b_parts = b.split()
        if len(b_parts) >= 2 and b_parts[-1] == last and b_parts[0].startswith(initial):
            return True
    if len(b.split()) == 2 and b.split()[0].endswith("."):
        initial = b.split()[0][0]
        last = b.split()[1]
        a_parts = a.split()
        if len(a_parts) >= 2 and a_parts[-1] == last and a_parts[0].startswith(initial):
            return True
    return False


def _pick_closing_odds(row: dict[str, Any], player_won: bool) -> float | None:
    """Extract bookmaker closing odds for the winner or loser."""
    if player_won:
        for col in ("b365w", "psw"):
            val = row.get(col)
            try:
                f = float(val)
                if f > 1.0:
                    return f
            except (TypeError, ValueError):
                pass
    else:
        for col in ("b365l", "psl"):
            val = row.get(col)
            try:
                f = float(val)
                if f > 1.0:
                    return f
            except (TypeError, ValueError):
                pass
    return None


def format_settlement_telegram(results: list[dict[str, Any]]) -> str:
    """Format a Telegram message summarising settled tennis signals."""
    if not results:
        return ""

    wins = [r for r in results if r["result"] == "win"]
    losses = [r for r in results if r["result"] == "loss"]
    total_pnl = sum(r.get("pnl_units", 0) or 0 for r in results)

    lines = ["📊 <b>Итоги прогнозов по теннису</b>\n"]
    for r in results:
        icon = "✅" if r["result"] == "win" else "❌"
        pnl = r.get("pnl_units", 0) or 0
        odds = r.get("entry_odds", "?")
        stake_rub = 1000
        if r["result"] == "win":
            payout = round(stake_rub * float(odds)) if isinstance(odds, (int, float)) else "?"
            pnl_str = f"+{payout - stake_rub} ₽" if isinstance(payout, int) else "+?"
        else:
            pnl_str = f"−{stake_rub} ₽"
        lines.append(
            f"{icon} <b>{r['player']}</b> vs {r['opponent']}\n"
            f"   Ставка @ {odds} → {pnl_str}"
        )

    pnl_sign = "+" if total_pnl >= 0 else ""
    lines.append(
        f"\n<b>Итого:</b> {len(wins)}✅ {len(losses)}❌  "
        f"P&L: {pnl_sign}{round(total_pnl * 1000)} ₽ (из расчёта 1 000 ₽/ставка)"
    )
    lines.append("\n📄 Бумажная статистика — реальных денег нет")
    return "\n".join(lines)
