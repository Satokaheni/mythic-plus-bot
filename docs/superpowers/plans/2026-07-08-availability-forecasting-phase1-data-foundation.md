# Availability Forecasting — Phase 1a (Data Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Durably log the four in-guild availability/attendance signals to an append-only `events.jsonl`, and stop discarding run history — building the dataset a future forecaster (Phase 2) will consume.

**Architecture:** A new leaf module `eventlog.py` owns pure slot/week helpers plus a best-effort append/read for `events.jsonl`. Logging calls are added only in the bot's flow layer (`on_reaction_add`, `hourly_check`) — never in the domain model — so nothing couples `Raider`/`Schedule` to IO or pollutes the test suite.

**Tech Stack:** Python 3.9+, discord.py v2+, pytest, `zoneinfo`. JSONL append-only persistence (no rewrite).

## Global Constraints

- Python 3.9+ compatible (`Optional`/`List`/`tuple` from typing where annotated; `zoneinfo.ZoneInfo`).
- All datetimes timezone-aware UTC internally; `ts_utc` serialized via `.astimezone(timezone.utc).isoformat()`.
- Event log path: `events.jsonl` (module constant `EVENTS_PATH`); **gitignored** like `state.json`/`watches.json`.
- Logging is **best-effort**: `log_event` catches `(OSError, ValueError)`, logs a warning, and NEVER raises — a logging failure must not break any bot flow.
- Logger convention: `logging.getLogger("discord")`.
- Local slot: `local_slot(ts_utc, tz)` returns `(weekday 0=Mon…6=Sun, block = local_hour // 2, 0–11)`.
- Week anchor: availability resets **Tuesday noon CST** (`ZoneInfo("America/Chicago")`); `week_of` returns the ISO date of the most recent Tuesday-noon-CST anchor at/before `ts_utc`.
- Four event types ONLY: `avail_reaction`, `offer_accepted`, `offer_declined`, `run_completed`. Do NOT add `run_created` or `run_joined` (dropped by the spec).
- Record shape (flat JSON, one per line): `type`, `ts_utc`, `user_id` (nullable), `source` (`"discord"`), `run_id` (nullable), `local_weekday`/`local_block`/`tz` (null when no tz), plus type-specific fields.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `eventlog.py` (create) | `EVENTS_PATH`, pure `local_slot`/`week_of`, best-effort `log_event`, `read_events`. |
| `tests/test_eventlog.py` (create) | Pure-helper tests + append/read round-trip, malformed/missing handling. |
| `.gitignore` (modify) | Ignore `events.jsonl`. |
| `bot.py` (modify) | `import eventlog`; log calls in `on_reaction_add` (avail branch + dm_map ✅/❌ branch) and `hourly_check` (run_completed before deletion). |
| `CLAUDE.md`, `README.md`, `CHANGELOG.md` (modify) | Docs. |

---

## Task 1: eventlog pure helpers (local_slot, week_of)

**Files:**
- Create: `eventlog.py`
- Test: `tests/test_eventlog.py`

**Interfaces:**
- Produces:
  - `local_slot(ts_utc: datetime, tz: ZoneInfo) -> tuple` → `(weekday, block)`
  - `week_of(ts_utc: datetime) -> str` (ISO date of Tuesday-noon-CST anchor)
  - `EVENTS_PATH = "events.jsonl"`

- [ ] **Step 1: Write the failing test**

Create `tests/test_eventlog.py`:

```python
"""Tests for the availability/attendance event log."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from eventlog import local_slot, week_of


def test_local_slot_basic():
    # 2026-07-08 20:30 UTC; in America/Chicago (CDT, UTC-5) that's 15:30 Wed.
    ts = datetime(2026, 7, 8, 20, 30, tzinfo=timezone.utc)
    wd, block = local_slot(ts, ZoneInfo("America/Chicago"))
    assert wd == 2          # Wednesday
    assert block == 7       # 15 // 2


def test_local_slot_crosses_day_and_block_boundary():
    # 2026-07-09 02:30 UTC; in America/Chicago that's 21:30 on Wed 2026-07-08.
    ts = datetime(2026, 7, 9, 2, 30, tzinfo=timezone.utc)
    wd, block = local_slot(ts, ZoneInfo("America/Chicago"))
    assert wd == 2          # still Wednesday locally
    assert block == 10      # 21 // 2


def test_week_of_wednesday_maps_to_that_tuesday():
    # Wed 2026-07-08 -> week anchored Tue 2026-07-07
    ts = datetime(2026, 7, 8, 18, 0, tzinfo=timezone.utc)
    assert week_of(ts) == "2026-07-07"


def test_week_of_tuesday_before_noon_is_previous_week():
    # Tue 2026-07-07 15:00 UTC == 10:00 CDT (before noon) -> previous Tue 2026-06-30
    ts = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)
    assert week_of(ts) == "2026-06-30"


def test_week_of_tuesday_after_noon_is_this_week():
    # Tue 2026-07-07 18:00 UTC == 13:00 CDT (after noon) -> this Tue 2026-07-07
    ts = datetime(2026, 7, 7, 18, 0, tzinfo=timezone.utc)
    assert week_of(ts) == "2026-07-07"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_eventlog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eventlog'`

