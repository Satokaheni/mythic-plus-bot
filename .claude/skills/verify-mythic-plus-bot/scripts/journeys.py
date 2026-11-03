"""Named user journeys, one per mapped feature (see ../features/). Each is `async def j(h: Harness)`.

Journeys act only through what a Discord user can do: type a message, react, click a button,
pick from a select, fill a modal. They check what the user would see AND the side effects
(state files, event log).
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

GREEN, YELLOW, RED = "🟢", "🟡", "🔴"


# ---------------------------------------------------------------------------
# Reusable steps
# ---------------------------------------------------------------------------


async def post_availability(h):
    """Coordinator types !avail in #availability; returns the availability message."""
    cmd = await h.say(h.coordinator, "!avail", "avail")
    msg = await h.wait_for("avail", "availability", predicate=lambda m: len(m.reactions) == 3)
    h.check("!avail posts the availability message with 🟢🟡🔴", all(e in msg.reactions for e in (GREEN, YELLOW, RED)),
            list(msg.reactions))
    h.check("!avail command message is deleted", cmd.deleted)
    return msg


async def fill_class_form(h, user, form, *, klass="Mage", primary="dps", secondary="none", tz="US/Central"):
    """Complete the WoWSelectionView DM (class, primary, secondary, timezone, Submit)."""
    await h.choose(user, form, "Choose your World of Warcraft class", klass)
    await h.choose(user, form, "Choose your primary role", primary)
    await h.choose(user, form, "Choose your secondary role (optional)", secondary)
    await h.choose(user, form, "Choose your US timezone", tz)
    await h.click(user, form, "Submit")
    await h.wait_for(user, "Your Selection")


async def register_via_availability(h, user, avail_msg, *, emoji=GREEN, **profile):
    """An unregistered user reacts on the availability post and completes the class/role DM."""
    await h.react(user, avail_msg, emoji)
    form = await h.wait_for(user, "World of Warcraft class")
    await fill_class_form(h, user, form, **profile)


async def signup_new_raider(h, user, schedule_msg, **profile):
    """An unregistered user clicks Sign Up: ephemeral notice, DM form, then auto-signup."""
    await h.click(user, schedule_msg, "Sign Up")
    note = await h.wait_for("key", "not registered yet", predicate=lambda m: m.ephemeral_to == user.id)
    h.check(f"{user.name}: unregistered Sign Up is answered ephemerally", bool(note))
    form = await h.wait_for(user, "Welcome!")
    await fill_class_form(h, user, form, **profile)
    await h.wait_for(user, "Registration complete")


async def create_key_request(h, organizer, *, level="10", time_label="8:00 PM", note="verify harness run",
                             register=None):
    """Organizer clicks the pinned key-request button and walks the DM flow to a posted schedule.

    register: profile dict when the organizer is not registered yet (the flow asks for it first).
    """
    pinned = [m for m in h.key.messages() if m.pinned and m.components]
    h.check("key-request button is pinned in #keys", len(pinned) == 1, len(pinned))
    await h.click(organizer, pinned[0], "CLICK TO CREATE A REQUEST")
    ack = await h.wait_for("key", "Check your DMs")
    h.check("button click acknowledged ephemerally to the organizer", ack.ephemeral_to == organizer.id)

    if register is not None:
        reg = await h.wait_for(organizer, "Before scheduling a key")
        await fill_class_form(h, organizer, reg, **register)

    form = await h.wait_for(organizer, "Request a key")
    tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()
    await h.choose(organizer, form, "Choose your level", level)
    await h.choose(organizer, form, "Choose the day", tomorrow)
    await h.choose(organizer, form, "Choose start time", time_label)
    await h.choose(organizer, form, "Do you want to run one key or multiple?", "One Key")
    await h.click(organizer, form, "Submit Key Request")

    confirm = await h.wait_for(organizer, "add a note")
    if note:
        await h.click(organizer, confirm, "Yes, Add Note")
        await h.submit_modal(organizer, {"Note": note})
    else:
        await h.click(organizer, confirm, "No, Continue")

    prepost = await h.wait_for(organizer, "Would you like to add any registered raiders")
    await h.click(organizer, prepost, "Post Schedule")
    posted = await h.wait_for("key", "Scheduled Mythic+ Run")
    done = await h.wait_for(organizer, "Schedule posted to the key channel")
    h.check("pre-post DM is edited to confirm and its buttons removed", not done.components, done.components)
    return posted, tomorrow


