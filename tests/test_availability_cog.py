"""Availability cog: reaction handling persists pool changes."""

import asyncio
from types import SimpleNamespace


def test_registered_raider_reaction_persists_pool_change(fresh_import, make_raider):
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
