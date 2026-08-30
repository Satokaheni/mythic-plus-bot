"""Tests for the pure gear-audit rules."""

from blizzard import Enchant, EquippedItem
from gearaudit import ENCHANTABLE_SLOTS, enchantable_slots, gem_ids, parse_enchant_tier

TIER2 = "Enchanted: Enchant Helm - Empowered Blessing of Speed |A:Professions-ChatIcon-Quality-12-Tier2:20:20|a"
TIER1 = "Enchanted: Enchant Chest - Mark of the Worldsoul |A:Professions-ChatIcon-Quality-12-Tier1:20:20|a"
RUNE = "Enchanted: Rune of Sanguination"


def item(slot, enchants=(), sockets=(), item_class_id=4, name="Test Item", item_id=1):
    return EquippedItem(
        slot=slot, name=name, item_id=item_id, item_class_id=item_class_id,
        enchants=tuple(enchants), sockets=tuple(sockets),
    )


def perm(display_string):
    return Enchant(slot_type="PERMANENT", display_string=display_string, enchantment_id=1)


def test_parse_enchant_tier():
    assert parse_enchant_tier(TIER2) == 2
    assert parse_enchant_tier(TIER1) == 1


def test_parse_enchant_tier_returns_none_without_a_marker():
    assert parse_enchant_tier(RUNE) is None
    assert parse_enchant_tier("") is None
    assert parse_enchant_tier("Quality-Tier") is None


def test_enchantable_slots_excludes_a_shield_off_hand():
    items = [item("MAIN_HAND", item_class_id=2), item("OFF_HAND", item_class_id=4)]
    slots = enchantable_slots(items)
    assert "MAIN_HAND" in slots
    assert "OFF_HAND" not in slots
    assert set(ENCHANTABLE_SLOTS).issubset(set(slots))


def test_enchantable_slots_includes_a_weapon_off_hand():
    items = [item("OFF_HAND", item_class_id=2)]
    assert "OFF_HAND" in enchantable_slots(items)


def test_gem_ids_skips_empty_sockets():
    items = [item("NECK", sockets=(240983, None)), item("FINGER_1", sockets=(240984,))]
    assert gem_ids(items) == {240983, 240984}
