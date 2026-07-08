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
