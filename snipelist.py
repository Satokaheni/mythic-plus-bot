"""Server-specific auction-snipe state, cross-realm detection, and formatting."""

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from watchlist import format_gold  # reuse the copper -> g/s/c formatter

logger = logging.getLogger("discord")

PET_ITEM_ID = 82800  # AH "caged battle pet" item id; pets are keyed by species id


def parse_gold(text: str) -> Optional[int]:
    """Parse a whole/decimal gold amount into copper. None if invalid or non-positive."""
    try:
        gold = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(gold) or gold <= 0:
        return None
    return int(round(gold * 10000))


def best_price_for(auctions: List[dict], kind: str, key_id: int) -> Optional[Tuple[int, int]]:
    """Cheapest (buyout_price, quantity) for a key within one realm's auctions, or None.

    Items match on item.id; pets match item.id == PET_ITEM_ID and pet_species_id.
    Bid-only auctions (no positive buyout) are ignored.
    """
    best: Optional[Tuple[int, int]] = None
    for a in auctions:
        item = a.get("item", {})
        if kind == "pet":
            if item.get("id") != PET_ITEM_ID or item.get("pet_species_id") != key_id:
                continue
        elif item.get("id") != key_id:
            continue
        price = a.get("buyout")
        if not price:  # None or 0 -> bid-only / not purchasable
            continue
        qty = int(a.get("quantity", 1))
        if best is None or price < best[0]:
            best = (int(price), qty)
    return best


def cheapest(realm_prices: Dict[int, Tuple[int, int]]) -> Optional[Tuple[int, int, int]]:
    """Given {realm_id: (price, qty)}, return (price, qty, realm_id) at the minimum price."""
    best: Optional[Tuple[int, int, int]] = None
    for realm_id, (price, qty) in realm_prices.items():
        if best is None or price < best[0]:
            best = (price, qty, realm_id)
    return best


def apply_anti_spam(fired: bool, state: str) -> Tuple[bool, str]:
    """Anti-spam state machine shared by a subscriber and the banker CC.

    Returns (should_dm, new_state). Fire while armed -> DM once, go alerted.
    Not firing re-arms. Never DMs twice for the same continuous dip.
    """
    if fired:
        if state == "armed":
            return True, "alerted"
        return False, "alerted"
    return False, "armed"
