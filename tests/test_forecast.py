"""Tests for the availability forecaster."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from forecast import Obs, observations

CST = ZoneInfo("America/Chicago")
NOW = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)


def _raider(uid, roles, tz="America/Chicago"):
    return SimpleNamespace(user_id=uid, roles=roles, timezone=ZoneInfo(tz), name=f"R{uid}")


def test_observations_single_user_events_use_stored_slot():
    events = [
        {"type": "offer_accepted", "user_id": 1, "ts_utc": "2026-07-02T01:00:00+00:00",
         "local_weekday": 2, "local_block": 9, "source": "discord", "run_id": 1},
        {"type": "offer_declined", "user_id": 1, "ts_utc": "2026-07-02T01:00:00+00:00",
         "local_weekday": 3, "local_block": 10, "source": "discord", "run_id": 2},
        {"type": "raiderio_run", "user_id": 1, "ts_utc": "2026-07-02T01:00:00+00:00",
         "local_weekday": 2, "local_block": 9, "source": "raiderio", "run_id": 3},
    ]
    obs = observations(events, {1: _raider(1, ["dps"])}, NOW)
    signs = sorted((o.weekday, o.block, o.sign) for o in obs)
    assert signs == [(2, 9, 1), (2, 9, 1), (3, 10, -1)]
    assert all(o.user_id == 1 for o in obs)
    assert all(o.age_weeks > 0 for o in obs)


def test_observations_expands_run_completed_roster_via_timezone():
    # Run at 2026-07-03 02:00 UTC. Central (UTC-5) -> 2026-07-02 21:00 (Thu=3, block 10);
    # Eastern (UTC-4) -> 2026-07-02 22:00 (Thu=3, block 11). Same instant, different local block.
    events = [{"type": "run_completed", "ts_utc": "2026-07-03T02:00:00+00:00",
               "user_id": None, "roster": [1, 2], "run_id": 9, "source": "discord",
               "local_weekday": None, "local_block": None}]
    raiders = {1: _raider(1, ["tank"]), 2: _raider(2, ["healer"], tz="America/New_York")}
    obs = observations(events, raiders, NOW)
    by_user = {o.user_id: (o.weekday, o.block, o.sign) for o in obs}
    assert by_user[1] == (3, 10, 1)   # Central: Thu 21:00 -> block 10
    assert by_user[2] == (3, 11, 1)   # Eastern: Thu 22:00 -> block 11


def test_observations_skips_null_slots_and_missing_tz():
    events = [
        {"type": "offer_accepted", "user_id": 1, "ts_utc": "2026-07-02T01:00:00+00:00",
         "local_weekday": None, "local_block": None, "source": "discord", "run_id": 1},
        {"type": "run_completed", "ts_utc": "2026-07-03T01:00:00+00:00", "roster": [99],
         "user_id": None, "run_id": 2, "source": "discord"},  # 99 not in raiders
        {"type": "avail_reaction", "user_id": 1, "ts_utc": "2026-07-02T01:00:00+00:00",
         "emoji": "🟢", "week_of": "2026-06-30", "local_weekday": 2, "local_block": 9},
    ]
    assert observations(events, {1: _raider(1, ["dps"])}, NOW) == []
