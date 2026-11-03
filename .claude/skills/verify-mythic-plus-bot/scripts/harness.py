#!/usr/bin/env python
"""Offline driver for the Mythic+ Discord bot.

Boots the real ``MyClient`` (real ``setup_hook``, ``on_ready``, handlers, views, state files) in a
scratch data dir, with a fake Discord world standing in for the gateway. Users act through the same
entry points Discord uses: ``client.dispatch("message"/"reaction_add")`` and discord.py's own
``ViewStore`` routing for buttons, selects and modals. Every bot action is recorded as evidence.

It never loads the production ``.env`` and never talks to Discord: the token is a sentinel and
discord.py's HTTP client is patched to raise. Outbound game-API HTTP (Blizzard, Undermine,
Raider.io) is blocked unless ``--network`` is given.

    python harness.py doctor
    python harness.py list
    python harness.py run <journey ...|all> [--network] [--keep]
    python harness.py script <scenario.py> [--network] [--keep]
    python harness.py cleanup
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import os
import re
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parents[3]  # <repo>/.claude/skills/verify-mythic-plus-bot/scripts
VERIFY_ROOT = REPO / ".verify"
RUNS_DIR = VERIFY_ROOT / "runs"  # scratch data dirs, removed by cleanup
EVIDENCE_DIR = VERIFY_ROOT / "evidence"  # proof, never removed by cleanup

SENTINEL_TOKEN = "verify-harness-offline-token"
IDS = {
    "guild": 900000000000000001,
    "avail": 900000000000000002,
    "key": 900000000000000003,
    "tank_role": 900000000000000011,
    "healer_role": 900000000000000012,
    "dps_role": 900000000000000013,
    "mplus_role": 900000000000000014,
    "coordinator": 100000000000000001,
    "banker": 100000000000000002,
    "admin": 100000000000000003,
    "bot": 100000000000000099,
}
FAKE_ENV = {
    "CLIENT_KEY": SENTINEL_TOKEN,
    "AVAIL_CHANNEL_ID": str(IDS["avail"]),
    "KEY_CHANNEL_ID": str(IDS["key"]),
    "GUILD_ID": str(IDS["guild"]),
    "TANK_ROLE_ID": str(IDS["tank_role"]),
    "HEALER_ROLE_ID": str(IDS["healer_role"]),
    "DPS_ROLE_ID": str(IDS["dps_role"]),
    "MYTHIC_PLUS_ID": str(IDS["mplus_role"]),
    "COORDINATOR_ID": str(IDS["coordinator"]),
    "BANKER_ID": str(IDS["banker"]),
    "ADMIN_ID": str(IDS["admin"]),
    "BLIZZ_CLIENT_ID": "offline",
    "BLIZZ_CLIENT_SECRET": "offline",
    "BLIZZ_REGION": "us",
    "UNDERMINE_API_KEY": "offline",
    "RAIDERIO_API_KEY": "offline",
    "LOG_LEVEL": "INFO",
    "ASSISTANT_ENABLED": "1",
    "ASSISTANT_USER_IDS": f"{IDS['banker']},{IDS['coordinator']}",
    "CLAUDE_BIN": json.dumps([sys.executable, str(Path(__file__).resolve().parent / "fake_claude.py")]),
    "ASSISTANT_CONFIG_DIR": "assistant-config",
    "ASSISTANT_WORK_DIR": "assistant-work",
    "ASSISTANT_TIMEOUT": "3",
    "CLAUDE_CODE_OAUTH_TOKEN": "offline-fake-token",
}
# Only these are copied from the repo .env under --network. CLIENT_KEY never is.
NETWORK_ENV_ALLOWLIST = (
    "BLIZZ_CLIENT_ID", "BLIZZ_CLIENT_SECRET", "BLIZZ_REGION",
    "UNDERMINE_API_KEY", "UNDERMINE_REGION", "RAIDERIO_API_KEY", "RAIDERIO_REGION",
)
LOOP_NAMES = (
    "hourly_check", "weekly_avail_reset", "price_watch_check", "raiderio_harvest",
    "forecast_preview", "auction_snipe_check", "token_watch_check",
)

import discord  # noqa: E402  (after constants so doctor can report a missing install cleanly)


class HarnessError(AssertionError):
    pass


class _Resp:
    def __init__(self, status: int, reason: str):
        self.status, self.reason = status, reason


def _not_found(what: str) -> discord.NotFound:
    return discord.NotFound(_Resp(404, "Not Found"), f"Unknown {what}")


def _forbidden(why: str) -> discord.Forbidden:
    return discord.Forbidden(_Resp(403, "Forbidden"), why)


def _bad_request(why: str) -> discord.HTTPException:
    return discord.HTTPException(_Resp(400, "Bad Request"), f"Invalid Form Body: {why}")


_MISSING: Any = object()
_snowflake_seq = 0


def _snowflake() -> int:
    global _snowflake_seq
    _snowflake_seq += 1
    return discord.utils.time_snowflake(datetime.now(timezone.utc)) + _snowflake_seq


# ---------------------------------------------------------------------------
# Discord payload limits — a payload Discord would reject raises 400 here too.
# ---------------------------------------------------------------------------


def _validate(content, embeds, view) -> None:
    if content is not None and len(str(content)) > 2000:
        raise _bad_request(f"content is {len(str(content))} chars (max 2000)")
    if len(embeds) > 10:
        raise _bad_request("more than 10 embeds")
    for e in embeds:
        d = e.to_dict()
        total = 0
        for key, cap in (("title", 256), ("description", 4096)):
            v = d.get(key) or ""
            if len(v) > cap:
                raise _bad_request(f"embed {key} is {len(v)} chars (max {cap})")
            total += len(v)
        fields = d.get("fields", [])
        if len(fields) > 25:
            raise _bad_request(f"embed has {len(fields)} fields (max 25)")
        for f in fields:
            name, value = str(f.get("name", "")), str(f.get("value", ""))
            if not name.strip() and name != "​":
                raise _bad_request("embed field name is empty")
            if not value.strip() and value != "​":
                raise _bad_request(f"embed field '{name}' value is empty")
            if len(name) > 256 or len(value) > 1024:
                raise _bad_request(f"embed field '{name}' too long ({len(name)}/{len(value)}, max 256/1024)")
            total += len(name) + len(value)
        total += len((d.get("footer") or {}).get("text", "")) + len((d.get("author") or {}).get("name", ""))
        if total > 6000:
            raise _bad_request(f"embed total is {total} chars (max 6000)")
    if view is not None:
        try:
            rows = view.to_components()
        except Exception as exc:  # noqa: BLE001 - discord.py's own layout check
            raise _bad_request(f"view layout: {exc}") from exc
        if len(rows) > 5:
            raise _bad_request("more than 5 component rows")
        for item in view.children:
            label = getattr(item, "label", None)
            if label and len(label) > 80:
                raise _bad_request(f"button label too long: {label!r}")
            for opt in getattr(item, "options", []) or []:
                if len(opt.label) > 100 or len(opt.value) > 100:
                    raise _bad_request(f"select option too long: {opt.label!r}")
            if len(getattr(item, "options", []) or []) > 25:
                raise _bad_request("select has more than 25 options")


def _render_components(view) -> List[dict]:
    """What the user sees: a snapshot of the view's items at send/edit time."""
    if view is None:
        return []
    out = []
    for item in view.children:
        entry = {
            "type": item.type.value,
            "custom_id": getattr(item, "custom_id", None),
            "disabled": bool(getattr(item, "disabled", False)),
        }
        if isinstance(item, discord.ui.Button):
            entry.update(kind="button", label=item.label, emoji=str(item.emoji) if item.emoji else None)
        elif isinstance(item, discord.ui.Select):
            entry.update(
                kind="select",
                placeholder=item.placeholder,
                min_values=item.min_values,
                max_values=item.max_values,
                options=[{"label": o.label, "value": o.value} for o in item.options],
            )
        else:
            entry.update(kind=type(item).__name__)
        out.append(entry)
    return out


