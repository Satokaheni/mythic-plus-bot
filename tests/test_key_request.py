"""Key request: finding an already-posted run at the requested time."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def _tomorrow_at(tz_name, hour):
    tz = ZoneInfo(tz_name)
    day = (datetime.now(tz) + timedelta(days=1)).date()
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=tz)


def test_finds_run_posted_by_someone_in_another_timezone(fresh_import, make_raider, make_schedule):
    fresh_import("config")
    from cogs.scheduling import _run_at

    central = make_raider(user_id=1, roles=["tank"], tz="US/Central")
    existing = make_schedule(raider=central, start_time=_tomorrow_at("US/Central", 20))
    request = _tomorrow_at("US/Eastern", 21)  # 9 PM Eastern == 8 PM Central

    assert _run_at({555: existing}, request) == (555, existing)


def test_same_wall_clock_in_another_timezone_is_a_different_run(fresh_import, make_raider, make_schedule):
    fresh_import("config")
    from cogs.scheduling import _run_at

    central = make_raider(user_id=1, roles=["tank"], tz="US/Central")
    existing = make_schedule(raider=central, start_time=_tomorrow_at("US/Central", 20))

    assert _run_at({555: existing}, _tomorrow_at("US/Eastern", 20)) == (None, None)


def test_matches_utc_instant_regardless_of_representation(fresh_import, make_raider, make_schedule):
    fresh_import("config")
    from cogs.scheduling import _run_at

    start = _tomorrow_at("US/Pacific", 18)
    existing = make_schedule(raider=make_raider(tz="US/Pacific"), start_time=start)

    assert _run_at({1: existing}, start.astimezone(timezone.utc)) == (1, existing)
