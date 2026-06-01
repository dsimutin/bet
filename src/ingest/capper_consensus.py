"""Read tennis capper tips from telegram_live jsonl and compute consensus.

Usage in signal scan:
    consensus = get_capper_consensus("Novak Djokovic", "Carlos Alcaraz")
    # Returns: {"support": 0.8, "n_tips": 5, "avg_odds": 1.72}
    # support = fraction of cappers picking this player (0..1)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

_log = logging.getLogger(__name__)

_DEFAULT_TIPS_PATH = (
    Path(os.environ.get("DATA_DIR", "data"))
    / "staging"
    / "free_sources"
    / "tennis_capper_tips.jsonl"
)
_TIP_MAX_AGE_HOURS = 48  # ignore tips older than 48h


def get_capper_consensus(
    player: str,
    opponent: str,
    tips_path: Path | None = None,
    max_age_hours: int = _TIP_MAX_AGE_HOURS,
) -> dict:
    """Return capper consensus for player beating opponent.

    Returns dict with:
        support     - fraction of tips backing this player (0..1)
        n_tips      - total relevant tips found
        avg_odds    - average odds given by cappers for this pick
        channels    - list of channels that picked this player
    """
    path = tips_path or _DEFAULT_TIPS_PATH
    if not path.exists():
        return {"support": None, "n_tips": 0, "avg_odds": None, "channels": []}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    p1_lower = player.lower().split()[-1]  # last name
    p2_lower = opponent.lower().split()[-1]

    tips_for: list[dict] = []  # tips picking player
    tips_against: list[dict] = []  # tips picking opponent

    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    tip = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Age filter
                try:
                    ts = datetime.fromisoformat(tip.get("date", ""))
                    if ts < cutoff:
                        continue
                except ValueError:
                    continue

                picked = tip.get("player_picked", "").lower()
                opp = tip.get("opponent", "").lower()

                # Match to our matchup
                if p1_lower in picked and p2_lower in opp:
                    tips_for.append(tip)
                elif p2_lower in picked and p1_lower in opp:
                    tips_against.append(tip)
    except Exception as exc:
        _log.debug("Capper consensus read failed: %s", exc)
        return {"support": None, "n_tips": 0, "avg_odds": None, "channels": []}

    total = len(tips_for) + len(tips_against)
    if total == 0:
        return {"support": None, "n_tips": 0, "avg_odds": None, "channels": []}

    support = len(tips_for) / total
    odds_list = [t["odds"] for t in tips_for if t.get("odds")]
    avg_odds = round(sum(odds_list) / len(odds_list), 3) if odds_list else None
    channels = list({t.get("channel", "") for t in tips_for})

    return {
        "support": round(support, 3),
        "n_tips": total,
        "n_for": len(tips_for),
        "avg_odds": avg_odds,
        "channels": channels,
    }
