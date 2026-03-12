# Test Suite Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pytest-based unit test suite covering `raider.py`, `schedule.py`, and `utils.py`, with GitHub Actions CI running on every push/PR to `main`.

**Architecture:** Shared `conftest.py` provides fixture-factory functions that construct `Raider` and `Schedule` objects via a `FakeMember` `SimpleNamespace`. Each test file covers one source module. `monkeypatch.chdir(tmp_path)` isolates file I/O in utils tests. `freezegun` handles the 8-hour displacement window in schedule tests.

**Tech Stack:** Python 3.11, pytest >= 7, pytest-mock >= 3, freezegun >= 1, discord.py (already a runtime dependency)

---

## Chunk 1: Infrastructure

### Task 1: Update `pyproject.toml`

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Open `pyproject.toml` and add test dependencies to the `dev` extra**

Replace the existing `[project.optional-dependencies]` and add `[tool.pytest.ini_options]`:

```toml
[project.optional-dependencies]
dev = [
    "black>=23.0.0",
    "ruff>=0.1.0",
    "pytest>=7.0",
    "pytest-mock>=3.0",
    "freezegun>=1.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

`pythonpath = ["."]` ensures `from raider import Raider` resolves when pytest runs from the project root (flat layout, no `src/`). Requires pytest >= 7.

- [ ] **Step 2: Install the updated dev extras**

```bash
pip install -e ".[dev]"
```

Expected: pip installs pytest, pytest-mock, freezegun without errors.

- [ ] **Step 3: Verify pytest is discoverable**

```bash
pytest --version
```

Expected: `pytest 7.x.x` (or higher)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build: add pytest, pytest-mock, freezegun to dev extras"
```

---

### Task 2: Create `.github/workflows/tests.yml`

**Files:**
- Create: `.github/workflows/tests.yml`

- [ ] **Step 1: Create the workflows directory and CI file**

```bash
mkdir -p .github/workflows
```

```yaml
# .github/workflows/tests.yml
name: Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -e ".[dev]"

      - name: Run tests
        run: pytest tests/ -v
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/tests.yml
git commit -m "ci: add GitHub Actions workflow to run pytest on push/PR to main"
```

---

## Chunk 2: Test Fixtures (`conftest.py`)

### Task 3: Create `tests/conftest.py`

**Files:**
- Create: `tests/conftest.py`

- [ ] **Step 1: Create the `tests/` directory**

```bash
mkdir tests
```

- [ ] **Step 2: Write `tests/conftest.py`**

```python
"""Shared pytest fixtures for the Mythic+ bot test suite."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from raider import Raider
from schedule import Schedule


@pytest.fixture
def make_raider():
    """Fixture-factory that returns a Raider built from a FakeMember SimpleNamespace.

    FakeMember is ONLY passed to Raider.__init__ — never directly to Schedule.__init__.
    Schedule.__init__ always receives a Raider object.
    """

    def _factory(user_id=1, roles=None, class_play="warrior", tz="US/Eastern", name="TestUser"):
        if roles is None:
            roles = ["tank"]
        member = SimpleNamespace(
            id=user_id,
            mention=f"<@{user_id}>",
            display_name=name,
        )
        return Raider(member, class_play, roles, tz)

    return _factory


@pytest.fixture
def make_schedule(make_raider):
    """Fixture-factory that returns a Schedule with the given raider as organizer.

    The organizer is always in the team (1 signup) after construction.
    start_time must be timezone-aware; defaults to now(UTC) + 24 hours.
    date_str is derived from start_time.strftime("%Y-%m-%d").
    """

    def _factory(raider=None, start_time=None, run_type="one", level="10"):
        if raider is None:
            raider = make_raider()
        if start_time is None:
            start_time = datetime.now(timezone.utc) + timedelta(hours=24)
        date_str = start_time.strftime("%Y-%m-%d")
        return Schedule(raider, level, date_str, start_time, run_type)

    return _factory


@pytest.fixture
def make_full_schedule(make_raider, make_schedule):
    """Fixture-factory that returns a Schedule with all 5 slots filled.

    Accepts an optional list of exactly 5 Raiders with roles:
    [tank], [healer], [dps], [dps], [dps].
    Default raiders use user_ids 1-5.
    """

    def _factory(raiders=None):
        if raiders is None:
            raiders = [
                make_raider(user_id=1, roles=["tank"], name="Tank"),
                make_raider(user_id=2, roles=["healer"], name="Healer"),
                make_raider(user_id=3, roles=["dps"], name="DPS1"),
                make_raider(user_id=4, roles=["dps"], name="DPS2"),
                make_raider(user_id=5, roles=["dps"], name="DPS3"),
            ]
        sched = make_schedule(raider=raiders[0])
        for r in raiders[1:]:
            sched.raider_signup(r)
        return sched

    return _factory
```

