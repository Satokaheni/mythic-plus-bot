# Test Suite Design — Mythic+ Bot (High-Value Layer)

**Date:** 2026-03-11
**Scope:** Unit tests for `raider.py`, `schedule.py`, and `utils.py`
**Out of scope:** `bot.py`, `views.py`, Discord API integration

---

## Goals

- Validate core business logic (availability rules, team composition, displacement, serialization) with fast, isolated unit tests
- Run automatically on every push and pull request to `main` via GitHub Actions
- All tests must pass; no minimum coverage threshold enforced at this stage

---

## Approach

Option A — shared `conftest.py` with `FakeMember` and factory fixtures. A `SimpleNamespace`-based `FakeMember` duck-types `discord.Member` (provides `id`, `mention`, `display_name`). Factory fixtures `make_raider` and `make_schedule` construct test objects with sensible defaults, keeping individual tests concise.

---

## File Structure

```
tests/
├── conftest.py          # FakeMember, make_raider, make_schedule
├── test_raider.py       # Raider availability, conflict reason, serialization
├── test_schedule.py     # signup, remove, displacement, fill queue, serialization
└── test_utils.py        # save_state/load_state roundtrip, deserialize, edge cases

.github/
└── workflows/
    └── tests.yml        # CI: pytest on push/PR to main

pyproject.toml           # [tool.pytest.ini_options] + dev deps
```

---

## conftest.py

- `FakeMember(id, mention, display_name)` — `SimpleNamespace` that satisfies `Raider.__init__`
- `make_raider(user_id, roles, timezone)` — returns a `Raider` using `FakeMember`; defaults to `user_id=1`, `roles=["tank"]`, `timezone="US/Eastern"`
- `make_schedule(raider, start_time, run_type, level, date_str)` — returns a `Schedule` with the given raider as organizer; defaults to a future datetime, `run_type="one"`, `level="10"`, `date_str` derived from `start_time`

---

## test_raider.py

### `check_availability`
| Test | Description |
|------|-------------|
| `test_available_no_conflicts` | Raider with no current runs is available |
| `test_unavailable_already_signed_up` | Returns False when schedule is in `current_runs` |
| `test_single_blocked_under_1h` | Two single-key runs 30 min apart → blocked |
| `test_single_allowed_at_exactly_1h` | Two single-key runs exactly 3600s apart → allowed |
| `test_single_allowed_over_1h` | Two single-key runs 90 min apart → allowed |
| `test_multiple_blocked_under_2h` | New run is multiple-key, existing within 90 min → blocked |
| `test_multiple_allowed_at_exactly_2h` | Multiple-key run exactly 7200s away → allowed |
| `test_existing_multiple_blocks_new_single` | Existing multiple-key run blocks new single within 2h |

### `get_schedule_conflict_reason`
| Test | Description |
|------|-------------|
| `test_no_conflict_returns_empty` | Returns `""` when no conflict |
| `test_already_signed_up_message` | Returns "already signed up" string |
| `test_single_conflict_message` | Contains time diff and "less than 1 hour" |
| `test_multiple_conflict_message_existing` | Contains "multiple-key" and time diff |
| `test_multiple_conflict_message_new` | New run is multiple-key, correct message |

### `add_run` / `remove_run`
| Test | Description |
|------|-------------|
| `test_add_run_adds_to_current` | Schedule appears in `current_runs` |
| `test_add_run_removes_from_denied` | Schedule removed from `denied_runs` on re-add |
| `test_remove_run_moves_to_denied` | Schedule moves from `current_runs` to `denied_runs` |

### Serialization
| Test | Description |
|------|-------------|
| `test_to_dict_from_dict_roundtrip` | All scalar fields survive roundtrip |
| `test_from_dict_wires_current_runs` | `current_runs` correctly resolved from message IDs |

---

## test_schedule.py

### `raider_signup`
| Test | Description |
|------|-------------|
| `test_signup_fills_tank_slot` | Tank raider goes to `team["tank"]` |
| `test_signup_fills_healer_slot` | Healer raider goes to `team["healer"]` |
| `test_signup_fills_dps_slot` | DPS raider appended to `team["dps"]` |
| `test_signup_3_dps_removes_from_missing` | `"dps"` removed from `missing` when 3rd DPS signs up |
| `test_signup_goes_to_fill_when_full` | 6th signup lands in `team["fill"]` |
| `test_signup_counter_increments` | `signup` count increments on each non-fill signup |
| `test_is_filled_at_5` | `is_filled()` returns True at exactly 5 signups |
| `test_auto_role_selection` | Raider with `["tank", "dps"]` auto-assigns to tank if tank slot open |

### `raider_remove`
| Test | Description |
|------|-------------|
| `test_remove_tank_reopens_slot` | `team["tank"]` becomes None, `"tank"` back in `missing` |
| `test_remove_healer_reopens_slot` | Same for healer |
| `test_remove_dps_reopens_slot` | Raider removed from `team["dps"]`, `"dps"` back in `missing` |
| `test_remove_decrements_signup` | `signup` decrements |
| `test_remove_from_fill_queue` | Fill raider removed cleanly |
| `test_fill_promoted_after_remove` | Fill raider promoted to vacated slot after removal |

### `try_displace_off_roler`
| Test | Description |
|------|-------------|
| `test_displaces_off_roler_tank` | Tank slot occupied by DPS main → displaced, returns Raider |
| `test_displaces_off_roler_healer` | Healer slot occupied by DPS main → displaced |
| `test_displaces_off_roler_dps` | DPS slot occupied by tank main → displaced |
| `test_no_displace_if_occupant_is_main_role` | Tank slot held by tank main → no displacement |
| `test_no_displace_if_not_main_role` | New raider's secondary role → no displacement |
| `test_no_displace_within_8h` | Schedule starting in 4h → no displacement (freezegun) |
| `test_displace_updates_missing` | Displaced slot re-added to `missing` |
| `test_displaced_raider_removed_from_current_runs` | Displaced raider's `current_runs` cleaned up |

### Serialization
| Test | Description |
|------|-------------|
| `test_to_dict_from_dict_roundtrip` | All fields survive roundtrip |
| `test_from_dict_resolves_raiders` | Team slots correctly resolved from user IDs |

---

## test_utils.py

### `save_state` / `load_state`
| Test | Description |
|------|-------------|
| `test_save_load_roundtrip` | Raiders and schedules survive write/read cycle (uses `tmp_path` + monkeypatch) |
| `test_load_empty_when_no_file` | Returns six empty defaults when no file exists |
| `test_load_corrupt_json_returns_defaults` | Corrupt `state.json` → empty defaults, no crash |
| `test_availability_roundtrip` | GREEN/YELLOW/RED lists survive roundtrip |
| `test_dm_map_roundtrip` | `dm_map` int keys survive JSON string-key roundtrip |
| `test_dm_timestamps_roundtrip` | `dm_timestamps` datetime values survive isoformat roundtrip |

### `_deserialize`
| Test | Description |
|------|-------------|
| `test_deserialize_cross_references` | Raider `current_runs` and `denied_runs` correctly wired to Schedule objects |
| `test_deserialize_missing_schedule_id_skipped` | Stale schedule ID in raider data is silently dropped |

---

## Dependencies

```toml
[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-mock>=3.0",
    "freezegun>=1.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

No `pytest-asyncio` required — all targeted logic is synchronous.

---

## CI Workflow (`.github/workflows/tests.yml`)

- **Trigger:** push and pull request to `main`
- **Runner:** `ubuntu-latest`, Python 3.11
- **Steps:** checkout → `pip install -e ".[dev]"` → `pytest tests/ -v`
