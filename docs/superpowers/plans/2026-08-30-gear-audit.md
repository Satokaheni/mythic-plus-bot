# Gear Audit (`!gearaudit`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an officer-only `!gearaudit [character]` command that reports which mapped characters are missing enchants, have empty gem sockets, or carry a Tier-1 enchant or below-epic gem.

**Architecture:** A new pure-logic module `gearaudit.py` (rules + findings dataclass + formatters) mirroring the existing `tokenwatch.py` / `snipelist.py` shape, plus one new method and one new parser on the existing `BlizzardClient`, and a thin flow layer in `bot.py` (one command handler and one private helper). Everything worth testing lives in `gearaudit.py` and the `blizzard.py` parsers; `bot.py` only wires it up. No background task, no persisted state.

**Tech Stack:** Python 3.9+, discord.py 2.x, aiohttp, pytest. No new dependencies, no new environment variables.

**Spec:** `docs/superpowers/specs/2026-08-30-gear-audit-design.md`

## Global Constraints

- **Branch:** `feature/loot-council-helper` (current branch, already pushed).
- **Python 3.9 target.** Use `typing.Optional[...]`, `typing.List[...]`, `typing.Tuple[...]`, `typing.Dict[...]`, `typing.Set[...]`. The `int | None` union syntax is a syntax error on 3.9.
- **Line length 120** (black and ruff are both configured to it).
- **Ruff rules `E, F, W, I`** — imports must be sorted or the build fails.
- **Do NOT modify `version.txt`.** The bot writes it after posting the changelog; editing it by hand suppresses that post. `CHANGELOG.md` *is* updated (Task 7), under the existing unreleased `[1.9.0]` heading.
- **Do not commit** `practice.py`, `test.py`, or `version.txt` — unrelated in-flight work in the same worktree. Always `git add` explicit paths, never `git add -A` or `git add .`.
- **`character_mappings.json` is gitignored** and must not be committed or modified by this work. It currently holds 30 entries matching the Midnight S2 roster.
- **`pyproject.toml` is deliberately not modified.** Its `py-modules` list already omits `blizzard`, `snipelist`, and `tokenwatch`; the bot runs as flat modules and is not pip-installed, so leaving `gearaudit` out is consistent with existing practice.
- **Enchant tier cap this expansion is Tier 2** (`MAX_ENCHANT_TIER = 2`). Flag Tier 1 only. Every enchant on the live roster measured 2026-08-30 was Tier 2; a "below max rank" rule would flag all 30 characters.
- **Enchantable slots:** `HEAD, SHOULDER, CHEST, LEGS, FEET, FINGER_1, FINGER_2, MAIN_HAND`, plus `OFF_HAND` only when the equipped off-hand is a weapon (`item_class.id == 2`).
- **No DMs to audited players and no opt-out state.** The report goes to the officer who ran the command.

---

### Task 1: Equipment payload parser

**Files:**
- Modify: `blizzard.py` (add `Enchant` and `EquippedItem` dataclasses beside `ItemInfo`; add `_parse_equipment` beside the other `_parse_*` functions)
- Test: `tests/test_blizzard.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Enchant(slot_type: str, display_string: str, enchantment_id: int)`, `EquippedItem(slot: str, name: str, item_id: int, item_class_id: int, enchants: Tuple[Enchant, ...], sockets: Tuple[Optional[int], ...])`, and `_parse_equipment(data: dict) -> List[EquippedItem]`. Tasks 2, 4 and 6 depend on these exact names.

`sockets` holds one entry per socket: the gem's item id, or `None` for an empty socket. Tuples (not lists) keep the dataclasses hashable and frozen, matching `ItemInfo`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_blizzard.py`:

