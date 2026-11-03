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


def test_remove_clears_raider_current_runs(make_raider, make_schedule):
    tank = make_raider(user_id=1, roles=["tank"])
    sched = make_schedule(raider=tank)
    tank.add_run(sched)
    assert sched in tank.current_runs
    sched.raider_remove(tank)
    assert sched not in tank.current_runs


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


# ---------------------------------------------------------------------------
# Fill queue promotion
# ---------------------------------------------------------------------------


def test_fill_promoted_to_tank_after_tank_leaves(make_raider, make_full_schedule):
    sched = make_full_schedule()
    tank = sched.team["tank"]
    backup = make_raider(user_id=6, roles=["tank"])
    sched.raider_signup(backup)

    sched.raider_remove(tank)

    assert sched.team["tank"] is backup
    assert backup not in sched.team["fill"]
    assert "tank" not in sched.missing
    assert sched.signup == 5
    assert sched.is_filled()


def test_fill_promoted_to_healer_after_healer_leaves(make_raider, make_full_schedule):
    sched = make_full_schedule()
    healer = sched.team["healer"]
    backup = make_raider(user_id=6, roles=["healer"])
    sched.raider_signup(backup)

    sched.raider_remove(healer)

    assert sched.team["healer"] is backup
    assert "healer" not in sched.missing


def test_dps_promotion_clears_missing(make_raider, make_full_schedule):
    sched = make_full_schedule()
    backup = make_raider(user_id=6, roles=["dps"])
    sched.raider_signup(backup)

    sched.raider_remove(sched.team["dps"][0])

    assert backup in sched.team["dps"]
    assert "dps" not in sched.missing


def test_multi_role_fill_takes_only_one_slot(make_raider, make_schedule):
    sched = make_schedule(raider=make_raider(user_id=1, roles=["healer"]))
    flex = make_raider(user_id=6, roles=["tank", "dps"])
    sched.team["fill"].append(flex)

    sched._check_fill("tank")

    assert sched.team["tank"] is flex
    assert flex not in sched.team["dps"]
    assert "tank" not in sched.missing
    assert sched.signup == 2


def test_raider_remove_returns_promoted_raider(make_raider, make_full_schedule):
    sched = make_full_schedule()
    backup = make_raider(user_id=6, roles=["healer"])
    sched.raider_signup(backup)

    assert sched.raider_remove(sched.team["healer"]) is backup


def test_raider_remove_returns_none_without_promotion(make_full_schedule):
    sched = make_full_schedule()

    assert sched.raider_remove(sched.team["healer"]) is None


# ---------------------------------------------------------------------------
# Roster fixes F1-F4 (counterexamples from .claude/skills/verify-schedule-roster)
# ---------------------------------------------------------------------------


def _seat_holders(sched):
    seats = [sched.team["tank"], sched.team["healer"], *sched.team["dps"], *sched.team["fill"]]
    return [r for r in seats if r is not None]


def test_f1_removal_promotes_only_into_the_freed_seat(make_raider, make_schedule):
    """T posts tank; H healer; DT picks Tank (queued); HD picks Healer (queued); H leaves."""
    t = make_raider(user_id=1, roles=["tank"], name="T")
    sched = make_schedule(raider=t)
    h = make_raider(user_id=2, roles=["healer"], name="H")
    dt = make_raider(user_id=3, roles=["dps", "tank"], name="DT")
    hd = make_raider(user_id=4, roles=["healer", "dps"], name="HD")
    sched.raider_signup(h)
    sched.raider_signup(dt, "tank")
    sched.raider_signup(hd, "healer")
    assert sched.team["fill"] == [dt, hd]

    promoted = sched.raider_remove(h)

    assert promoted is hd
    assert sched.team["healer"] is hd
    assert sched.team["fill"] == [dt]
    assert sched.team["dps"] == []
    assert "healer" not in sched.missing
    assert sched.signup == 2


