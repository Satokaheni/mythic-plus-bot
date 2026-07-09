# Availability Forecasting — Phase 1b (Raider.io Backfill/Harvest) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seed and continuously enrich `events.jsonl` with each mapped character's real Mythic+ runs from Raider.io — per-person `raiderio_run` attendance events — so Phase 2's forecaster has data from day one.

**Architecture:** A new `raiderio.py` (mirrors `undermine.py`): pure parsers + a thin async client + a `harvest` orchestrator that appends deduped `raiderio_run` events via the existing `eventlog.log_event`. `bot.py` loads `character_mappings.json` and runs a daily harvest task. No change to `eventlog.py`.

**Tech Stack:** Python 3.9+, discord.py v2+, `aiohttp` (bundled), pytest. Reads Phase 1a's `eventlog`.

## Global Constraints

- Python 3.9+ compatible; datetimes tz-aware UTC. Parse Raider.io's `"…Z"` millisecond timestamps 3.9-safe via `s.replace("Z", "+00:00")` then `datetime.fromisoformat(...)`.
- Env read **lazily** at call time (not import): `RAIDERIO_API_KEY`, `RAIDERIO_REGION` (default `"us"`). (Lesson from the Undermine import-order bug.)
- Raider.io base: `https://raider.io/api/v1/characters/profile`; query `region`, `realm` (= `realm_slug`), `name` (= character), `fields=mythic_plus_recent_runs,mythic_plus_best_runs`, `access_key`. **URL-encode all query values** (names include `Reíka`, `ßovinity`, etc.).
- Best-effort everywhere: a bad character/API/network/parse error → log a warning and return `[]` / skip; never raise out of the client or harvest.
- **Skip unregistered:** harvest only `discord_id`s present in the `raiders` dict (registered → timezone known). Ignore `alt_of` — every mapping entry is a character to fetch, attributed to its `discord_id`.
- Event: `type="raiderio_run"`, `user_id=<discord_id>`, `ts_utc=<completed_at UTC>`, `tz=<raider.timezone>`, `source="raiderio"`, `run_id=<keystone_run_id>`, `level=<int>`. Dedupe by `(user_id, run_id)`.
- Logger `logging.getLogger("discord")`.
- `character_mappings.json` is **gitignored** (Discord IDs) and provided at runtime like `.env` (NOT COPYed into the Docker image).

## File Structure

| File | Responsibility |
|------|----------------|
| `raiderio.py` (create) | `Run`, `_parse_completed_at`, `_parse_runs`, `fetch_character_runs`, `load_character_mappings`, `harvest`. |
| `tests/test_raiderio.py` (create) | Parser, mapping-loader, and harvest tests (client mocked). |
| `bot.py` (modify) | `import raiderio`; load mappings in `setup_hook`; `raiderio_harvest` daily task. |
| `.gitignore` (modify) | Ignore `character_mappings.json`. |
| `pyproject.toml`, `Dockerfile` (modify) | Package `raiderio`. |
| `CLAUDE.md`, `README.md`, `CHANGELOG.md` (modify) | Docs. |

---

## Task 1: raiderio parsers (Run, _parse_completed_at, _parse_runs)

**Files:**
- Create: `raiderio.py`
- Test: `tests/test_raiderio.py`

**Interfaces:**
- Produces: `Run(run_id: int, completed_at: datetime, level: int)` (frozen dataclass); `_parse_completed_at(s) -> datetime`; `_parse_runs(data: dict) -> List[Run]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_raiderio.py`:

```python
"""Tests for the Raider.io client, mapping loader, and harvest."""

from datetime import datetime, timezone

from raiderio import Run, _parse_completed_at, _parse_runs


def test_parse_completed_at_utc():
    dt = _parse_completed_at("2026-04-22T06:07:47.000Z")
    assert dt == datetime(2026, 4, 22, 6, 7, 47, tzinfo=timezone.utc)
    assert dt.tzinfo is not None


def test_parse_runs_combines_recent_and_best_and_dedups():
    data = {
        "result": {
            "mythic_plus_recent_runs": [
                {"keystone_run_id": 1, "completed_at": "2026-07-08T20:00:00.000Z", "mythic_level": 12},
                {"keystone_run_id": 2, "completed_at": "2026-07-07T02:30:00.000Z", "mythic_level": 15},
            ],
            "mythic_plus_best_runs": [
                # overlaps run_id 2 (dedup) + one new (3)
                {"keystone_run_id": 2, "completed_at": "2026-07-07T02:30:00.000Z", "mythic_level": 15},
                {"keystone_run_id": 3, "completed_at": "2026-07-06T18:00:00.000Z", "mythic_level": 20},
            ],
        }
    }
    runs = _parse_runs(data)
    assert {r.run_id for r in runs} == {1, 2, 3}
    r3 = next(r for r in runs if r.run_id == 3)
    assert r3.level == 20
    assert r3.completed_at == datetime(2026, 7, 6, 18, 0, tzinfo=timezone.utc)


def test_parse_runs_skips_malformed_and_handles_empty():
    data = {
        "result": {
            "mythic_plus_recent_runs": [
                {"keystone_run_id": 1, "completed_at": "2026-07-08T20:00:00.000Z", "mythic_level": 12},
                {"keystone_run_id": 9, "mythic_level": 10},          # missing completed_at
                {"completed_at": "2026-07-08T20:00:00.000Z", "mythic_level": 10},  # missing id
            ]
        }
    }
    runs = _parse_runs(data)
    assert [r.run_id for r in runs] == [1]
    assert _parse_runs({"result": {}}) == []
    assert _parse_runs({}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_raiderio.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'raiderio'`

- [ ] **Step 3: Write minimal implementation**

Create `raiderio.py`:

```python
"""Async client + harvester for Raider.io Mythic+ run history (forecasting backfill)."""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List
from urllib.parse import urlencode

import aiohttp

import eventlog

logger = logging.getLogger("discord")

BASE_URL = "https://raider.io/api/v1/characters/profile"
_MAPPINGS_PATH = "character_mappings.json"


def _api_key() -> str:
    return os.getenv("RAIDERIO_API_KEY", "")


def _region() -> str:
    return os.getenv("RAIDERIO_REGION", "us")


@dataclass(frozen=True)
class Run:
    """One Mythic+ run: keystone run id, completion time (UTC), and level."""

    run_id: int
    completed_at: datetime
    level: int


def _parse_completed_at(s: str) -> datetime:
    """Parse Raider.io's '2026-04-22T06:07:47.000Z' to a UTC-aware datetime (py3.9-safe)."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _parse_runs(data: dict) -> List[Run]:
    """Combine recent + best runs into a list of Run, deduped by run_id; skip malformed entries."""
    result = data.get("result", {})
    raw = list(result.get("mythic_plus_recent_runs", [])) + list(result.get("mythic_plus_best_runs", []))
    runs = {}
    for entry in raw:
        try:
            run_id = int(entry["keystone_run_id"])
            completed = _parse_completed_at(entry["completed_at"])
            level = int(entry["mythic_level"])
        except (KeyError, ValueError, TypeError):
            continue
        runs[run_id] = Run(run_id=run_id, completed_at=completed, level=level)
    return list(runs.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_raiderio.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add raiderio.py tests/test_raiderio.py
git commit -m "feat: add Raider.io run parsers"
```

---

## Task 2: fetch_character_runs + load_character_mappings

**Files:**
- Modify: `raiderio.py`
- Test: `tests/test_raiderio.py`

**Interfaces:**
- Consumes: `_parse_runs`, `Run` (Task 1).
- Produces:
  - `async fetch_character_runs(session, realm_slug: str, character: str) -> List[Run]`
  - `load_character_mappings(path: str = _MAPPINGS_PATH) -> List[dict]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_raiderio.py`:

```python
import asyncio
import json

import raiderio
from raiderio import fetch_character_runs, load_character_mappings


class _FakeResp:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload or {}
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self): return self._payload


class _FakeSession:
    def __init__(self, resp=None, get_exc=None):
        self._resp = resp
        self._get_exc = get_exc
    def get(self, url):
        if self._get_exc:
            raise self._get_exc
        return self._resp


def test_fetch_character_runs_parses_ok():
    payload = {"result": {"mythic_plus_recent_runs": [
        {"keystone_run_id": 5, "completed_at": "2026-07-08T20:00:00.000Z", "mythic_level": 12}]}}
    session = _FakeSession(resp=_FakeResp(200, payload))
    runs = asyncio.run(fetch_character_runs(session, "malganis", "Reíka"))
    assert [r.run_id for r in runs] == [5]


def test_fetch_character_runs_best_effort_on_non_200():
    session = _FakeSession(resp=_FakeResp(404, {}))
    assert asyncio.run(fetch_character_runs(session, "malganis", "Nobody")) == []


def test_fetch_character_runs_best_effort_on_error():
    session = _FakeSession(get_exc=asyncio.TimeoutError())
    assert asyncio.run(fetch_character_runs(session, "malganis", "Nobody")) == []


def test_load_character_mappings(tmp_path):
    path = str(tmp_path / "character_mappings.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([{"character": "Main", "realm_slug": "malganis", "discord_id": "1", "alt_of": None},
                   {"character": "Alt", "realm_slug": "malganis", "discord_id": "1", "alt_of": "Main"}], f)
    m = load_character_mappings(path)
    assert len(m) == 2
    assert m[1]["character"] == "Alt"


def test_load_character_mappings_missing_and_corrupt(tmp_path):
    assert load_character_mappings(str(tmp_path / "nope.json")) == []
    bad = str(tmp_path / "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{ not json")
    assert load_character_mappings(bad) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_raiderio.py -k "fetch or mappings" -v`
Expected: FAIL with `ImportError: cannot import name 'fetch_character_runs'`

- [ ] **Step 3: Write minimal implementation**

Append to `raiderio.py`:

```python
async def fetch_character_runs(session: aiohttp.ClientSession, realm_slug: str, character: str) -> List[Run]:
    """Fetch a character's recent + best M+ runs. Best-effort: returns [] on any error."""
    params = {
        "region": _region(),
        "realm": realm_slug,
        "name": character,
        "fields": "mythic_plus_recent_runs,mythic_plus_best_runs",
        "access_key": _api_key(),
    }
    url = f"{BASE_URL}?{urlencode(params)}"
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.warning("Raider.io %s/%s returned status %s", realm_slug, character, resp.status)
                return []
            data = await resp.json()
        return _parse_runs(data)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("Raider.io request failed for %s/%s: %s", realm_slug, character, exc)
        return []


def load_character_mappings(path: str = _MAPPINGS_PATH) -> List[dict]:
    """Load the character->discord mapping array. Missing/corrupt -> []. `alt_of` is ignored by callers."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            logger.warning("%s is not a JSON array; ignoring", path)
            return []
        return data
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_raiderio.py -v`
Expected: PASS (all Task 1 + Task 2 tests)

- [ ] **Step 5: Commit**

```bash
git add raiderio.py tests/test_raiderio.py
git commit -m "feat: add Raider.io fetch + mapping loader"
```

---

## Task 3: harvest orchestrator

**Files:**
- Modify: `raiderio.py`
- Test: `tests/test_raiderio.py`

**Interfaces:**
- Consumes: `fetch_character_runs`, `Run` (Tasks 1–2), `eventlog.log_event`/`read_events`/`EVENTS_PATH`.
- Produces: `async harvest(raiders: dict, session, mappings: List[dict], events_path: str = eventlog.EVENTS_PATH) -> int` (returns new-event count).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_raiderio.py`:

```python
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import eventlog
from raiderio import Run, harvest


