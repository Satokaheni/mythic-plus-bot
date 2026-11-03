"""Tests for MyClient flows in bot.py."""

import asyncio
from types import SimpleNamespace


def _scheduling_cog(bot_module, **bot_attrs):
    from cogs.scheduling import Scheduling

    cog = Scheduling.__new__(Scheduling)
    cog.bot = SimpleNamespace(**bot_attrs)
    return cog


class _FakeMessage:
    id = 500

    async def add_reaction(self, _emoji):
        pass


class _FakeDM:
    id = 400

    def __init__(self, on_send):
        self._on_send = on_send

    async def send(self, _content):
        self._on_send()
        return _FakeMessage()


def test_run_deleted_during_dms_is_not_resurrected(bot_module, monkeypatch, make_raider, make_schedule):
    schedule_id = 123
    schedule = make_schedule(raider=make_raider(user_id=1, roles=["tank"]))
    healer = make_raider(user_id=2, roles=["healer"])

    from state import BotState

    state = BotState(raiders={}, schedules={schedule_id: schedule},
                            availability={bot_module.GREEN: [healer], bot_module.YELLOW: [], bot_module.RED: []},
                            availability_message_id=None, dm_map={}, dm_timestamps={})
    state.save = lambda: None

    def delete_run():
        state.schedules.pop(schedule_id, None)

    async def create_dm():
        return _FakeDM(delete_run)

    cog = _scheduling_cog(
        bot_module,
        state=state,
        get_channel=lambda _id: object(),
        get_user=lambda _id: SimpleNamespace(create_dm=create_dm),
    )

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr("cogs.scheduling.asyncio.sleep", no_sleep)

    asyncio.run(cog.fill_remaining_spots(schedule_id))

    assert schedule_id not in state.schedules


def test_notify_promoted_dms_new_slot(bot_module, make_raider, make_full_schedule):
    sched = make_full_schedule()
    backup = make_raider(user_id=6, roles=["tank"], name="Backup")
    sched.raider_signup(backup)
    sched.raider_remove(sched.team["tank"])
    sent = []

    async def send(content):
        sent.append(content)

    cog = _scheduling_cog(
        bot_module, get_user=lambda uid: SimpleNamespace(send=send) if uid == backup.user_id else None
    )

    asyncio.run(cog._notify_promoted(backup, sched))

    assert len(sent) == 1
    assert "Tank" in sent[0]
    assert f"Level {sched.level}" in sent[0]
