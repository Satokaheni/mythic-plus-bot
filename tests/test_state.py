from datetime import datetime, timezone

import utils
from state import BotState
from utils import GREEN, RED, YELLOW


def test_fresh_state_matches_load_state_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = BotState.load()
    assert (s.raiders, s.schedules, s.availability, s.availability_message_id, s.dm_map, s.dm_timestamps) == (
        {}, {}, {utils.GREEN: [], utils.YELLOW: [], utils.RED: []}, 0, {}, {})
    assert s.role_mentions == {} and s.reminder_messages == {}


def test_botstate_round_trip_is_byte_identical(tmp_path, monkeypatch, make_raider, make_schedule):
    monkeypatch.chdir(tmp_path)
    tank = make_raider(user_id=1, roles=["tank"])
    healer = make_raider(user_id=2, roles=["healer", "dps"], name="Heals")
    sched = make_schedule(raider=tank)
    tank.add_run(sched)
    sched.raider_signup(healer)
    healer.add_run(sched)
    sched.note = "bring flasks"
    utils.save_state({1: tank, 2: healer}, {555: sched}, {GREEN: [tank], YELLOW: [healer], RED: []}, 777,
                     {9: {10: 555}}, {9: {10: datetime(2026, 1, 1, tzinfo=timezone.utc)}})
    before = (tmp_path / "state.json").read_bytes()

    BotState.load().save()

    assert (tmp_path / "state.json").read_bytes() == before


def test_instances_do_not_share_containers():
    a, b = BotState(), BotState()
    a.raiders[1] = "x"
    a.reminder_messages[1] = [2]
    assert b.raiders == {} and b.reminder_messages == {}


def test_default_availability_has_pools_and_is_not_shared():
    a, b = BotState(), BotState()
    assert a.availability == {GREEN: [], YELLOW: [], RED: []}
    assert a.availability[GREEN] is not b.availability[GREEN]
