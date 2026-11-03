"""Roster handler paths (views.py / cogs/scheduling.py) for the F1-F4 roster fixes."""

import asyncio
from types import SimpleNamespace

import pytest

MSG_ID = 777


class _Response:
    def __init__(self, on_defer=None):
        self.sent = []
        self._on_defer = on_defer

    async def defer(self, **_kwargs):
        if self._on_defer:
            self._on_defer()

    async def send_message(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


class _Followup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


class _KeyMessage:
    id = MSG_ID

    def __init__(self):
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


def _interaction(user_id, on_defer=None):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        message=_KeyMessage(),
        response=_Response(on_defer),
        followup=_Followup(),
    )


async def _noop(*_args, **_kwargs):
    return None


def _bot(schedule, raiders):
    saves, promoted = [], []

    async def notify_promoted(raider, _schedule):
        promoted.append(raider)

    from mythicbot.state import BotState

    state = BotState(schedules={MSG_ID: schedule}, raiders={r.user_id: r for r in raiders})
    state.save = lambda: saves.append(1)
    bot = SimpleNamespace(
        state=state, elevated_ids=set(), message_user=_noop, notify_schedule=_noop,
        _notify_displaced=_noop, _notify_promoted=notify_promoted,
    )
    return bot, saves, promoted


def _places(schedule, raider):
    seats = [schedule.team["tank"], schedule.team["healer"], *schedule.team["dps"], *schedule.team["fill"]]
    return sum(1 for r in seats if r == raider)


@pytest.fixture
def roster(make_raider, make_schedule):
    """T posts as tank (organizer), H is a registered healer who is not yet in the run."""
    t = make_raider(user_id=1, roles=["tank"], name="T")
    schedule = make_schedule(raider=t)
    t.add_run(schedule)
    h = make_raider(user_id=2, roles=["healer"], name="H")
    return schedule, t, h


# ---------------------------------------------------------------------------
# F2: a raider seated while their Sign Up handler is suspended is not seated again
# ---------------------------------------------------------------------------


def test_f2_sign_up_resuming_after_raider_was_seated_elsewhere(roster):
    from mythicbot.scheduling.views import ScheduleButtonView

    schedule, t, h = roster

    def seated_by_dm_offer():
        schedule.raider_signup(h)
        h.add_run(schedule)

    bot, saves, _ = _bot(schedule, [t, h])
    interaction = _interaction(h.user_id, on_defer=seated_by_dm_offer)

    async def click():
        view = ScheduleButtonView(schedule, bot)
        await view.signup_button.callback(interaction)

    asyncio.run(click())

    assert _places(schedule, h) == 1
    assert schedule.members.count(h) == 1
    assert schedule.team["fill"] == []
    assert interaction.message.edits == []
    assert saves == []
    assert any("already signed up" in content for content, _ in interaction.followup.sent)


def test_f2_dm_offer_fill_accept_after_raider_was_seated_elsewhere(
    bot_module, make_raider, make_full_schedule, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)  # eventlog writes events.jsonl relative to cwd
    from mythicbot.cogs.scheduling import Scheduling

    raiders = [
        make_raider(user_id=1, roles=["tank"], name="Tank"),
        make_raider(user_id=2, roles=["healer"], name="Healer"),
        make_raider(user_id=3, roles=["dps"], name="DPS1"),
        make_raider(user_id=4, roles=["dps"], name="DPS2"),
        make_raider(user_id=5, roles=["dps"], name="DPS3"),
    ]
    schedule = make_full_schedule(raiders)
    for r in raiders:
        r.add_run(schedule)
    extra = make_raider(user_id=6, roles=["healer"], name="Extra")
    bot, _, _ = _bot(schedule, [*raiders, extra])
    dm_sent = []

    class _DM:
        id = 900

        async def send(self, content):
            dm_sent.append(content)
            return SimpleNamespace(id=901, add_reaction=_noop)

    dm = _DM()
    bot.state.dm_map = {dm.id: {902: MSG_ID}}
    bot.state.dm_timestamps = {}
    bot.user = SimpleNamespace(id=999)
    user = SimpleNamespace(id=extra.user_id)

    async def wait_for(_event, timeout=None, check=None):
        # While the fill-queue question is open, Extra joins the queue another way (e.g. a Sign Up click).
        schedule.raider_signup(extra)
        extra.add_run(schedule)
        return SimpleNamespace(emoji="✅"), user

    def get_channel(_channel_id):
        raise AssertionError("the run post must not be edited when the signup is refused")

    bot.wait_for = wait_for
    bot.get_channel = get_channel
    cog = Scheduling.__new__(Scheduling)
    cog.bot = bot
    reaction = SimpleNamespace(emoji="✅", message=SimpleNamespace(id=902, channel=dm))

    asyncio.run(cog.on_spot_offer_reaction(reaction, user))

    assert schedule.team["fill"] == [extra]
    assert schedule.members.count(extra) == 1
    assert any("already signed up" in content for content in dm_sent)


