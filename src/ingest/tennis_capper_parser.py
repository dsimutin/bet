"""Parse tennis tip messages from Telegram capper channels.

Extracts: player picked, opponent, match odds, confidence.
Works with EN/RU messages in typical capper formats.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Known ATP players (top-100 last names for matching)
_ATP_LASTNAMES = {
    "djokovic",
    "alcaraz",
    "sinner",
    "medvedev",
    "zverev",
    "tsitsipas",
    "rublev",
    "ruud",
    "fritz",
    "de minaur",
    "deminaur",
    "hurkacz",
    "musetti",
    "dimitrov",
    "paul",
    "norrie",
    "korda",
    "tiafoe",
    "auger-aliassime",
    "schwartzman",
    "berrettini",
    "carreno",
    "busta",
    "wawrinka",
    "kyrgios",
    "khachanov",
    "cilic",
    "sonego",
    "krajinovic",
    "davidovich",
    "fokina",
    "nakashima",
    "brooksby",
    "mpetshi",
    "perricard",
    "shelton",
    "draper",
    "struff",
    "bublik",
    "safiullin",
    "kecmanovic",
    "tabilo",
    "cerundolo",
    "etcheverry",
    "arnaldi",
    "cobolli",
    "marozsan",
    "fils",
    "gasquet",
    "simon",
    "monfils",
    "thiem",
    "verdasco",
    "nishioka",
    "isner",
    "opelka",
    "giron",
    "wolf",
    "kokkinakis",
    "popyrin",
    "comesana",
    "navone",
}

# Patterns for match prediction direction
_P1_WIN = re.compile(
    r"\b(п1|p1|home|w1|1x2:1|победа\s+1|win\s+1|bet\s+1|tip:?\s*1)\b",
    re.IGNORECASE,
)
_P2_WIN = re.compile(
    r"\b(п2|p2|away|w2|1x2:2|победа\s+2|win\s+2|bet\s+2|tip:?\s*2)\b",
    re.IGNORECASE,
)
_ODDS_PATTERN = re.compile(r"[@\s]\s*([1-9]\.\d{1,3})\b")
_PLAYER_VS = re.compile(
    r"([\w\s'\-\.]{3,25})\s+[-–—vs\.]+\s+([\w\s'\-\.]{3,25})",
    re.IGNORECASE,
)


@dataclass
class TennisTip:
    channel: str
    player_picked: str
    opponent: str
    odds: float | None
    confidence: float  # 0..1, based on odds and signal clarity
    raw_text: str
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def parse_tennis_tip(text: str, channel: str = "") -> TennisTip | None:
    """Extract a tennis tip from a capper message. Returns None if not a tennis tip."""
    text_lower = text.lower()

    # Must look like a tennis message
    tennis_keywords = [
        "tennis",
        "теннис",
        "atp",
        "wta",
        "roland",
        "wimbledon",
        "grand slam",
        "открытый",
        "🎾",
    ]
    if not any(k in text_lower for k in tennis_keywords):
        # Try without keyword if has player names + odds
        if not _has_player_names(text_lower) or not _ODDS_PATTERN.search(text):
            return None

    # Extract match participants
    match = _PLAYER_VS.search(text)
    if not match:
        return None

    p1_raw = match.group(1).strip()
    p2_raw = match.group(2).strip()

    # Determine which player is being backed
    picked, opponent = _detect_pick(text, p1_raw, p2_raw)
    if not picked:
        return None

    # Extract odds
    odds_match = _ODDS_PATTERN.search(text)
    odds = float(odds_match.group(1)) if odds_match else None

    # Confidence: higher odds = potentially more value but less certain
    # Simple heuristic: clear signal language + known player name
    confidence = _score_confidence(text, picked, odds)
    if confidence < 0.3:
        return None

    return TennisTip(
        channel=channel,
        player_picked=_normalize_name(picked),
        opponent=_normalize_name(opponent),
        odds=odds,
        confidence=confidence,
        raw_text=text[:500],
    )


def _has_player_names(text_lower: str) -> bool:
    return sum(1 for name in _ATP_LASTNAMES if name in text_lower) >= 2


def _detect_pick(text: str, p1: str, p2: str) -> tuple[str, str]:
    """Determine which player is being picked. Returns (picked, opponent)."""
    text_lower = text.lower()

    if _P1_WIN.search(text_lower):
        return p1, p2
    if _P2_WIN.search(text_lower):
        return p2, p1

    # Look for "bet: PlayerName" or "pick: PlayerName" or "TIP: PlayerName"
    tip_pattern = re.compile(
        r"(?:tip|ставка|pick|bet|прогноз|прог)[:\s]+([А-Яа-яA-Za-z\s'\-\.]{3,25})",
        re.IGNORECASE,
    )
    m = tip_pattern.search(text)
    if m:
        named = m.group(1).strip().lower()
        if any(n in named for n in p1.lower().split()):
            return p1, p2
        if any(n in named for n in p2.lower().split()):
            return p2, p1

    # Fallback: if p1 last name appears near "win" or "победит"
    for player, opp in [(p1, p2), (p2, p1)]:
        last = player.strip().split()[-1].lower()
        win_near = re.compile(
            rf"{re.escape(last)}.{{0,20}}(win|победит|берёт|выиграет)",
            re.IGNORECASE,
        )
        if win_near.search(text):
            return player, opp

    return "", ""


def _normalize_name(raw: str) -> str:
    """Capitalize each word, strip noise."""
    return " ".join(w.capitalize() for w in raw.strip().split() if len(w) > 1)


def _score_confidence(text: str, picked: str, odds: float | None) -> float:
    """Heuristic confidence score 0..1."""
    score = 0.5

    # Known player → +0.2
    if any(n in picked.lower() for n in _ATP_LASTNAMES):
        score += 0.2

    # Explicit odds mentioned → +0.1
    if odds and 1.3 <= odds <= 5.0:
        score += 0.1

    # Bet365/Pinnacle/Betway mentioned → +0.1
    if re.search(r"\b(pinnacle|pin|bet365|betway|1xbet|marathon)\b", text, re.I):
        score += 0.1

    # Vague language → -0.2
    if re.search(r"\b(maybe|возможно|вероятно|might|could)\b", text, re.I):
        score -= 0.2

    return max(0.0, min(1.0, score))
