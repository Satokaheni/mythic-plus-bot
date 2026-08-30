"""Gear audit rules: missing enchants, empty sockets, and low-quality enchants and gems.

Pure logic — every function here takes an already-fetched equipment payload, so the whole
module is testable without Discord or Blizzard. The flow layer in bot.py does the I/O.
"""

import re
from dataclasses import dataclass
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


@dataclass
class CharacterFindings:
    """Everything wrong with one character's gear. Empty lists and a zero count mean clean."""

    name: str
    realm: str
    missing_enchants: List[str]
    low_enchants: List[Tuple[str, int]]
    empty_sockets: int
    low_gems: List[Tuple[str, str]]

    @property
    def problem_count(self) -> int:
        return len(self.missing_enchants) + len(self.low_enchants) + self.empty_sockets + len(self.low_gems)

    @property
    def is_clean(self) -> bool:
        return self.problem_count == 0


def _is_below_min_quality(quality: str) -> bool:
    """True only when the quality is known and ranks below GEM_MIN_QUALITY."""
    if quality not in _QUALITY_ORDER:
        return False
    return _QUALITY_ORDER.index(quality) < _QUALITY_ORDER.index(GEM_MIN_QUALITY)


def audit_character(
    name: str,
    realm: str,
    items: Sequence[EquippedItem],
    gem_quality: Dict[int, str],
) -> CharacterFindings:
    """Audit one character's equipped items.

    `gem_quality` maps a gem's item id to its quality string; ids missing from it are left
    ungraded, so an unresolvable gem never produces a false positive.
    """
    by_slot = {item.slot: item for item in items}
    missing: List[str] = []
    low_enchants: List[Tuple[str, int]] = []
    low_gems: List[Tuple[str, str]] = []
    empty_sockets = 0

    for slot in enchantable_slots(items):
        item = by_slot.get(slot)
        if item is None:
            continue  # An empty slot is a different problem, and not this command's job.
        permanent = [e for e in item.enchants if e.slot_type == "PERMANENT"]
        if not permanent:
            missing.append(slot)
            continue
        for enchant in permanent:
            tier = parse_enchant_tier(enchant.display_string)
            if tier is not None and tier < MAX_ENCHANT_TIER:
                low_enchants.append((slot, tier))

    for item in items:
        for gem_id in item.sockets:
            if gem_id is None:
                empty_sockets += 1
                continue
            quality = gem_quality.get(gem_id, "")
            if _is_below_min_quality(quality):
                low_gems.append((item.slot, quality))

    return CharacterFindings(
        name=name,
        realm=realm,
        missing_enchants=missing,
        low_enchants=low_enchants,
        empty_sockets=empty_sockets,
        low_gems=low_gems,
    )