# ---------------------------------------------------------------------------
# Journeys
# ---------------------------------------------------------------------------


async def help_command(h):
    """!help in #keys shows every command category."""
    anyone = h.user("Curious")
    await h.say(anyone, "!help", "key")
    msg = await h.wait_for("key", "Mythic+ Bot — Commands")
    names = [f.name for f in msg.embed.fields]
    for category in ("Scheduling", "Availability", "Gear Audit", "Price Watch", "WoW Token", "Auction Sniper", "Help"):
        h.check(f"help lists '{category}'", any(category in n for n in names), names)
    await h.say(anyone, "!tools", "dm")
    h.check("!tools alias answers in DM", bool(await h.wait_for(anyone, "Mythic+ Bot — Commands")))


async def availability_signup(h):
    """Coordinator posts availability; a new user reacts 🟢 and registers through the DM form."""
    avail = await post_availability(h)
    alice = h.user("Alice")
    await register_via_availability(h, alice, avail, klass="Priest", primary="healer", secondary="dps",
                                    tz="US/Pacific")
    state = h.state_file()
    raider = (state or {}).get("raiders", {}).get(str(alice.id))
    h.check("state.json registers Alice", raider is not None, state and list(state.get("raiders", {})))
    if raider:
        h.check("Alice's class/roles/timezone persisted",
                (raider["class_play"], raider["roles"], raider["timezone"]) == ("priest", ["healer", "dps"],
                                                                               "US/Pacific"), raider)
    h.check("Alice is in the 🟢 pool", alice.id in (state or {}).get("availability", {}).get(GREEN, []),
            (state or {}).get("availability"))
    h.check("availability_message_id persisted", (state or {}).get("availability_message_id") == avail.id)
    events = h.state_file("events.jsonl") or []
    h.check("avail_reaction event logged for Alice",
            any(e.get("type") == "avail_reaction" and e.get("user_id") == alice.id and e.get("emoji") == GREEN
                for e in events), events)

    # Switching to 🔴 moves her between pools rather than duplicating her.
    await h.react(alice, avail, RED)
    pools = (h.state_file() or {}).get("availability", {})
    h.check("re-reacting 🔴 moves Alice out of 🟢 into 🔴",
            alice.id in pools.get(RED, []) and alice.id not in pools.get(GREEN, []), pools)


async def key_request(h):
    """A new tank registers inside the key-request flow and posts a run from the pinned button."""
    tank = h.user("Tanky")
    posted, day = await create_key_request(h, tank, note="verify harness run",
                                           register={"klass": "Warrior", "primary": "tank"})
    h.check("schedule embed shows level 10", (posted.embed_field("Key Level") or "") == "**10**",
            posted.embed_field("Key Level"))
    h.check("organizer holds the tank slot", tank.mention in (posted.embed_field("Tank") or ""),
            posted.embed_field("Tank"))
    h.check("schedule shows Sign Up / Remove / Manage",
            [c["label"] for c in posted.components] == ["Sign Up", "Remove", "Manage"], posted.components)
    h.check("role pings in content for missing roles", "<@&" in posted.content, posted.content)
    state = h.state_file()
    sched = state["schedules"].get(str(posted.id))
    h.check("state.json keys the schedule by its message id", sched is not None, list(state["schedules"]))
    if sched:
        h.check("schedule date and note persisted",
                str(sched.get("date_scheduled")).startswith(day) and sched.get("note") == "verify harness run",
                {k: sched.get(k) for k in ("date_scheduled", "note")})
    h.check("organizer's current_runs includes the schedule",
            posted.id in state["raiders"][str(tank.id)]["current_runs"], state["raiders"][str(tank.id)])


