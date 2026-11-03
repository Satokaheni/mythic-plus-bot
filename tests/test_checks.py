import asyncio
from types import SimpleNamespace

import pytest
from discord.ext import commands


def _ctx(author_id, channel_id=None, guild=True):
    return SimpleNamespace(author=SimpleNamespace(id=author_id), channel=SimpleNamespace(id=channel_id),
                           guild=object() if guild else None)


def _predicate(decorator):
    @decorator
    async def cmd(ctx):
        pass
    return cmd.__commands_checks__[0]


def test_checks_mirror_todays_gates(bot_module):
    from cogs import _checks

    assert _predicate(_checks.is_banker())(_ctx(8))
    assert not _predicate(_checks.is_banker())(_ctx(7))
    assert _predicate(_checks.is_elevated())(_ctx(7)) and _predicate(_checks.is_elevated())(_ctx(10))
    assert not _predicate(_checks.is_elevated())(_ctx(8))
    assert _predicate(_checks.in_channels(2))(_ctx(1, 2))
    assert not _predicate(_checks.in_channels(2))(_ctx(1, 2, guild=False))


@pytest.mark.parametrize("error", [commands.CommandNotFound("x"), commands.CheckFailure("x")])
def test_on_command_error_swallows_expected(bot_module, error, caplog):
    client = bot_module.MyClient.__new__(bot_module.MyClient)
    asyncio.run(client.on_command_error(SimpleNamespace(command=None, message=None), error))
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_on_command_error_logs_the_rest(bot_module, caplog):
    client = bot_module.MyClient.__new__(bot_module.MyClient)
    err = commands.CommandInvokeError(ValueError("boom"))
    asyncio.run(client.on_command_error(SimpleNamespace(command=SimpleNamespace(qualified_name="x"),
                                                        message=None), err))
    assert any(r.levelname == "ERROR" for r in caplog.records)