def test_f1_freed_dps_seat_skips_queued_raider_who_cannot_play_dps(make_raider, make_full_schedule):
    sched = make_full_schedule()
    tank_backup = make_raider(user_id=6, roles=["tank"])
    dps_backup = make_raider(user_id=7, roles=["dps"])
    sched.raider_signup(tank_backup)
    sched.raider_signup(dps_backup)

    promoted = sched.raider_remove(sched.team["dps"][0])

    assert promoted is dps_backup
    assert sched.team["fill"] == [tank_backup]
    assert sched.is_filled()


def test_f1_removing_a_fill_raider_promotes_no_one(make_raider, make_schedule):
    sched = make_schedule(raider=make_raider(user_id=1, roles=["tank"]))
    queued = make_raider(user_id=2, roles=["tank"])
    waiting = make_raider(user_id=3, roles=["dps", "tank"])
    sched.raider_signup(queued)
    sched.raider_signup(waiting, "tank")

    assert sched.raider_remove(queued) is None
    assert sched.team["fill"] == [waiting]
    assert sched.team["dps"] == []


def test_f2_signup_returns_true_on_success(make_raider, make_schedule):
    sched = make_schedule(raider=make_raider(user_id=1, roles=["tank"]))
    assert sched.raider_signup(make_raider(user_id=2, roles=["healer"])) is True


def test_f2_second_signup_of_same_raider_is_refused_and_mutates_nothing(make_raider, make_schedule):
    sched = make_schedule(raider=make_raider(user_id=1, roles=["tank"]))
    h = make_raider(user_id=2, roles=["healer"])
    sched.raider_signup(h)
    before = (dict(sched.team, dps=list(sched.team["dps"]), fill=list(sched.team["fill"])),
              list(sched.members), list(sched.missing), sched.signup, sched.full)

    assert sched.raider_signup(h) is False
    assert sched.raider_signup(h, "dps") is False

    after = (dict(sched.team, dps=list(sched.team["dps"]), fill=list(sched.team["fill"])),
             list(sched.members), list(sched.missing), sched.signup, sched.full)
    assert after == before
    assert _seat_holders(sched).count(h) == 1


def test_f2_try_signup_refuses_existing_member(make_raider, make_schedule):
    tank = make_raider(user_id=1, roles=["tank"])
    sched = make_schedule(raider=tank)
    assert sched.try_signup(tank) is False
    assert sched.try_signup(make_raider(user_id=2, roles=["healer"])) is True


def test_f3_remove_frees_seat_outside_registered_roles(make_raider, make_schedule):
    """Tank-only T is moved to healer by the staff path, then leaves."""
    t = make_raider(user_id=1, roles=["tank"], name="T")
    sched = make_schedule(raider=t)
    t.add_run(sched)
    d = make_raider(user_id=2, roles=["dps"], name="D")
    sched.raider_signup(d)
    d.add_run(sched)
    sched.raider_remove(t)
    sched.raider_signup(t, role="healer")
    t.add_run(sched)
    assert sched.team["healer"] is t

    sched.raider_remove(t)

    assert sched.team["healer"] is None
    assert sched.team["tank"] is None
    assert "healer" in sched.missing
    assert t not in _seat_holders(sched)
    assert sched.members == [d]
    assert sched not in t.current_runs
    assert sched.signup == 1


def test_f3_remove_frees_dps_seat_for_raider_without_dps_role(make_raider, make_schedule):
    sched = make_schedule(raider=make_raider(user_id=1, roles=["tank"]))
    healer = make_raider(user_id=2, roles=["healer"])
    sched.raider_signup(healer, role="dps")
    backup = make_raider(user_id=3, roles=["dps"])
    sched.team["fill"].append(backup)
    sched.members.append(backup)

    assert sched.raider_remove(healer) is backup
    assert sched.team["dps"] == [backup]
    assert healer not in sched.members