def test_f2_staff_add_of_existing_member_is_refused(roster):
    from mythicbot.scheduling.views import _apply_manage_add

    schedule, t, h = roster
    bot, saves, _ = _bot(schedule, [t, h])
    interaction = _interaction(99)
    key_msg = _KeyMessage()

    asyncio.run(_apply_manage_add(t, schedule, bot, key_msg, interaction, role="healer"))

    assert schedule.team["tank"] is t and schedule.team["healer"] is None
    assert key_msg.edits == [] and saves == []
    assert "already signed up" in interaction.response.sent[0][0]


# ---------------------------------------------------------------------------
# F3 + F4: Change Role only into open roles; a raider moved off their roles can still leave
# ---------------------------------------------------------------------------


def _change_role_target(raider, schedule, bot, key_msg):
    from mythicbot.scheduling.views import ChangeRoleTargetView

    return ChangeRoleTargetView(raider, schedule, bot, key_msg).children[0]


def test_f4_change_role_offers_only_open_roles(roster):
    schedule, t, h = roster
    schedule.raider_signup(h)
    bot, _, _ = _bot(schedule, [t, h])

    async def build():
        return [o.value for o in _change_role_target(t, schedule, bot, _KeyMessage()).options]

    assert asyncio.run(build()) == ["dps"]


def test_f4_change_role_into_role_taken_since_the_menu_opened_is_refused(roster):
    schedule, t, h = roster
    bot, saves, promoted = _bot(schedule, [t, h])
    key_msg = _KeyMessage()
    interaction = _interaction(99)

    async def pick_healer_after_it_was_taken():
        select = _change_role_target(t, schedule, bot, key_msg)
        assert "healer" in [o.value for o in select.options]
        schedule.raider_signup(h)
        h.add_run(schedule)
        select._values = ["healer"]
        await select.callback(interaction)

    asyncio.run(pick_healer_after_it_was_taken())

    assert schedule.team["tank"] is t and schedule.team["healer"] is h
    assert schedule.team["fill"] == []
    assert schedule in t.current_runs
    assert key_msg.edits == [] and saves == [] and promoted == []
    content, kwargs = interaction.response.sent[0]
    assert "no longer open" in content and kwargs.get("ephemeral")


def test_f4_change_role_with_no_open_roles_says_so(make_raider, make_full_schedule):
    from mythicbot.scheduling.views import ChangeRaiderRoleView

    schedule = make_full_schedule()
    tank = schedule.team["tank"]
    bot, _, _ = _bot(schedule, schedule.members)
    interaction = _interaction(99)

    async def pick_tank():
        select = ChangeRaiderRoleView(schedule, bot, _KeyMessage()).children[0]
        select._values = [str(tank.user_id)]
        await select.callback(interaction)

    asyncio.run(pick_tank())

    content, kwargs = interaction.response.sent[0]
    assert "no open roles" in content.lower()
    assert kwargs.get("ephemeral") and "view" not in kwargs


def test_f4_change_role_moves_raider_and_promotes_queued_player_for_freed_seat(roster, make_raider):
    schedule, t, h = roster
    backup = make_raider(user_id=3, roles=["tank"], name="Backup")
    schedule.raider_signup(backup)
    schedule.raider_signup(make_raider(user_id=4, roles=["tank"], name="Backup2"))
    backup.add_run(schedule)
    bot, saves, promoted = _bot(schedule, [t, h, backup])
    key_msg = _KeyMessage()
    interaction = _interaction(99)

    async def move_to_healer():
        select = _change_role_target(t, schedule, bot, key_msg)
        select._values = ["healer"]
        await select.callback(interaction)

    asyncio.run(move_to_healer())

    assert schedule.team["healer"] is t and schedule.team["tank"] is backup
    assert promoted == [backup]
    assert saves and key_msg.edits


def test_f3_raider_moved_outside_their_roles_can_leave(roster):
    from mythicbot.scheduling.views import ScheduleButtonView

    schedule, t, h = roster
    d_bot, _, _ = _bot(schedule, [t, h])

    async def move_then_leave():
        select = _change_role_target(t, schedule, d_bot, _KeyMessage())
        select._values = ["healer"]
        await select.callback(_interaction(99))
        assert schedule.team["healer"] is t
        view = ScheduleButtonView(schedule, d_bot)
        interaction = _interaction(t.user_id)
        interaction.message.delete = _noop
        await view.remove_button.callback(interaction)

    asyncio.run(move_then_leave())

    assert schedule.team["healer"] is None and schedule.team["tank"] is None
    assert schedule.members == []
    assert schedule not in t.current_runs