- [ ] **Step 3: Verify conftest is importable (no Discord bot token required)**

```bash
pytest tests/ --collect-only
```

Expected: "no tests ran" with 0 errors (conftest loads cleanly; discord.py is installed as a runtime dep).

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add conftest.py with FakeMember and fixture-factories"
```

---

## Chunk 3: `test_raider.py`

### Task 4: Create `tests/test_raider.py`

**Files:**
- Create: `tests/test_raider.py`
- Reference: `raider.py`

- [ ] **Step 1: Write the failing tests (run FIRST, before any implementation)**

All tests run against the already-written `raider.py`. "Failing" here means writing the test first and confirming your understanding before running the full suite.

```python
"""Tests for raider.py — availability rules, conflict messages, run tracking, serialization."""

from datetime import datetime, timedelta, timezone

import pytest

from raider import Raider


# ---------------------------------------------------------------------------
# check_availability
# ---------------------------------------------------------------------------


def test_available_no_conflicts(make_raider, make_schedule):
    raider = make_raider()
    sched = make_schedule(raider=make_raider(user_id=99, roles=["healer"]))
    assert raider.check_availability(sched) is True


def test_unavailable_already_signed_up(make_raider, make_schedule):
    raider = make_raider()
    sched = make_schedule(raider=raider)
    raider.add_run(sched)
    assert raider.check_availability(sched) is False


def test_single_blocked_under_1h(make_raider, make_schedule):
    raider = make_raider()
    base_time = datetime.now(timezone.utc) + timedelta(hours=24)

    existing = make_schedule(raider=make_raider(user_id=99, roles=["healer"]), start_time=base_time, run_type="one")
    raider.add_run(existing)

    new_sched = make_schedule(
        raider=make_raider(user_id=98, roles=["healer"]),
        start_time=base_time + timedelta(minutes=30),
        run_type="one",
    )
    assert raider.check_availability(new_sched) is False


def test_single_allowed_at_exactly_1h(make_raider, make_schedule):
    raider = make_raider()
    base_time = datetime.now(timezone.utc) + timedelta(hours=24)

    existing = make_schedule(raider=make_raider(user_id=99, roles=["healer"]), start_time=base_time, run_type="one")
    raider.add_run(existing)

    new_sched = make_schedule(
        raider=make_raider(user_id=98, roles=["healer"]),
        start_time=base_time + timedelta(seconds=3600),
        run_type="one",
    )
    assert raider.check_availability(new_sched) is True


def test_single_allowed_over_1h(make_raider, make_schedule):
    raider = make_raider()
    base_time = datetime.now(timezone.utc) + timedelta(hours=24)

    existing = make_schedule(raider=make_raider(user_id=99, roles=["healer"]), start_time=base_time, run_type="one")
    raider.add_run(existing)

    new_sched = make_schedule(
        raider=make_raider(user_id=98, roles=["healer"]),
        start_time=base_time + timedelta(minutes=90),
        run_type="one",
    )
    assert raider.check_availability(new_sched) is True


def test_multiple_blocked_under_2h(make_raider, make_schedule):
    raider = make_raider()
    base_time = datetime.now(timezone.utc) + timedelta(hours=24)

    existing = make_schedule(raider=make_raider(user_id=99, roles=["healer"]), start_time=base_time, run_type="one")
    raider.add_run(existing)

    new_sched = make_schedule(
        raider=make_raider(user_id=98, roles=["healer"]),
        start_time=base_time + timedelta(minutes=90),
        run_type="multiple",
    )
    assert raider.check_availability(new_sched) is False


def test_multiple_allowed_at_exactly_2h(make_raider, make_schedule):
    raider = make_raider()
    base_time = datetime.now(timezone.utc) + timedelta(hours=24)

    existing = make_schedule(raider=make_raider(user_id=99, roles=["healer"]), start_time=base_time, run_type="one")
    raider.add_run(existing)

    new_sched = make_schedule(
        raider=make_raider(user_id=98, roles=["healer"]),
        start_time=base_time + timedelta(seconds=7200),
        run_type="multiple",
    )
    assert raider.check_availability(new_sched) is True


def test_existing_multiple_blocks_new_single(make_raider, make_schedule):
    raider = make_raider()
    base_time = datetime.now(timezone.utc) + timedelta(hours=24)

    existing = make_schedule(
        raider=make_raider(user_id=99, roles=["healer"]), start_time=base_time, run_type="multiple"
    )
    raider.add_run(existing)

    new_sched = make_schedule(
        raider=make_raider(user_id=98, roles=["healer"]),
        start_time=base_time + timedelta(minutes=90),
        run_type="one",
    )
    assert raider.check_availability(new_sched) is False


