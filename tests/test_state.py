from datetime import datetime, timezone

from mythicbot import storage
from mythicbot.state import BotState
from mythicbot.storage import GREEN, RED, YELLOW


def test_fresh_state_matches_load_state_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = BotState.load()
    assert (s.raiders, s.schedules, s.availability, s.availability_message_id, s.dm_map, s.dm_timestamps) == (
        {}, {}, {storage.GREEN: [], storage.YELLOW: [], storage.RED: []}, 0, {}, {})
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
    storage.save_state({1: tank, 2: healer}, {555: sched}, {GREEN: [tank], YELLOW: [healer], RED: []}, 777,
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


def test_register_raider_creates_and_stores_a_new_raider():
    from types import SimpleNamespace

    s = BotState()
    user = SimpleNamespace(id=5, mention="<@5>", display_name="Five")

    raider = s.register_raider(user, "Mage", ["dps"], "US/Central")

    assert s.raiders[5] is raider and raider.roles == ["dps"] and raider.class_play == "Mage"


def test_register_raider_keeps_an_existing_record(make_raider):
    from types import SimpleNamespace

    existing = make_raider(user_id=5, roles=["tank"])
    s = BotState(raiders={5: existing})

    raider = s.register_raider(SimpleNamespace(id=5, mention="<@5>", display_name="Five"), "Mage", ["dps"], "US/Central")

    assert raider is existing and s.raiders[5] is existing and existing.roles == ["tank"]