def test_welcome_signup_of_existing_member_displaces_nobody(make_raider, make_schedule, monkeypatch):
    from mythicbot.scheduling import views
    from mythicbot.scheduling.views import ScheduleButtonView

    off_roler = make_raider(user_id=1, roles=["dps"], name="Off")
    schedule = make_schedule(raider=off_roler)  # start_time is 24h out, beyond the 8h displacement cutoff
    schedule.team["dps"].clear()
    schedule.members.clear()
    schedule.signup, schedule.missing = 0, ["tank", "healer", "dps"]
    assert schedule.raider_signup(off_roler, role="tank")
    off_roler.add_run(schedule)
    old_self = make_raider(user_id=2, roles=["tank"], name="Me")
    assert schedule.raider_signup(old_self, role="dps")  # already a member, e.g. via the first of two clicks
    bot, _, _ = _bot(schedule, [off_roler])  # user 2 is not in state.raiders: the welcome branch runs
    displaced = []

    async def notify_displaced(*args):
        displaced.append(args)

    bot._notify_displaced = notify_displaced
    sent = []

    async def send(content=None, **_kwargs):
        sent.append(content)

    class _Selection:
        selected_class, selected_primary, selected_secondary, selected_timezone = "warrior", "tank", None, "US/Eastern"

        def __init__(self, timeout=None):
            pass

        async def wait(self):
            return None

    monkeypatch.setattr(views, "WoWSelectionView", _Selection)
    interaction = _interaction(2)
    interaction.user = SimpleNamespace(id=2, mention="<@2>", display_name="Me", send=send)

    async def click():
        await ScheduleButtonView(schedule, bot).signup_button.callback(interaction)

    asyncio.run(click())

    assert displaced == []
    assert schedule.team["tank"] is off_roler and off_roler in schedule.members
    assert schedule in off_roler.current_runs
    assert _places(schedule, old_self) == 1
    assert any("already signed up or unavailable" in (c or "") for c in sent)


def test_dm_offer_accept_by_existing_member_displaces_nobody(bot_module, make_raider, make_schedule, monkeypatch,
                                                             tmp_path):
    monkeypatch.chdir(tmp_path)  # eventlog writes events.jsonl relative to cwd
    from mythicbot.cogs.scheduling import Scheduling

    off_roler = make_raider(user_id=1, roles=["dps"], name="Off")
    schedule = make_schedule(raider=off_roler)  # start_time is 24h out, beyond the 8h displacement cutoff
    schedule.team["dps"].clear()
    schedule.members.clear()
    schedule.signup, schedule.missing = 0, ["tank", "healer", "dps"]
    assert schedule.raider_signup(off_roler, role="tank")
    off_roler.add_run(schedule)
    me = make_raider(user_id=2, roles=["tank"], name="Me")
    assert schedule.raider_signup(me, role="dps")  # a member whose current_runs doesn't list the run yet
    bot, _, _ = _bot(schedule, [off_roler, me])
    displaced = []

    async def notify_displaced(*args):
        displaced.append(args)

    class _DM:
        id = 900

        async def send(self, content):
            return SimpleNamespace(id=901, add_reaction=_noop)

    def get_channel(_channel_id):
        raise AssertionError("the run post must not be edited for an existing member")

    dm = _DM()
    bot.state.dm_map, bot.state.dm_timestamps = {dm.id: {902: MSG_ID}}, {}
    bot.user = SimpleNamespace(id=999)
    bot.get_channel = get_channel
    cog = Scheduling.__new__(Scheduling)
    cog.bot = bot
    cog._notify_displaced = notify_displaced
    reaction = SimpleNamespace(emoji="✅", message=SimpleNamespace(id=902, channel=dm))

    asyncio.run(cog.on_spot_offer_reaction(reaction, SimpleNamespace(id=me.user_id)))

    assert displaced == []
    assert schedule.team["tank"] is off_roler and off_roler in schedule.members
    assert _places(schedule, me) == 1


def test_change_role_that_fills_the_run_sends_the_filled_dms(roster, make_raider):
    """Moving a queued player into the last open seat fills the run, so the roster gets the 'filled' DM."""
    schedule, t, h = roster
    schedule.raider_signup(h)
    for n in (3, 4):
        schedule.raider_signup(make_raider(user_id=n, roles=["dps"], name=f"D{n}"))
    queued = make_raider(user_id=6, roles=["tank"], name="Queued")
    schedule.raider_signup(queued)
    assert schedule.team["fill"] == [queued] and not schedule.is_filled()
    bot, _, _ = _bot(schedule, [t, h, queued])
    notified = []

    async def notify_schedule(s):
        notified.append(s)

    bot.notify_schedule = notify_schedule

    async def move_to_dps():
        select = _change_role_target(queued, schedule, bot, _KeyMessage())
        select._values = ["dps"]
        await select.callback(_interaction(99))

    asyncio.run(move_to_dps())

    assert schedule.is_filled() and queued in schedule.team["dps"]
    assert notified == [schedule]


def test_change_role_between_open_seats_sends_no_filled_dms(roster):
    schedule, t, h = roster
    bot, _, _ = _bot(schedule, [t, h])
    notified = []

    async def notify_schedule(s):
        notified.append(s)

    bot.notify_schedule = notify_schedule

    async def move_to_healer():
        select = _change_role_target(t, schedule, bot, _KeyMessage())
        select._values = ["healer"]
        await select.callback(_interaction(99))

    asyncio.run(move_to_healer())

    assert schedule.team["healer"] is t and notified == []