def test_conflict_with_second_run_not_first(make_raider, make_schedule):
    raider = make_raider()
    base_time = datetime.now(timezone.utc) + timedelta(hours=24)

    # First run: 2 hours away — no conflict for single key
    run1 = make_schedule(
        raider=make_raider(user_id=99, roles=["healer"]),
        start_time=base_time,
        run_type="one",
    )
    raider.add_run(run1)

    # Second run: 30 min away — conflict for single key
    run2 = make_schedule(
        raider=make_raider(user_id=98, roles=["healer"]),
        start_time=base_time + timedelta(hours=2, minutes=30),
        run_type="one",
    )
    raider.add_run(run2)

    new_sched = make_schedule(
        raider=make_raider(user_id=97, roles=["healer"]),
        start_time=base_time + timedelta(hours=2, minutes=5),
        run_type="one",
    )
    assert raider.check_availability(new_sched) is False


# ---------------------------------------------------------------------------
# get_schedule_conflict_reason
# ---------------------------------------------------------------------------


def test_no_conflict_returns_empty(make_raider, make_schedule):
    raider = make_raider()
    sched = make_schedule(raider=make_raider(user_id=99, roles=["healer"]))
    assert raider.get_schedule_conflict_reason(sched) == ""


def test_already_signed_up_message(make_raider, make_schedule):
    raider = make_raider()
    sched = make_schedule(raider=raider)
    raider.add_run(sched)
    reason = raider.get_schedule_conflict_reason(sched)
    assert "already signed up" in reason.lower()


def test_single_conflict_message(make_raider, make_schedule):
    raider = make_raider()
    base_time = datetime.now(timezone.utc) + timedelta(hours=24)

    existing = make_schedule(raider=make_raider(user_id=99, roles=["healer"]), start_time=base_time, run_type="one")
    raider.add_run(existing)

    new_sched = make_schedule(
        raider=make_raider(user_id=98, roles=["healer"]),
        start_time=base_time + timedelta(minutes=30),
        run_type="one",
    )
    reason = raider.get_schedule_conflict_reason(new_sched)
    assert "less than 1 hour" in reason.lower()
    assert "0.5" in reason  # time diff = 0.5 hours


def test_multiple_conflict_message_existing(make_raider, make_schedule):
    raider = make_raider()
    base_time = datetime.now(timezone.utc) + timedelta(hours=24)

    existing = make_schedule(
        raider=make_raider(user_id=99, roles=["healer"]), start_time=base_time, run_type="multiple"
    )
    raider.add_run(existing)

    new_sched = make_schedule(
        raider=make_raider(user_id=98, roles=["healer"]),
        start_time=base_time + timedelta(minutes=90),
        run_type="one",
    )
    reason = raider.get_schedule_conflict_reason(new_sched)
    assert "multiple-key" in reason.lower()
    assert "1.5" in reason  # time diff = 1.5 hours


def test_multiple_conflict_message_new(make_raider, make_schedule):
    raider = make_raider()
    base_time = datetime.now(timezone.utc) + timedelta(hours=24)

    existing = make_schedule(raider=make_raider(user_id=99, roles=["healer"]), start_time=base_time, run_type="one")
    raider.add_run(existing)

    new_sched = make_schedule(
        raider=make_raider(user_id=98, roles=["healer"]),
        start_time=base_time + timedelta(minutes=90),
        run_type="multiple",
    )
    reason = raider.get_schedule_conflict_reason(new_sched)
    assert "multiple-key" in reason.lower()
    assert "1.5" in reason


# ---------------------------------------------------------------------------
# add_run / remove_run
# ---------------------------------------------------------------------------


def test_add_run_adds_to_current(make_raider, make_schedule):
    raider = make_raider()
    sched = make_schedule(raider=make_raider(user_id=99, roles=["healer"]))
    raider.add_run(sched)
    assert sched in raider.current_runs


def test_add_run_removes_from_denied(make_raider, make_schedule):
    raider = make_raider()
    sched = make_schedule(raider=make_raider(user_id=99, roles=["healer"]))
    raider.denied_runs.add(sched)
    raider.add_run(sched)
    assert sched not in raider.denied_runs
    assert sched in raider.current_runs


def test_remove_run_moves_to_denied(make_raider, make_schedule):
    raider = make_raider()
    sched = make_schedule(raider=make_raider(user_id=99, roles=["healer"]))
    raider.add_run(sched)
    raider.remove_run(sched)
    assert sched not in raider.current_runs
    assert sched in raider.denied_runs


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_to_dict_from_dict_roundtrip(make_raider):
    raider = make_raider(user_id=42, roles=["tank", "dps"], class_play="warrior")
    d = raider.to_dict(schedule_to_id={})
    restored = Raider.from_dict(d)
    assert restored.user_id == raider.user_id
    assert restored.roles == raider.roles
    assert restored.class_play == raider.class_play
    assert str(restored.timezone) == str(raider.timezone)
    assert restored.current_runs == set()
    assert restored.denied_runs == set()