def _embed_text(d: dict) -> str:
    parts = [d.get("title") or "", d.get("description") or ""]
    for f in d.get("fields", []):
        parts.append(f"{f.get('name')}: {f.get('value')}")
    parts.append((d.get("footer") or {}).get("text", ""))
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Fake Discord world. It outlives a bot restart, like Discord does.
# ---------------------------------------------------------------------------


class FakeRole:
    def __init__(self, id: int, name: str):
        self.id, self.name, self.mention = id, name, f"<@&{id}>"


class FakeUser:
    def __init__(self, world: "World", id: int, name: str, *, bot: bool = False, dms_open: bool = True):
        self._world = world
        self.id, self.name, self.display_name, self.global_name = id, name, name, name
        self.mention = f"<@{id}>"
        self.bot = bot
        self.dms_open = dms_open
        self.roles: List[FakeRole] = []

    def __eq__(self, other):
        return getattr(other, "id", None) == self.id

    def __hash__(self):
        return hash(self.id)

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"<FakeUser {self.name} {self.id}>"

    @property
    def dm_channel(self):
        return self._world.dms.get(self.id)

    async def create_dm(self) -> "FakeDMChannel":
        return self._world.dm_for(self)

    async def send(self, content=None, **kwargs):
        return await (await self.create_dm()).send(content, **kwargs)


class FakeGuild:
    def __init__(self, world: "World", id: int):
        self._world, self.id, self.name = world, id, "Verify Guild"
        self._roles: Dict[int, FakeRole] = {}

    def get_role(self, id: int):
        return self._roles.get(id)

    def get_member(self, id: int):
        return self._world.users.get(id)

    async def fetch_member(self, id: int):
        m = self.get_member(id)
        if m is None:
            raise _not_found("Member")
        return m

    @property
    def members(self):
        return list(self._world.users.values())

    @property
    def roles(self):
        return list(self._roles.values())


class FakeMessage:
    @property
    def _state(self):
        return self._world.state()

    def __init__(self, world, channel, author, content, embeds, view, *, ephemeral_to=None, reference=None):
        self._world = world
        self.id = _snowflake()
        self.channel = channel
        self.guild = getattr(channel, "guild", None)
        self.author = author
        self.content = content or ""
        self.embeds = list(embeds)
        self.attachments = []
        self.components = _render_components(view)
        self.ephemeral_to = ephemeral_to
        self.reference = reference
        self.reactions: Dict[str, List[int]] = {}
        self.pinned = False
        self.deleted = False
        self.interaction = None
        self.created_at = discord.utils.snowflake_time(self.id)
        self.flags = SimpleNamespace(ephemeral=ephemeral_to is not None)
        ids = [int(m) for m in re.findall(r"<@!?(\d+)>", self.content)]
        self.raw_mentions = list(dict.fromkeys(ids))
        self.mentions = [world.users[i] for i in self.raw_mentions if i in world.users]
        self.mention_everyone = "@everyone" in self.content or "@here" in self.content
        self.role_mentions = []
        self.type = discord.MessageType.default
        self.webhook_id = None

    def __eq__(self, other):
        return getattr(other, "id", None) == self.id

    def __hash__(self):
        return hash(self.id)

    def __repr__(self):
        return f"<FakeMessage {self.id} in {self.channel.label}>"

    @property
    def embed(self):
        return self.embeds[0] if self.embeds else None

    @property
    def jump_url(self):
        gid = self.guild.id if self.guild else "@me"
        return f"https://discord.com/channels/{gid}/{self.channel.id}/{self.id}"

    def text(self) -> str:
        """Content plus every embed's text, for contains-style checks."""
        return "\n".join([self.content] + [_embed_text(e.to_dict()) for e in self.embeds]).strip()

    def embed_field(self, name_contains: str) -> Optional[str]:
        for e in self.embeds:
            for f in e.fields:
                if name_contains.lower() in (f.name or "").lower():
                    return f.value
        return None

    def _check_live(self):
        if self.deleted:
            raise _not_found("Message")

    async def edit(self, *, content=_MISSING, embed=_MISSING, embeds=_MISSING, view=_MISSING, **_ignored):
        self._check_live()
        new_content = self.content if content is _MISSING else (content or "")
        if embed is not _MISSING:
            new_embeds = [] if embed is None else [embed]
        elif embeds is not _MISSING:
            new_embeds = list(embeds or [])
        else:
            new_embeds = self.embeds
        live_view = None if view is _MISSING else view
        try:
            _validate(new_content, new_embeds, live_view)
        except discord.HTTPException as exc:
            self._world.record("rejected", channel=self.channel, message=self, detail=str(exc), op="edit")
            raise
        self.content, self.embeds = new_content, new_embeds
        state = self._world.state()
        if view is not _MISSING:
            self.components = _render_components(view)
            if view is None:
                state.prevent_view_updates_for(self.id)
            elif not view.is_finished():
                state.store_view(view, self.id)
        self._world.record("edit", channel=self.channel, message=self)
        return self

    async def delete(self, *, delay: Optional[float] = None):
        self._check_live()
        self.deleted = True
        self.channel._messages.pop(self.id, None)
        self._world.record("delete", channel=self.channel, message=self)

    async def add_reaction(self, emoji):
        self._check_live()
        users = self.reactions.setdefault(str(emoji), [])
        if IDS["bot"] not in users:
            users.append(IDS["bot"])
        self._world.record("bot_react", channel=self.channel, message=self, emoji=str(emoji))

    async def remove_reaction(self, emoji, member):
        users = self.reactions.get(str(emoji), [])
        if member.id in users:
            users.remove(member.id)
        self._world.record("remove_reaction", channel=self.channel, message=self, emoji=str(emoji), user=member.id)

    async def clear_reactions(self):
        self.reactions.clear()
        self._world.record("clear_reactions", channel=self.channel, message=self)

    async def pin(self, *, reason=None):
        self._check_live()
        self.pinned = True
        self._world.record("pin", channel=self.channel, message=self)

    async def unpin(self, *, reason=None):
        self.pinned = False
        self._world.record("unpin", channel=self.channel, message=self)

    async def reply(self, content=None, **kwargs):
        return await self.channel.send(content, reference=self, **kwargs)


