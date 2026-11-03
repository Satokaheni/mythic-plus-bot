"""Availability cog: reaction handling persists pool changes."""

import asyncio
from types import SimpleNamespace


def test_registered_raider_reaction_persists_pool_change(fresh_import, make_raider, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # eventlog writes events.jsonl relative to cwd
    config = fresh_import("config")
    from cogs.availability import Availability
    from state import BotState
    from utils import GREEN, RED, YELLOW

    raider = make_raider(user_id=5)
    state = BotState(
        raiders={5: raider},
        availability={GREEN: [raider], YELLOW: [], RED: []},
        availability_message_id=4242,
    )
    saves = []
    state.save = lambda: saves.append(1)

    async def _noop(*args, **kwargs):
        return None

    cog = Availability.__new__(Availability)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999), state=state, new_availability_signup_fill_schedule=_noop)
    reaction = SimpleNamespace(
        emoji=RED, message=SimpleNamespace(id=4242, channel=SimpleNamespace(id=config.AVAIL_CHANNEL_ID))
    )
    user = SimpleNamespace(id=5, display_name="TestUser")

    asyncio.run(cog.on_availability_reaction(reaction, user))

    assert state.availability[GREEN] == [] and state.availability[RED] == [raider]
    assert saves


class _AvailChannel:
    def __init__(self):
        self.reactions = []

    async def send(self, _content):
        channel = self

        class _Msg:
            id = 9090

            async def add_reaction(self, emoji):
                channel.reactions.append(emoji)

        return _Msg()


def test_posting_availability_saves_the_new_message_id(fresh_import):
    config = fresh_import("config")
    from cogs.availability import Availability
    from state import BotState

    state = BotState()
    saved_ids = []
    state.save = lambda: saved_ids.append(state.availability_message_id)
    channel = _AvailChannel()
    guild = SimpleNamespace(get_role=lambda _id: SimpleNamespace(mention="<@&9>"))

    cog = Availability.__new__(Availability)
    cog.bot = SimpleNamespace(
        state=state,
        get_channel=lambda cid: channel if cid == config.AVAIL_CHANNEL_ID else None,
        get_guild=lambda _id: guild,
    )

    asyncio.run(cog._post_avail_message())

    assert state.availability_message_id == 9090
    assert 9090 in saved_ids


def test_registration_finishing_after_another_registration_keeps_the_first_record(
    fresh_import, make_raider, make_schedule, tmp_path, monkeypatch
):
    """The class/role form waits minutes; if the user registered another way meanwhile, keep that record."""
    monkeypatch.chdir(tmp_path)
    config = fresh_import("config")
    import cogs.availability as availability_mod
    from state import BotState
    from utils import GREEN, RED, YELLOW

    first = make_raider(user_id=5, roles=["tank"], name="TestUser")
    seated_in = make_schedule(raider=first)
    first.add_run(seated_in)
    state = BotState(availability={GREEN: [], YELLOW: [], RED: []}, availability_message_id=4242)
    state.save = lambda: None

    class _FormFinishedAfterOtherRegistration:
        def __init__(self, **_kwargs):
            self.selected_class, self.selected_primary = "Mage", "dps"
            self.selected_secondary, self.selected_timezone = None, "US/Pacific"

        async def wait(self):
            state.raiders[5] = first  # e.g. the Sign Up welcome form completed while this one was open

    monkeypatch.setattr(availability_mod, "WoWSelectionView", _FormFinishedAfterOtherRegistration)

    async def _noop(*args, **kwargs):
        return None

    cog = availability_mod.Availability.__new__(availability_mod.Availability)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999), state=state, new_availability_signup_fill_schedule=_noop)
    reaction = SimpleNamespace(
        emoji=GREEN, message=SimpleNamespace(id=4242, channel=SimpleNamespace(id=config.AVAIL_CHANNEL_ID))
    )
    user = SimpleNamespace(id=5, display_name="TestUser", mention="<@5>", send=_noop)

    asyncio.run(cog.on_availability_reaction(reaction, user))

    assert state.raiders[5] is first
    assert first.roles == ["tank"] and seated_in in first.current_runs
    assert state.availability[GREEN] == [first]