def test_from_dict_wires_current_runs(make_raider, make_schedule):
    raider = make_raider(user_id=1)
    # The schedule needs a different organizer so raider isn't auto-added
    sched = make_schedule(raider=make_raider(user_id=99, roles=["healer"]))
    raider.add_run(sched)

    msg_id = 999
    schedule_to_id = {id(sched): msg_id}
    d = raider.to_dict(schedule_to_id)

    restored = Raider.from_dict(d, schedules_by_id={msg_id: sched})
    assert sched in restored.current_runs


def test_from_dict_wires_denied_runs(make_raider, make_schedule):
    raider = make_raider(user_id=1)
    sched = make_schedule(raider=make_raider(user_id=99, roles=["healer"]))
    raider.denied_runs.add(sched)

    msg_id = 888
    schedule_to_id = {id(sched): msg_id}
    d = raider.to_dict(schedule_to_id)

    restored = Raider.from_dict(d, schedules_by_id={msg_id: sched})
    assert sched in restored.denied_runs
```

- [ ] **Step 2: Run the tests**

```bash
pytest tests/test_raider.py -v
```

Expected: all 20 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_raider.py
git commit -m "test: add test_raider.py — availability, conflict messages, run tracking, serialization"
```

---

## Chunk 4: `test_schedule.py`

### Task 5: Create `tests/test_schedule.py`

**Files:**
- Create: `tests/test_schedule.py`
- Reference: `schedule.py`

**Key implementation note:** `raider_signup` does NOT call `raider.add_run(schedule)` — that is done separately in `bot.py`. Tests that exercise `try_displace_off_roler` (which calls `displaced.current_runs.discard(self)`) must manually call `raider.add_run(sched)` to wire up `current_runs` before testing displacement.

- [ ] **Step 1: Write `tests/test_schedule.py`**

