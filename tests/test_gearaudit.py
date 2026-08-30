"""Tests for the pure gear-audit rules."""

from blizzard import Enchant, EquippedItem
from gearaudit import (
    ENCHANTABLE_SLOTS,
    audit_character,
    enchantable_slots,
    gem_ids,
    parse_enchant_tier,
)

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


def full_kit(**overrides):
    """A character with a valid Tier-2 enchant in every enchantable slot and no sockets."""
    items = [item(slot, enchants=(perm(TIER2),)) for slot in ENCHANTABLE_SLOTS]
    return overrides.get("items", items)


def test_audit_character_clean():
    findings = audit_character("Talvan", "Mal'Ganis", full_kit(), {})
    assert findings.is_clean
    assert findings.problem_count == 0
    assert findings.missing_enchants == []


def test_audit_character_reports_missing_enchants():
    items = [i for i in full_kit() if i.slot not in ("HEAD", "LEGS")]
    items += [item("HEAD"), item("LEGS")]
    findings = audit_character("Boongus", "Mal'Ganis", items, {})
    assert sorted(findings.missing_enchants) == ["HEAD", "LEGS"]
    assert findings.problem_count == 2
    assert not findings.is_clean


def test_audit_character_ignores_a_slot_with_no_equipped_item():
    items = [i for i in full_kit() if i.slot != "FEET"]
    findings = audit_character("Someone", "Mal'Ganis", items, {})
    assert "FEET" not in findings.missing_enchants


def test_audit_character_ignores_non_permanent_enchants():
    temporary = Enchant(slot_type="TEMPORARY", display_string="Thalassian Phoenix Oil", enchantment_id=8052)
    items = [i for i in full_kit() if i.slot != "MAIN_HAND"]
    items.append(item("MAIN_HAND", enchants=(temporary,), item_class_id=2))
    findings = audit_character("Oiled", "Mal'Ganis", items, {})
    assert findings.missing_enchants == ["MAIN_HAND"]


def test_audit_character_flags_tier_one_enchant():
    items = [i for i in full_kit() if i.slot != "CHEST"]
    items.append(item("CHEST", enchants=(perm(TIER1),)))
    findings = audit_character("Cheap", "Mal'Ganis", items, {})
    assert findings.low_enchants == [("CHEST", 1)]
    assert findings.missing_enchants == []


def test_audit_character_counts_empty_sockets():
    items = full_kit() + [item("NECK", sockets=(None, None)), item("WRIST", sockets=(240983,))]
    findings = audit_character("Socketless", "Mal'Ganis", items, {240983: "EPIC"})
    assert findings.empty_sockets == 2
    assert findings.low_gems == []


def test_audit_character_flags_below_epic_gems():
    items = full_kit() + [item("NECK", sockets=(111,))]
    findings = audit_character("Rare Gem", "Mal'Ganis", items, {111: "RARE"})
    assert findings.low_gems == [("NECK", "RARE")]


def test_audit_character_does_not_flag_unknown_gem_quality():
    items = full_kit() + [item("NECK", sockets=(999,))]
    findings = audit_character("Unknown Gem", "Mal'Ganis", items, {})
    assert findings.low_gems == []


def test_problem_count_sums_every_kind():
    items = [i for i in full_kit() if i.slot != "HEAD"]
    items += [item("HEAD"), item("NECK", sockets=(None, 111))]
    findings = audit_character("Messy", "Mal'Ganis", items, {111: "RARE"})
    assert findings.problem_count == 3  # 1 missing enchant + 1 empty socket + 1 low gem