```python
def test_parse_equipment_reads_enchants_and_sockets():
    data = {
        "equipped_items": [
            {
                "slot": {"type": "HEAD"},
                "name": "Baleful Grave-Knight's Casque",
                "item": {"id": 246001},
                "item_class": {"id": 4, "name": "Armor"},
                "enchantments": [
                    {
                        "display_string": "Enchanted: Enchant Helm |A:Professions-ChatIcon-Quality-12-Tier2:20:20|a",
                        "enchantment_id": 7991,
                        "enchantment_slot": {"id": 0, "type": "PERMANENT"},
                    }
                ],
                "sockets": [
                    {"socket_type": {"type": "PRISMATIC"}, "item": {"id": 240983, "name": "Eversong Diamond"}},
                    {"socket_type": {"type": "PRISMATIC"}},
                ],
            }
        ]
    }
    items = _parse_equipment(data)
    assert len(items) == 1
    item = items[0]
    assert item.slot == "HEAD"
    assert item.item_id == 246001
    assert item.item_class_id == 4
    assert item.sockets == (240983, None)
    assert len(item.enchants) == 1
    assert item.enchants[0].slot_type == "PERMANENT"
    assert item.enchants[0].enchantment_id == 7991


def test_parse_equipment_tolerates_missing_and_malformed_entries():
    data = {
        "equipped_items": [
            "not a dict",
            {"name": "no slot key"},
            {"slot": {"type": "BACK"}, "name": "Cloak", "item": {"id": 5}},
        ]
    }
    items = _parse_equipment(data)
    assert len(items) == 1
    assert items[0].slot == "BACK"
    assert items[0].enchants == ()
    assert items[0].sockets == ()
    assert _parse_equipment({}) == []
```

Add `_parse_equipment` to the existing `from blizzard import (...)` block, keeping the names alphabetical (it sorts before `_parse_item_info`).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_blizzard.py -v`
Expected: FAIL with `ImportError: cannot import name '_parse_equipment' from 'blizzard'`

- [ ] **Step 3: Write minimal implementation**

In `blizzard.py`, add to the imports at the top:

```python
from typing import Dict, List, Optional, Tuple
```

(`Tuple` is already imported; confirm `List`, `Optional`, `Dict` are present.)

Add beside `ItemInfo`:

```python
@dataclass(frozen=True)
class Enchant:
    """One enchantment on an equipped item. `slot_type` is PERMANENT, TEMPORARY, or ON_USE_SPELL."""

    slot_type: str
    display_string: str
    enchantment_id: int


@dataclass(frozen=True)
class EquippedItem:
    """One equipped item. `sockets` holds a gem item id per socket, None where the socket is empty."""

    slot: str
    name: str
    item_id: int
    item_class_id: int
    enchants: Tuple[Enchant, ...]
    sockets: Tuple[Optional[int], ...]
```

Add beside the other parsers:

```python
def _parse_equipment(data: dict) -> List[EquippedItem]:
    """Parse a character equipment payload. Skips malformed entries rather than raising."""
    items: List[EquippedItem] = []
    for raw in data.get("equipped_items", []):
        if not isinstance(raw, dict):
            continue
        slot = (raw.get("slot") or {}).get("type")
        if not slot:
            continue
        enchants = tuple(
            Enchant(
                slot_type=(e.get("enchantment_slot") or {}).get("type", ""),
                display_string=e.get("display_string", ""),
                enchantment_id=int(e.get("enchantment_id", 0)),
            )
            for e in raw.get("enchantments", [])
            if isinstance(e, dict)
        )
        sockets = tuple(
            (s.get("item") or {}).get("id") for s in raw.get("sockets", []) if isinstance(s, dict)
        )
        items.append(
            EquippedItem(
                slot=slot,
                name=raw.get("name", ""),
                item_id=int((raw.get("item") or {}).get("id", 0)),
                item_class_id=int((raw.get("item_class") or {}).get("id", 0)),
                enchants=enchants,
                sockets=sockets,
            )
        )
    return items
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_blizzard.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add blizzard.py tests/test_blizzard.py
git commit -m "feat: parse character equipment payloads (enchants + sockets)"
```

---

### Task 2: Equipment fetch and gem quality on the client

**Files:**
- Modify: `blizzard.py` (add `quality` to `ItemInfo` and `_parse_item_info`; add `character_equipment` to `BlizzardClient`)
- Test: `tests/test_blizzard.py`

**Interfaces:**
- Consumes: `_parse_equipment` and `EquippedItem` from Task 1; the existing `BlizzardClient._get(session, path, namespace)`, which already handles OAuth, the 401 re-mint, and `?namespace={ns}-{region}&locale=en_US`.
- Produces: `ItemInfo(name: str, is_recipe: bool, quality: str = "")` and `async BlizzardClient.character_equipment(session, realm_slug, character) -> Optional[List[EquippedItem]]`, returning `None` on 404. Task 6 calls both.

`quality` gets a default so every existing `snipelist` call site that constructs or reads `ItemInfo` keeps working untouched. Gem grading reuses the client's existing `_item_cache`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_blizzard.py`:

```python
def test_parse_item_info_reads_quality():
    data = {"name": "Flawless Deadly Peridot", "item_class": {"id": 3}, "quality": {"type": "EPIC"}}
    info = _parse_item_info(data)
    assert info.name == "Flawless Deadly Peridot"
    assert info.is_recipe is False
    assert info.quality == "EPIC"


def test_parse_item_info_quality_defaults_to_empty():
    info = _parse_item_info({"name": "Mystery Item", "item_class": {"id": 9}})
    assert info.is_recipe is True
    assert info.quality == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_blizzard.py::test_parse_item_info_reads_quality -v`
Expected: FAIL with `AttributeError: 'ItemInfo' object has no attribute 'quality'`

- [ ] **Step 3: Write minimal implementation**

In `blizzard.py`, add the import for URL quoting. Ruff's `I` rule sorts plain `import x` lines
before `from x import y` lines, so it goes directly below the existing `from typing import ...`
line, not next to `import time`:

```python
from urllib.parse import quote
```

Extend the dataclass:

```python
@dataclass(frozen=True)
class ItemInfo:
    name: str
    is_recipe: bool
    quality: str = ""
```

Extend the parser:

```python
def _parse_item_info(data: dict) -> ItemInfo:
    name = data.get("name", "")
    is_recipe = data.get("item_class", {}).get("id") == RECIPE_ITEM_CLASS_ID
    quality = (data.get("quality") or {}).get("type", "")
    return ItemInfo(name=name, is_recipe=is_recipe, quality=quality)
```

Add the method to `BlizzardClient`, after `item_info`:

```python
    async def character_equipment(
        self, session: aiohttp.ClientSession, realm_slug: str, character: str
    ) -> Optional[List[EquippedItem]]:
        """Equipped items for one character, or None when Blizzard has no such character.

        A 404 means the character was renamed, transferred, or deleted — a stale mapping,
        which the caller reports rather than treating as an error.
        """
        path = f"/profile/wow/character/{realm_slug}/{quote(character.lower())}/equipment"
        async with await self._get(session, path, "profile") as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            return _parse_equipment(await resp.json())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_blizzard.py -v`
Expected: PASS

- [ ] **Step 5: Verify nothing else broke**

Run: `python -m pytest -q`
Expected: PASS — in particular `tests/test_snipelist.py`, which uses `ItemInfo`.

- [ ] **Step 6: Commit**

```bash
git add blizzard.py tests/test_blizzard.py
git commit -m "feat: fetch character equipment and expose item quality"
```

---

### Task 3: Gear audit rules module — constants and tier parsing

**Files:**
- Create: `gearaudit.py`
- Test: `tests/test_gearaudit.py`

**Interfaces:**
- Consumes: `EquippedItem` from Task 1 (type only — imported for annotations).
- Produces: `ENCHANTABLE_SLOTS: Tuple[str, ...]`, `WEAPON_ITEM_CLASS_ID = 2`, `GEM_MIN_QUALITY = "EPIC"`, `MAX_ENCHANT_TIER = 2`, `SLOT_LABELS: Dict[str, str]`, `parse_enchant_tier(display_string: str) -> Optional[int]`, `enchantable_slots(items) -> List[str]`, `gem_ids(items) -> Set[int]`. Tasks 4, 5 and 6 use these.