```python
"""Tests for schedule.py — signup, removal, displacement, fill queue, serialization."""

from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from raider import Raider
from schedule import Schedule


# ---------------------------------------------------------------------------
# Schedule.__init__ — organizer signup
# ---------------------------------------------------------------------------


def test_organizer_assigned_to_correct_slot(make_raider, make_schedule):
    tank = make_raider(user_id=1, roles=["tank"])
    sched = make_schedule(raider=tank)
    assert sched.team["tank"] is tank
    assert sched.signup == 1
    assert "tank" not in sched.missing


# ---------------------------------------------------------------------------
# raider_signup
# ---------------------------------------------------------------------------


def test_signup_fills_tank_slot(make_raider, make_schedule):
    healer_org = make_raider(user_id=99, roles=["healer"])
    sched = make_schedule(raider=healer_org)
    tank = make_raider(user_id=1, roles=["tank"])
    sched.raider_signup(tank)
    assert sched.team["tank"] is tank


def test_signup_fills_healer_slot(make_raider, make_schedule):
    tank_org = make_raider(user_id=99, roles=["tank"])
    sched = make_schedule(raider=tank_org)
    healer = make_raider(user_id=2, roles=["healer"])
    sched.raider_signup(healer)
    assert sched.team["healer"] is healer


def test_signup_fills_dps_slot(make_raider, make_schedule):
    tank_org = make_raider(user_id=99, roles=["tank"])
    sched = make_schedule(raider=tank_org)
    dps = make_raider(user_id=3, roles=["dps"])
    sched.raider_signup(dps)
    assert dps in sched.team["dps"]


def test_signup_3_dps_removes_from_missing(make_raider, make_schedule):
    tank_org = make_raider(user_id=99, roles=["tank"])
    sched = make_schedule(raider=tank_org)
    for i in range(3):
        sched.raider_signup(make_raider(user_id=i + 1, roles=["dps"]))
    assert "dps" not in sched.missing


def test_signup_goes_to_fill_when_full(make_raider, make_full_schedule):
    sched = make_full_schedule()
    extra = make_raider(user_id=6, roles=["dps"])
    sched.raider_signup(extra)
    assert extra in sched.team["fill"]


def test_signup_counter_increments(make_raider, make_schedule):
    tank_org = make_raider(user_id=1, roles=["tank"])
    sched = make_schedule(raider=tank_org)
    assert sched.signup == 1

    healer = make_raider(user_id=2, roles=["healer"])
    sched.raider_signup(healer)
    assert sched.signup == 2


def test_is_filled_at_5(make_full_schedule):
    sched = make_full_schedule()
    assert sched.is_filled() is True


def test_auto_role_primary_used(make_raider, make_schedule):
    """Raider with [tank, dps] is assigned to tank when tank slot is open."""
    healer_org = make_raider(user_id=99, roles=["healer"])
    sched = make_schedule(raider=healer_org)
    multi = make_raider(user_id=2, roles=["tank", "dps"])
    sched.raider_signup(multi)
    assert sched.team["tank"] is multi


def test_auto_role_fallback_to_secondary(make_raider, make_schedule):
    """Raider with [tank, dps] is assigned to dps when tank slot is already taken."""
    tank_org = make_raider(user_id=1, roles=["tank"])
    sched = make_schedule(raider=tank_org)
    multi = make_raider(user_id=2, roles=["tank", "dps"])
    sched.raider_signup(multi)
    assert multi in sched.team["dps"]


def test_auto_role_goes_to_fill_when_both_taken(make_raider, make_schedule):
    """Raider with [tank, dps] goes to fill when both tank and dps slots are full."""
    tank_org = make_raider(user_id=1, roles=["tank"])
    sched = make_schedule(raider=tank_org)
    for i in range(3):
        sched.raider_signup(make_raider(user_id=i + 2, roles=["dps"]))
    # tank and dps both full; healer still open but multi has no healer role
    multi = make_raider(user_id=99, roles=["tank", "dps"])
    sched.raider_signup(multi)
    assert multi in sched.team["fill"]


# ---------------------------------------------------------------------------
# raider_remove
# ---------------------------------------------------------------------------


def test_remove_tank_reopens_slot(make_raider, make_schedule):
    tank = make_raider(user_id=1, roles=["tank"])
    sched = make_schedule(raider=tank)
    sched.raider_remove(tank)
    assert sched.team["tank"] is None
    assert "tank" in sched.missing
    assert tank not in sched.members


def test_remove_healer_reopens_slot(make_raider, make_schedule):
    tank_org = make_raider(user_id=99, roles=["tank"])
    sched = make_schedule(raider=tank_org)
    healer = make_raider(user_id=2, roles=["healer"])
    sched.raider_signup(healer)
    sched.raider_remove(healer)
    assert sched.team["healer"] is None
    assert "healer" in sched.missing
    assert healer not in sched.members


def test_remove_dps_reopens_slot(make_raider, make_schedule):
    tank_org = make_raider(user_id=99, roles=["tank"])
    sched = make_schedule(raider=tank_org)
    dps = make_raider(user_id=3, roles=["dps"])
    sched.raider_signup(dps)
    sched.raider_remove(dps)
    assert dps not in sched.team["dps"]
    assert "dps" in sched.missing


def test_remove_decrements_signup(make_raider, make_schedule):
    tank = make_raider(user_id=1, roles=["tank"])
    sched = make_schedule(raider=tank)
    assert sched.signup == 1
    sched.raider_remove(tank)
    assert sched.signup == 0


def test_remove_from_fill_queue(make_raider, make_full_schedule):
    extra = make_raider(user_id=6, roles=["dps"])
    sched = make_full_schedule()
    sched.raider_signup(extra)
    assert extra in sched.team["fill"]

    before_signup = sched.signup
    sched.raider_remove(extra)
    assert extra not in sched.team["fill"]
    assert sched.signup == before_signup  # fill doesn't count toward signup


def test_fill_promoted_after_remove(make_raider, make_schedule):
    tank = make_raider(user_id=1, roles=["tank"])
    healer = make_raider(user_id=2, roles=["healer"])
    dps1 = make_raider(user_id=3, roles=["dps"])
    dps2 = make_raider(user_id=4, roles=["dps"])
    dps3 = make_raider(user_id=5, roles=["dps"])
    fill = make_raider(user_id=6, roles=["dps"])

    sched = make_schedule(raider=tank)
    sched.raider_signup(healer)
    sched.raider_signup(dps1)
    sched.raider_signup(dps2)
    sched.raider_signup(dps3)
    sched.raider_signup(fill)

    assert fill in sched.team["fill"]
    sched.raider_remove(dps3)

    assert fill in sched.team["dps"]
    assert fill not in sched.team["fill"]
    assert sched.signup == 5  # 4 after remove + 1 for promoted fill


# ---------------------------------------------------------------------------
# try_displace_off_roler
# ---------------------------------------------------------------------------


def _make_sched_with_off_roler_in_slot(make_raider, make_schedule, slot, off_role_main):
    """Helper: creates a schedule where slot is occupied by an off-roler."""
    org = make_raider(user_id=99, roles=["healer"])
    sched = make_schedule(raider=org, start_time=datetime.now(timezone.utc) + timedelta(hours=24))
    off_roler = make_raider(user_id=2, roles=[off_role_main])
    sched.raider_signup(off_roler, role=slot)
    off_roler.add_run(sched)  # wire current_runs so discard works
    return sched, off_roler


def test_displaces_off_roler_tank(make_raider, make_schedule):
    sched, off_roler = _make_sched_with_off_roler_in_slot(make_raider, make_schedule, "tank", "dps")
    tank_main = make_raider(user_id=1, roles=["tank"])
    displaced = sched.try_displace_off_roler(tank_main, "tank")
    assert displaced is off_roler


def test_displaces_off_roler_healer(make_raider, make_schedule):
    org = make_raider(user_id=99, roles=["tank"])
    sched = make_schedule(raider=org, start_time=datetime.now(timezone.utc) + timedelta(hours=24))
    off_roler = make_raider(user_id=2, roles=["dps"])  # DPS main in healer slot
    sched.raider_signup(off_roler, role="healer")
    off_roler.add_run(sched)

    healer_main = make_raider(user_id=1, roles=["healer"])
    displaced = sched.try_displace_off_roler(healer_main, "healer")
    assert displaced is off_roler


def test_displaces_off_roler_dps(make_raider, make_schedule):
    """DPS slot fully occupied; one player is a tank main filling off-role."""
    org = make_raider(user_id=99, roles=["tank"])
    sched = make_schedule(raider=org, start_time=datetime.now(timezone.utc) + timedelta(hours=24))

    tank_off_roler = make_raider(user_id=2, roles=["tank"])  # tank main in dps slot
    dps2 = make_raider(user_id=3, roles=["dps"])
    dps3 = make_raider(user_id=4, roles=["dps"])
    sched.raider_signup(tank_off_roler, role="dps")
    sched.raider_signup(dps2)
    sched.raider_signup(dps3)
    tank_off_roler.add_run(sched)

    dps_main = make_raider(user_id=5, roles=["dps"])
    displaced = sched.try_displace_off_roler(dps_main, "dps")
    assert displaced is tank_off_roler


def test_no_displace_if_occupant_is_main_role(make_raider, make_schedule):
    """Tank slot held by a tank main — no displacement."""
    tank_org = make_raider(user_id=1, roles=["tank"])
    sched = make_schedule(raider=tank_org, start_time=datetime.now(timezone.utc) + timedelta(hours=24))
    tank_org.add_run(sched)

    another_tank = make_raider(user_id=2, roles=["tank"])
    displaced = sched.try_displace_off_roler(another_tank, "tank")
    assert displaced is None


def test_no_displace_if_slot_empty(make_raider, make_schedule):
    """Tank slot is already empty — nothing to displace."""
    healer_org = make_raider(user_id=99, roles=["healer"])
    sched = make_schedule(raider=healer_org, start_time=datetime.now(timezone.utc) + timedelta(hours=24))

    tank_main = make_raider(user_id=1, roles=["tank"])
    displaced = sched.try_displace_off_roler(tank_main, "tank")
    assert displaced is None


def test_no_displace_if_not_new_raiders_main_role(make_raider, make_schedule):
    """effective_role is new raider's secondary, not main — no displacement."""
    sched, _ = _make_sched_with_off_roler_in_slot(make_raider, make_schedule, "tank", "dps")
    multi = make_raider(user_id=1, roles=["dps", "tank"])  # main is dps
    # passing "tank" as effective_role, but multi.roles[0] == "dps" → mismatch
    displaced = sched.try_displace_off_roler(multi, "tank")
    assert displaced is None


def test_no_displace_within_8h(make_raider, make_schedule):
    """Schedule starting in 4 hours — displacement window has closed."""
    start_time = datetime(2026, 3, 11, 20, 0, 0, tzinfo=timezone.utc)
    with freeze_time("2026-03-11 16:00:00"):  # 4h before start
        org = make_raider(user_id=99, roles=["healer"])
        sched = make_schedule(raider=org, start_time=start_time)
        off_roler = make_raider(user_id=2, roles=["dps"])
        sched.raider_signup(off_roler, role="tank")
        off_roler.add_run(sched)

        tank_main = make_raider(user_id=1, roles=["tank"])
        displaced = sched.try_displace_off_roler(tank_main, "tank")

    assert displaced is None


def test_displace_updates_missing(make_raider, make_schedule):
    sched, _ = _make_sched_with_off_roler_in_slot(make_raider, make_schedule, "tank", "dps")
    tank_main = make_raider(user_id=1, roles=["tank"])
    sched.try_displace_off_roler(tank_main, "tank")
    assert "tank" in sched.missing


def test_displaced_raider_removed_from_current_runs(make_raider, make_schedule):
    sched, off_roler = _make_sched_with_off_roler_in_slot(make_raider, make_schedule, "tank", "dps")
    assert sched in off_roler.current_runs

    tank_main = make_raider(user_id=1, roles=["tank"])
    sched.try_displace_off_roler(tank_main, "tank")
    assert sched not in off_roler.current_runs


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_to_dict_from_dict_roundtrip(make_raider, make_schedule):
    tank = make_raider(user_id=1, roles=["tank"])
    sched = make_schedule(raider=tank, run_type="multiple", level="12")
    d = sched.to_dict(message_id=1234)

    restored = Schedule.from_dict(d, raiders_by_id={tank.user_id: tank})
    assert restored.level == sched.level
    assert restored.run_type == sched.run_type
    assert restored.signup == sched.signup
    assert restored.organizer_id == sched.organizer_id


def test_from_dict_resolves_raiders(make_raider, make_schedule):
    tank = make_raider(user_id=1, roles=["tank"])
    healer = make_raider(user_id=2, roles=["healer"])
    sched = make_schedule(raider=tank)
    sched.raider_signup(healer)

    d = sched.to_dict(message_id=1)
    raiders_by_id = {tank.user_id: tank, healer.user_id: healer}
    restored = Schedule.from_dict(d, raiders_by_id)

    assert restored.team["tank"] is tank
    assert restored.team["healer"] is healer


def test_from_dict_skips_stale_user_ids(make_raider, make_schedule):
    """Unknown user IDs in team/members are silently skipped."""
    tank = make_raider(user_id=1, roles=["tank"])
    sched = make_schedule(raider=tank)
    d = sched.to_dict(message_id=1)
    # Replace with unknown uid
    d["team"]["tank"] = 9999
    d["members"] = [9999]

    restored = Schedule.from_dict(d, raiders_by_id={tank.user_id: tank})
    assert restored.team["tank"] is None
    assert restored.members == []
```

