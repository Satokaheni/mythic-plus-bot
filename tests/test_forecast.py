"""Tests for the availability forecaster."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from forecast import Obs, observations, predict, Team, can_field_team, select_team

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


def _obs(weekday, block, age_weeks, sign):
    return Obs(1, weekday, block, age_weeks, sign)


def test_predict_no_data_returns_base_prior():
    from forecast import BASE_PRIOR
    assert predict([], 2, 9) == BASE_PRIOR


def test_predict_recent_positive_scores_high():
    p = predict([_obs(2, 9, 0.0, 1), _obs(2, 9, 0.0, 1), _obs(2, 9, 0.0, 1)], 2, 9)
    assert p > 0.7


def test_predict_negatives_pull_down():
    hi = predict([_obs(2, 9, 0.0, 1), _obs(2, 9, 0.0, 1)], 2, 9)
    lo = predict([_obs(2, 9, 0.0, 1), _obs(2, 9, 0.0, 1),
                  _obs(2, 9, 0.0, -1), _obs(2, 9, 0.0, -1)], 2, 9)
    assert lo < hi


def test_predict_recency_decay_old_positive_weaker():
    # A recent negative elsewhere on the same weekday holds the base-rate prior
    # below 1.0, so the block positive's recency actually moves the score
    # (an all-positive history would give prior=1.0 and mask the decay).
    recent = predict([_obs(2, 9, 0.0, 1), _obs(2, 10, 0.0, -1)], 2, 9)
    old = predict([_obs(2, 9, 52.0, 1), _obs(2, 10, 0.0, -1)], 2, 9)  # ~1yr old -> decayed
    assert old < recent


def test_predict_prior_from_same_weekday_when_block_thin():
    # No obs in (2, 5), but strong positives elsewhere on weekday 2 -> prior lifts it above BASE_PRIOR.
    from forecast import BASE_PRIOR
    user_obs = [_obs(2, 9, 0.0, 1), _obs(2, 10, 0.0, 1), _obs(2, 11, 0.0, 1)]
    assert predict(user_obs, 2, 5) > BASE_PRIOR


def test_select_team_picks_role_valid_max_mean():
    cands = [
        (_raider(1, ["tank"]), 0.9),
        (_raider(2, ["healer"]), 0.8),
        (_raider(3, ["dps"]), 0.7),
        (_raider(4, ["dps"]), 0.6),
        (_raider(5, ["dps"]), 0.5),
        (_raider(6, ["dps"]), 0.1),  # weakest dps should be left out
    ]
    team = select_team(cands)
    assert team is not None
    assert team.tank.user_id == 1 and team.healer.user_id == 2
    assert {r.user_id for r in team.dps} == {3, 4, 5}
    assert 0.6 < team.mean < 0.75


def test_select_team_multirole_fills_scarce_role():
    # Only raider 1 can tank; a multi-role raider must cover healer.
    cands = [
        (_raider(1, ["tank", "dps"]), 0.9),
        (_raider(2, ["healer", "dps"]), 0.8),
        (_raider(3, ["dps"]), 0.7),
        (_raider(4, ["dps"]), 0.6),
        (_raider(5, ["dps"]), 0.5),
    ]
    team = select_team(cands)
    assert team is not None
    assert team.tank.user_id == 1
    assert team.healer.user_id == 2
    assert {r.user_id for r in team.dps} == {3, 4, 5}


def test_select_team_none_when_roles_uncoverable():
    cands = [(_raider(i, ["dps"]), 0.5) for i in range(1, 6)]  # no tank/healer
    assert select_team(cands) is None


def test_can_field_team():
    ok = [_raider(1, ["tank"]), _raider(2, ["healer"]), _raider(3, ["dps"]),
          _raider(4, ["dps"]), _raider(5, ["dps"])]
    assert can_field_team(ok) is True
    assert can_field_team(ok[:4]) is False           # only 4
    assert can_field_team([_raider(i, ["dps"]) for i in range(5)]) is False  # no tank/healer