async def signup_fill_restart(h):
    """Run posted, bot restarts, four raiders sign up via the old message's button; run fills; all DMed."""
    tank = h.user("Tanky")
    posted, _ = await create_key_request(h, tank, note=None, register={"klass": "Warrior", "primary": "tank"})

    await h.restart()
    h.check("schedule survives restart in state", str(posted.id) in h.state_file()["schedules"])

    # Multi-role raider: the welcome flow signs them up on their primary (dps); they leave and rejoin,
    # and the rejoin must ask which role.
    flex = h.user("Flex")
    await signup_new_raider(h, flex, posted, klass="Druid", primary="dps", secondary="healer")
    dps_field = h.key._messages[posted.id].embed_field("DPS") or ""
    h.check("welcome flow signs Flex up as primary dps", flex.mention in dps_field, dps_field)
    await h.click(flex, posted, "Remove")
    await h.wait_for("key", "removed from this run", predicate=lambda m: m.ephemeral_to == flex.id)
    await h.click(flex, posted, "Sign Up")
    pick = await h.wait_for("key", "multiple roles", predicate=lambda m: m.ephemeral_to == flex.id)
    await h.choose(flex, pick, "Choose the role to sign up as", "Healer")
    await h.wait_for("key", "signed up as **Healer**")
    healer_field = h.key._messages[posted.id].embed_field("Healer") or ""
    h.check("multi-role raider placed in the role they picked", flex.mention in healer_field, healer_field)

    dps = [h.user(n) for n in ("Pew", "Zap", "Stab")]
    for d in dps:
        await signup_new_raider(h, d, posted, klass="Mage", primary="dps")

    final = h.key._messages[posted.id]
    h.check("embed status is FULL after the fifth signup", "FULL" in (final.embed_field("Status") or ""),
            final.embed_field("Status"))
    h.check("role pings cleared once full", not final.content, final.content)
    for member in [tank, flex] + dps:
        filled = [m for m in h.bot_messages(member) if "now **filled**" in m.text()]
        h.check(f"{member.name} got the 'now filled' DM with the roster",
                len(filled) == 1 and "Tanky" in filled[0].text(), [m.text()[:60] for m in h.bot_messages(member)])
    team = h.state_file()["schedules"][str(posted.id)]["team"]
    h.check("persisted team is full",
            team.get("tank") == tank.id and team.get("healer") == flex.id and len(team.get("dps", [])) == 3, team)

    # A sixth raider joining a full run lands in the fill queue, not a slot.
    late = h.user("Late")
    await signup_new_raider(h, late, posted, klass="Rogue", primary="dps")
    queue = h.key._messages[posted.id].embed_field("Fill Queue") or ""
    h.check("sixth signup goes to the Fill Queue", late.mention in queue, queue)


async def snipe(h):
    """Anyone adds, lists and removes an auction snipe from DMs."""
    hunter = h.user("Hunter")
    await h.say(hunter, "!snipe 215147 5000 Vibrant Shard", "dm")
    reply = await h.wait_for(hunter, "Sniping")
    h.check("snipe confirmed with label and price", "Vibrant Shard" in reply.text() and "5,000" in reply.text(),
            reply.text())
    snipes = h.state_file("snipes.json") or {}
    h.check("snipes.json records Hunter's target", str(hunter.id) in json_dump(snipes), snipes)
    await h.say(hunter, "!snipes", "dm")
    listing = await h.wait_for(hunter, "Your snipes")
    h.check("!snipes lists the item", "Vibrant Shard" in listing.text(), listing.text())
    await h.say(hunter, "!snipe 1 notgold", "dm")
    h.check("bad price is rejected with guidance", bool(await h.wait_for(hunter, "positive number of gold")))
    await h.say(hunter, "!unsnipe 215147", "dm")
    h.check("!unsnipe confirms", bool(await h.wait_for(hunter, "Stopped sniping")))
    h.check("snipes.json no longer has Hunter", str(hunter.id) not in json_dump(h.state_file("snipes.json")))


