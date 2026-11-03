import re
from types import SimpleNamespace

import discord
import pytest

from cogs.assistant import should_handle

BOT, OWNER = 99, 5


def msg(content, *, dm=False, author=OWNER, bot=False, webhook=None, mtype=discord.MessageType.default,
        group=False, raw=None, mentions=()):
    ctype = discord.ChannelType.group if group else (discord.ChannelType.private if dm else discord.ChannelType.text)
    raw = raw if raw is not None else [int(x) for x in re.findall(r"<@!?(\d+)>", content)]
    return SimpleNamespace(content=content, author=SimpleNamespace(id=author, bot=bot), webhook_id=webhook,
                           type=mtype, channel=SimpleNamespace(type=ctype), raw_mentions=raw,
                           mentions=[SimpleNamespace(id=i) for i in mentions])


def run(m, enabled=True):
    return should_handle(m, bot_user_id=BOT, allowed_ids={OWNER}, enabled=enabled)


@pytest.mark.parametrize("m,expected", [
    (msg("hi", dm=True), "hi"),
    (msg("hi"), None),
    (msg("<@99> hi"), "hi"),
    (msg("<@!99>  hi <@99>"), "hi"),
    (msg("hi", mentions=[BOT]), "hi"),                      # reply with ping
    (msg("@everyone hi", raw=[]), None),
    (msg("<@99> hi", author=7), None),                      # not allowlisted
    (msg("hi", dm=True, author=7), None),
    (msg("<@99> hi", bot=True), None),
    (msg("<@99> hi", webhook=1), None),
    (msg("<@99> hi", mtype=discord.MessageType.pins_add), None),
    (msg("!keys", dm=True), None),
    (msg("<@99> !keys"), None),
    (msg("hi", group=True), None),                          # group DM needs a mention
    (msg("<@99> hi", group=True), "hi"),
    (msg("<@99>"), ""),
])
def test_should_handle(m, expected):
    assert run(m) == expected


def test_disabled_ignores_everything():
    assert run(msg("hi", dm=True), enabled=False) is None