- [ ] **Step 3: Write minimal implementation**

Create `eventlog.py`:

```python
"""Append-only event log for availability/attendance signals (forecasting data)."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("discord")

EVENTS_PATH = "events.jsonl"

_CST = ZoneInfo("America/Chicago")


def local_slot(ts_utc: datetime, tz: ZoneInfo) -> tuple:
    """Return (weekday 0=Mon..6=Sun, block 0..11) for ts_utc in local tz; block = local_hour // 2."""
    local = ts_utc.astimezone(tz)
    return (local.weekday(), local.hour // 2)


def week_of(ts_utc: datetime) -> str:
    """ISO date of the Tuesday-noon-CST anchor of ts_utc's availability week.

    Availability resets Tuesday at noon CST. Returns the date of the most recent
    Tuesday-noon-CST at or before ts_utc.
    """
    local = ts_utc.astimezone(_CST)
    days_since_tue = (local.weekday() - 1) % 7  # Tuesday.weekday() == 1
    anchor = local.replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=days_since_tue)
    if anchor > local:
        anchor -= timedelta(days=7)
    return anchor.date().isoformat()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_eventlog.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add eventlog.py tests/test_eventlog.py
git commit -m "feat: add eventlog slot/week helpers"
```

---

## Task 2: eventlog append/read + gitignore

**Files:**
- Modify: `eventlog.py`
- Modify: `.gitignore`
- Test: `tests/test_eventlog.py`

**Interfaces:**
- Consumes: `local_slot` (Task 1).
- Produces:
  - `log_event(event_type: str, *, ts_utc: datetime, user_id=None, tz=None, source="discord", run_id=None, path=EVENTS_PATH, **fields) -> None`
  - `read_events(path: str = EVENTS_PATH) -> List[dict]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eventlog.py`:

```python
from eventlog import log_event, read_events


def test_log_and_read_round_trip(tmp_path):
    path = str(tmp_path / "events.jsonl")
    ts = datetime(2026, 7, 8, 20, 30, tzinfo=timezone.utc)
    log_event("offer_accepted", ts_utc=ts, user_id=42, tz=ZoneInfo("America/Chicago"),
              run_id=999, path=path)
    log_event("run_completed", ts_utc=ts, run_id=1000, path=path,
              level="10", run_type="one", roster=[1, 2, 3])

    events = read_events(path)
    assert len(events) == 2

    a = events[0]
    assert a["type"] == "offer_accepted"
    assert a["user_id"] == 42
    assert a["run_id"] == 999
    assert a["source"] == "discord"
    assert a["local_weekday"] == 2 and a["local_block"] == 7
    assert a["tz"] == "America/Chicago"

    b = events[1]
    assert b["type"] == "run_completed"
    assert b["user_id"] is None
    assert b["roster"] == [1, 2, 3]
    assert b["level"] == "10"
    # No tz supplied -> local fields null
    assert b["local_weekday"] is None and b["local_block"] is None and b["tz"] is None


def test_read_events_missing_file(tmp_path):
    assert read_events(str(tmp_path / "nope.jsonl")) == []


def test_read_events_skips_malformed(tmp_path):
    path = str(tmp_path / "events.jsonl")
    ts = datetime(2026, 7, 8, 20, 30, tzinfo=timezone.utc)
    log_event("avail_reaction", ts_utc=ts, user_id=7, path=path, emoji="🟢", week_of="2026-07-07")
    with open(path, "a", encoding="utf-8") as f:
        f.write("{ this is not valid json\n")
    log_event("avail_reaction", ts_utc=ts, user_id=8, path=path, emoji="🔴", week_of="2026-07-07")

    events = read_events(path)
    assert len(events) == 2
    assert [e["user_id"] for e in events] == [7, 8]


def test_log_event_never_raises_on_bad_path():
    # Directory that does not exist -> open() fails; must be swallowed, not raised.
    ts = datetime(2026, 7, 8, 20, 30, tzinfo=timezone.utc)
    log_event("avail_reaction", ts_utc=ts, user_id=1,
              path="no_such_dir/deeper/events.jsonl", emoji="🟢", week_of="2026-07-07")
    # Reaching here without an exception is the assertion.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_eventlog.py -k "round_trip or missing_file or malformed or never_raises" -v`