class _Messageable:
    label = "?"

    def __init__(self, world, id):
        self._world, self.id = world, id
        self._messages: Dict[int, FakeMessage] = {}

    def typing(self):
        world, channel = self._world, self

        class _Typing:
            async def __aenter__(self_inner):
                world.record("typing", channel=channel)

            async def __aexit__(self_inner, *exc):
                return False

        return _Typing()

    def _post(self, author, content=None, *, embed=None, embeds=None, view=None, ephemeral_to=None,
              reference=None, kind="send"):
        embeds = ([embed] if embed is not None else []) + list(embeds or [])
        try:
            _validate(content, embeds, view)
        except discord.HTTPException as exc:
            self._world.record("rejected", channel=self, detail=str(exc), op=kind, content=content)
            raise
        msg = FakeMessage(self._world, self, author, content, embeds, view, ephemeral_to=ephemeral_to,
                          reference=reference)
        self._messages[msg.id] = msg
        if view is not None and not view.is_finished():
            self._world.state().store_view(view, msg.id)
        return msg

    async def send(self, content=None, *, embed=None, embeds=None, view=None, reference=None, **_ignored):
        msg = self._post(self._world.bot_user, content, embed=embed, embeds=embeds, view=view, reference=reference)
        self._world.record("send", channel=self, message=msg)
        return msg

    async def fetch_message(self, id: int):
        msg = self._messages.get(id)
        if msg is None:
            raise _not_found("Message")
        return msg

    def get_partial_message(self, id: int):
        return self._messages.get(id)

    async def pins(self):
        return [m for m in self._messages.values() if m.pinned]

    async def purge(self, *, limit: Optional[int] = 100, check: Optional[Callable] = None, **_ignored):
        victims = [m for m in reversed(list(self._messages.values())) if check is None or check(m)]
        victims = victims[:limit] if limit else victims
        for m in victims:
            await m.delete()
        return victims

    async def history(self, *, limit: Optional[int] = 100, **_ignored):
        for m in list(reversed(list(self._messages.values())))[:limit]:
            yield m

    def messages(self) -> List[FakeMessage]:
        return list(self._messages.values())


class FakeTextChannel(_Messageable):
    def __init__(self, world, id, name, guild):
        super().__init__(world, id)
        self.name, self.guild, self.label = name, guild, f"#{name}"
        self.mention = f"<#{id}>"
        self.type = discord.ChannelType.text


class FakeDMChannel(_Messageable):
    def __init__(self, world, recipient: FakeUser):
        super().__init__(world, _snowflake())
        self.recipient, self.guild = recipient, None
        self.label = f"DM:{recipient.name}"
        self.type = discord.ChannelType.private

    async def send(self, content=None, **kwargs):
        if not self.recipient.dms_open:
            self._world.record("rejected", channel=self, detail="Cannot send messages to this user", op="send",
                               content=content)
            raise _forbidden("Cannot send messages to this user")
        return await super().send(content, **kwargs)


class FakeReaction:
    def __init__(self, emoji: str, message: FakeMessage):
        self.emoji, self.message = emoji, message
        self.count = len(message.reactions.get(emoji, []))
        self.me = IDS["bot"] in message.reactions.get(emoji, [])

    def __str__(self):
        return self.emoji


