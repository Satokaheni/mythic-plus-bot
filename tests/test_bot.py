"""Tests for MyClient flows in bot.py."""

import asyncio
from types import SimpleNamespace

import pytest

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

    client = bot_module.MyClient.__new__(bot_module.MyClient)
    client.schedules = {schedule_id: schedule}
    client.availability = {bot_module.GREEN: [healer], bot_module.YELLOW: [], bot_module.RED: []}
    client.raiders = {}
    client.availability_message_id = None
    client.dm_map = {}
    client.dm_timestamps = {}

    def delete_run():
        client.schedules.pop(schedule_id, None)

    async def create_dm():
        return _FakeDM(delete_run)

    client.get_channel = lambda _id: object()
    client.get_user = lambda _id: SimpleNamespace(create_dm=create_dm)

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr(bot_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(bot_module, "save_state", lambda *args, **kwargs: None)

    asyncio.run(client.fill_remaining_spots(schedule_id))

    assert schedule_id not in client.schedules


def test_notify_promoted_dms_new_slot(bot_module, make_raider, make_full_schedule):
    sched = make_full_schedule()
    backup = make_raider(user_id=6, roles=["tank"], name="Backup")
    sched.raider_signup(backup)
    sched.raider_remove(sched.team["tank"])
    sent = []

    async def send(content):
        sent.append(content)

    client = bot_module.MyClient.__new__(bot_module.MyClient)
    client.get_user = lambda uid: SimpleNamespace(send=send) if uid == backup.user_id else None

    asyncio.run(client._notify_promoted(backup, sched))

    assert len(sent) == 1
    assert "Tank" in sent[0]
    assert f"Level {sched.level}" in sent[0]
