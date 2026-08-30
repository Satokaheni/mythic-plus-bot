# Gear Audit (`!gearaudit`) — Design

**Date:** 2026-08-30
**Status:** Approved design → ready for implementation plan

## Summary

Add an officer-facing gear audit to the Mythic+ bot. `!gearaudit` fetches every
mapped character's equipped items from Blizzard's
[character equipment endpoint](https://develop.battle.net/documentation/world-of-warcraft/profile-apis)
and reports who is missing an enchant, who has an empty gem socket, and who is
carrying a Tier-1 enchant or a below-epic gem — so officers can tell those people
to grab the free replacements from the guild bank.

The audit is **on demand only**. There is no background task and the bot never
DMs the affected player: officers handle the social side themselves.

Existing credentials cover this. `BLIZZ_CLIENT_ID` / `BLIZZ_CLIENT_SECRET`
already reach the profile namespace, and `character_mappings.json` already maps
`character` → `realm_slug` → `discord_id`.

## Evidence

Measured against the live Midnight S2 roster on 2026-08-30 (30 mapped
characters, all of which fetched successfully):

**Which slots are enchantable this expansion** — adoption across the roster:

| Slot | Enchanted | Slot | Enchanted |
|------|-----------|------|-----------|
| CHEST | 30/30 | NECK | 0/30 |
| FINGER_2 | 29/30 | WAIST | 0/30 |
| MAIN_HAND | 29/30 | WRIST | 0/30 |
| FINGER_1 | 28/30 | BACK | 0/30 |
| LEGS | 27/30 | TRINKET_1/2 | 0/30 |
| SHOULDER | 26/30 | HANDS | 1/30 |
| FEET | 26/30 | OFF_HAND | 5/30 |
| HEAD | 25/30 | | |

The zero columns are not neglect — those slots take no permanent enchant in this
expansion. HANDS at 1/30 is a profession tinker, not a stat enchant.

**Enchant quality** — every permanent enchant on the roster carries the
`|A:Professions-ChatIcon-Quality-12-Tier2:20:20|a` marker. Not one Tier 1, not
one Tier 3. Tier 2 is the cap this expansion, so **Tier 1 is the only quality
level worth flagging**. A "below max rank" rule would flag all 30 people and be
worthless.

**Gems** — a socket reports only `{socket_type, item: {id, name}, display_string}`;
there is no quality marker. The gem's own item record does carry
`quality: {type: "EPIC"}`, so grading requires a separate item lookup. Gems seen:
`Indecipherable Eversong Diamond` (epic, neck), and `Flawless <stat> <stone>`
variants.

**Off-hands** — only 5/30 characters have an enchanted off-hand, because most
off-hands are shields or held-in-off-hand items rather than weapons (verified on
a live character: `item_class: Armor`, `item_subclass: Shield`). Equipped items
expose `item_class` (Weapon = 2) and `item_subclass`, so the off-hand can be
checked only when it is actually a weapon.

Running the proposed rules over that roster flags **10 of 30 characters** —
a list an officer can work down, not spam.

## Goals

- One command, `!gearaudit`, that reports every mapped character's enchant and
  gem problems, worst-first.
- `!gearaudit <character>` for a single character.
- Zero false positives: a flagged slot is genuinely missing something.
- Surface characters that fail to fetch, since a 404 means a rename, transfer,
  or stale mapping.
- Keep every rule in a pure, tested module; keep `bot.py` to wiring.

## Non-Goals (YAGNI)

- **No background task.** Explicitly cut. The audit runs when an officer asks.
- **No DMs to the audited player.** The original idea was a daily nag DM; the
  owner reduced scope so officers relay it. Nothing about this design forecloses
  adding that later — `audit_character` returns per-character findings that a
  future task could DM directly.
- **No opt-out list, no reminder state, no JSON state file.** There is no
  recurring message to suppress, so there is nothing to persist.
- **No BiS / "what to grab" table.** Naming the correct enchant per slot and
  spec needs a table someone maintains every patch, and it goes stale silently.
  The DM line is "the guild bank has them free", not a shopping list.
- **No separate `!gearmap` command.** The gap between `character_mappings.json`
  and the wowaudit roster was reconciled by hand on 2026-08-30 (file now matches
  the S2 roster exactly, 30/30). Failed fetches in the audit output cover the
  ongoing drift case.
- **No temporary-enchant (weapon oil) check.** Consumable, per-run, and
  re-applying it is not a guild-bank errand.
- **No wowaudit API dependency at runtime.** The roster comes from
  `character_mappings.json`. wowaudit keys are per-team and rotate every season;
  binding a bot command to one would break the feature at every season
  rollover.

## Configuration

No new environment variables and no new dependencies.

| Var | Purpose | Default |
|-----|---------|---------|
| `BLIZZ_CLIENT_ID` | Blizzard API client ID | *(required, already present)* |
| `BLIZZ_CLIENT_SECRET` | Blizzard API client secret | *(required, already present)* |
| `BLIZZ_REGION` | Region for the profile lookups | `us` |
| `KEY_CHANNEL_ID` | Channel the command is allowed in (DM also allowed) | *(already present)* |

## Rules

A finding is one of four kinds:

| Kind | Condition |
|------|-----------|
| `missing_enchant` | An enchantable slot holds an item with no `PERMANENT` enchantment. |
| `empty_socket` | An equipped item has a socket whose `item` key is absent. |
| `low_enchant` | A `PERMANENT` enchantment whose display string parses to Tier 1. |
| `low_gem` | A socketed gem whose item quality is below `EPIC`. |

**Enchantable slots** are a module constant, derived from the evidence above:

```
HEAD, SHOULDER, CHEST, LEGS, FEET, FINGER_1, FINGER_2, MAIN_HAND
```

plus `OFF_HAND` **only when the equipped off-hand is a weapon**
(`item_class.id == 2`), which excludes shields and held-in-off-hand items.

A constant is deliberate over deriving the set from the roster each run: officers
need a rule they can explain, and a self-tuning threshold would silently stop
flagging a slot the whole team neglected. It is one line to edit per expansion,
and the comment records how it was measured.

A slot with no equipped item is never flagged — an empty slot is a different
problem, and not this command's job.

**Known limit, stated rather than hidden:** a Tier-1 *crafted* variant of an
otherwise-epic gem is indistinguishable through this API. Crafted quality lives
in `bonus_list` entries the equipment payload does not resolve, and the gem's
item record reports only the base quality. `low_gem` therefore catches
wrong-rarity gems (a rare gem in an epic slot), not a low-rank craft of the
right gem.

## Architecture

Three layers, matching the shape `tokenwatch.py` / `snipelist.py` already use:
a pure logic module holding everything worth testing, one new client method, and
a thin command in `bot.py`.

### `blizzard.py` (modified)

- `_parse_equipment(data: dict) -> List[EquippedItem]` — pure, beside the other
  `_parse_*` functions.
- `EquippedItem` frozen dataclass: `slot`, `name`, `item_id`, `item_class_id`,
  `enchants: List[Enchant]`, `sockets: List[Optional[int]]` (gem item id per
  socket, `None` when empty).
- `Enchant` frozen dataclass: `slot_type` (`PERMANENT` / `TEMPORARY` /
  `ON_USE_SPELL`), `display_string`, `enchantment_id`.
- `async BlizzardClient.character_equipment(session, realm_slug, character) ->
  Optional[List[EquippedItem]]` — uses the existing `_get` (which already
  handles OAuth, the 401 re-mint, and `?namespace={ns}-{region}&locale=en_US`)
  with the `profile` namespace. Returns `None` on 404 so the caller can report a
  stale mapping instead of crashing.
- `ItemInfo` gains `quality: str = ""`, populated by `_parse_item_info`. The
  default keeps every existing `snipelist` call site working unchanged, and gem
  grading reuses the client's existing `_item_cache`.

### `gearaudit.py` (new, pure — no I/O, no discord imports)

- `ENCHANTABLE_SLOTS`, `WEAPON_ITEM_CLASS_ID = 2`, `GEM_MIN_QUALITY = "EPIC"`.
- `parse_enchant_tier(display_string: str) -> Optional[int]` — regex
  `Quality-\d+-Tier(\d)`; `None` when the enchant carries no quality marker
  (runes and old-style enchants do not).
- `audit_character(name, realm, items, gem_quality) -> CharacterFindings` —
  `gem_quality` is a `Dict[int, str]` of gem item id → quality, injected by the
  caller so the module stays I/O-free.
- `CharacterFindings`: `name`, `realm`, `missing_enchants: List[str]`,
  `low_enchants: List[Tuple[str, int]]`, `empty_sockets: int`,
  `low_gems: List[Tuple[str, str]]`, plus `problem_count` and `is_clean`.
- `gem_ids(items) -> Set[int]` — the ids the caller must look up before auditing.
- `format_report(findings, failures) -> List[str]` — the officer-facing message,
  sorted worst-first, split into chunks under Discord's 2000-character limit.
- `SLOT_LABELS` — `FINGER_1` → `Ring 1`, `MAIN_HAND` → `Weapon`, etc.

### `bot.py` (modified)

`!gearaudit [character]`, gated on `message.author.id in ELEVATED_IDS` (the
module-level constant `!cleanup` uses, not the `self.elevated_ids` copy). Like
`!watch` and `!token`, it works anywhere the author can reach the bot, deletes
its own invocation in a guild channel, and replies by DM.

Flow: `raiderio.load_character_mappings()` → filter to one character if an
argument was given → open one `aiohttp.ClientSession` → fetch equipment with an
`asyncio.Semaphore(5)` → collect gem ids and resolve each through
`blizzard.item_info` (cached) → `audit_character` per character → `format_report`
→ reply with each chunk.

## Report Format

```
**Gear Audit** — 10 of 30 characters need something

**Desdemona** (Mal'Ganis) — missing Head, Shoulder, Legs, Feet
**Britannia** (Mal'Ganis) — missing Shoulder, Feet, Ring 1
**Boongus** (Mal'Ganis) — missing Head, Legs
**Bitterbee** (Dalaran) — 1 empty socket
**Talvan** (Mal'Ganis) — missing Head

20 characters clean.
Could not fetch: Someguy (Mal'Ganis) — check the mapping.

Enchants and gems are free in the guild bank — grab what you need.
```

Sorted by problem count descending, then name. Clean characters collapse to one
count. The guild-bank footer is worded so an officer can paste a line straight
to the player.

## Error Handling

- A per-character fetch failure (404, timeout, malformed payload) is collected
  into `failures` and reported; it never aborts the sweep.
- The command always replies with something, even if every fetch failed.
- A missing or corrupt `character_mappings.json` yields `[]` from the existing
  loader, and the command replies that no characters are mapped.
- Chunked output: never send a message over Discord's limit; split on character
  boundaries at ~1900 characters.
- Follows the codebase convention of narrow excepts around parsing and a broad
  `except Exception` only at the command's outer edge, logged not raised.

## Testing

`tests/test_gearaudit.py` — pure-function coverage, fixtures taken from real
payloads captured 2026-08-30:

- `parse_enchant_tier` — Tier 2 marker, Tier 1 marker, no marker (rune), garbage.
- `audit_character` — clean character; missing enchant on each enchantable slot;
  empty socket detection; Tier-1 enchant; below-epic gem; several problems at
  once.
- Off-hand: a shield off-hand with no enchant is **not** flagged; a weapon
  off-hand with no enchant **is**.
- A slot with no equipped item is not flagged.
- `format_report` — worst-first ordering, clean count, failure list, chunking
  above 2000 characters.

`tests/test_blizzard.py` — `_parse_equipment` against a real payload fragment
(enchant + socket + empty socket), and `_parse_item_info` still parsing name and
`is_recipe` with the new `quality` field defaulted.

## Documentation

Per the repo convention, the same change updates:

- `CLAUDE.md` — file map row for `gearaudit.py`, a "Gear Audit" section under Key
  Workflows, and the command table.
- `README.md` — the command and what it checks.
- `CHANGELOG.md` — a bullet under the existing unreleased `[1.9.0]` section.
  `version.txt` stays at `1.8.0`: the bot writes it after posting the changelog,
  and editing it by hand suppresses that post.