class _FakeResponse:
    def __init__(self, interaction: "FakeInteraction"):
        self._i = interaction
        self._done = False
        self.kind: Optional[str] = None

    def is_done(self) -> bool:
        return self._done

    def _claim(self, kind: str):
        if self._done:
            raise discord.InteractionResponded(self._i)
        self._done, self.kind = True, kind

    async def send_message(self, content=None, *, embed=None, embeds=None, view=None, ephemeral=False, **_ignored):
        self._claim("message")
        i = self._i
        msg = i.channel._post(i._world.bot_user, content, embed=embed, embeds=embeds, view=view,
                              ephemeral_to=i.user.id if ephemeral else None, kind="interaction_response")
        i._original = msg
        i._world.record("interaction_response", channel=i.channel, message=msg, user=i.user.id)

    async def defer(self, *, ephemeral=False, thinking=False):
        self._claim("defer")
        self._i._world.record("interaction_defer", channel=self._i.channel, user=self._i.user.id)

    async def edit_message(self, *, content=_MISSING, embed=_MISSING, embeds=_MISSING, view=_MISSING, **_ignored):
        self._claim("edit_message")
        await self._i.message.edit(content=content, embed=embed, embeds=embeds, view=view)

    async def send_modal(self, modal):
        self._claim("modal")
        i = self._i
        i._world.state().store_view(modal)
        i._world.open_modals[i.user.id] = modal
        fields = [getattr(c, "label", None) for c in modal.children]
        i._world.record("modal_open", channel=i.channel, user=i.user.id, title=modal.title, fields=fields)


class _FakeFollowup:
    def __init__(self, interaction: "FakeInteraction"):
        self._i = interaction

    async def send(self, content=None, *, embed=None, embeds=None, view=None, ephemeral=False, **_ignored):
        i = self._i
        if not i.response.is_done():
            raise _not_found("Webhook (followup before the interaction was acknowledged)")
        msg = i.channel._post(i._world.bot_user, content, embed=embed, embeds=embeds, view=view,
                              ephemeral_to=i.user.id if ephemeral else None, kind="followup")
        i._world.record("followup", channel=i.channel, message=msg, user=i.user.id)
        return msg


class FakeInteraction:
    def __init__(self, world: "World", user: FakeUser, message: Optional[FakeMessage], data: dict, channel):
        self._world = world
        self.id = _snowflake()
        self.user = user
        self.message = message
        self.data = data
        self.channel = channel
        self.channel_id = channel.id
        self.guild = getattr(channel, "guild", None)
        self.guild_id = self.guild.id if self.guild else None
        self.client = world.client
        self.type = discord.InteractionType.component
        self.extras: dict = {}
        self.command = None
        self.locale = discord.Locale.american_english
        self.response = _FakeResponse(self)
        self.followup = _FakeFollowup(self)
        self._original: Optional[FakeMessage] = None

    @property
    def _state(self):
        return self._world.state()

    async def original_response(self):
        return self._original or self.message

    async def edit_original_response(self, *, content=_MISSING, embed=_MISSING, embeds=_MISSING, view=_MISSING,
                                     **_ignored):
        if not self.response.is_done():
            raise _not_found("Webhook (no original response yet)")
        # After a component defer, the "original response" is the message the component lives on.
        target = self._original or self.message
        return await target.edit(content=content, embed=embed, embeds=embeds, view=view)


class World:
    """Users, guild, channels and messages; plus the evidence recorder."""

    def __init__(self):
        self.t0 = time.monotonic()
        self.events: List[dict] = []
        self.client = None
        self.users: Dict[int, FakeUser] = {}
        self.dms: Dict[int, FakeDMChannel] = {}
        self.open_modals: Dict[int, Any] = {}
        self.guild = FakeGuild(self, IDS["guild"])
        for key, name in (("tank_role", "tank"), ("healer_role", "healer"), ("dps_role", "dps"),
                          ("mplus_role", "Mythic+")):
            self.guild._roles[IDS[key]] = FakeRole(IDS[key], name)
        self.channels: Dict[int, _Messageable] = {
            IDS["avail"]: FakeTextChannel(self, IDS["avail"], "availability", self.guild),
            IDS["key"]: FakeTextChannel(self, IDS["key"], "keys", self.guild),
        }
        self.bot_user = FakeUser(self, IDS["bot"], "MythicPlusBot", bot=True)
        self.users[IDS["bot"]] = self.bot_user
        self._next_user_id = 200000000000000001

    def state(self):
        return self.client._connection

    def add_user(self, name: str, id: Optional[int] = None, dms_open: bool = True) -> FakeUser:
        if id is None:
            id, self._next_user_id = self._next_user_id, self._next_user_id + 1
        user = FakeUser(self, id, name, dms_open=dms_open)
        self.users[id] = user
        return user

    def dm_for(self, user: FakeUser) -> FakeDMChannel:
        if user.id not in self.dms:
            self.dms[user.id] = FakeDMChannel(self, user)
            self.channels[self.dms[user.id].id] = self.dms[user.id]
        return self.dms[user.id]

    def record(self, kind: str, *, channel=None, message: Optional[FakeMessage] = None, **extra) -> dict:
        ev = {"seq": len(self.events) + 1, "t": round(time.monotonic() - self.t0, 3), "kind": kind}
        if channel is not None:
            ev["channel"] = channel.label
        if message is not None:
            ev["message_id"] = message.id
            ev["author"] = message.author.name
            if message.content:
                ev["content"] = message.content
            if message.embeds:
                ev["embeds"] = [e.to_dict() for e in message.embeds]
            if message.components:
                ev["components"] = message.components
            if message.ephemeral_to:
                ev["ephemeral_to"] = self.users[message.ephemeral_to].name
        for k, v in extra.items():
            if k == "user" and isinstance(v, int) and v in self.users:
                v = self.users[v].name
            ev[k] = v
        self.events.append(ev)
        return ev


class _LogCapture(logging.Handler):
    def __init__(self, world: World):
        super().__init__(level=logging.WARNING)
        self.world = world

    def emit(self, record):
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001
            text = str(record.msg)
        extra = {"logger": record.name, "text": text}
        if record.exc_info:
            extra["traceback"] = "".join(traceback.format_exception(*record.exc_info))
        self.world.record("log_error" if record.levelno >= logging.ERROR else "log_warning", **extra)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

_bot_module = None
_network_attempts: List[dict] = []


