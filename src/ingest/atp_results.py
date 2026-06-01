"""Fetch recent ATP match results for signal settlement."""
from __future__ import annotations

import logging
import urllib.request
from datetime import datetime, timedelta, timezone

_log = logging.getLogger(__name__)


def fetch_recent_results(days: int = 7) -> list[dict]:
    """Return list of {winner, loser, date, tournament} for recent ATP matches.

    Uses the public Jeff Sackmann tennis_atp CSV repository.
    Falls back to an empty list if the source is unavailable.
    """
    year = datetime.now(timezone.utc).year
    url = (
        f"https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/"
        f"atp_matches_{year}.csv"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bet-analytics/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")
        results: list[dict] = []
        lines = text.strip().split("\n")
        if not lines:
            return results
        header = lines[0].split(",")
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) < len(header):
                continue
            row = dict(zip(header, parts))
            date_str = row.get("tourney_date", "")
            if date_str >= cutoff:
                results.append({
                    "winner": row.get("winner_name", ""),
                    "loser": row.get("loser_name", ""),
                    "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}",
                    "tournament": row.get("tourney_name", ""),
                })
        _log.info("[atp_results] Fetched %d recent results (days=%d)", len(results), days)
        return results
    except Exception as e:
        _log.debug("[atp_results] Fetch failed: %s", e)
        return []