- [ ] **Step 1: Write the failing test**

Create `tests/test_gearaudit.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gearaudit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gearaudit'`

- [ ] **Step 3: Write minimal implementation**

Create `gearaudit.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_gearaudit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gearaudit.py tests/test_gearaudit.py
git commit -m "feat: add gear audit constants, tier parsing, and slot rules"
```

---

### Task 4: Per-character audit

**Files:**
- Modify: `gearaudit.py`
- Test: `tests/test_gearaudit.py`

**Interfaces:**
- Consumes: Task 3's constants and helpers; `EquippedItem` from Task 1.
- Produces: `CharacterFindings` dataclass with fields `name: str`, `realm: str`, `missing_enchants: List[str]`, `low_enchants: List[Tuple[str, int]]`, `empty_sockets: int`, `low_gems: List[Tuple[str, str]]`, plus properties `problem_count: int` and `is_clean: bool`; and `audit_character(name, realm, items, gem_quality) -> CharacterFindings` where `gem_quality: Dict[int, str]` maps gem item id to quality string. Tasks 5 and 6 use both.

`gem_quality` is injected by the caller so this module stays I/O-free. An unknown gem id is left ungraded rather than flagged — the audit must never produce a false positive.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gearaudit.py` (extend the existing import line to add `audit_character`):

```python
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
```

Extend the imports at the top of the test file:

```python
from gearaudit import (
    ENCHANTABLE_SLOTS,
    audit_character,
    enchantable_slots,
    gem_ids,
    parse_enchant_tier,
)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gearaudit.py -v`
Expected: FAIL with `ImportError: cannot import name 'audit_character' from 'gearaudit'`

- [ ] **Step 3: Write minimal implementation**

First add the dataclass import to the top of `gearaudit.py` — ruff's `I` rule puts `from`
imports after plain ones, so it goes directly above the `typing` line:

```python
from dataclasses import dataclass
```

Then append:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_gearaudit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gearaudit.py tests/test_gearaudit.py
git commit -m "feat: audit a character's enchants, sockets, and gems"
```

---

### Task 5: Report formatting

**Files:**
- Modify: `gearaudit.py`
- Test: `tests/test_gearaudit.py`

**Interfaces:**
- Consumes: `CharacterFindings` and `slot_label` from Tasks 3-4.
- Produces: `MAX_MESSAGE_CHARS = 1900`, `format_character_line(findings) -> str`, and `format_report(findings: Sequence[CharacterFindings], failures: Sequence[Tuple[str, str]]) -> List[str]`. Task 6 sends each returned chunk as its own message.

`failures` is a list of `(character, realm)` pairs that could not be fetched. `format_report` always returns at least one chunk, even with no findings at all.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gearaudit.py` (add `CharacterFindings`, `format_character_line`, `format_report` to the `gearaudit` import):

```python
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
    assert "Tier 1 enchant on Chest" in line
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
    chunks = format_report([a_finding("Clean")], [("Ghost", "Mal'Ganis")])
    body = "\n".join(chunks)
    assert "Could not fetch: Ghost (Mal'Ganis)" in body
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
```

Update the import block to:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gearaudit.py -v`
Expected: FAIL with `ImportError: cannot import name 'MAX_MESSAGE_CHARS' from 'gearaudit'`

- [ ] **Step 3: Write minimal implementation**

Append to `gearaudit.py`:

```python
MAX_MESSAGE_CHARS = 1900  # Discord's hard limit is 2000; leave room for the trailing newline.

GUILD_BANK_LINE = "Enchants and gems are free in the guild bank — grab what you need."


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def format_character_line(findings: CharacterFindings) -> str:
    """One report line for a character with at least one problem."""
    bits: List[str] = []
    if findings.missing_enchants:
        bits.append("missing " + ", ".join(slot_label(s) for s in findings.missing_enchants))
    if findings.empty_sockets:
        bits.append(_plural(findings.empty_sockets, "empty socket"))
    if findings.low_enchants:
        bits.append("Tier 1 enchant on " + ", ".join(slot_label(s) for s, _ in findings.low_enchants))
    if findings.low_gems:
        bits.append(_plural(len(findings.low_gems), "low-quality gem"))
    return f"**{findings.name}** ({findings.realm}) — " + "; ".join(bits)


def _chunk(lines: Sequence[str]) -> List[str]:
    """Pack lines into messages under Discord's limit, never splitting a line."""
    chunks: List[str] = []
    current: List[str] = []
    length = 0
    for line in lines:
        if current and length + len(line) + 1 > MAX_MESSAGE_CHARS:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


def format_report(
    findings: Sequence[CharacterFindings],
    failures: Sequence[Tuple[str, str]],
) -> List[str]:
    """The officer-facing report, worst-first, split into sendable chunks."""
    problems = sorted(
        (f for f in findings if not f.is_clean),
        key=lambda f: (-f.problem_count, f.name.lower()),
    )
    clean = len(findings) - len(problems)
    lines = [f"**Gear Audit** — {len(problems)} of {len(findings)} characters need something", ""]
    lines.extend(format_character_line(f) for f in problems)
    if clean:
        lines.extend(["", f"{_plural(clean, 'character')} clean."])
    if failures:
        lines.append("")
        for character, realm in failures:
            lines.append(f"Could not fetch: {character} ({realm}) — check the mapping.")
    lines.extend(["", GUILD_BANK_LINE])
    return _chunk(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_gearaudit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gearaudit.py tests/test_gearaudit.py
git commit -m "feat: format the gear audit report"
```

---

### Task 6: Wire `!gearaudit` into the bot

**Files:**
- Modify: `bot.py` (import; `GEAR_AUDIT_CONCURRENCY` constant; `_run_gear_audit` helper on `MyClient`; command handler in `on_message`; `!help` entry)

**Interfaces:**
- Consumes: `gearaudit.gem_ids`, `gearaudit.audit_character`, `gearaudit.format_report` (Tasks 3-5); `self.blizzard.character_equipment` and `self.blizzard.item_info` (Task 2); the existing `raiderio.load_character_mappings()` and `ELEVATED_IDS`.
- Produces: the `!gearaudit [character]` command. Nothing later depends on it.

There is no test for this task: it is pure I/O wiring, matching how `!watch` / `!token` are structured. All logic is already covered by Tasks 1-5.

- [ ] **Step 1: Add the import and the concurrency constant**

In `bot.py`, add to the local imports (they are alphabetical — `gearaudit` sorts between `forecast` and `raiderio`):

```python
import gearaudit
```

Add beside the other feature constants near `BANKER_BULK_QTY`:

```python
GEAR_AUDIT_CONCURRENCY = 5  # Parallel Blizzard profile fetches; the roster is ~30 characters.
```

- [ ] **Step 2: Add the fetch-and-audit helper**

Add this method to `MyClient`, next to the other private helpers:

```python
    async def _run_gear_audit(
        self, mappings: List[dict]
    ) -> Tuple[List[gearaudit.CharacterFindings], List[Tuple[str, str]]]:
        """Fetch and audit every mapped character. Returns (findings, failures).

        One character's failure never ends the sweep — it is collected and reported, since a
        404 usually means a rename or transfer the mapping file hasn't caught up with.
        """
        semaphore = asyncio.Semaphore(GEAR_AUDIT_CONCURRENCY)
        findings: List[gearaudit.CharacterFindings] = []
        failures: List[Tuple[str, str]] = []

        async with aiohttp.ClientSession() as session:

            async def audit_one(entry: dict) -> None:
                character = entry.get("character") or ""
                realm_slug = entry.get("realm_slug") or ""
                realm_name = entry.get("realm_name") or realm_slug
                if not character or not realm_slug:
                    # Report it rather than dropping it silently — a half-filled mapping entry is
                    # exactly the kind of drift this command is meant to surface.
                    failures.append((character or "(unnamed entry)", realm_name or "?"))
                    return
                # The semaphore wraps the whole body, not just the equipment fetch: the per-gem
                # item_info lookups are Blizzard calls too, and bounding only the first request
                # would let ~30 characters' worth of gem lookups fan out at once.
                async with semaphore:
                    try:
                        items = await self.blizzard.character_equipment(session, realm_slug, character)
                    except Exception as exc:  # noqa: BLE001 - one bad character must not end the sweep
                        logger.warning("gearaudit: %s/%s failed: %s", realm_slug, character, exc)
                        failures.append((character, realm_name))
                        return
                    if items is None:
                        failures.append((character, realm_name))
                        return
                    gem_quality: Dict[int, str] = {}
                    for gem_id in gearaudit.gem_ids(items):
                        try:
                            gem_quality[gem_id] = (await self.blizzard.item_info(session, gem_id)).quality
                        except Exception as exc:  # noqa: BLE001 - an unknown gem is simply not graded
                            logger.warning("gearaudit: gem %s lookup failed: %s", gem_id, exc)
                    findings.append(gearaudit.audit_character(character, realm_name, items, gem_quality))

            await asyncio.gather(*(audit_one(entry) for entry in mappings))

        return findings, failures
```

- [ ] **Step 3: Add the command handler**

In `on_message`, directly after the `!watches` block and before the `!tokenalert` block, add:

```python
        if message.content.startswith("!gearaudit") and message.author.id in ELEVATED_IDS:
            if message.guild is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
            wanted = message.content[len("!gearaudit"):].strip()
            mappings = raiderio.load_character_mappings()
            if wanted:
                mappings = [m for m in mappings if (m.get("character") or "").lower() == wanted.lower()]
                if not mappings:
                    await message.author.send(f"No mapping for `{wanted}`.")
                    return
            if not mappings:
                await message.author.send("No characters are mapped, so there is nothing to audit.")
                return
            try:
                findings, failures = await self._run_gear_audit(mappings)
            except Exception as exc:  # noqa: BLE001 - a failed audit must not kill on_message
                logger.warning("gearaudit failed: %s", exc)
                await message.author.send("The gear audit failed. Check the logs.")
                return
            for chunk in gearaudit.format_report(findings, failures):
                await message.author.send(chunk)
            return
```

- [ ] **Step 4: Add the `!help` entry**

In the `!help` / `!tools` embed, add a field after the scheduling block:

```python
            embed.add_field(
                name="🔍 Gear Audit (coordinator/admin)",
                value=(
                    "`!gearaudit` — check every mapped character for missing enchants, "
                    "empty sockets, and low-quality enchants/gems\n"
                    "`!gearaudit <character>` — check one character"
                ),
                inline=False,
            )
```

- [ ] **Step 5: Verify the module imports and the suite still passes**

Run: `python -m py_compile bot.py` — expected: no output.
Run: `python -c "import ast; ast.parse(open('bot.py', encoding='utf-8').read())"` — expected: no output.
**Never run `python -c "import bot"` or `python bot.py`.** `bot.py` has no `if __name__ == "__main__"` guard: `client.run(...)` executes at import, so importing it starts the live bot against the real Discord server — posting the changelog, DMing users, and rewriting `version.txt`.
Run: `python -m pytest -q` — expected: PASS.
Run: `python -m ruff check .` — expected: no violations (import order in particular).

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "feat: add !gearaudit command for coordinators and admins"
```

---

### Task 7: Documentation

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `CHANGELOG.md`

**Interfaces:**
- Consumes: the finished feature from Tasks 1-6.
- Produces: nothing code-facing.

- [ ] **Step 1: Update `CLAUDE.md`**

Add a row to the File Map table, after the `snipelist.py` row:

```markdown
| `gearaudit.py` | Gear audit rules — enchant/socket/gem findings and officer report formatting |
```

Add a section under Key Workflows, after "WoW Token Sell Alert (Blizzard)":

```markdown
### Gear Audit (Blizzard)