def _import_bot(data_dir: Path, network: bool):
    """Import bot.py once per process, from inside the scratch data dir, with fake env."""
    global _bot_module
    os.chdir(data_dir)
    if _bot_module is not None:
        return _bot_module
    import dotenv

    values = dotenv.dotenv_values(REPO / ".env") if network and (REPO / ".env").exists() else {}
    for k in list(os.environ):
        if k in FAKE_ENV or k in NETWORK_ENV_ALLOWLIST or k == "LOG_FILE":
            del os.environ[k]
    os.environ.update(FAKE_ENV)
    for k in NETWORK_ENV_ALLOWLIST:
        if values.get(k):
            os.environ[k] = values[k]
    os.environ["LOG_FILE"] = str(data_dir / "bot.log")
    dotenv.load_dotenv = lambda *a, **k: False  # bot.py must never read the production .env

    async def _no_discord_http(self, route, **kwargs):
        raise RuntimeError(f"verify harness: real Discord HTTP is forbidden ({route.method} {route.path})")

    async def _no_login(self, *a, **k):
        raise RuntimeError("verify harness: logging in to Discord is forbidden")

    discord.http.HTTPClient.request = _no_discord_http
    discord.Client.login = _no_login
    discord.Client.connect = _no_login

    from discord.ext import commands as _commands

    async def _ctx_send(self, content=None, **kwargs):
        return await self.channel.send(content, **kwargs)

    async def _ctx_reply(self, content=None, **kwargs):
        return await self.message.reply(content, **kwargs)

    _commands.Context.send = _ctx_send  # Discord delivers ctx.send to the invoking channel
    _commands.Context.reply = _ctx_reply

    if not network:
        import aiohttp

        async def _blocked(self, method, url, *a, **k):
            _network_attempts.append({"method": method, "url": str(url)})
            raise aiohttp.ClientConnectionError(f"verify harness: network blocked ({method} {url})")

        aiohttp.ClientSession._request = _blocked

    sys.path.insert(0, str(REPO))
    sys.modules.pop("bot", None)
    _bot_module = importlib.import_module("bot")
    if _bot_module.CLIENT_ID != SENTINEL_TOKEN:
        raise RuntimeError("verify harness: bot.py picked up a real token — refusing to continue")
    return _bot_module