async def banker_token(h):
    """Banker sets/inspects the WoW Token alert; a non-banker is ignored."""
    cmd = await h.say(h.banker, "!tokenalert 300,000", "key")
    reply = await h.wait_for(h.banker, "WoW Token alert set")
    h.check("banker's command is deleted from #keys", cmd.deleted)
    h.check("alert DM names the threshold", "300,000" in reply.text(), reply.text())
    tw = h.state_file("token_watch.json") or {}
    h.check("token_watch.json threshold is 300,000g in copper",
            tw.get("token_watch", {}).get("threshold") == 300_000 * 10_000, tw)
    await h.say(h.banker, "!token", "dm")
    status = await h.wait_for(h.banker, "Threshold")
    h.check("!token shows threshold and armed state", "armed" in status.text(), status.text())

    intruder = h.user("NotTheBanker")
    before = len(h.world.events)
    await h.say(intruder, "!tokenalert 5", "key")
    replies = [e for e in h.world.events[before:] if e["kind"] in ("send", "delete")]
    h.check("non-banker !tokenalert gets no reply and changes nothing",
            not replies and h.state_file("token_watch.json") == tw, replies)
    await h.say(h.banker, "!tokenalert off", "dm")
    await h.wait_for(h.banker, "alerts disabled")
    h.check("!tokenalert off clears the threshold",
            (h.state_file("token_watch.json") or {}).get("token_watch", {}).get("threshold", "missing") is None)


BOT_ACTIONS = ("send", "edit", "delete", "bot_react", "pin", "unpin", "interaction_response", "followup", "rejected")


async def _assert_silent(h, user, text, where, label):
    before = len(h.world.events)
    msg = await h.say(user, text, where)
    acts = [e for e in h.world.events[before:] if e["kind"] in BOT_ACTIONS or e["kind"] == "log_error"]
    h.check(f"silent: {label}", not acts and not msg.deleted, acts)


async def command_gates(h):
    """Who gets silence, what gets deleted, and where gated commands answer."""
    nobody = h.user("Nobody")
    await _assert_silent(h, nobody, "!watch 123", "key", "non-banker !watch")
    await _assert_silent(h, nobody, "!watches", "dm", "non-banker !watches")
    await _assert_silent(h, nobody, "!tokenalert 5", "dm", "non-banker !tokenalert")
    await _assert_silent(h, nobody, "!cleanup", "key", "non-elevated !cleanup")
    await _assert_silent(h, nobody, "!setup", "key", "non-elevated !setup")
    await _assert_silent(h, nobody, "!avail", "avail", "non-elevated !avail in #availability")
    await _assert_silent(h, nobody, "!gearaudit", "dm", "non-elevated !gearaudit")
    await _assert_silent(h, nobody, "!key", "dm", "!key in a DM")
    await _assert_silent(h, nobody, "!key", "avail", "!key in #availability")
    await _assert_silent(h, nobody, "!keys", "key", "!keys from an unregistered user")
    await _assert_silent(h, nobody, "!foo", "key", "unknown command")
    await _assert_silent(h, nobody, "hello there", "key", "plain chat in #keys")
    await _assert_silent(h, nobody, "what runs are up this week?", "dm", "plain chat in a DM")

    avail_in_keys = await h.say(nobody, "!avail", "key")
    h.check("!avail typed in #keys is deleted", avail_in_keys.deleted)

    await h.say(h.coordinator, "!cleanup", "dm")
    reply = await h.wait_for(h.coordinator, "must be used in the availability or key channel")
    h.check("elevated !cleanup outside the channels gets a DM explaining where", bool(reply))

    errors = [e for e in h.world.events if e["kind"] == "log_error"]
    h.check("no errors logged", not errors, errors)


def _acts(h, before):
    kinds = ("send", "edit", "delete", "interaction_response", "followup", "log_error")
    return [e for e in h.world.events[before:] if e["kind"] in kinds]


def _claude_calls(h):
    path = h.data_dir / "assistant-work" / "fake_claude_calls.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