- [ ] **Step 2: Run the tests**

```bash
pytest tests/test_schedule.py -v
```

Expected: all 29 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_schedule.py
git commit -m "test: add test_schedule.py — signup, removal, displacement, fill queue, serialization"
```

---

## Chunk 5: `test_utils.py`

### Task 6: Create `tests/test_utils.py`

**Files:**
- Create: `tests/test_utils.py`
- Reference: `utils.py`

**Key implementation note:** All tests that touch the filesystem use `monkeypatch.chdir(tmp_path)`. `save_state` and `load_state` use relative paths (`state.json`, `state.pkl`) so this redirects all reads and writes to the pytest temp directory, never touching the real project `state.json`.

- [ ] **Step 1: Write `tests/test_utils.py`**

```python
"""Tests for utils.py — save/load state, deserialization, pickle migration."""

import json
import pickle
from datetime import datetime, timedelta, timezone

import pytest

from raider import Raider
from utils import GREEN, RED, YELLOW, _deserialize, load_state, save_state


# ---------------------------------------------------------------------------
# save_state / load_state
# ---------------------------------------------------------------------------


def test_save_load_roundtrip(tmp_path, monkeypatch, make_raider, make_schedule):
    monkeypatch.chdir(tmp_path)

    raider = make_raider(user_id=1)
    sched = make_schedule(raider=raider)
    raider.add_run(sched)

    msg_id = 100
    raiders = {raider.user_id: raider}
    schedules = {msg_id: sched}
    availability = {GREEN: [raider], YELLOW: [], RED: []}

    save_state(raiders, schedules, availability, 42, {}, {})
    r_out, s_out, av_out, av_id_out, dm_map_out, dm_ts_out = load_state()

    assert 1 in r_out
    assert msg_id in s_out
    assert av_id_out == 42
    assert len(av_out[GREEN]) == 1
    assert av_out[GREEN][0].user_id == raider.user_id