class Harness:
    def __init__(self, run_id: str, *, network: bool = False, quiet: Optional[float] = None):
        self.run_id = run_id
        self.network = network
        self.quiet = quiet if quiet is not None else (2.0 if network else 0.15)
        self.run_dir = RUNS_DIR / run_id
        self.data_dir = self.run_dir / "data"
        self.evidence_dir = EVIDENCE_DIR / run_id
        self.world = World()
        self.checks: List[dict] = []
        self.started_loops: List[str] = []
        self.bot = None
        self.client = None
        self._cursor = 0
        self._baseline_tasks: set = set()
        self.coordinator = self.world.add_user("Coordinator", IDS["coordinator"])
        self.banker = self.world.add_user("Banker", IDS["banker"])
        self.admin = self.world.add_user("Admin", IDS["admin"])
        self.avail = self.world.channels[IDS["avail"]]
        self.key = self.world.channels[IDS["key"]]

    # ---- lifecycle -------------------------------------------------------

    async def launch(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Deploy-time files a fresh checkout ships with. The bot reads them from its cwd.
        shutil.copy2(REPO / "CHANGELOG.md", self.data_dir / "CHANGELOG.md")
        self.bot = _import_bot(self.data_dir, self.network)
        root = logging.getLogger()
        for old in [h for h in root.handlers if isinstance(h, _LogCapture)]:
            root.removeHandler(old)
        root.addHandler(_LogCapture(self.world))
        self._baseline_tasks = set(asyncio.all_tasks())
        await self._boot()

    async def _boot(self):
        from discord.ext import tasks

        bot, world = self.bot, self.world
        client = bot.MyClient(intents=bot.intents)
        world.client = self.client = client
        await client._async_setup_hook()
        client._connection.user = world.bot_user
        client.get_channel = lambda cid: world.channels.get(cid)
        client.get_partial_messageable = lambda cid, **k: world.channels.get(cid)
        client.get_user = lambda uid: world.users.get(uid)
        client.get_guild = lambda gid: world.guild if gid == world.guild.id else None

        async def fetch_user(uid):
            if uid not in world.users:
                raise _not_found("User")
            return world.users[uid]

        async def fetch_channel(cid):
            if cid not in world.channels:
                raise _not_found("Channel")
            return world.channels[cid]

        client.fetch_user, client.fetch_channel = fetch_user, fetch_channel

        original_start = tasks.Loop.start
        started = self.started_loops

        def _record_start(loop_self, *a, **k):
            started.append(getattr(loop_self.coro, "__name__", "?"))

        tasks.Loop.start = _record_start
        try:
            await client.setup_hook()
        finally:
            tasks.Loop.start = original_start
        world.record("boot", loops_not_started=sorted(set(started)))
        client._ready.set()
        client.dispatch("ready")
        await self.settle()

    async def restart(self):
        """Simulate a bot restart: new MyClient from the same data dir; Discord's side persists."""
        await self.settle()
        self._cancel_bot_tasks()
        self.world.open_modals.clear()
        self.world.record("restart")
        await self._boot()

    def _cancel_bot_tasks(self):
        me = asyncio.current_task()
        for t in asyncio.all_tasks():
            if t is not me and t not in self._baseline_tasks and not t.done():
                t.cancel()

    async def close(self):
        if self.client is not None:
            self._cancel_bot_tasks()
            await asyncio.sleep(0)

    # ---- people and places -------------------------------------------------

    def mention(self) -> str:
        return f"<@{IDS['bot']}>"

    def user(self, name: str, *, id: Optional[int] = None, dms_open: bool = True) -> FakeUser:
        return self.world.add_user(name, id, dms_open)

    def dm(self, user: FakeUser) -> FakeDMChannel:
        return self.world.dm_for(user)

    def _where(self, where):
        if isinstance(where, _Messageable):
            return where
        if isinstance(where, FakeUser):
            return self.dm(where)
        return {"avail": self.avail, "key": self.key}[where]

    # ---- user actions (all go through Discord's entry points) ------------

    async def say(self, user: FakeUser, content: str, where="key") -> FakeMessage:
        """User sends a message. where: "key", "avail", "dm", or a channel."""
        channel = self.dm(user) if where == "dm" else self._where(where)
        msg = channel._post(user, content)
        self.world.record("user_message", channel=channel, message=msg)
        self._cursor = msg_seq = self.world.events[-1]["seq"]
        self.client.dispatch("message", msg)
        await self.settle()
        self._cursor = msg_seq
        return msg

    async def react(self, user: FakeUser, message: FakeMessage, emoji: str):
        message._check_live()
        users = message.reactions.setdefault(emoji, [])
        if user.id not in users:
            users.append(user.id)
        self.world.record("user_react", channel=message.channel, user=user.id, emoji=emoji,
                          message_id=message.id)
        self._cursor = self.world.events[-1]["seq"]
        self.client.dispatch("reaction_add", FakeReaction(emoji, message), user)
        await self.settle()

    def _component(self, message: FakeMessage, handle: str, kind: str) -> dict:
        if message.ephemeral_to is not None and message.ephemeral_to not in self.world.users:
            raise HarnessError("ephemeral message owner unknown")
        comps = [c for c in message.components if c["kind"] == kind]
        h = handle.lower()
        for key in ("custom_id", "label", "placeholder"):
            exact = [c for c in comps if (c.get(key) or "").lower() == h]
            if len(exact) == 1:
                return exact[0]
        loose = [c for c in comps if h in ((c.get("label") or "") + " " + (c.get("placeholder") or "")).lower()]
        if len(loose) == 1:
            return loose[0]
        seen = [c.get("label") or c.get("placeholder") for c in comps]
        raise HarnessError(f"{kind} {handle!r} not found on message {message.id} ({message.channel.label}); "
                           f"visible {kind}s: {seen}")

    async def _interact(self, user: FakeUser, message: FakeMessage, comp: dict, data: dict, desc: str):
        message._check_live()
        if message.ephemeral_to is not None and message.ephemeral_to != user.id:
            raise HarnessError(f"{user.name} cannot see ephemeral message {message.id}")
        if comp["disabled"]:
            raise HarnessError(f"{desc} is disabled")
        store = self.world.state()._view_store
        key = (comp["type"], comp["custom_id"])
        live = store._views.get(message.id, {}).get(key) or store._views.get(None, {}).get(key)
        self.world.record("user_interact", channel=message.channel, user=user.id, message_id=message.id,
                          action=desc)
        self._cursor = self.world.events[-1]["seq"]
        if live is None:
            self.world.record("interaction_failed", channel=message.channel, user=user.id,
                              detail=f"no live view handles {desc} (Discord shows 'This interaction failed')")
            raise HarnessError(f"This interaction failed: no live view handles {desc} on message {message.id}")
        interaction = FakeInteraction(self.world, user, message, data, message.channel)
        store.dispatch_view(comp["type"], comp["custom_id"], interaction)
        await self.settle()
        return interaction

    async def click(self, user: FakeUser, message: FakeMessage, handle: str):
        """Click a button by label or custom_id."""
        comp = self._component(message, handle, "button")
        data = {"component_type": comp["type"], "custom_id": comp["custom_id"]}
        return await self._interact(user, message, comp, data, f"button [{comp['label']}]")

    async def choose(self, user: FakeUser, message: FakeMessage, handle: str, *values: str):
        """Pick option(s) in a select menu, by option value or label."""
        comp = self._component(message, handle, "select")
        picked = []
        for v in values:
            match = [o["value"] for o in comp["options"] if v.lower() in (o["value"].lower(), o["label"].lower())]
            if not match:
                raise HarnessError(f"option {v!r} is not offered by select {comp['placeholder']!r}; "
                                   f"offered: {[o['label'] for o in comp['options']]}")
            picked.append(match[0])
        if not comp["min_values"] <= len(picked) <= comp["max_values"]:
            raise HarnessError(f"select {comp['placeholder']!r} takes {comp['min_values']}-{comp['max_values']} values")
        data = {"component_type": comp["type"], "custom_id": comp["custom_id"], "values": picked}
        return await self._interact(user, message, comp, data, f"select [{comp['placeholder']}] = {picked}")

    async def submit_modal(self, user: FakeUser, values: Dict[str, str]):
        """Fill the modal currently open for this user (field label -> text) and submit it."""
        modal = self.world.open_modals.pop(user.id, None)
        if modal is None:
            raise HarnessError(f"no modal is open for {user.name}")
        rows = []
        by_label = {c.label.lower(): c for c in modal.children if hasattr(c, "label")}
        for label, text in values.items():
            item = by_label.get(label.lower())
            if item is None:
                raise HarnessError(f"modal {modal.title!r} has no field {label!r}; fields: {list(by_label)}")
            if item.max_length and len(text) > item.max_length:
                raise HarnessError(f"field {label!r} max length is {item.max_length}")
            rows.append({"type": 1, "components": [{"type": 4, "custom_id": item.custom_id, "value": text}]})
        for item in by_label.values():
            if item.required and item.label.lower() not in {k.lower() for k in values}:
                raise HarnessError(f"required modal field {item.label!r} left empty")
        channel = self.dm(user)
        self.world.record("user_modal_submit", channel=channel, user=user.id, title=modal.title, values=values)
        self._cursor = self.world.events[-1]["seq"]
        interaction = FakeInteraction(self.world, user, None, {"custom_id": modal.custom_id, "components": rows},
                                      channel)
        self.world.state()._view_store.dispatch_modal(modal.custom_id, interaction, rows)
        await self.settle()
        return interaction

    async def tick(self, loop_name: str):
        """Run one iteration of a background task loop (they are never auto-started)."""
        if loop_name not in LOOP_NAMES:
            raise HarnessError(f"unknown loop {loop_name!r}; known: {LOOP_NAMES}")
        owners = [self.client, *self.client.cogs.values()]
        loop = next((getattr(o, loop_name) for o in owners if hasattr(type(o), loop_name)), None)
        if loop is None:
            raise HarnessError(f"no client or cog defines {loop_name}")
        self.world.record("tick", loop=loop_name)
        await loop()
        await self.settle()

    # ---- waiting and reading ---------------------------------------------

    async def settle(self, quiet: Optional[float] = None, timeout: float = 30.0):
        """Wait until the bot stops producing events for `quiet` seconds."""
        quiet = self.quiet if quiet is None else quiet
        deadline = time.monotonic() + timeout
        last, since = len(self.world.events), time.monotonic()
        while True:
            await asyncio.sleep(0.02)
            now = time.monotonic()
            if len(self.world.events) != last:
                last, since = len(self.world.events), now
            elif now - since >= quiet:
                return
            if now > deadline:
                raise HarnessError("bot never went quiet")

    async def wait_for(self, where, contains: Optional[str] = None, *, predicate: Optional[Callable] = None,
                       timeout: float = 10.0, since_last_action: bool = True) -> FakeMessage:
        """Wait for a bot message (new or edited) in a channel or a user's DM."""
        channel = self._where(where)
        floor = self._cursor if since_last_action else 0
        deadline = time.monotonic() + timeout
        while True:
            for ev in self.world.events:
                if ev["seq"] <= floor or ev.get("channel") != channel.label or ev["kind"] not in (
                        "send", "edit", "interaction_response", "followup"):
                    continue
                msg = channel._messages.get(ev["message_id"])
                if msg is None:
                    continue
                if contains is not None and contains.lower() not in msg.text().lower():
                    continue
                if predicate is not None and not predicate(msg):
                    continue
                return msg
            if time.monotonic() > deadline:
                recent = [m.text()[:80] for m in channel.messages()[-5:]]
                raise HarnessError(f"no bot message in {channel.label} matching {contains!r} within {timeout}s; "
                                   f"latest: {recent}")
            await asyncio.sleep(0.02)

    def bot_messages(self, where) -> List[FakeMessage]:
        return [m for m in self._where(where).messages() if m.author.id == IDS["bot"]]

    def state_file(self, name: str = "state.json") -> Optional[Any]:
        path = self.data_dir / name
        if not path.exists():
            return None
        if name.endswith(".jsonl"):
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return json.loads(path.read_text(encoding="utf-8"))

    # ---- proof -------------------------------------------------------------

    def check(self, name: str, ok: bool, detail: Any = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail if ok else f"FAILED: {detail}"})
        self.world.record("check", name=name, ok=bool(ok), detail=str(detail)[:300])
        return bool(ok)

    def write_evidence(self, journey: str, error: Optional[str]) -> dict:
        ev_dir = self.evidence_dir
        (ev_dir / "data").mkdir(parents=True, exist_ok=True)
        if self.data_dir.exists():
            for f in self.data_dir.iterdir():
                if f.is_file() and f.name != "CHANGELOG.md":
                    shutil.copy2(f, ev_dir / "data" / f.name)
        errors = [e for e in self.world.events if e["kind"] == "log_error"]
        failed = [c for c in self.checks if not c["ok"]]
        status = "pass" if not error and not failed and not errors and self.checks else "fail"
        summary = {
            "journey": journey,
            "status": status,
            "run_id": self.run_id,
            "network": self.network,
            "checks": self.checks,
            "error": error,
            "bot_errors_logged": [e.get("text") for e in errors],
            "network_attempts_blocked": list(_network_attempts),
            "loops_not_started": sorted(set(self.started_loops)),
            "event_count": len(self.world.events),
        }
        if not self.checks and not error:
            summary["error"] = "journey made no checks"
        (ev_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        with open(ev_dir / "transcript.jsonl", "w", encoding="utf-8") as f:
            for e in self.world.events:
                f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
        (ev_dir / "transcript.md").write_text(_render_markdown(journey, summary, self.world.events),
                                              encoding="utf-8")
        return summary


def _render_markdown(journey: str, summary: dict, events: List[dict]) -> str:
    lines = [f"# Verify run: {journey} — {summary['status'].upper()}", ""]
    lines.append(f"run `{summary['run_id']}` · network {'on' if summary['network'] else 'blocked'}")
    if summary["error"]:
        lines += ["", "## Error", "```", str(summary["error"]), "```"]
    lines += ["", "## Checks"]
    for c in summary["checks"]:
        detail = str(c["detail"]).replace("\n", " ⏎ ")[:200]
        lines.append(f"- {'✅' if c['ok'] else '❌'} {c['name']} — {detail}")
    if summary["bot_errors_logged"]:
        lines += ["", "## Errors logged by the bot"] + [f"- {t}" for t in summary["bot_errors_logged"]]
    lines += ["", "## Transcript", ""]
    for e in events:
        k = e["kind"]
        where = e.get("channel", "")
        head = f"`{e['seq']:>4}` {e['t']:>7.2f}s {where:<18}"
        if k in ("send", "edit", "interaction_response", "followup", "user_message"):
            who = e.get("author", "")
            body = (e.get("content") or "").replace("\n", " ⏎ ")[:300]
            for emb in e.get("embeds", []):
                body += f" [embed: {_embed_text(emb).replace(chr(10), ' | ')[:400]}]"
            comps = " ".join(
                f"[{c.get('label') or c.get('placeholder')}{' (disabled)' if c['disabled'] else ''}]"
                for c in e.get("components", [])
            )
            eph = f" (ephemeral to {e['ephemeral_to']})" if e.get("ephemeral_to") else ""
            lines.append(f"{head} **{k}** {who}{eph} msg {e.get('message_id')}: {body} {comps}".rstrip())
        else:
            rest = {x: y for x, y in e.items() if x not in ("seq", "t", "kind", "channel", "embeds", "traceback")}
            lines.append(f"{head} **{k}** {json.dumps(rest, ensure_ascii=False, default=str)[:400]}")
            if e.get("traceback"):
                lines += ["```", e["traceback"].rstrip(), "```"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _new_run_id(name: str) -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{name}-{os.getpid()}"


async def _run_one(name: str, fn: Callable, network: bool, keep: bool) -> dict:
    h = Harness(_new_run_id(name), network=network)
    error = None
    try:
        await h.launch()
        await fn(h)
        await h.settle()
    except Exception:  # noqa: BLE001 - report every failure as evidence
        error = traceback.format_exc()
    finally:
        await h.close()
        _close_file_logs()
        summary = h.write_evidence(name, error)
        os.chdir(REPO)
        if not keep:
            shutil.rmtree(h.run_dir, ignore_errors=True)
    return summary


def _close_file_logs():
    """Release bot.log so the data dir can be copied and removed (Windows locks open files)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.FileHandler):
            handler.close()
            root.removeHandler(handler)


def _print_summary(s: dict, keep: bool):
    mark = "PASS" if s["status"] == "pass" else "FAIL"
    print(f"[{mark}] {s['journey']}  ({len([c for c in s['checks'] if c['ok']])}/{len(s['checks'])} checks)")
    for c in s["checks"]:
        if not c["ok"]:
            print(f"    x {c['name']}: {c['detail']}")
    if s["error"]:
        print("    error:\n      " + str(s["error"]).strip().replace("\n", "\n      "))
    for t in s["bot_errors_logged"]:
        print(f"    bot logged error: {t}")
    print(f"    evidence: {EVIDENCE_DIR / s['run_id']}")
    if keep:
        print(f"    data dir kept: {RUNS_DIR / s['run_id'] / 'data'}")


def _load_scenario(path: str) -> Callable:
    import importlib.util

    spec = importlib.util.spec_from_file_location("verify_scenario", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "scenario"):
        raise SystemExit(f"{path} must define `async def scenario(h)`")
    return mod.scenario


def doctor() -> int:
    ok = True

    def line(good: bool, text: str):
        nonlocal ok
        ok &= good
        print(f"[{'ok' if good else 'XX'}] {text}")

    line(sys.version_info >= (3, 9), f"python {sys.version.split()[0]} ({sys.executable})")
    line(discord.version_info.major == 2, f"discord.py {discord.__version__}")
    from discord.ui.view import View, ViewStore

    line(all(hasattr(ViewStore, a) for a in ("dispatch_view", "dispatch_modal")) and hasattr(View, "_scheduled_task"),
         "discord.py internals the harness routes through (ViewStore.dispatch_view/dispatch_modal) exist")
    for mod in ("dotenv", "aiohttp", "tzdata"):
        try:
            importlib.import_module(mod)
            line(True, f"import {mod}")
        except ImportError:
            line(False, f"import {mod} — run: pip install -e \".[dev]\"")
    line((REPO / "bot.py").exists(), f"repo root {REPO}")
    line(".verify" in (REPO / ".gitignore").read_text(encoding="utf-8"), ".verify/ is gitignored")
    prod_env = REPO / ".env"
    print(f"[--] production .env {'present' if prod_env.exists() else 'absent'} — the harness never loads it")

    async def boot_probe():
        h = Harness(_new_run_id("doctor"))
        try:
            await h.launch()
            pinned = [m for m in h.key.messages() if m.pinned and m.components]
            import config as _c

            return h.bot.CLIENT_ID == SENTINEL_TOKEN, bool(pinned), h.started_loops, sorted(h.client.cogs), _c.CLAUDE_CMD
        finally:
            await h.close()
            _close_file_logs()
            os.chdir(REPO)
            shutil.rmtree(h.run_dir, ignore_errors=True)

    try:
        token_ok, pinned, loops, cog_names, claude_cmd = asyncio.run(boot_probe())
        line(token_ok, "bot booted offline with the sentinel token (no production token loaded)")
        line(pinned, "on_ready pinned the key-request button in #keys")
        line(set(loops) == set(LOOP_NAMES), f"background loops intercepted, not started: {sorted(loops)}")
        cogs_ok = len(cog_names) == 8
        line(cogs_ok, f"cogs loaded: {cog_names}")
        line(claude_cmd[-1].endswith("fake_claude.py"), f"assistant uses the fake claude: {claude_cmd}")
    except Exception as exc:  # noqa: BLE001
        line(False, f"offline boot failed: {exc!r}")
    stale = list(RUNS_DIR.glob("*")) if RUNS_DIR.exists() else []
    print(f"[--] scratch run dirs present: {len(stale)}{' (run: harness.py cleanup)' if stale else ''}")
    return 0 if ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    sub.add_parser("list")
    r = sub.add_parser("run")
    r.add_argument("journeys", nargs="+")
    s = sub.add_parser("script")
    s.add_argument("path")
    for sp in (r, s):
        sp.add_argument("--network", action="store_true", help="allow real game-API HTTP (keys from repo .env)")
        sp.add_argument("--keep", action="store_true", help="keep the scratch data dir")
    sub.add_parser("cleanup")
    args = p.parse_args(argv)

    if args.cmd == "doctor":
        return doctor()
    if args.cmd == "cleanup":
        n = 0
        if RUNS_DIR.exists():
            for d in RUNS_DIR.iterdir():
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        print(f"removed {n} scratch run dir(s) under {RUNS_DIR}; evidence kept in {EVIDENCE_DIR}")
        return 0

    sys.path.insert(0, str(SCRIPTS))
    import journeys

    if args.cmd == "list":
        for name, (fn, doc) in journeys.JOURNEYS.items():
            print(f"{name:<22} {doc}")
        return 0
    if args.cmd == "script":
        todo = [(Path(args.path).stem, _load_scenario(str(Path(args.path).resolve())))]
    else:
        names = list(journeys.JOURNEYS) if args.journeys == ["all"] else args.journeys
        unknown = [n for n in names if n not in journeys.JOURNEYS]
        if unknown:
            print(f"unknown journey(s): {unknown}; see: harness.py list")
            return 2
        todo = [(n, journeys.JOURNEYS[n][0]) for n in names]

    if len(todo) > 1:
        # One process per journey: bot.py is a module with import-time globals and a log file handle.
        import subprocess

        flags = (["--network"] if args.network else []) + (["--keep"] if args.keep else [])
        codes = [subprocess.call([sys.executable, str(Path(__file__).resolve()), "run", n, *flags]) for n, _ in todo]
        return 1 if any(codes) else 0
    name, fn = todo[0]
    summary = asyncio.run(_run_one(name, fn, args.network, args.keep))
    _print_summary(summary, args.keep)
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
