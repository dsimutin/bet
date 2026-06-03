"""Settle open tennis paper signals against Jeff Sackmann ATP results."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import pandas as pd
    from src.models.signal_ledger import SignalLedger

_log = logging.getLogger(__name__)


def settle_tennis_from_sackmann(
    ledger: SignalLedger,
    cache_dir: Path,
    api_key: str = "",
) -> dict[str, Any]:
    """Check open tennis signals against latest ATP results.

    Downloads current-year ATP CSV (cached), looks for matches where
    the signal player won or lost, marks them settled.
    Falls back to live API if historical data doesn't have the match.

    Returns dict with settled/unmatched counts and per-signal results.
    """
    from src.models.signal_ledger import SignalLedger  # noqa: F811
    from src.ingest.tennis_atp import download_atp_season

    current_year = datetime.utcnow().year
    df = download_atp_season(current_year, cache_dir, use_cache=True)
    if df.empty:
        _log.warning("[tennis_settle] No ATP data for %d", current_year)
        df = None

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
            signal_date = datetime.fromisoformat(generated_at.replace("Z", "+00:00")).date()
        except Exception:
            signal_date = None

        match_row = None
        if df is not None:
            match_row = _find_match(df, player, opponent, signal_date)

        # Fallback to live API if historical data doesn't have it
        if match_row is None and api_key:
            live_result = _get_live_tennis_result_fallback(entry, api_key)
            if live_result:
                # Convert to Sackmann-like row format
                match_row = {
                    "winner_name": player if live_result.get("player_won") else opponent,
                    "loser_name": opponent if live_result.get("player_won") else player,
                }

        if match_row is None:
            unmatched += 1
            continue

        winner = str(match_row.get("winner_name", "")).strip()
        player_won = _names_match(player, winner)
        closing_odds = _pick_closing_odds(match_row, player_won)

        market = str(entry.get("market", "h2h"))
        if market == "spreads":
            result = _settle_spread(match_row, player, player_won, entry)
        elif market == "totals":
            result = _settle_total(match_row, entry)
        else:
            result = "win" if player_won else "loss"

        ledger.update_result(
            signal_id,
            result=result,
            closing_odds=closing_odds,
            settlement_source="sackmann_or_live",
            settlement_reason=f"tennis_{market}",
        )
        settled += 1

        results.append(
            {
                "signal_id": signal_id,
                "player": player,
                "opponent": opponent,
                "result": result,
                "entry_odds": entry.get("entry_odds"),
                "closing_odds": closing_odds,
                "pnl_units": ledger.get(signal_id).get("pnl_units"),
            }
        )
        _log.info(
            "[tennis_settle] %s vs %s → %s (odds=%.2f)",
            player,
            opponent,
            result,
            entry.get("entry_odds", 0),
        )

    _log.info(
        "[tennis_settle] Settled=%d unmatched=%d total_open_tennis=%d",
        settled,
        unmatched,
        settled + unmatched,
    )
    return {
        "settled": settled,
        "unmatched": unmatched,
        "results": results,
        "summary": ledger.summary(),
    }


def _find_match(
    df: pd.DataFrame,
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
            (df["match_date"].notna())
            & (df["match_date"].dt.date >= cutoff_start)
            & (df["match_date"].dt.date <= cutoff_end)
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


def _parse_score(score_str: str) -> tuple[int, int, int] | None:
    """Parse '6-4 5-7 7-6(4)' → (winner_sets, loser_sets, total_games).

    Returns None if score is incomplete (RET before last set, W/O).
    """
    if not score_str:
        return None
    parts = str(score_str).strip().split()
    winner_sets = 0
    loser_sets = 0
    total_games = 0
    for part in parts:
        upper = part.upper()
        if upper in ("RET", "W/O", "DEF", "ABD"):
            return None  # incomplete match, skip
        clean = part.split("(")[0]
        if "-" not in clean:
            continue
        try:
            w_g, l_g = (int(x) for x in clean.split("-", 1))
            total_games += w_g + l_g
            if w_g > l_g:
                winner_sets += 1
            else:
                loser_sets += 1
        except ValueError:
            continue
    if winner_sets + loser_sets == 0:
        return None
    return winner_sets, loser_sets, total_games


def _settle_spread(
    row: dict[str, Any],
    player: str,
    player_won: bool,
    entry: dict[str, Any],
) -> Literal["win", "loss", "push", "void"]:
    """Settle a set-handicap bet.

    `handicap` is from the selected player's bookmaker side. It covers when:
    `(player_sets - opponent_sets) + handicap > 0`.

    Examples: 2-0 with -1.5 wins; 2-1 with -1.5 loses; 1-2 with +1.5 wins;
    2-0 with -2.0 pushes.
    """
    score_str = str(row.get("score", ""))
    parsed = _parse_score(score_str)
    if parsed is None:
        return "void"
    winner_sets, loser_sets, _ = parsed
    player_sets = winner_sets if player_won else loser_sets
    opp_sets = loser_sets if player_won else winner_sets
    handicap = float(entry.get("handicap", 0))
    margin = (player_sets - opp_sets) + handicap
    if margin > 0:
        return "win"
    if margin == 0:
        return "push"
    return "loss"


def _settle_total(
    row: dict[str, Any],
    entry: dict[str, Any],
) -> Literal["win", "loss", "push", "void"]:
    """Settle a total-games over/under bet."""
    score_str = str(row.get("score", ""))
    parsed = _parse_score(score_str)
    if parsed is None:
        return "void"
    _, _, total_games = parsed
    threshold = float(
        entry.get("total_threshold", entry.get("threshold", entry.get("handicap", 0)))
    )
    is_over = str(entry.get("selection", "")).lower().startswith("over")
    if total_games == threshold:
        return "push"
    if is_over:
        return "win" if total_games > threshold else "loss"
    return "win" if total_games < threshold else "loss"


def _pick_closing_odds(row: dict[str, Any], player_won: bool) -> float | None:
    """Extract bookmaker closing odds for the winner or loser."""
    if player_won:
        for col in ("b365w", "psw"):
            val = row.get(col)
            try:
                if val is not None:
                    f = float(val)
                    if f > 1.0:
                        return f
            except (TypeError, ValueError):
                pass
    else:
        for col in ("b365l", "psl"):
            val = row.get(col)
            try:
                if val is not None:
                    f = float(val)
                    if f > 1.0:
                        return f
            except (TypeError, ValueError):
                pass
    return None


def _get_live_tennis_result_fallback(entry: dict[str, Any], api_key: str) -> dict[str, Any] | None:
    """Try to get tennis result from live API when historical data doesn't have it."""
    try:
        from src.ingest.live_results import get_live_tennis_result

        player = str(entry.get("player", "")).strip()
        opponent = str(entry.get("opponent", "")).strip()
        tour = entry.get("league", "ATP")
        event_date_str = entry.get("event_date") or entry.get("event_time_utc") or ""

        if not all([player, opponent, event_date_str]):
            return None

        try:
            event_date = datetime.fromisoformat(event_date_str.replace("Z", "+00:00")).date()
        except ValueError:
            return None

        live_result = get_live_tennis_result(player, opponent, event_date, tour, api_key)
        if live_result:
            result_ft = live_result.get("result_ft", "")
            player_won = result_ft == "H"
            return {
                "player": player,
                "opponent": opponent,
                "player_won": player_won,
                "_source": "live_api",
            }
    except Exception as exc:
        _log.debug("[tennis_settle] live_result fallback failed: %s", exc)

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
            f"{icon} <b>{r['player']}</b> vs {r['opponent']}\n" f"   Ставка @ {odds} → {pnl_str}"
        )

    pnl_sign = "+" if total_pnl >= 0 else ""
    lines.append(
        f"\n<b>Итого:</b> {len(wins)}✅ {len(losses)}❌  "
        f"P&L: {pnl_sign}{round(total_pnl * 1000)} ₽ (из расчёта 1 000 ₽/ставка)"
    )
    lines.append("\n📄 Бумажная статистика — реальных денег нет")
    return "\n".join(lines)
