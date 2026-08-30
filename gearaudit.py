"""Gear audit rules: missing enchants, empty sockets, and low-quality enchants and gems.

Pure logic — every function here takes an already-fetched equipment payload, so the whole
module is testable without Discord or Blizzard. The flow layer in bot.py does the I/O.
"""

import re
from typing import Dict, List, Optional, Sequence, Set, Tuple

from blizzard import EquippedItem

# Slots that take a permanent enchant this expansion. Measured against the live roster on
# 2026-08-30 (30 characters): CHEST 30/30, FINGER_2 29/30, MAIN_HAND 29/30, FINGER_1 28/30,
# LEGS 27/30, SHOULDER 26/30, FEET 26/30, HEAD 25/30 — while NECK, WAIST, WRIST, BACK and
# both TRINKETs sat at 0/30 because they take no enchant. Update this list per expansion.
ENCHANTABLE_SLOTS: Tuple[str, ...] = (
    "HEAD",
    "SHOULDER",
    "CHEST",
    "LEGS",
    "FEET",
    "FINGER_1",
    "FINGER_2",
    "MAIN_HAND",
)

WEAPON_ITEM_CLASS_ID = 2  # OFF_HAND counts only when it holds a weapon; shields take no enchant.
GEM_MIN_QUALITY = "EPIC"
MAX_ENCHANT_TIER = 2  # The cap this expansion, so Tier 1 is the only rank worth flagging.

# Blizzard's quality ladder, lowest first. Anything not listed is left ungraded.
_QUALITY_ORDER = ("POOR", "COMMON", "UNCOMMON", "RARE", "EPIC", "LEGENDARY", "ARTIFACT", "HEIRLOOM")

_TIER_RE = re.compile(r"Quality-\d+-Tier(\d)")

SLOT_LABELS: Dict[str, str] = {
    "HEAD": "Head",
    "SHOULDER": "Shoulder",
    "CHEST": "Chest",
    "LEGS": "Legs",
    "FEET": "Feet",
    "FINGER_1": "Ring 1",
    "FINGER_2": "Ring 2",
    "MAIN_HAND": "Weapon",
    "OFF_HAND": "Off-hand",
    "NECK": "Neck",
    "WRIST": "Wrist",
    "WAIST": "Waist",
    "BACK": "Back",
    "HANDS": "Hands",
}


def slot_label(slot: str) -> str:
    """Human-readable slot name, falling back to the raw Blizzard value."""
    return SLOT_LABELS.get(slot, slot)


def parse_enchant_tier(display_string: str) -> Optional[int]:
    """Crafted quality tier from an enchant's display string, or None when it carries no marker.

    Runes and old-style enchants have no quality marker; those are never graded.
    """
    match = _TIER_RE.search(display_string or "")
    return int(match.group(1)) if match else None


def enchantable_slots(items: Sequence[EquippedItem]) -> List[str]:
    """The slots to check for this character: the fixed set, plus OFF_HAND if it holds a weapon."""
    slots = list(ENCHANTABLE_SLOTS)
    for item in items:
        if item.slot == "OFF_HAND" and item.item_class_id == WEAPON_ITEM_CLASS_ID:
            slots.append("OFF_HAND")
    return slots


def gem_ids(items: Sequence[EquippedItem]) -> Set[int]:
    """Every socketed gem's item id, so the caller can look their qualities up once."""
    return {gem_id for item in items for gem_id in item.sockets if gem_id is not None}