async def assistant(h):
    owner, other, stranger, bot = h.banker, h.coordinator, h.user("Stranger"), h.mention()

    await h.say(owner, "what is the best mage spec?", "dm")
    h.check("1:1 DM without a mention is answered", bool(await h.wait_for(owner, "fake answer: what is the best mage spec?")))

    before, calls = len(h.world.events), len(_claude_calls(h))
    await h.say(owner, "what is the best mage spec?", "key")
    await h.say(owner, "@everyone hello", "key")
    await h.say(stranger, "hello", "dm")
    await h.say(stranger, f"{bot} hello", "key")
    h.check("unaddressed, @everyone, and non-allowlisted messages: no reply and no claude call",
            not _acts(h, before) and len(_claude_calls(h)) == calls, _acts(h, before))

    await h.say(owner, f"{bot} how do keys work?", "key")
    h.check("#keys @mention is answered", bool(await h.wait_for("key", "fake answer: how do keys work?")))
    await h.say(owner, bot, "key")
    h.check("empty mention asks what to do", bool(await h.wait_for("key", "What can I help with?")))

    before = len(h.world.events)
    await h.say(owner, "!snipes", "dm")
    h.check("typed !command gets exactly one reply (assistant stays out)",
            len([e for e in h.world.events[before:] if e["kind"] == "send"]) == 1)

    await h.say(owner, "please snipe 215147 for me under 5000", "dm")
    ask = await h.wait_for(owner, "I'll run `!snipe 215147 5000 Vibrant Shard`")
    await h.click(owner, ask, "Confirm")
    await h.wait_for(owner, "Sniping")
    h.check("confirmed write ran through the real command", str(owner.id) in json.dumps(h.state_file("snipes.json")))

    snapshot = h.state_file("snipes.json")
    await h.say(owner, "unsnipe 215147", "dm")
    ask = await h.wait_for(owner, "I'll run `!unsnipe 215147`")
    await h.click(owner, ask, "Cancel")
    await h.wait_for(owner, "Cancelled")
    h.check("cancelled write changes nothing", h.state_file("snipes.json") == snapshot)

    before = len(h.world.events)
    await h.say(owner, "show my snipes", "dm")
    await h.wait_for(owner, "Your snipes")
    h.check("read command runs without a confirm prompt",
            not any("I'll run" in (e.get("content") or "") for e in h.world.events[before:]))

    await h.say(other, "watch item 241326", "dm")
    ask = await h.wait_for(other, "I'll run `!watch 241326`")
    await h.click(other, ask, "Confirm")
    await h.wait_for(other, "don't have access to `!watch`")
    h.check("gates follow the Discord author, not Claude", h.state_file("watches.json") in (None, {}, []))

    await h.say(owner, "make a run thursday", "dm")
    h.check("key request opens the DM flow", bool(await h.wait_for(owner, "Before scheduling a key")))

    for text in ("evil", "inject", "fake:badjson", "fake:error", "fake:crash", "fake:timeout"):
        await h.say(other, text, "dm")
        h.check(f"{text}: one failure reply", bool(await h.wait_for(other, "couldn't work that out", timeout=15)))

    calls = _claude_calls(h)
    secrets = {"CLIENT_KEY", "BLIZZ_CLIENT_SECRET", "BLIZZ_CLIENT_ID", "UNDERMINE_API_KEY", "RAIDERIO_API_KEY"}
    h.check("no claude call saw a bot secret", all(not (secrets & set(c["env_keys"])) for c in calls), len(calls))
    h.check("every call is locked down",
            all("--strict-mcp-config" in c["argv"] and c["argv"][c["argv"].index("--tools") + 1] == "" for c in calls))
    prompt_files = [c["argv"][c["argv"].index("--system-prompt-file") + 1] for c in calls]
    h.check("--system-prompt-file is an absolute path that exists (claude runs with cwd=work dir)",
            all(Path(f).is_absolute() and Path(f).is_file() for f in prompt_files), sorted(set(prompt_files)))
    h.check("mentions never reach the prompt", all("<@" not in c["prompt"] for c in calls))
    h.check("no errors logged", not [e for e in h.world.events if e["kind"] == "log_error"])


def json_dump(obj) -> str:
    import json

    return json.dumps(obj)


JOURNEYS = {
    "help": (help_command, "!help / !tools list every command category"),
    "availability-signup": (availability_signup, "!avail post, 🟢 reaction, DM class/role form, pool switch"),
    "key-request": (key_request, "pinned button -> DM key form -> note modal -> posted schedule"),
    "signup-fill-restart": (signup_fill_restart, "restart, Sign Up via persistent button, role pick, fill DMs, queue"),
    "snipe": (snipe, "!snipe / !snipes / !unsnipe from DMs (offline: label fallback)"),
    "banker-token": (banker_token, "!tokenalert / !token, banker gate, off"),
    "command-gates": (command_gates, "permission/channel gates stay silent; unknown commands and chat are ignored"),
    "assistant": (assistant, "plain-language DM/mention → answer or confirmed command via fake claude"),
}
