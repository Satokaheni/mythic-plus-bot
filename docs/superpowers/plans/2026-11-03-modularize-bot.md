# Modularize the bot into commands.Bot cogs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `bot.py`'s `MyClient` god object into a `commands.Bot` with one cog per feature and a single `BotState` owner, with no user-visible behavior change beyond the spec's three deltas.

**Architecture:** Foundation (Tasks 1–4, serial) extracts config/messages/state, converts `MyClient` to `commands.Bot`, registers eight empty cogs, and teaches the offline harness about `commands.Context`. Then three parallel worktrees (Tasks 5–7) each move one feature group out of `bot.py` into its cogs. Task 8 merges and documents; Task 9 fixes the fresh-install availability bug as 1.10.1.

**Tech Stack:** Python 3.10, discord.py 2.3.1 (`discord.ext.commands`, `discord.ext.tasks`), pytest, ruff 0.15, the offline harness at `.claude/skills/verify-mythic-plus-bot/scripts/harness.py`.

**Spec:** `docs/superpowers/specs/2026-11-02-modularize-bot-design.md` — read it before any task.

## Global Constraints

- Work only in worktrees under `C:\Users\zach\Work\`; the main checkout `C:\Users\zach\Work\mythic-plus-bot` is off limits (another agent is using it).
- **Never run `python bot.py` and never load a `.env`.** The only way to run the bot is the harness. Worktrees have no `.env`; keep it that way.
- Set `PYTHONIOENCODING=utf-8` for every harness command (Windows console + emoji).
- Commit on your task's branch only; never push; never touch `version.txt`.
- Pure modules are not edited: `schedule.py`, `raider.py`, `watchlist.py`, `tokenwatch.py`, `snipelist.py`, `forecast.py`, `gearaudit.py`, `eventlog.py`, `blizzard.py`, `undermine.py`, `raiderio.py`, `loot.py`, `lootcouncil/`. `utils.py` is edited only in Task 9.
- `state.json`, `watches.json`, `snipes.json`, `token_watch.json`, `events.jsonl` formats must not change.
- Command prefix `!`, `help_command=None`, `case_insensitive=False`.
- Function names of the seven loops stay exactly: `hourly_check`, `weekly_avail_reset`, `price_watch_check`, `raiderio_harvest`, `forecast_preview`, `auction_snipe_check`, `token_watch_check` (harness `doctor` and `tick` key on them).
- Cog module names: `cogs/scheduling.py`, `cogs/availability.py`, `cogs/admin.py`, `cogs/forecasting.py`, `cogs/pricewatch.py`, `cogs/wowtoken.py`, `cogs/gear.py`, `cogs/sniper.py`. Cog class names: `Scheduling`, `Availability`, `Admin`, `Forecasting`, `PriceWatch`, `WowToken`, `Gear`, `Sniper`.
- Command bodies move **verbatim**; only these mechanical rewrites are allowed: `self.<client attr>` → `self.bot.<client attr>`; persisted fields → `self.bot.state.<field>`; `message` stays bound via `message = ctx.message` as the first line of a command.
- **The gate** (run at the end of every task, in the task's worktree):
  1. `python -m pytest -q` → all pass (379 at baseline, plus tests added by this plan).
  2. `ruff check . --exclude tests` → `All checks passed!` (baseline: product code clean; the 12 pre-existing errors are all under `tests/` and are not yours).
  3. `PYTHONIOENCODING=utf-8 python .claude/skills/verify-mythic-plus-bot/scripts/harness.py run all` → `help`, `key-request`, `signup-fill-restart`, `snipe`, `banker-token`, `command-gates` PASS; `availability-signup` FAIL with `bot logged error: Error in event on_reaction_add` (the known `KeyError: '🟢'`) — until Task 9, after which it PASSes.
  4. `PYTHONIOENCODING=utf-8 python .claude/skills/verify-mythic-plus-bot/scripts/harness.py doctor` → every line `[ok]` or `[--]`.

## Review Focus

1. **Unauthorized users stay silent.** A non-banker typing `!watch`/`!tokenalert`, a non-elevated user typing `!cleanup`/`!setup`/`!avail`/`!gearaudit`: no reply, no deletion, no error logged. A failing `commands.check` must not surface. Pinned by the `command-gates` journey (Task 1) and `test_on_command_error_*` (Task 4).
2. **Non-command chat is ignored without errors.** `!foo`, `hello there`, a DM question: no reply, no ERROR log (`CommandNotFound` swallowed). This is also the hook point for the later headless-Claude experiment. Pinned by `command-gates`.
3. **Existing production data loads and saves unchanged.** A `state.json` written by today's code must round-trip byte-identically through `BotState`. Pinned by `test_botstate_round_trip_is_byte_identical` (Task 3).
4. **Buttons on messages posted before a restart keep working.** Persistent `ScheduleButtonView`s are re-registered in `setup_hook`; moving state must not break that. Pinned by `signup-fill-restart` (gate).
5. **Channel-scoped commands keep their scope.** `!avail` typed in #keys is deleted silently; `!key` in a DM or #availability does nothing; elevated `!cleanup` in a DM gets the "must be used in the availability or key channel" DM. Pinned by `command-gates`.

---

## File Structure (end state)

| File | Responsibility |
|---|---|
| `bot.py` | Entry: `MyClient(commands.Bot)`, `setup_hook` (state load, shared clients, persistent views, extension loading), facade methods for views, `on_command_error`, `on_error`, `intents`, `client`, `run()` |
| `config.py` | `_configure_logging`, `_require_env`, every env-derived constant, `BOT_VERSION`, `_CST` |
| `messages.py` | `AVAILABILITY_MESSAGE`, `_dm_spot_available`, `_dm_reminder`, `_read_last_version`, `_write_version`, `_read_changelog`, `_VERSION_FILE` |
| `state.py` | `BotState` |
| `cogs/__init__.py` | `EXTENSIONS` |
| `cogs/_checks.py` | `is_banker`, `is_elevated`, `in_channels`, `dm_or_channels` |
| `cogs/*.py` | one cog each (ownership map in the spec) |
| `views.py` | unchanged except `bot_client.<field>` → `bot_client.state.<field>` and `save_state(...)` → `bot_client.state.save()` |
| `tests/test_config.py`, `tests/test_state.py`, `tests/test_checks.py` | new unit tests |
| `tests/test_bot.py` | updated fixture + tests follow moved code |
| `Dockerfile`, `pyproject.toml` | ship/declare new modules |
| `.claude/skills/verify-mythic-plus-bot/scripts/{harness.py,journeys.py}` | Context support, cog-aware `tick`, `command-gates` journey |

Line numbers below refer to `bot.py` at commit `b653be9` (start of this branch); they shift as tasks land, so locate code by the function/branch name given alongside.

---

### Task 1: Pin command gating with a characterization journey

**Files:**
- Modify: `.claude/skills/verify-mythic-plus-bot/scripts/journeys.py` (add `command_gates`, register it)

**Interfaces:**
- Produces: journey `command-gates` (used by every later gate).

- [ ] **Step 1: Create the worktree branch state.** The worktree `C:\Users\zach\Work\mpb-refactor` already exists on branch `refactor/modularize-bot` at `b653be9`. Run `git -C C:/Users/zach/Work/mpb-refactor status --short` → expect clean.

- [ ] **Step 2: Add the journey** — append before the `def json_dump` line in `journeys.py`:

```python
BOT_ACTIONS = ("send", "edit", "delete", "bot_react", "pin", "unpin", "interaction_response", "followup", "rejected")


async def _assert_silent(h, user, text, where, label):
    before = len(h.world.events)
    msg = await h.say(user, text, where)
    acts = [e for e in h.world.events[before:] if e["kind"] in BOT_ACTIONS or e["kind"] == "log_error"]
    h.check(f"silent: {label}", not acts and not msg.deleted, acts)


async def command_gates(h):
    """Who gets silence, what gets deleted, and where gated commands answer."""
    nobody = h.user("Nobody")
    await _assert_silent(h, nobody, "!watch 123", "key", "non-banker !watch")
    await _assert_silent(h, nobody, "!watches", "dm", "non-banker !watches")
    await _assert_silent(h, nobody, "!tokenalert 5", "dm", "non-banker !tokenalert")
    await _assert_silent(h, nobody, "!cleanup", "key", "non-elevated !cleanup")
    await _assert_silent(h, nobody, "!setup", "key", "non-elevated !setup")
    await _assert_silent(h, nobody, "!avail", "avail", "non-elevated !avail in #availability")
    await _assert_silent(h, nobody, "!gearaudit", "dm", "non-elevated !gearaudit")
    await _assert_silent(h, nobody, "!key", "dm", "!key in a DM")
    await _assert_silent(h, nobody, "!key", "avail", "!key in #availability")
    await _assert_silent(h, nobody, "!keys", "key", "!keys from an unregistered user")
    await _assert_silent(h, nobody, "!foo", "key", "unknown command")
    await _assert_silent(h, nobody, "hello there", "key", "plain chat in #keys")
    await _assert_silent(h, nobody, "what runs are up this week?", "dm", "plain chat in a DM")

    avail_in_keys = await h.say(nobody, "!avail", "key")
    h.check("!avail typed in #keys is deleted", avail_in_keys.deleted)

    await h.say(h.coordinator, "!cleanup", "dm")
    reply = await h.wait_for(h.coordinator, "must be used in the availability or key channel")
    h.check("elevated !cleanup outside the channels gets a DM explaining where", bool(reply))

    errors = [e for e in h.world.events if e["kind"] == "log_error"]
    h.check("no errors logged", not errors, errors)
```

and add to the `JOURNEYS` dict:

```python
    "command-gates": (command_gates, "permission/channel gates stay silent; unknown commands and chat are ignored"),
```

- [ ] **Step 3: Run it against today's code.**

Run: `cd C:/Users/zach/Work/mpb-refactor && PYTHONIOENCODING=utf-8 python .claude/skills/verify-mythic-plus-bot/scripts/harness.py run command-gates`
Expected: `[PASS] command-gates  (16/16 checks)` (already verified once against `b653be9`).

- [ ] **Step 4: Run the gate** (Global Constraints). Expected as listed, including `command-gates` PASS.

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/verify-mythic-plus-bot/scripts/journeys.py
git commit -m "test(verify): pin command gating with a command-gates journey"
```

---

### Task 2: Extract `config.py` and `messages.py`

**Files:**
- Create: `config.py`, `messages.py`, `tests/test_config.py`
- Modify: `bot.py` (lines 39–193: logging setup, env constants, version/changelog helpers, message builders), `tests/test_bot.py` (fixture), `pyproject.toml`, `Dockerfile`

**Interfaces:**
- Produces: `config.CLIENT_ID, AVAIL_CHANNEL_ID, KEY_CHANNEL_ID, GUILD_ID, TANK_ID, HEALER_ID, DPS_ID, COORDINATOR_ID, BANKER_ID, BANKER_BUDGET_COPPER, BANKER_BULK_QTY, GEAR_AUDIT_CONCURRENCY, BLIZZ_CLIENT_ID, BLIZZ_CLIENT_SECRET, SNIPE_SWEEP_MINUTES, TOKEN_POLL_MINUTES, MYTHIC_PLUS_ID, ADMINS, ELEVATED_IDS, BOT_VERSION, _CST, _configure_logging(), _require_env(name) -> str`; `messages.AVAILABILITY_MESSAGE(x, y, z) -> str, _dm_spot_available(...), _dm_reminder(...), _read_last_version() -> str, _write_version(v) -> None, _read_changelog(v) -> list[str], _VERSION_FILE`.
- `bot.py` re-exports every one of these names (`from config import *`-style explicit imports) so `bot.CLIENT_ID`, `bot.BOT_VERSION`, `bot.MyClient`, `bot.intents` keep working for the harness and tests.

- [ ] **Step 1: Move the env fixture into `tests/conftest.py`** so every test file can import a fresh copy of an env-reading module. Cut `_REQUIRED_ENV` and the `bot_module` fixture out of `tests/test_bot.py` and add to `tests/conftest.py` (plus imports `importlib, logging, sys, dotenv`):

```python
_REQUIRED_ENV = {
    "CLIENT_KEY": "x", "AVAIL_CHANNEL_ID": "1", "KEY_CHANNEL_ID": "2", "GUILD_ID": "3", "TANK_ROLE_ID": "4",
    "HEALER_ROLE_ID": "5", "DPS_ROLE_ID": "6", "COORDINATOR_ID": "7", "BANKER_ID": "8", "BLIZZ_CLIENT_ID": "x",
    "BLIZZ_CLIENT_SECRET": "x", "MYTHIC_PLUS_ID": "9", "ADMIN_ID": "10",
}
_FRESH = ("bot", "config", "messages", "state")


def _forget_bot_modules():
    for name in list(sys.modules):
        if name in _FRESH or name == "cogs" or name.startswith("cogs."):
            sys.modules.pop(name, None)


@pytest.fixture
def fresh_import(monkeypatch, tmp_path):
    """Import an env-reading module (bot, config, ...) fresh, with fake env; undo its log handlers."""
    for name, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "bot.log"))
    monkeypatch.setenv("BLIZZ_REGION", "us")
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level

    def _import(name: str):
        _forget_bot_modules()
        return importlib.import_module(name)

    yield _import
    _forget_bot_modules()
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers, root.level = handlers, level


@pytest.fixture
def bot_module(fresh_import):
    return fresh_import("bot")
```

`tests/test_bot.py` keeps using the `bot_module` fixture by name; delete its now-unused imports.

Then write the failing test `tests/test_config.py`:

```python
import pytest


def test_elevated_ids_are_coordinator_plus_admins(fresh_import):
    assert fresh_import("config").ELEVATED_IDS == {7, 10}


def test_admin_id_list_skips_blanks(fresh_import, monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "10, ,11,")
    assert fresh_import("config").ADMINS == [10, 11]


def test_missing_required_env_raises(fresh_import, monkeypatch):
    monkeypatch.delenv("BANKER_ID")
    with pytest.raises(RuntimeError, match="BANKER_ID"):
        fresh_import("config")
```

- [ ] **Step 2: Run to see it fail.** `python -m pytest tests/test_config.py -v` → FAIL `ModuleNotFoundError: No module named 'config'`. `python -m pytest tests/test_bot.py -v` → still PASS (fixture moved, not changed).

- [ ] **Step 3: Create `config.py`.** Move, verbatim, from `bot.py`: `_configure_logging` (lines 45–63), the `load_dotenv(".env")` + `_configure_logging()` calls (68–69), `_require_env` (71–75), every constant on lines 78–102, and `BOT_VERSION` (106). Header:

```python
"""Runtime configuration: environment variables, IDs, and tunables. Imported once at startup."""

import logging
import os
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
```

`load_dotenv` must be referenced as the module-level name imported here (the harness and tests patch `dotenv.load_dotenv` before import; `from dotenv import load_dotenv` at import time picks up the patch).

- [ ] **Step 4: Create `messages.py`.** Move verbatim: `_VERSION_FILE`, `_read_last_version`, `_write_version`, `_read_changelog` (lines 108–144), `AVAILABILITY_MESSAGE` (147–160), `_dm_spot_available` (168–177), `_dm_reminder` (180–190). Header imports: `from textwrap import dedent`.

- [ ] **Step 5: Rewire `bot.py`.** Delete the moved blocks; replace them with:

```python
from config import (  # noqa: F401  (re-exported for the harness and tests)
    _CST, ADMINS, AVAIL_CHANNEL_ID, BANKER_BUDGET_COPPER, BANKER_BULK_QTY, BANKER_ID, BLIZZ_CLIENT_ID,
    BLIZZ_CLIENT_SECRET, BOT_VERSION, CLIENT_ID, COORDINATOR_ID, DPS_ID, ELEVATED_IDS, GEAR_AUDIT_CONCURRENCY,
    GUILD_ID, HEALER_ID, KEY_CHANNEL_ID, MYTHIC_PLUS_ID, SNIPE_SWEEP_MINUTES, TANK_ID, TOKEN_POLL_MINUTES,
)
from messages import (  # noqa: F401
    AVAILABILITY_MESSAGE, _dm_reminder, _dm_spot_available, _read_changelog, _read_last_version, _write_version,
)
```

Keep `logger = logging.getLogger("discord")` in `bot.py`. Remove now-unused imports (`RotatingFileHandler`, `load_dotenv`, `dedent` if unused — let `ruff` tell you). `os.environ.setdefault("BLIZZ_REGION", "us")` moves to `config.py` beside the Blizzard constants.

- [ ] **Step 6:** (fixture already moved in Step 1.)

- [ ] **Step 7: Declare and ship the modules.** `pyproject.toml`: add `"config", "messages"` to `[tool.setuptools] py-modules` and to `known-first-party`. `Dockerfile`: add `config.py messages.py` to the `COPY` line.

- [ ] **Step 8: Run tests.** `python -m pytest tests/test_config.py -v` → 3 PASS. Then the full gate.

- [ ] **Step 9: Commit**

```bash
git add config.py messages.py bot.py tests/test_config.py tests/test_bot.py tests/conftest.py pyproject.toml Dockerfile
git commit -m "refactor: extract config.py and messages.py from bot.py"
```

---

### Task 3: `BotState` owns persisted state

**Files:**
- Create: `state.py`, `tests/test_state.py`
- Modify: `bot.py` (class attrs lines 199–206, `setup_hook` load at 1326–1333, all 22 `save_state(` calls, every `self.raiders|schedules|availability|availability_message_id|dm_map|dm_timestamps|role_mentions|reminder_messages`), `views.py` (all `bot_client.<field>` and `save_state(` uses), `tests/test_bot.py`, `pyproject.toml`, `Dockerfile`

**Interfaces:**
- Produces:

```python
class BotState:
    raiders: Dict[int, Raider]; schedules: Dict[int, Schedule]; availability: Dict[str, List[Raider]]
    availability_message_id: Optional[int]; dm_map: Dict[int, Dict[int, int]]
    dm_timestamps: Dict[int, Dict[int, datetime]]
    role_mentions: Dict[str, Any]           # runtime only
    reminder_messages: Dict[int, List[int]] # runtime only
    def __init__(self, raiders=None, schedules=None, availability=None, availability_message_id=None,
                 dm_map=None, dm_timestamps=None) -> None
    @classmethod
    def load(cls) -> "BotState"
    def save(self) -> None
```

- `MyClient.state: BotState` (set in `setup_hook`, first thing). Later tasks use `self.bot.state`.

- [ ] **Step 1: Write the failing tests** `tests/test_state.py`:

```python
from datetime import datetime, timezone

import utils
from state import BotState
from utils import GREEN, RED, YELLOW


def test_fresh_state_matches_load_state_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = BotState.load()
    assert (s.raiders, s.schedules, s.availability, s.availability_message_id, s.dm_map, s.dm_timestamps) == (
        {}, {}, {}, 0, {}, {})
    assert s.role_mentions == {} and s.reminder_messages == {}


def test_botstate_round_trip_is_byte_identical(tmp_path, monkeypatch, make_raider, make_schedule):
    monkeypatch.chdir(tmp_path)
    tank = make_raider(user_id=1, roles=["tank"])
    healer = make_raider(user_id=2, roles=["healer", "dps"], name="Heals")
    sched = make_schedule(raider=tank)
    tank.add_run(sched)
    sched.raider_signup(healer)
    healer.add_run(sched)
    sched.note = "bring flasks"
    utils.save_state({1: tank, 2: healer}, {555: sched}, {GREEN: [tank], YELLOW: [healer], RED: []}, 777,
                     {9: {10: 555}}, {9: {10: datetime(2026, 1, 1, tzinfo=timezone.utc)}})
    before = (tmp_path / "state.json").read_bytes()

    BotState.load().save()

    assert (tmp_path / "state.json").read_bytes() == before


def test_instances_do_not_share_containers():
    a, b = BotState(), BotState()
    a.raiders[1] = "x"
    a.reminder_messages[1] = [2]
    assert b.raiders == {} and b.reminder_messages == {}
```

- [ ] **Step 2: Run** `python -m pytest tests/test_state.py -v` → FAIL `No module named 'state'`.

- [ ] **Step 3: Create `state.py`:**

```python
"""BotState: the bot's persisted scheduling state and its single save path."""

from datetime import datetime
from typing import Any, Dict, List, Optional

import utils


class BotState:
    def __init__(self, raiders=None, schedules=None, availability=None, availability_message_id=None,
                 dm_map=None, dm_timestamps=None) -> None:
        self.raiders: Dict[int, Any] = raiders if raiders is not None else {}
        self.schedules: Dict[int, Any] = schedules if schedules is not None else {}
        self.availability: Dict[str, List[Any]] = availability if availability is not None else {}
        self.availability_message_id: Optional[int] = availability_message_id
        self.dm_map: Dict[int, Dict[int, int]] = dm_map if dm_map is not None else {}
        self.dm_timestamps: Dict[int, Dict[int, datetime]] = dm_timestamps if dm_timestamps is not None else {}
        self.role_mentions: Dict[str, Any] = {}
        self.reminder_messages: Dict[int, List[int]] = {}

    @classmethod
    def load(cls) -> "BotState":
        return cls(*utils.load_state())

    def save(self) -> None:
        utils.save_state(self.raiders, self.schedules, self.availability, self.availability_message_id,
                         self.dm_map, self.dm_timestamps)
```

Call `utils.save_state`/`utils.load_state` through the module (not `from utils import save_state`) so tests can monkeypatch `utils.save_state`.

- [ ] **Step 4: Run** `python -m pytest tests/test_state.py -v` → 3 PASS.

- [ ] **Step 5: Rewire `bot.py`.**
  - Delete the class-level attributes `raiders … role_mentions` (lines 199–206).
  - In `setup_hook`, replace the six-way `load_state()` unpack with `self.state = BotState.load()`.
  - Replace every `save_state(\n self.raiders, …, self.dm_timestamps,\n)` block with `self.state.save()`.
  - Rewrite attribute uses: `self.raiders` → `self.state.raiders`, likewise `schedules`, `availability`, `availability_message_id`, `dm_map`, `dm_timestamps`, `role_mentions`, `reminder_messages`. (`!cleanup` reassigns several of these — keep the reassignments, on `self.state`.)
  - Imports: drop `load_state, save_state` from the `utils` import; add `from state import BotState`.

  Verify nothing was missed: `grep -nE "self\.(raiders|schedules|availability|availability_message_id|dm_map|dm_timestamps|role_mentions|reminder_messages)\b" bot.py` → no output; `grep -n "save_state\|load_state" bot.py` → no output.

- [ ] **Step 6: Rewire `views.py`.** `bot_client.raiders` → `bot_client.state.raiders` and likewise for `schedules`, `availability`, `availability_message_id`, `dm_map`, `dm_timestamps`, `role_mentions` (43 uses, plus `bot.` locals aliased from `self.bot_client` in `ScheduleButtonView` — e.g. `bot.raiders` → `bot.state.raiders`). Every `save_state(<six args>)` → `<client>.state.save()`. Remove `from utils import save_state`. Verify: `grep -nE "(bot_client|\bbot)\.(raiders|schedules|availability|availability_message_id|dm_map|dm_timestamps|role_mentions)\b" views.py` → no output.

- [ ] **Step 7: Update `tests/test_bot.py`.** In `test_run_deleted_during_dms_is_not_resurrected`, replace the six `client.<field> = …` lines and the `save_state` monkeypatch with:

```python
    from state import BotState

    client.state = BotState(raiders={}, schedules={schedule_id: schedule},
                            availability={bot_module.GREEN: [healer], bot_module.YELLOW: [], bot_module.RED: []},
                            availability_message_id=None, dm_map={}, dm_timestamps={})
    client.state.save = lambda: None
```

and the final assertion → `assert schedule_id not in client.state.schedules`.

- [ ] **Step 8: Declare and ship.** `pyproject.toml`: add `"state"` to `py-modules` and `known-first-party`. `Dockerfile`: add `state.py` to `COPY`.

- [ ] **Step 9: Gate.** All four gate items as in Global Constraints.

- [ ] **Step 10: Commit**

```bash
git add state.py tests/test_state.py bot.py views.py tests/test_bot.py pyproject.toml Dockerfile
git commit -m "refactor: BotState owns persisted state; one save() replaces 22 save_state calls"
```

---

### Task 4: `commands.Bot` + empty cogs + harness support (foundation commit)

**Files:**
- Create: `cogs/__init__.py`, `cogs/_checks.py`, the eight cog modules (empty classes), `tests/test_checks.py`
- Modify: `bot.py` (class base, `__init__`, `setup_hook`, `on_message` head, new `on_command_error`, bottom `client = MyClient(...)`), `.claude/skills/verify-mythic-plus-bot/scripts/harness.py`, `pyproject.toml`, `Dockerfile`

**Interfaces:**
- Produces:
  - `cogs.EXTENSIONS: list[str]` (the eight module paths, order: admin, availability, scheduling, forecasting, pricewatch, wowtoken, sniper, gear).
  - `cogs._checks.is_banker()`, `is_elevated()`, `in_channels(*ids: int)`, `dm_or_channels(*ids: int)` — each returns a `commands.check` decorator; predicates read `config.<ID>` at call time.
  - Each cog module: `class <Name>(commands.Cog)` with `__init__(self, bot)` storing `self.bot`, and `async def setup(bot): await bot.add_cog(<Name>(bot))`.
  - `MyClient(intents=...)` constructs with `command_prefix="!"`, `help_command=None`, `case_insensitive=False`.
  - `MyClient.on_message`: `if message.author.id == self.user.id: return` then `await self.process_commands(message)` then the existing legacy `if` chain. Tasks 5–7 delete chain branches as they move them.
  - Facade on `MyClient` (stable names for views and other cogs, real methods today): `notify_schedule`, `message_user`, `_do_key_request_flow`, `_notify_promoted`, `_notify_displaced`, `new_availability_signup_fill_schedule`.
  - Harness: `commands.Context` works with fake messages; `Harness.tick(name)` finds loops on the client or any cog.

- [ ] **Step 1: Write the failing tests** `tests/test_checks.py`:

```python
import asyncio
from types import SimpleNamespace

import pytest
from discord.ext import commands


def _ctx(author_id, channel_id=None, guild=True):
    return SimpleNamespace(author=SimpleNamespace(id=author_id), channel=SimpleNamespace(id=channel_id),
                           guild=object() if guild else None)


def _predicate(decorator):
    @decorator
    async def cmd(ctx):
        pass
    return cmd.__commands_checks__[0]


def test_checks_mirror_todays_gates(bot_module):
    from cogs import _checks

    assert _predicate(_checks.is_banker())(_ctx(8))
    assert not _predicate(_checks.is_banker())(_ctx(7))
    assert _predicate(_checks.is_elevated())(_ctx(7)) and _predicate(_checks.is_elevated())(_ctx(10))
    assert not _predicate(_checks.is_elevated())(_ctx(8))
    assert _predicate(_checks.in_channels(2))(_ctx(1, 2))
    assert not _predicate(_checks.in_channels(2))(_ctx(1, 2, guild=False))
    assert _predicate(_checks.dm_or_channels(2))(_ctx(1, 99, guild=False))
    assert not _predicate(_checks.dm_or_channels(2))(_ctx(1, 99))


@pytest.mark.parametrize("error", [commands.CommandNotFound("x"), commands.CheckFailure("x")])
def test_on_command_error_swallows_expected(bot_module, error, caplog):
    client = bot_module.MyClient.__new__(bot_module.MyClient)
    asyncio.run(client.on_command_error(SimpleNamespace(command=None, message=None), error))
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_on_command_error_logs_the_rest(bot_module, caplog):
    client = bot_module.MyClient.__new__(bot_module.MyClient)
    err = commands.CommandInvokeError(ValueError("boom"))
    asyncio.run(client.on_command_error(SimpleNamespace(command=SimpleNamespace(qualified_name="x"),
                                                        message=None), err))
    assert any(r.levelname == "ERROR" for r in caplog.records)
```

- [ ] **Step 2: Run** `python -m pytest tests/test_checks.py -v` → FAIL `No module named 'cogs'`.

- [ ] **Step 3: Create `cogs/_checks.py`:**

```python
"""Command gates mirroring the author/channel conditions bot.py used before commands.Bot."""

from discord.ext import commands

import config


def is_banker():
    return commands.check(lambda ctx: ctx.author.id == config.BANKER_ID)


def is_elevated():
    return commands.check(lambda ctx: ctx.author.id in config.ELEVATED_IDS)


def in_channels(*channel_ids: int):
    return commands.check(lambda ctx: ctx.guild is not None and ctx.channel.id in channel_ids)


def dm_or_channels(*channel_ids: int):
    return commands.check(lambda ctx: ctx.guild is None or ctx.channel.id in channel_ids)
```

- [ ] **Step 4: Create `cogs/__init__.py`:**

```python
EXTENSIONS = [
    "cogs.admin",
    "cogs.availability",
    "cogs.scheduling",
    "cogs.forecasting",
    "cogs.pricewatch",
    "cogs.wowtoken",
    "cogs.sniper",
    "cogs.gear",
]
```

and the eight cog modules, each exactly this shape (shown for `cogs/scheduling.py`; the others differ only in docstring and class name `Availability`, `Admin`, `Forecasting`, `PriceWatch`, `WowToken`, `Sniper`, `Gear`):

```python
"""Scheduling: key requests, signups, spot offers, fill/notify, hourly checks."""

from discord.ext import commands


class Scheduling(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot


async def setup(bot) -> None:
    await bot.add_cog(Scheduling(bot))
```

- [ ] **Step 5: Convert `MyClient`.** In `bot.py`:

```python
from discord.ext import commands, tasks

import cogs


class MyClient(commands.Bot):
    """Discord bot for Mythic+ scheduling. Features live in cogs/; this class owns startup and shared state."""

    def __init__(self, *, intents: discord.Intents) -> None:
        super().__init__(command_prefix="!", help_command=None, case_insensitive=False, intents=intents)
```

In `setup_hook`, after the persistent views are registered and before the loop starts, add:

```python
        for extension in cogs.EXTENSIONS:
            await self.load_extension(extension)
```

At the top of `on_message`, after the self-author guard: `await self.process_commands(message)`.

Add beside `on_error`:

```python
    async def on_command_error(self, ctx, error) -> None:
        """Unknown commands and failed gates stay silent, as before; anything else is logged."""
        if isinstance(error, (commands.CommandNotFound, commands.CheckFailure)):
            return
        logger.exception("Command %s failed", getattr(ctx.command, "qualified_name", None), exc_info=error)
```

`client = MyClient(intents=intents)` at the bottom stays as written.

- [ ] **Step 6: Run** `python -m pytest tests/test_checks.py -v` → 4 PASS.

- [ ] **Step 7: Teach the harness `commands.Context`.** In `harness.py`:
  - In `FakeMessage`, add:

```python
    @property
    def _state(self):
        return self._world.state()
```

  - In `_import_bot`, after the `discord.Client.connect = _no_login` line, add:

```python
    from discord.ext import commands as _commands

    async def _ctx_send(self, content=None, **kwargs):
        return await self.channel.send(content, **kwargs)

    async def _ctx_reply(self, content=None, **kwargs):
        return await self.message.reply(content, **kwargs)

    _commands.Context.send = _ctx_send  # Discord delivers ctx.send to the invoking channel
    _commands.Context.reply = _ctx_reply
```

  - Replace `Harness.tick`'s body with:

```python
        if loop_name not in LOOP_NAMES:
            raise HarnessError(f"unknown loop {loop_name!r}; known: {LOOP_NAMES}")
        owners = [self.client, *self.client.cogs.values()]
        loop = next((getattr(o, loop_name) for o in owners if hasattr(type(o), loop_name)), None)
        if loop is None:
            raise HarnessError(f"no client or cog defines {loop_name}")
        self.world.record("tick", loop=loop_name)
        await loop()
        await self.settle()
```

  - In `doctor`, add a line after the loops check:

```python
        line(cogs_ok, f"cogs loaded: {cog_names}")
```

    with `boot_probe` returning `sorted(h.client.cogs)` as a fourth value and `cogs_ok = len(cog_names) == 8`.

- [ ] **Step 8: Declare and ship.** `pyproject.toml`: `packages = ["lootcouncil", "cogs"]`; add `"cogs"` to `known-first-party`. `Dockerfile`: add a line `COPY cogs/ ./cogs/` after the existing `COPY`.

- [ ] **Step 9: Gate.** Also run `harness.py doctor` and confirm `[ok] cogs loaded: ['Admin', 'Availability', 'Forecasting', 'Gear', 'PriceWatch', 'Scheduling', 'Sniper', 'WowToken']`.

- [ ] **Step 10: Commit** (this is the **foundation commit** Tasks 5–7 branch from)

```bash
git add bot.py cogs tests/test_checks.py .claude/skills/verify-mythic-plus-bot/scripts/harness.py pyproject.toml Dockerfile
git commit -m "refactor: MyClient is a commands.Bot that loads eight feature cogs"
git rev-parse HEAD   # record as FOUNDATION
```

---

### Parallel phase setup (lead, before Tasks 5–7)

```bash
cd C:/Users/zach/Work/mpb-refactor
git worktree add ../mpb-cog-scheduling -b refactor/cog-scheduling FOUNDATION
git worktree add ../mpb-cog-availability-admin -b refactor/cog-availability-admin FOUNDATION
git worktree add ../mpb-cog-economy -b refactor/cog-economy FOUNDATION
```

Each of Tasks 5–7 runs in its own worktree, touches only its cogs, `bot.py` regions it owns, and its tests. Shared rules for 5–7:

- **Move recipe for a command branch** (today in `MyClient.on_message`): create a cog method, put the gate in decorators, bind `message = ctx.message`, paste the branch body verbatim, then apply the mechanical rewrites. Delete the branch from `on_message`. Example, `!token` (lines 1648–1663):

```python
    @commands.command(name="token")
    @is_banker()
    async def cmd_token(self, ctx: commands.Context) -> None:
        message = ctx.message
        if message.guild is not None:
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
        price = None
        try:
            async with aiohttp.ClientSession() as session:
                result = await self.bot.blizzard.token_price(session)
            if result is not None:
                price = result[0]
        except Exception as exc:  # noqa: BLE001 - report state even if the API is down
            logger.warning("!token: could not fetch token price: %s", exc)
        await message.author.send(tokenwatch.format_status(self.token_watch, price))
```

- **Move recipe for a loop:** move the `@tasks.loop(...)` method (and its `before_loop` if any) into the cog unchanged except rewrites (`self.is_ready()` → `self.bot.is_ready()`, `self.wait_until_ready()` → `self.bot.wait_until_ready()`); add `async def cog_load(self): self.<loop>.start()` and `async def cog_unload(self): self.<loop>.cancel()`; delete its `if not self.<loop>.is_running(): self.<loop>.start()` lines from `MyClient.setup_hook`.
- **Move recipe for a listener:** `@commands.Cog.listener("on_reaction_add")` (or `"on_ready"`) on a cog method named for its job; first line repeats `if user.id == self.bot.user.id: return`; delete the moved part from `MyClient`'s method.
- **Facade rule:** when you move a facade method (`notify_schedule`, `message_user`, `_do_key_request_flow`, `_notify_promoted`, `_notify_displaced`, `new_availability_signup_fill_schedule`) into a cog, replace it on `MyClient` with a one-line delegator, e.g.:

```python
    async def notify_schedule(self, schedule):
        await self.get_cog("Scheduling").notify_schedule(schedule)
```

  Code outside your cog always calls the facade (`self.bot.notify_schedule(...)`), never another cog directly.
- **Imports in cogs:** import IDs from `config` (`import config` and use `config.KEY_CHANNEL_ID`), logger as `logger = logging.getLogger("discord")`.
- Gate at the end, then commit on your branch. Don't edit other groups' regions even if they look wrong; note them in your report.

---

### Task 5: Scheduling cog (worktree `mpb-cog-scheduling`)

**Files:**
- Modify: `cogs/scheduling.py`, `bot.py`, `tests/test_bot.py`

**Interfaces:**
- Consumes: `self.bot.state`, `config.*`, facade names, `views.*` (unchanged).
- Produces on `Scheduling`: `message_user, repost_schedule, retry_unanswered_dms, check_schedule_conflicts, new_availability_signup_fill_schedule, fill_remaining_spots, _notify_schedule_changed, _notify_displaced, _notify_promoted, notify_schedule, _ask_organizer_role, _do_key_request_flow, hourly_check` (same signatures as today minus nothing); commands `!key`, `!keys`, `!modify`; listener `on_spot_offer_reaction`.

- [ ] **Step 1: Update the two `tests/test_bot.py` tests to target the cog** (they will fail until the move):

```python
def _scheduling_cog(bot_module, **bot_attrs):
    from cogs.scheduling import Scheduling

    cog = Scheduling.__new__(Scheduling)
    cog.bot = SimpleNamespace(**bot_attrs)
    return cog
```

In `test_run_deleted_during_dms_is_not_resurrected`: build `state` as in Task 3, then `cog = _scheduling_cog(bot_module, state=state, get_channel=lambda _id: object(), get_user=lambda _id: SimpleNamespace(create_dm=create_dm))`, patch `asyncio.sleep` on `cogs.scheduling`'s module (`monkeypatch.setattr("cogs.scheduling.asyncio.sleep", no_sleep)`), run `asyncio.run(cog.fill_remaining_spots(schedule_id))`, assert `schedule_id not in state.schedules`.
In `test_notify_promoted_dms_new_slot`: `cog = _scheduling_cog(bot_module, get_user=lambda uid: SimpleNamespace(send=send) if uid == backup.user_id else None)`; `asyncio.run(cog._notify_promoted(backup, sched))`.

Run `python -m pytest tests/test_bot.py -v` → both FAIL (`AttributeError: 'Scheduling' object has no attribute ...`).

- [ ] **Step 2: Move methods** `message_user` (212), `repost_schedule` (240), `retry_unanswered_dms` (282), `check_schedule_conflicts` (374), `new_availability_signup_fill_schedule` (437), `fill_remaining_spots` (521), `_notify_schedule_changed` (621), `_notify_displaced` (633), `_notify_promoted` (649), `notify_schedule` (670), `_ask_organizer_role` (1106), `_do_key_request_flow` (1120) into `Scheduling`, applying rewrites (`self.get_user` → `self.bot.get_user`, `self.get_channel` → `self.bot.get_channel`, `self.wait_for` → `self.bot.wait_for`, `self.loop.create_task` → `self.bot.loop.create_task`, `self.state` → `self.bot.state`, `self.user` → `self.bot.user`). Replace the six facade names on `MyClient` with delegators (Facade rule).

- [ ] **Step 3: Move `hourly_check`** (735–856) per the loop recipe.

- [ ] **Step 4: Move commands.**
  - `!keys` (1816–1826): `@commands.command(name="keys")`, no gate (body checks registration).
  - `!modify` (1828–1937): `@commands.command(name="modify")`, no decorator gate — keep the body's own `is_dm`/`is_allowed_channel` early return verbatim.
  - `!key` (2023–2029): `@commands.command(name="key")` + `@in_channels(config.KEY_CHANNEL_ID)`; body = the delete + `await self._do_key_request_flow(message.author)`.
  Delete those branches from `on_message`. **Leave** the `elif message.content in ["!key", "!avail"] and message.channel.id == KEY_CHANNEL_ID` deletion branch for Task 6 (it belongs to `!avail`); after your change, `!key` in #keys is handled by your command, so that branch only ever sees `!avail`.

- [ ] **Step 5: Move the spot-offer reaction branch** (the `elif reaction.message.channel.id in self.dm_map and user.id in self.raiders:` block, ~2122–2245, through its trailing `self.state.save()`), into `@commands.Cog.listener("on_reaction_add") async def on_spot_offer_reaction(self, reaction, user)`, guarded by `if user.id == self.bot.user.id: return` and `if reaction.message.channel.id not in self.bot.state.dm_map or user.id not in self.bot.state.raiders: return`. Delete it from `MyClient.on_reaction_add` (leave the availability branch for Task 6).

- [ ] **Step 6: Run** `python -m pytest tests/test_bot.py -v` → PASS. Then the gate. `signup-fill-restart`, `key-request`, and `command-gates` exercise this cog end to end.

- [ ] **Step 7: Commit**

```bash
git add cogs/scheduling.py bot.py tests/test_bot.py
git commit -m "refactor: move scheduling flows, !key/!keys/!modify, spot offers, hourly_check into Scheduling cog"
```

---

### Task 6: Availability, Admin, Forecasting cogs (worktree `mpb-cog-availability-admin`)

**Files:**
- Modify: `cogs/availability.py`, `cogs/admin.py`, `cogs/forecasting.py`, `bot.py`

**Interfaces:**
- Consumes: `self.bot.state`, facade `self.bot.new_availability_signup_fill_schedule`, `self.bot.message_user`, `config.*`, `messages.*`.
- Produces: `Availability._post_avail_message() -> None`, `Availability._log_avail_reaction(raider, emoji) -> None`, `Availability.weekly_avail_reset`, listener `on_availability_reaction`, command `!avail`; `Admin` commands `!cleanup`, `!setup`, `!help` (alias `tools`), listener `on_ready_setup`, method `get_message_history()`; `Forecasting.raiderio_harvest` (+ `before_loop`), `Forecasting.forecast_preview`, attribute `Forecasting.char_mappings`.

- [ ] **Step 1: Availability.** Move `_post_avail_message` (711–733), `_log_avail_reaction` (2037–2050), `weekly_avail_reset` (1038–1071, loop recipe). Move the availability reaction branch (the `if reaction.message.channel.id == AVAIL_CHANNEL_ID and reaction.message.id == self.availability_message_id and reaction.emoji in [GREEN, YELLOW, RED]:` block, ~2062–2119) into `@commands.Cog.listener("on_reaction_add") async def on_availability_reaction(self, reaction, user)` with the self-author guard and the same condition as an early return. Cross-feature calls use the facade (`await self.bot.new_availability_signup_fill_schedule(...)`). Do **not** fix the `KeyError` (Task 9).

  `!avail` replaces both `on_message` branches that handle it (1989–2000 and the `["!key", "!avail"]` deletion at 2031–2035):

```python
    @commands.command(name="avail")
    async def cmd_avail(self, ctx: commands.Context) -> None:
        message = ctx.message
        if message.guild is None:
            return
        if message.channel.id == config.KEY_CHANNEL_ID:
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                logger.warning(f"Could not delete command from {message.author} (message not found or forbidden)")
            return
        if message.channel.id == config.AVAIL_CHANNEL_ID and message.author.id in config.ELEVATED_IDS:
            await self._post_avail_message()
            try:
                await message.delete()
            except discord.Forbidden:
                logger.warning(f"Could not delete !avail commands from {message.author}")
```

  `MyClient.on_reaction_add` should be empty after Tasks 5 and 6 — delete it in Task 8 if both branches merged; in this task, delete only your branch.

- [ ] **Step 2: Admin.**
  - `!cleanup` (1939–1987): `@commands.command(name="cleanup")` + `@is_elevated()`; body verbatim (it keeps its own channel check and the "must be used in the availability or key channel" DM).
  - `!setup` (2002–2021): `@commands.command(name="setup")` + `@is_elevated()` + `@in_channels(config.KEY_CHANNEL_ID)`; body verbatim (`KeyRequestButtonView(self.bot)`).
  - `!help` (1746–1814): `@commands.command(name="help", aliases=["tools"])`; body verbatim.
  - `get_message_history` (259–280) → `Admin.get_message_history`.
  - `on_ready` body (1376–1414) → `@commands.Cog.listener("on_ready") async def on_ready_setup(self)` with rewrites (`self.user` → `self.bot.user`, `self.get_guild` → `self.bot.get_guild`, `self.get_channel` → `self.bot.get_channel`, `self.get_message_history()` → `self.get_message_history()`, `self.role_mentions` → `self.bot.state.role_mentions`, `KeyRequestButtonView(self)` → `KeyRequestButtonView(self.bot)`). Keep `logger.info("Logged in as %s", self.user)` on `MyClient.on_ready` (only that line), delete the rest of `MyClient.on_ready`.

- [ ] **Step 3: Forecasting.** Move `raiderio_harvest` + `before_raiderio_harvest` (1015–1036) and `forecast_preview` (1073–1104) per the loop recipe. Move `self._char_mappings = raiderio.load_character_mappings()` from `setup_hook` into `Forecasting.__init__` as `self.char_mappings`, and rewrite uses.

- [ ] **Step 4: Gate.** `command-gates` covers `!avail`/`!cleanup`/`!setup` gating; `help` covers `!help`/`!tools`; doctor's pinned-button check covers `on_ready_setup`; `availability-signup` must still fail **only** on the known `KeyError`.

- [ ] **Step 5: Commit**

```bash
git add cogs/availability.py cogs/admin.py cogs/forecasting.py bot.py
git commit -m "refactor: move availability, admin commands/on_ready, and forecasting loops into cogs"
```

---

### Task 7: PriceWatch, WowToken, Sniper, Gear cogs (worktree `mpb-cog-economy`)

**Files:**
- Modify: `cogs/pricewatch.py`, `cogs/wowtoken.py`, `cogs/sniper.py`, `cogs/gear.py`, `bot.py`, `tests/test_watchlist.py` / `tests/test_tokenwatch.py` / `tests/test_snipelist.py` only if they reference `bot` (they should not — verify with `grep -ln "import bot\|bot_module" tests/`)

**Interfaces:**
- Consumes: `self.bot.blizzard` (stays on `MyClient`, created in `setup_hook`), `config.BANKER_ID, BANKER_BULK_QTY, BANKER_BUDGET_COPPER, GEAR_AUDIT_CONCURRENCY, SNIPE_SWEEP_MINUTES, TOKEN_POLL_MINUTES, ELEVATED_IDS`.
- Produces: `PriceWatch.watchlist`, loop `price_watch_check`, commands `!watch`, `!unwatch`, `!watches`; `WowToken.token_watch`, loop `token_watch_check`, commands `!token`, `!tokenalert`; `Sniper.snipelist` + realm caches, loop `auction_snipe_check`, `_send_snipe_dm`, commands `!snipe`, `!snipepet`, `!unsnipe`, `!snipes`; `Gear._run_gear_audit`, command `!gearaudit`.

- [ ] **Step 1: PriceWatch.** `self.watchlist = Watchlist.load()` moves from `setup_hook` to `PriceWatch.__init__`. Loop `price_watch_check` (857–895). Commands (all `@is_banker()`): `!watch` (1476–1531, `name="watch"`), `!unwatch` (1533–1555), `!watches` (1557–1586). The `!watch` body keeps `args = message.content.split()[1:]`.
- [ ] **Step 2: WowToken.** `self.token_watch = tokenwatch.TokenWatch.load()` → `WowToken.__init__`. Loop `token_watch_check` (897–940). Commands `!tokenalert` (1614–1646) and `!token` (1648–1663, worked example above), both `@is_banker()`.
- [ ] **Step 3: Sniper.** `self.snipelist = snipelist_mod.Snipelist.load()` and the four `_snipe_*` cache attributes move from `setup_hook` into `Sniper.__init__` (same names, on the cog). Loop `auction_snipe_check` (942–1002), `_send_snipe_dm` (1004–1013). Commands (no gate): `!snipe` (1665–1691), `!snipepet` (1693–1715), `!unsnipe` (1717–1735), `!snipes` (1737–1744). Keep each body's `message.content.split()[1:]` parsing.
- [ ] **Step 4: Gear.** `_run_gear_audit` (1416–1469) and `!gearaudit` (1588–1612, `@is_elevated()`); keep `wanted = message.content[len("!gearaudit"):].strip()`.
- [ ] **Step 5: Gate.** `snipe`, `banker-token`, `command-gates` exercise these cogs.
- [ ] **Step 6: Commit**

```bash
git add cogs/pricewatch.py cogs/wowtoken.py cogs/sniper.py cogs/gear.py bot.py
git commit -m "refactor: move price watch, token alert, sniper, and gear audit into cogs"
```

---

### Task 8: Integrate, finish `bot.py`, document

**Files:**
- Modify: `bot.py`, `CLAUDE.md`, `README.md`, `.claude/skills/verify-mythic-plus-bot/SKILL.md`

- [ ] **Step 1: Merge** in `C:\Users\zach\Work\mpb-refactor`:

```bash
git merge --no-ff refactor/cog-scheduling -m "merge: Scheduling cog"
git merge --no-ff refactor/cog-availability-admin -m "merge: Availability/Admin/Forecasting cogs"
git merge --no-ff refactor/cog-economy -m "merge: economy cogs"
```

Expected conflicts: `bot.py` `setup_hook` loop-start lines and adjacent `on_message`/`on_reaction_add` deletions. Resolve by keeping every branch's deletion.

- [ ] **Step 2: Finish `MyClient`.** `on_message`'s legacy chain is now empty → delete `MyClient.on_message` entirely (commands.Bot's default calls `process_commands` and ignores bots — spec delta 2). Delete the empty `on_reaction_add`. `MyClient` keeps: `__init__`, `setup_hook` (state load, `self.blizzard`, persistent views, extension loading), `on_ready` (one log line), facade delegators, `on_command_error`, `on_error`. Remove unused imports (`ruff check . --exclude tests`). Confirm `wc -l bot.py` ≲ 150.

- [ ] **Step 3: Gate** + `grep -n "save_state" bot.py views.py cogs/*.py` → no output.

- [ ] **Step 4: Docs** (owner's standing rule: update CLAUDE.md and README.md after code changes). CLAUDE.md: File Map gains `config.py`, `messages.py`, `state.py`, `cogs/*` rows and the verify skill; Core Data Model's `MyClient` section becomes "`MyClient(commands.Bot)` + `BotState` + cogs", with the ownership table from the spec; every Key Workflows reference to "`bot.py`" for a flow points at its cog; Notable Conventions adds "commands are `commands.Bot` prefix commands in cogs; gates in `cogs/_checks.py`; unknown commands and failed gates are silent via `on_command_error`" and the three deltas. README: architecture/layout section. SKILL.md: replace "`bot.py` MyClient" wording with "`bot.py` MyClient and its cogs"; describe `h.tick` finding loops on cogs.

- [ ] **Step 5: Commit**

```bash
git add -A ':!.verify'
git commit -m "refactor: finish MyClient as entry point; document cog layout"
```

- [ ] **Step 6: Whole-branch review** (fresh reviewer): diff `b653be9..HEAD`, spec in hand; check every Review Focus line against the code, not just the tests.

---

### Task 9: Fix the fresh-install availability `KeyError`; ship 1.10.1

**Files:**
- Modify: `utils.py` (`load_state` fresh/corrupt return), `bot.py` (`BOT_VERSION` in `config.py`), `CHANGELOG.md`, `tests/test_state.py`, `tests/test_utils.py`, CLAUDE.md version line

**Interfaces:**
- Changes: `utils.load_state()` returns `{GREEN: [], YELLOW: [], RED: []}` (not `{}`) for availability when `state.json` is missing or unreadable. Persisted format unchanged.

- [ ] **Step 1: Failing tests.** In `tests/test_state.py`, change `test_fresh_state_matches_load_state_defaults`'s expected availability from `{}` to `{GREEN: [], YELLOW: [], RED: []}`. In `tests/test_utils.py` add:

```python
def test_load_state_corrupt_file_has_availability_pools(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "state.json").write_text("{not json", encoding="utf-8")
    _, _, availability, _, _, _ = utils.load_state()
    assert availability == {utils.GREEN: [], utils.YELLOW: [], utils.RED: []}
```

(import `utils` at the top of the test file if it isn't already). Run `python -m pytest tests/test_state.py tests/test_utils.py -v` → the two FAIL. Also run `harness.py run availability-signup` → FAIL (`KeyError: '🟢'`).

- [ ] **Step 2: Fix.** In `utils.load_state`, both `return {}, {}, {}, 0, {}, {}` lines become `return {}, {}, {GREEN: [], YELLOW: [], RED: []}, 0, {}, {}`.

- [ ] **Step 3: Verify.** The two tests PASS; `harness.py run availability-signup` → `[PASS]`; full gate with **all seven journeys passing**.

- [ ] **Step 4: Version + changelog.** `config.BOT_VERSION = "1.10.1"`. `CHANGELOG.md` new top section:

```markdown
## [1.10.1] - 2026-11-03

### Bug Fixes
- Availability reactions no longer fail after a fresh install or a damaged state file — the 🟢/🟡/🔴 pools now exist from the start.

### Improvements
- Internal restructure: each feature now lives in its own module. No change to commands or behavior.
```

CLAUDE.md `**Version:**` → 1.10.1. Do not touch `version.txt`.

- [ ] **Step 5: Commit**

```bash
git add utils.py config.py CHANGELOG.md CLAUDE.md tests/test_state.py tests/test_utils.py
git commit -m "fix: create availability pools on fresh/corrupt state; release 1.10.1"
```