Expected: FAIL with `ImportError: cannot import name 'log_event'`

- [ ] **Step 3: Write minimal implementation**

Append to `eventlog.py`:

```python
def log_event(
    event_type: str,
    *,
    ts_utc: datetime,
    user_id: Optional[int] = None,
    tz: Optional[ZoneInfo] = None,
    source: str = "discord",
    run_id: Optional[int] = None,
    path: str = EVENTS_PATH,
    **fields,
) -> None:
    """Append one event record to the JSONL log. Best-effort: never raises."""
    try:
        record = {
            "type": event_type,
            "ts_utc": ts_utc.astimezone(timezone.utc).isoformat(),
            "user_id": user_id,
            "source": source,
            "run_id": run_id,
        }
        if tz is not None:
            weekday, block = local_slot(ts_utc, tz)
            record["local_weekday"] = weekday
            record["local_block"] = block
            record["tz"] = str(tz)
        else:
            record["local_weekday"] = None
            record["local_block"] = None
            record["tz"] = None
        record.update(fields)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except (OSError, ValueError) as exc:
        logger.warning("eventlog: failed to log %s event: %s", event_type, exc)


def read_events(path: str = EVENTS_PATH) -> List[dict]:
    """Read all events from the JSONL log, skipping malformed lines. Missing file -> []."""
    events: List[dict] = []
    if not os.path.exists(path):
        return events
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("eventlog: skipping malformed line in %s", path)
    except OSError as exc:
        logger.warning("eventlog: failed to read %s: %s", path, exc)
    return events
```

- [ ] **Step 4: Add events.jsonl to .gitignore**

In `.gitignore`, under the `# State files` section (which lists `state.json`, `watches.json`), add:

```
events.jsonl
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_eventlog.py -v`
Expected: PASS (all Task 1 + Task 2 tests). Then `python -m pytest -q` — full suite still green.

- [ ] **Step 6: Commit**

```bash
git add eventlog.py tests/test_eventlog.py .gitignore
git commit -m "feat: add eventlog append/read and gitignore events.jsonl"
```

---

## Task 3: Wire event logging into bot.py flows

**Files:**
- Modify: `bot.py` (import near top; `on_reaction_add` ~L1467–1626; `hourly_check` ~L688–715)

**Interfaces:**
- Consumes: `eventlog.log_event`, `eventlog.week_of` (Tasks 1–2).
- Produces: four event types written during live bot operation. No new public API.

Line numbers below are approximate — locate anchors by content. Every call passes the raider's `timezone` (a `ZoneInfo`) so the local slot is derived; all calls are best-effort (the helper swallows errors).

- [ ] **Step 1: Add the import**

In `bot.py`, add to the import block (with the other local module imports such as `import undermine` / `import watchlist`):

```python
import eventlog
```

- [ ] **Step 2: Log avail_reaction (both raider paths)**

In `on_reaction_add`, the availability-channel branch. **Existing-raider path** — right after `self.availability[reaction.emoji].append(self.raiders[user.id])` and its `await self.new_availability_signup_fill_schedule(...)` call (the line currently ~1485–1486), add:

```python
                    _now = datetime.now(timezone.utc)
                    eventlog.log_event(
                        "avail_reaction",
                        ts_utc=_now,
                        user_id=user.id,
                        tz=self.raiders[user.id].timezone,
                        emoji=str(reaction.emoji),
                        week_of=eventlog.week_of(_now),
                    )
```

**New-raider path** — right after the new raider is created and appended
(`self.availability[reaction.emoji].append(self.raiders[user.id])` at ~1508,
after the `new_availability_signup_fill_schedule` call), add the identical block:

```python
                        _now = datetime.now(timezone.utc)
                        eventlog.log_event(
                            "avail_reaction",
                            ts_utc=_now,
                            user_id=user.id,
                            tz=self.raiders[user.id].timezone,
                            emoji=str(reaction.emoji),
                            week_of=eventlog.week_of(_now),
                        )
```

(Indentation must match each surrounding block.)

- [ ] **Step 3: Log offer_accepted / offer_declined (dm_map branch)**

In the `elif reaction.message.channel.id in self.dm_map ...` branch:

**✅ path** — after `schedule = self.schedules.get(schedule_id)` and its
`if schedule is None: return` guard (~L1540–1541), before the availability
logic, add:

```python
                eventlog.log_event(
                    "offer_accepted",
                    ts_utc=schedule.start_time,
                    user_id=user.id,
                    tz=raider.timezone,
                    run_id=schedule_id,
                )
```