Officer-only, on demand. `!gearaudit [character]` (coordinator/admin, `ELEVATED_IDS`) reads
`character_mappings.json`, fetches each character's equipped items from Blizzard's
`/profile/wow/character/{realm}/{name}/equipment` endpoint (reusing `BLIZZ_CLIENT_ID`/
`BLIZZ_CLIENT_SECRET`), and DMs the caller a worst-first report.

Flagged: a missing **permanent** enchant on an enchantable slot, an **empty gem socket**, a
**Tier-1 enchant**, and a **below-epic gem**. Not flagged: Tier-2 enchants (the cap this
expansion), temporary weapon oils, and off-hands that are not weapons (`item_class.id != 2`).
`ENCHANTABLE_SLOTS` is a module constant in `gearaudit.py` — head, shoulder, chest, legs, feet,
both rings, main hand, plus a weapon off-hand — measured against the live roster and updated
per expansion.

There is **no background task, no DM to the audited player, and no persisted state**: officers
run the command and relay the result. Characters that fail to fetch (404 = rename or transfer)
are listed separately, which doubles as a stale-mapping report. Gem grading reuses the client's
`_item_cache` via `ItemInfo.quality`; a gem whose quality can't be resolved is left ungraded
rather than flagged. A Tier-1 crafted variant of an epic gem is not detectable — that lives in
`bonus_list` entries the equipment payload doesn't resolve.
```

Add a row to the Commands table, after the `!snipes` row and before the `!help`, `!tools` row:

```markdown
| `!gearaudit [character]` | KEY/DM | Coord/Admin | Report missing enchants, empty sockets, and low-quality enchants/gems |
```

- [ ] **Step 2: Update `README.md`**

Add a section directly after the `### Auction Sniper` section (before `### Availability Forecasting`):

```markdown
### Gear Audit

A coordinator/admin command for checking the roster's enchants and gems against Blizzard's armory data. Every character in `character_mappings.json` is fetched and checked; the report comes back as a DM, worst-first, so an officer can work down the list.

- `!gearaudit` — audit every mapped character
- `!gearaudit <character>` — audit one character

Flagged: a missing permanent enchant on an enchantable slot (head, shoulder, chest, legs, feet, both rings, main hand, and a weapon off-hand), an empty gem socket, a Tier-1 enchant, and a below-epic gem. Not flagged: Tier-2 enchants (the cap this expansion), temporary weapon oils, and shields. Characters that fail to fetch are listed separately — a 404 usually means a rename or transfer, so the mapping needs updating.

Runs on demand only: there is no background task, and the audited player is never DM'd. Uses the existing `BLIZZ_CLIENT_ID` / `BLIZZ_CLIENT_SECRET` credentials, and needs a populated `character_mappings.json`.
```

Add a row to the command table, after the `!snipes` row:

```markdown
| `!gearaudit [character]` | `KEY_CHANNEL` or DM | Coord/Admin | Report missing enchants, empty sockets, and low-quality enchants/gems |
```

Add a line to the Project Structure block, after the `snipelist.py` line:

```
├── gearaudit.py        # Gear audit rules — enchant/socket/gem findings and report formatting
```

- [ ] **Step 3: Update `CHANGELOG.md`**

Add a bullet under the existing `[1.9.0]` → `### Improvements` section (do **not** create a new version heading and do **not** touch `version.txt`):

```markdown
- **Gear Audit** — new coordinator/admin command `!gearaudit [character]` that checks every mapped character against Blizzard's armory data and reports who is missing an enchant, who has an empty gem socket, and who is carrying a Tier-1 enchant or a below-epic gem, worst-first. Characters that fail to fetch are listed separately so stale entries in `character_mappings.json` surface. On demand only — no background task and no DMs to the audited player.
```

- [ ] **Step 4: Verify**

Run: `python -m pytest -q` — expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md CHANGELOG.md
git commit -m "docs: document the gear audit command"
```