def test_load_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = load_state()
    assert result == ({}, {}, {}, 0, {}, {})


def test_load_corrupt_json_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "state.json").write_text("{ not valid json }", encoding="utf-8")
    result = load_state()
    assert result == ({}, {}, {}, 0, {}, {})


def test_load_key_error_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Schedule.from_dict uses direct indexing for "date_scheduled", "full", etc.
    # Omitting those keys triggers a KeyError inside _deserialize → defaults.
    bad_data = {
        "version": 2,
        "raiders": {},
        "schedules": {"1": {"level": "10"}},  # missing "date_scheduled", "full", etc.
        "availability": {},
        "availability_message_id": 0,
        "dm_map": {},
        "dm_timestamps": {},
    }
    (tmp_path / "state.json").write_text(json.dumps(bad_data), encoding="utf-8")
    result = load_state()
    assert result == ({}, {}, {}, 0, {}, {})


def test_load_value_error_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bad_data = {
        "version": 2,
        "raiders": {},
        "schedules": {},
        "availability": {},
        "availability_message_id": 0,
        "dm_map": {},
        "dm_timestamps": {"1": {"100": "NOT_A_VALID_ISOFORMAT"}},
    }
    (tmp_path / "state.json").write_text(json.dumps(bad_data), encoding="utf-8")
    result = load_state()
    assert result == ({}, {}, {}, 0, {}, {})


def test_availability_roundtrip(tmp_path, monkeypatch, make_raider):
    monkeypatch.chdir(tmp_path)

    green_raider = make_raider(user_id=1)
    yellow_raider = make_raider(user_id=2, roles=["healer"])
    red_raider = make_raider(user_id=3, roles=["dps"])

    raiders = {1: green_raider, 2: yellow_raider, 3: red_raider}
    availability = {GREEN: [green_raider], YELLOW: [yellow_raider], RED: [red_raider]}

    save_state(raiders, {}, availability, 0, {}, {})
    _, _, av_out, _, _, _ = load_state()

    assert len(av_out[GREEN]) == 1 and av_out[GREEN][0].user_id == 1
    assert len(av_out[YELLOW]) == 1 and av_out[YELLOW][0].user_id == 2
    assert len(av_out[RED]) == 1 and av_out[RED][0].user_id == 3


