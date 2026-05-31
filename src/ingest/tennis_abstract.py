"""Tennis Abstract live stats scraper.

Fetches current-season serve/return stats from tennisabstract.com
(Jeff Sackmann's analytics site). Used to update Markov model's
p_serve with the most recent data.

Stats fetched per player per surface:
  spw   - service points won %  (used as p_serve in Markov model)
  rpw   - return points won %
  fs_pct - first serve % in

Stats are cached locally for 24h to avoid hammering the site.
Falls back gracefully to None if the site is unavailable.
"""

from __future__ import annotations

import html.parser
import json
import logging
import time
import urllib.request as _urllib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_BASE_URL = "https://www.tennisabstract.com"
_LEADERS_URL = _BASE_URL + "/cgi-bin/leaders.cgi"
_PLAYER_URL = _BASE_URL + "/cgi-bin/player.cgi?p={player_id}"

_DEFAULT_CACHE_PATH = Path("data/staging/tennis_abstract_stats.json")
_CACHE_MAX_AGE_HOURS = 24
_REQUEST_TIMEOUT = 20
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; bet-analytics/1.0; "
        "+https://github.com/dsimutin/bet)"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

# Surface param codes for Tennis Abstract leaders page
_SURFACE_PARAMS = {
    "hard": "hard",
    "clay": "clay",
    "grass": "grass",
    "all": "all",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_serve_stats(
    cache_path: Path | None = None,
    max_age_hours: int = _CACHE_MAX_AGE_HOURS,
    force_refresh: bool = False,
) -> dict[str, dict[str, float]]:
    """Return {player_name: {surface: p_serve}} from Tennis Abstract.

    Result is cached locally. Returns empty dict on failure.
    p_serve is the fraction of service points won (0..1).
    """
    path = cache_path or _DEFAULT_CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    if not force_refresh and _is_cache_fresh(path, max_age_hours):
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            stats = cached.get("stats", {})
            _log.debug("[tennis_abstract] Cache hit: %d players", len(stats))
            return stats
        except Exception as exc:
            _log.debug("[tennis_abstract] Cache read error: %s", exc)

    stats = _fetch_all_surfaces()
    if stats:
        _save_cache(path, stats)
        _log.info("[tennis_abstract] Fetched %d player records", len(stats))
    else:
        _log.warning("[tennis_abstract] Fetch failed — returning empty stats")

    return stats


def get_player_serve_prob(
    player: str,
    surface: str = "hard",
    stats: dict[str, dict[str, float]] | None = None,
) -> float | None:
    """Lookup p_serve for one player from (optionally pre-loaded) stats.

    Tries exact name, then last-name-only match.
    Returns None if not found or stats unavailable.
    """
    if stats is None:
        stats = fetch_serve_stats()
    if not stats:
        return None

    surface = surface.lower()

    # Exact match
    player_stats = stats.get(player)
    if player_stats:
        return player_stats.get(surface) or player_stats.get("hard")

    # Last-name fallback
    last = player.strip().split()[-1].lower() if player.strip() else ""
    if last:
        for name, ps in stats.items():
            if name.strip().split()[-1].lower() == last:
                return ps.get(surface) or ps.get("hard")

    return None


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def _fetch_all_surfaces() -> dict[str, dict[str, float]]:
    """Fetch serve stats for all players across surfaces."""
    combined: dict[str, dict[str, float]] = {}
    for surface in ("hard", "clay", "grass"):
        rows = _fetch_leaders_surface(surface)
        for player_name, spw in rows.items():
            if player_name not in combined:
                combined[player_name] = {}
            combined[player_name][surface] = spw
    return combined


def _fetch_leaders_surface(surface: str) -> dict[str, float]:
    """Fetch SPW% leaders for one surface from Tennis Abstract."""
    # Tennis Abstract CGI params:
    # p=s{surface}{stat_code}{filters...}
    # Known stat codes: s=SPW, r=RPW, f=FSP, a=ACE%
    # Surface codes: 1=all, 2=hard, 3=clay, 4=grass
    surface_code = {"hard": "2", "clay": "3", "grass": "4", "all": "1"}.get(surface, "1")
    url = f"{_LEADERS_URL}?p=s{surface_code}vAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    try:
        html_content = _get_url(url)
    except Exception as exc:
        _log.debug("[tennis_abstract] GET %s failed: %s", url, exc)
        return {}

    if not html_content:
        return {}

    rows = _parse_leaders_table(html_content)
    _log.debug("[tennis_abstract] surface=%s: %d rows parsed", surface, len(rows))
    return rows


def _get_url(url: str) -> str:
    """Fetch URL with retry."""
    for attempt in range(3):
        try:
            req = _urllib.Request(url, headers=_HEADERS)
            with _urllib.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise exc
    return ""


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

class _TableParser(html.parser.HTMLParser):
    """Extract rows from the first <table> in the HTML."""

    def __init__(self) -> None:
        super().__init__()
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_row: list[str] = []
        self.rows: list[list[str]] = []
        self._cell_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        tag = tag.lower()
        if tag == "table" and not self._in_table:
            self._in_table = True
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._current_row = []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("td", "th") and self._in_cell:
            self._current_row.append(" ".join(self._cell_text).strip())
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            self.rows.append(self._current_row[:])
            self._in_row = False
        elif tag == "table" and self._in_table:
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_text.append(data)


def _parse_leaders_table(html_content: str) -> dict[str, float]:
    """Parse Tennis Abstract leaders HTML table → {player: spw_pct}."""
    parser = _TableParser()
    try:
        parser.feed(html_content)
    except Exception as exc:
        _log.debug("[tennis_abstract] HTML parse error: %s", exc)
        return {}

    rows = parser.rows
    if len(rows) < 3:
        return {}

    # Find header row with 'Player' and 'SPW' columns
    header_idx = -1
    player_col = -1
    spw_col = -1

    for i, row in enumerate(rows[:5]):
        row_lower = [c.lower().strip() for c in row]
        if "player" in row_lower or "name" in row_lower:
            header_idx = i
            for j, cell in enumerate(row_lower):
                if "player" in cell or "name" in cell:
                    player_col = j
                if cell in ("spw", "spw%", "sp%", "svptw%", "s pts w"):
                    spw_col = j
            break

    if header_idx == -1 or player_col == -1:
        # Try a different heuristic: look for rows where col[1] looks like a %
        # Tennis Abstract format: rank, player, SPW%, RPW%, ...
        results = {}
        for row in rows[1:]:
            if len(row) < 3:
                continue
            # Typical TA format: [rank, player_name, spw_pct, ...]
            player_name = _clean_player_name(row[1]) if len(row) > 1 else ""
            if not player_name or len(player_name) < 3:
                continue
            spw_str = row[2] if len(row) > 2 else ""
            spw = _parse_pct(spw_str)
            if spw is not None and 0.3 <= spw <= 0.85:
                results[player_name] = spw
        return results

    results = {}
    for row in rows[header_idx + 1:]:
        if not row or len(row) <= max(player_col, spw_col if spw_col >= 0 else 0):
            continue
        player_name = _clean_player_name(row[player_col])
        if not player_name or len(player_name) < 3:
            continue
        if spw_col >= 0 and spw_col < len(row):
            spw = _parse_pct(row[spw_col])
            if spw is not None and 0.3 <= spw <= 0.85:
                results[player_name] = spw

    return results


def _clean_player_name(name: str) -> str:
    """Normalize player name from Tennis Abstract HTML."""
    # Remove common suffixes/prefixes
    name = name.strip()
    for noise in ["()", "[]", "*", "†"]:
        name = name.replace(noise, "")
    return name.strip()


def _parse_pct(value: str) -> float | None:
    """Parse '68.4' or '68.4%' → 0.684, or None."""
    v = str(value).strip().rstrip("%")
    try:
        f = float(v)
        # If > 1, assume it's a percentage like 68.4 → 0.684
        return f / 100.0 if f > 1.0 else f
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _is_cache_fresh(path: Path, max_age_hours: int) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ts_str = data.get("fetched_at")
        if not ts_str:
            return False
        ts = datetime.fromisoformat(ts_str)
        return datetime.now(timezone.utc) - ts < timedelta(hours=max_age_hours)
    except Exception:
        return False


def _save_cache(path: Path, stats: dict[str, dict[str, float]]) -> None:
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "n_players": len(stats),
        "stats": stats,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
