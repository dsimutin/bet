"""ATP/WTA rankings downloader — Jeff Sackmann's public dataset.

Downloads:
  atp_players.csv      — player_id → full name
  atp_rankings_current.csv — player_id → rank, points

Provides:
  get_rankings(top_n)  → [{rank, player_id, name, points}]
  resolve_name(abbrev) → "Jannik Sinner" from "J. Sinner"

Data is cached locally for 12h.
Source: https://github.com/JeffSackmann/tennis_atp (public domain)
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone, timedelta
from io import StringIO
from pathlib import Path
from urllib.request import Request, urlopen

_log = logging.getLogger(__name__)

_BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master"
_PLAYERS_URL  = f"{_BASE}/atp_players.csv"
_RANKINGS_URL = f"{_BASE}/atp_rankings_current.csv"

# WTA equivalents
_WTA_BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master"
_WTA_PLAYERS_URL  = f"{_WTA_BASE}/wta_players.csv"
_WTA_RANKINGS_URL = f"{_WTA_BASE}/wta_rankings_current.csv"

_CACHE_MAX_AGE_HOURS = 12
_TIMEOUT = 20
_UA = "bet-analytics/1.0"


def get_rankings(
    top_n: int = 200,
    tour: str = "atp",
    cache_dir: Path | None = None,
) -> list[dict]:
    """Return top-N ranked players as list of dicts.

    Each dict: {rank, player_id, name_first, name_last, full_name, points}
    """
    cache_dir = cache_dir or Path("data/staging")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{tour}_rankings_top{top_n}.json"

    if _is_fresh(cache_path, _CACHE_MAX_AGE_HOURS):
        try:
            return json.loads(cache_path.read_text(encoding="utf-8")).get("rankings", [])
        except Exception:
            pass

    players_url  = _PLAYERS_URL  if tour == "atp" else _WTA_PLAYERS_URL
    rankings_url = _RANKINGS_URL if tour == "atp" else _WTA_RANKINGS_URL

    players  = _download_players(players_url)
    rankings = _download_rankings(rankings_url)

    if not players or not rankings:
        _log.warning("[atp_rankings] Download failed for %s", tour)
        return []

    result = []
    for entry in rankings[:top_n]:
        pid = entry["player_id"]
        p   = players.get(pid, {})
        first = p.get("name_first", "")
        last  = p.get("name_last", "")
        result.append({
            "rank":        entry["rank"],
            "player_id":   pid,
            "name_first":  first,
            "name_last":   last,
            "full_name":   f"{first} {last}".strip(),
            "points":      entry["points"],
        })

    cache_path.write_text(
        json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "rankings": result},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _log.info("[atp_rankings] %s top-%d fetched", tour.upper(), len(result))
    return result


def build_name_resolver(
    top_n: int = 300,
    cache_dir: Path | None = None,
) -> dict[str, str]:
    """Build a map of abbreviated names → full names from ATP+WTA rankings.

    Keys include:
      "J. Sinner" → "Jannik Sinner"
      "Sinner"    → "Jannik Sinner"   (last-name only)
      "Jannik Sinner" → "Jannik Sinner"  (passthrough)
    """
    resolver: dict[str, str] = {}

    for tour in ("atp", "wta"):
        rows = get_rankings(top_n=top_n, tour=tour, cache_dir=cache_dir)
        for row in rows:
            full  = row["full_name"]
            first = row["name_first"]
            last  = row["name_last"]
            if not full.strip():
                continue
            # Full name passthrough
            resolver[full]                    = full
            resolver[full.lower()]            = full
            # "Last" only
            if last:
                resolver[last]                = full
                resolver[last.lower()]        = full
            # "F. Last" abbreviated
            if first and last:
                abbrev = f"{first[0]}. {last}"
                resolver[abbrev]              = full
                resolver[abbrev.lower()]      = full
            # "Last, F." format (some APIs)
            if first and last:
                alt = f"{last}, {first[0]}."
                resolver[alt]                 = full
                resolver[alt.lower()]         = full

    return resolver


def resolve_name(
    name: str,
    resolver: dict[str, str] | None = None,
    cache_dir: Path | None = None,
) -> str:
    """Resolve abbreviated/variant name to canonical full name.

    Returns original name unchanged if no match found.
    """
    if resolver is None:
        resolver = build_name_resolver(cache_dir=cache_dir)
    return (
        resolver.get(name)
        or resolver.get(name.lower())
        or name
    )


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _download_players(url: str) -> dict[str, dict]:
    """Download atp_players.csv → {player_id: {name_first, name_last, ...}}"""
    try:
        content = _get(url)
    except Exception as exc:
        _log.debug("[atp_rankings] players download failed: %s", exc)
        return {}

    result: dict[str, dict] = {}
    for row in csv.DictReader(StringIO(content)):
        pid = str(row.get("player_id", "")).strip()
        if pid:
            result[pid] = {
                "name_first": row.get("name_first", "").strip(),
                "name_last":  row.get("name_last", "").strip(),
                "hand":       row.get("hand", ""),
                "ioc":        row.get("ioc", ""),
            }
    return result


def _download_rankings(url: str) -> list[dict]:
    """Download atp_rankings_current.csv → [{rank, player_id, points}]"""
    try:
        content = _get(url)
    except Exception as exc:
        _log.debug("[atp_rankings] rankings download failed: %s", exc)
        return []

    rows = list(csv.DictReader(StringIO(content)))
    if not rows:
        return []

    # File may have multiple dates — keep only the latest date's rows
    dates = sorted({r.get("ranking_date", "") for r in rows}, reverse=True)
    latest = dates[0] if dates else ""
    result = []
    for row in rows:
        if row.get("ranking_date", "") != latest:
            continue
        try:
            # Sackmann uses "player" column (not "player_id")
            pid = str(row.get("player") or row.get("player_id") or "").strip()
            result.append({
                "rank":      int(row.get("rank", 9999)),
                "player_id": pid,
                "points":    int(row.get("points", 0) or 0),
            })
        except (ValueError, TypeError):
            continue
    result.sort(key=lambda r: r["rank"])
    return result


def _get(url: str) -> str:
    req = Request(url, headers={"User-Agent": _UA})
    with urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _is_fresh(path: Path, max_age_hours: int) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(data.get("fetched_at", ""))
        return datetime.now(timezone.utc) - ts < timedelta(hours=max_age_hours)
    except Exception:
        return False