def test_harvest_registered_only_and_dedup(tmp_path, monkeypatch):
    path = str(tmp_path / "events.jsonl")
    raiders = {111: SimpleNamespace(timezone=ZoneInfo("America/Chicago"))}  # 111 registered; 222 not
    mappings = [
        {"discord_id": "111", "realm_slug": "malganis", "character": "Main", "alt_of": None},
        {"discord_id": "222", "realm_slug": "dalaran", "character": "Ghost", "alt_of": None},
    ]
    runs_by_char = {
        "Main": [Run(1, datetime(2026, 7, 8, 20, 0, tzinfo=timezone.utc), 20)],
        "Ghost": [Run(2, datetime(2026, 7, 8, 20, 0, tzinfo=timezone.utc), 15)],
    }

    async def fake_fetch(session, realm_slug, character):
        return runs_by_char.get(character, [])

    monkeypatch.setattr(raiderio, "fetch_character_runs", fake_fetch)

    added = asyncio.run(harvest(raiders, None, mappings, events_path=path))
    assert added == 1  # only the registered user's run
    events = eventlog.read_events(path)
    assert len(events) == 1
    e = events[0]
    assert e["type"] == "raiderio_run"
    assert e["user_id"] == 111
    assert e["run_id"] == 1
    assert e["source"] == "raiderio"
    assert e["level"] == 20
    assert e["local_weekday"] is not None  # timezone applied -> real local slot

    # Idempotent: re-running adds nothing (dedup by (user_id, run_id))
    added2 = asyncio.run(harvest(raiders, None, mappings, events_path=path))
    assert added2 == 0
    assert len(eventlog.read_events(path)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_raiderio.py -k harvest -v`
Expected: FAIL with `ImportError: cannot import name 'harvest'`

- [ ] **Step 3: Write minimal implementation**

Append to `raiderio.py`:

```python
async def harvest(raiders: dict, session, mappings: List[dict], events_path: str = eventlog.EVENTS_PATH) -> int:
    """Append new raiderio_run events for registered mapped raiders. Idempotent (dedup by (user_id, run_id))."""
    existing = eventlog.read_events(events_path)
    seen = {(e.get("user_id"), e.get("run_id")) for e in existing if e.get("source") == "raiderio"}
    added = 0
    for entry in mappings:
        try:
            discord_id = int(entry["discord_id"])
        except (KeyError, ValueError, TypeError):
            continue
        raider = raiders.get(discord_id)
        if raider is None:
            continue  # unregistered -> no timezone -> skip
        realm_slug = entry.get("realm_slug")
        character = entry.get("character")
        if not realm_slug or not character:
            continue
        runs = await fetch_character_runs(session, realm_slug, character)
        for run in runs:
            key = (discord_id, run.run_id)
            if key in seen:
                continue
            eventlog.log_event(
                "raiderio_run",
                ts_utc=run.completed_at,
                user_id=discord_id,
                tz=raider.timezone,
                source="raiderio",
                run_id=run.run_id,
                level=run.level,
                path=events_path,
            )
            seen.add(key)
            added += 1
    return added
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_raiderio.py -v`
Expected: PASS. Then `python -m pytest -q` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add raiderio.py tests/test_raiderio.py
git commit -m "feat: add Raider.io harvest (registered-only, deduped)"
```

---

## Task 4: Wire harvest into bot.py + packaging

**Files:**
- Modify: `bot.py` (imports; `setup_hook`; new task near the other `@tasks.loop` tasks)
- Modify: `.gitignore`, `pyproject.toml`, `Dockerfile`

**Interfaces:**
- Consumes: `raiderio.load_character_mappings`, `raiderio.harvest`, `aiohttp`.

- [ ] **Step 1: Add imports**

In `bot.py`, add `import raiderio` to the local-module import group (with `import undermine` / `import watchlist` / `import eventlog`). (`aiohttp` is already imported.)

- [ ] **Step 2: Load mappings in setup_hook**

In `setup_hook`, after `self.watchlist = Watchlist.load()` (or alongside the other startup loads), add:

```python
        self._char_mappings = raiderio.load_character_mappings()
```

- [ ] **Step 3: Add the daily harvest task**

Add a method to `MyClient`, placed near the other `@tasks.loop` tasks (e.g. after `price_watch_check`):

```python
    @tasks.loop(hours=24)
    async def raiderio_harvest(self):
        """Daily Raider.io backfill/harvest: append new raiderio_run events (idempotent)."""
        if not self.is_ready():
            logger.warning("raiderio_harvest: Bot not ready yet, skipping this iteration")
            return
        if not self._char_mappings:
            return
        try:
            async with aiohttp.ClientSession() as session:
                added = await raiderio.harvest(self.raiders, session, self._char_mappings)
            logger.info("raiderio_harvest: appended %d new run events", added)
        except Exception as exc:  # noqa: BLE001 - harvest must never kill the loop
            logger.warning("raiderio_harvest failed: %s", exc)
```

- [ ] **Step 4: Start the task in setup_hook**

After the existing task-start block (e.g. after `self.price_watch_check.start()`), add:

```python
        # Start daily Raider.io harvest (initial backfill runs on first iteration)
        if not self.raiderio_harvest.is_running():
            self.raiderio_harvest.start()
```

- [ ] **Step 5: gitignore + packaging**

- In `.gitignore`, under the state/config section, add:

```
character_mappings.json
```

- In `pyproject.toml`, add `"raiderio"` to the `[tool.setuptools] py-modules` list.
- In `Dockerfile`, add `raiderio.py` to the `COPY ... ./` module line. Do NOT add `character_mappings.json` (gitignored; provided at runtime like `.env`).

- [ ] **Step 6: Verify**

Run: `python -m py_compile bot.py && python -m pip install -e ".[dev]" && python -m pytest -q`
Expected: compiles; editable install builds (py-modules valid); full suite passes (unchanged count — bot.py has no unit tests). Confirm from the diff: one `import raiderio`, one `raiderio_harvest` method, started once; `character_mappings.json` gitignored; `raiderio` in py-modules and Dockerfile COPY.

- [ ] **Step 7: Commit**

```bash
git add bot.py .gitignore pyproject.toml Dockerfile
git commit -m "feat: wire daily Raider.io harvest; package raiderio module"
```

---

## Task 5: Documentation

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `CHANGELOG.md`, `bot.py`

- [ ] **Step 1: Update CLAUDE.md**

- File Map: add `raiderio.py` ("Raider.io client + daily harvester seeding raiderio_run events") and `character_mappings.json` (gitignored config, `discord_id → characters`, provided at runtime).
- Under the event-logging workflow: note the daily `raiderio_harvest` task appends single-user `raiderio_run` events for registered mapped raiders (ignores `alt_of`, skips unregistered, deduped by `(user_id, run_id)`).
- Env vars: add `RAIDERIO_API_KEY`, `RAIDERIO_REGION` (default `us`).

- [ ] **Step 2: Update README.md**

Add a note: the bot backfills/harvests Mythic+ run history from Raider.io (daily) for mapped, registered raiders to seed the forecasting dataset; requires `RAIDERIO_API_KEY` and a runtime-provided `character_mappings.json`.

- [ ] **Step 3: Update CHANGELOG.md + version**

Add a new top entry (bump `1.2.0` → `1.3.0`), matching the file's style (`## [x.y.z]`, no date, `### Improvements`):

```markdown
## [1.3.0]

### Improvements
- The bot now backfills and daily-harvests Mythic+ run history from Raider.io for mapped, registered raiders, seeding the availability dataset with real play-time data.
```

Bump `BOT_VERSION = "1.2.0"` → `"1.3.0"` in `bot.py`. Do NOT edit `version.txt`.

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest -q`
Expected: full suite passes.

```bash
git add CLAUDE.md README.md CHANGELOG.md bot.py
git commit -m "docs: document Raider.io harvest (v1.3.0)"
```

---

## Self-Review Notes

- **Spec coverage:** parsers (Task 1), client + mapping loader (Task 2), harvest incl. skip-unregistered + dedupe (Task 3), bot wiring + daily task + gitignore + packaging (Task 4), docs (Task 5). All spec sections map to a task.
- **No eventlog change:** harvest calls the existing `eventlog.log_event` with `type="raiderio_run"` + `**fields`.
- **Best-effort invariant:** `fetch_character_runs` catches `(aiohttp.ClientError, asyncio.TimeoutError, ValueError)`; `harvest` skips bad entries; the bot task wraps everything in `try/except Exception`.
- **Lazy env:** `_api_key()`/`_region()` read at call time (no import-order bug).
- **Type consistency:** `Run(run_id, completed_at, level)`, `fetch_character_runs(session, realm_slug, character)`, `harvest(raiders, session, mappings, events_path)` used identically across tasks.
- **Registered-only + tz:** `raiders.get(discord_id)` (int key, matching `self.raiders`); `tz=raider.timezone` yields a real local slot; unregistered skipped.
