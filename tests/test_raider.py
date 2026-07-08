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