**❌ path** — after the corresponding `schedule = self.schedules.get(schedule_id)`
and its `if schedule is None: return` guard (~L1602–1604), before the removal
logic, add:

```python
                eventlog.log_event(
                    "offer_declined",
                    ts_utc=schedule.start_time,
                    user_id=user.id,
                    tz=raider.timezone,
                    run_id=schedule_id,
                )
```

(`raider` is already bound to `self.raiders[user.id]` in both paths.)

- [ ] **Step 4: Log run_completed in hourly_check (retention)**

In `hourly_check`, immediately after `past_schedule_ids = { ... }` is computed
(~L689) and BEFORE the message-deletion / `self.schedules` filtering, add:

```python
        # Record completed runs to the event log before their schedules are removed
        for _sid in past_schedule_ids:
            _sched = self.schedules.get(_sid)
            if _sched is None:
                continue
            _roster = []
            if _sched.team["tank"]:
                _roster.append(_sched.team["tank"].user_id)
            if _sched.team["healer"]:
                _roster.append(_sched.team["healer"].user_id)
            _roster.extend(r.user_id for r in _sched.team["dps"])
            eventlog.log_event(
                "run_completed",
                ts_utc=_sched.start_time,
                run_id=_sid,
                level=_sched.level,
                run_type=_sched.run_type,
                roster=_roster,
            )
```

This runs before the existing deletion block, so `self.schedules[_sid]` is still present. The Discord message is still deleted afterward exactly as today — only the durable record is added.

- [ ] **Step 5: Verify compile and tests**

Run: `python -m py_compile bot.py && python -m pytest -q`
Expected: no compile errors; full suite passes (unchanged count — bot.py has no unit tests). Confirm by reading the diff that exactly four `eventlog.log_event(` calls were added (two `avail_reaction`, one `offer_accepted`, one `offer_declined`) plus the `run_completed` call, and the `import eventlog` once.

- [ ] **Step 6: Manual smoke check (optional, recommended)**

With a valid `.env`, run the bot; react 🟢 on the availability post and confirm a line appears in `events.jsonl`. (No assertion — integration sanity check.)

- [ ] **Step 7: Commit**

```bash
git add bot.py
git commit -m "feat: log availability/attendance events from bot flows"
```

---

## Task 4: Documentation

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `CHANGELOG.md`

- [ ] **Step 1: Update CLAUDE.md**

- File Map: add `eventlog.py` ("Append-only availability/attendance event log for forecasting data") and note `events.jsonl` under State Persistence (gitignored, JSONL, append-only).
- Add a short "Event Logging (forecasting data)" subsection under Key Workflows: four event types (`avail_reaction`, `offer_accepted`, `offer_declined`, `run_completed`) logged from `on_reaction_add`/`hourly_check`; best-effort; `hourly_check` now writes `run_completed` before deleting a passed run's message.

- [ ] **Step 2: Update README.md**

Add a brief note: the bot now records anonymized availability/run events locally (`events.jsonl`) to power a future automatic-scheduling feature; no user-facing change.

- [ ] **Step 3: Update CHANGELOG.md**

Add a new top entry (bump `1.1.0` → `1.2.0`) with an `### Improvements` section:

```markdown
## [1.2.0]

### Improvements
- The bot now logs availability reactions, DM offer accept/declines, and completed-run rosters to a local event log (events.jsonl), and no longer discards run history — building the dataset for a future automatic scheduler.
```

Also bump `BOT_VERSION = "1.1.0"` → `"1.2.0"` in `bot.py` (near the version block). Do NOT edit `version.txt`.

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest -q`
Expected: full suite passes.

```bash
git add CLAUDE.md README.md CHANGELOG.md bot.py
git commit -m "docs: document event logging (v1.2.0)"
```

---

## Self-Review Notes

- **Spec coverage:** event store module + helpers (Task 1–2), gitignore (Task 2), the four flow-layer hooks + retention change (Task 3), docs (Task 4). All spec sections map to a task. The dropped `run_created`/`run_joined` are intentionally absent.
- **No model coupling:** all logging is in `bot.py` flow methods; `eventlog.py` has no dependency on `Raider`/`Schedule`; tests write to `tmp_path`, never the repo's `events.jsonl`.
- **Type consistency:** `log_event(event_type, *, ts_utc, user_id, tz, source, run_id, path, **fields)` and `read_events(path)` are used identically across tasks; `local_slot`/`week_of` signatures match Task 1.
- **Best-effort invariant:** `log_event` wraps all IO in `try/except (OSError, ValueError)` and never raises — verified by `test_log_event_never_raises_on_bad_path`.