def test_dm_map_roundtrip(tmp_path, monkeypatch, make_raider):
    monkeypatch.chdir(tmp_path)

    raider = make_raider(user_id=1)
    dm_map = {1: {100: 200, 101: 202}}

    save_state({1: raider}, {}, {GREEN: [], YELLOW: [], RED: []}, 0, dm_map, {})
    _, _, _, _, dm_map_out, _ = load_state()

    assert dm_map_out == dm_map


def test_dm_timestamps_roundtrip(tmp_path, monkeypatch, make_raider):
    monkeypatch.chdir(tmp_path)

    raider = make_raider(user_id=1)
    ts = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    dm_timestamps = {1: {100: ts}}

    save_state({1: raider}, {}, {GREEN: [], YELLOW: [], RED: []}, 0, {}, dm_timestamps)
    _, _, _, _, _, dm_ts_out = load_state()

    assert 1 in dm_ts_out
    assert 100 in dm_ts_out[1]
    assert dm_ts_out[1][100] == ts


# ---------------------------------------------------------------------------
# _deserialize
# ---------------------------------------------------------------------------


def test_deserialize_cross_references(make_raider, make_schedule):
    """Raider.current_runs and denied_runs are wired to the correct Schedule objects."""
    raider = make_raider(user_id=1)
    sched = make_schedule(raider=raider)
    raider.add_run(sched)

    msg_id = 100
    schedule_to_id = {id(sched): msg_id}

    data = {
        "version": 2,
        "raiders": {str(raider.user_id): raider.to_dict(schedule_to_id)},
        "schedules": {str(msg_id): sched.to_dict(msg_id)},
        "availability": {},
        "availability_message_id": 0,
        "dm_map": {},
        "dm_timestamps": {},
    }

    raiders_out, schedules_out, *_ = _deserialize(data)

    assert msg_id in schedules_out
    assert schedules_out[msg_id] in raiders_out[1].current_runs


def test_deserialize_missing_schedule_id_skipped(make_raider):
    """A stale schedule ID in raider data that has no matching schedule is silently dropped."""
    raider = make_raider(user_id=1)
    stale_mid = 9999

    data = {
        "version": 2,
        "raiders": {
            "1": {
                "user_id": 1,
                "mention": "<@1>",
                "name": "TestUser",
                "class_play": "warrior",
                "roles": ["tank"],
                "timezone": "US/Eastern",
                "current_runs": [stale_mid],
                "denied_runs": [],
            }
        },
        "schedules": {},  # stale_mid not present
        "availability": {},
        "availability_message_id": 0,
        "dm_map": {},
        "dm_timestamps": {},
    }

    raiders_out, schedules_out, *_ = _deserialize(data)

    assert 1 in raiders_out
    assert raiders_out[1].current_runs == set()


# ---------------------------------------------------------------------------
# _migrate_from_pickle
# ---------------------------------------------------------------------------


def test_migrate_from_pickle(tmp_path, monkeypatch, make_raider):
    """Valid state.pkl is loaded, re-saved as state.json, and correct state returned."""
    monkeypatch.chdir(tmp_path)

    raider = make_raider(user_id=1)
    state = {
        "raiders": {raider.user_id: raider},
        "schedules": {},
        "availability": {GREEN: [raider], YELLOW: [], RED: []},
        "availability_message_id": 7,
        "dm_map": {},
        "dm_timestamps": {},
    }
    with open("state.pkl", "wb") as f:
        pickle.dump(state, f)

    # load_state should detect state.pkl (no state.json) and migrate
    raiders_out, schedules_out, av_out, av_id_out, _, _ = load_state()

    assert raider.user_id in raiders_out
    assert av_id_out == 7
    assert (tmp_path / "state.json").exists()
```

- [ ] **Step 2: Run the tests**

```bash
pytest tests/test_utils.py -v
```

Expected: all 11 tests PASS.

- [ ] **Step 3: Run the full suite**

```bash
pytest tests/ -v
```

Expected: 60 tests PASS, 0 failures.

- [ ] **Step 4: Commit**

```bash
git add tests/test_utils.py
git commit -m "test: add test_utils.py — save/load state, deserialization, pickle migration"
```

---

## Final Verification

- [ ] **Confirm full suite passes**

```bash
pytest tests/ -v --tb=short
```

Expected output ends with `60 passed`.

- [ ] **Confirm CI workflow is syntactically valid (optional local check)**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/tests.yml'))"
```

Expected: no output (parses cleanly).
