"""Tests for the pure gear-audit rules."""

from blizzard import Enchant, EquippedItem
from gearaudit import (
    ENCHANTABLE_SLOTS,
    MAX_MESSAGE_CHARS,
    CharacterFindings,
    audit_character,
    enchantable_slots,
    format_character_line,
    format_report,
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


def test_enchantable_slots_includes_a_weapon_off_hand():
    items = [item("OFF_HAND", item_class_id=2)]
    assert "OFF_HAND" in enchantable_slots(items)


def test_gem_ids_skips_empty_sockets():
    items = [item("NECK", sockets=(240983, None)), item("FINGER_1", sockets=(240984,))]
    assert gem_ids(items) == {240983, 240984}


def full_kit():
    """A character with a valid Tier-2 enchant in every enchantable slot and no sockets."""
    return [item(slot, enchants=(perm(TIER2),)) for slot in ENCHANTABLE_SLOTS]


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


def test_audit_character_flags_rank_one_gems():
    """Rank 1 gems are UNCOMMON (e.g. "Deadly Peridot"); rank 2 is RARE ("Flawless Deadly Peridot")."""
    items = full_kit() + [item("NECK", sockets=(111,))]
    findings = audit_character("Rank One", "Mal'Ganis", items, {111: "UNCOMMON"})
    assert findings.low_gems == [("NECK", "UNCOMMON")]


def test_audit_character_accepts_rank_two_and_meta_gems():
    """RARE is the max-rank stat gem and EPIC is the meta diamond — neither is a finding."""
    items = full_kit() + [item("NECK", sockets=(240890,)), item("WRIST", sockets=(240983,))]
    findings = audit_character("Geared", "Mal'Ganis", items, {240890: "RARE", 240983: "EPIC"})
    assert findings.low_gems == []
    assert findings.is_clean


def test_audit_character_does_not_flag_unknown_gem_quality():
    items = full_kit() + [item("NECK", sockets=(999,))]
    findings = audit_character("Unknown Gem", "Mal'Ganis", items, {})
    assert findings.low_gems == []


def test_problem_count_sums_every_kind():
    items = [i for i in full_kit() if i.slot != "HEAD"]
    items += [item("HEAD"), item("NECK", sockets=(None, 111))]
    findings = audit_character("Messy", "Mal'Ganis", items, {111: "UNCOMMON"})
    assert findings.problem_count == 3  # 1 missing enchant + 1 empty socket + 1 low gem


def a_finding(name, missing=(), low_enchants=(), empty_sockets=0, low_gems=(), realm="Mal'Ganis"):
    return CharacterFindings(
        name=name, realm=realm, missing_enchants=list(missing), low_enchants=list(low_enchants),
        empty_sockets=empty_sockets, low_gems=list(low_gems),
    )


def test_format_character_line_uses_friendly_slot_names():
    line = format_character_line(a_finding("Talvan", missing=["HEAD", "FINGER_1", "MAIN_HAND"]))
    assert "**Talvan** (Mal'Ganis)" in line
    assert "missing Head, Ring 1, Weapon" in line


def test_format_character_line_pluralizes_sockets():
    assert "1 empty socket" in format_character_line(a_finding("A", empty_sockets=1))
    assert "2 empty sockets" in format_character_line(a_finding("B", empty_sockets=2))


def test_format_character_line_reports_every_kind():
    line = format_character_line(
        a_finding("Messy", missing=["HEAD"], low_enchants=[("CHEST", 1)], empty_sockets=1, low_gems=[("NECK", "RARE")])
    )
    assert "missing Head" in line
    assert "low-tier enchant on Chest (Tier 1)" in line
    assert "1 empty socket" in line
    assert "1 low-quality gem" in line


def test_format_report_sorts_worst_first_then_by_name():
    chunks = format_report(
        [
            a_finding("Talvan", missing=["HEAD"]),
            a_finding("Desdemona", missing=["HEAD", "SHOULDER", "LEGS"]),
            a_finding("Bitterbee", empty_sockets=1),
            a_finding("Clean"),
        ],
        [],
    )
    body = "\n".join(chunks)
    assert body.index("Desdemona") < body.index("Bitterbee") < body.index("Talvan")
    assert "3 of 4 characters need something" in body
    assert "1 character clean." in body


def test_format_report_lists_failures_and_guild_bank_line():
    chunks = format_report(
        [a_finding("Clean")],
        [("Ghost", "Mal'Ganis", "not found — renamed, transferred, or a stale mapping")],
    )
    body = "\n".join(chunks)
    assert "Could not fetch: Ghost (Mal'Ganis) — not found — renamed, transferred, or a stale mapping." in body
    assert "guild bank" in body


def test_format_report_handles_an_empty_roster():
    chunks = format_report([], [])
    assert len(chunks) == 1
    assert "0 of 0" in chunks[0]


def test_format_report_chunks_long_output():
    many = [a_finding(f"Character{n:03d}", missing=["HEAD", "SHOULDER", "LEGS", "FEET"]) for n in range(120)]
    chunks = format_report(many, [])
    assert len(chunks) > 1
    assert all(len(chunk) <= MAX_MESSAGE_CHARS for chunk in chunks)
    assert "Character119" in "\n".join(chunks)


def test_audit_character_end_to_end_shield_off_hand_not_flagged():
    """Shield off-hand should not be flagged as missing enchant, but weapon off-hand should be."""
    # Shield off-hand: item_class_id=4 (armor), should be excluded from enchantable slots
    items = full_kit() + [item("OFF_HAND", item_class_id=4, name="Shield")]
    findings = audit_character("Shield Bearer", "Mal'Ganis", items, {})
    assert findings.is_clean
    assert "OFF_HAND" not in findings.missing_enchants


def test_audit_character_end_to_end_weapon_off_hand_flagged():
    """Weapon off-hand without enchant should be flagged."""
    # Weapon off-hand: item_class_id=2, should be included and flagged when unenchanted
    items = [i for i in full_kit() if i.slot != "MAIN_HAND"]
    items.append(item("MAIN_HAND", enchants=(perm(TIER2),), item_class_id=2))
    items.append(item("OFF_HAND", item_class_id=2, name="Weapon"))  # No enchant
    findings = audit_character("Dual Wielder", "Mal'Ganis", items, {})
    assert "OFF_HAND" in findings.missing_enchants
    assert not findings.is_clean
