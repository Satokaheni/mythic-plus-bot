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
