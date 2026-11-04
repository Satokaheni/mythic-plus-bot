"""Assistant cog: daily cap, usage counting, answer chunking, conversation context (claude_runner stubbed)."""

import asyncio
import importlib
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from itertools import count
from types import SimpleNamespace

import discord
import pytest
from discord.ext import commands, tasks

from mythicbot.assistant.claude_runner import RunResult

OWNER, BOT = 5, 99
ANSWER = {"kind": "answer", "text": "pong"}
_ids = count(1)


class _Channel:
    type = discord.ChannelType.private

    def __init__(self, earlier=()):
        self.sent, self.kwargs = [], []
        self.earlier = list(earlier)  # newest first, as discord.py's history() yields
        self.history_calls = 0

    async def send(self, content=None, **kwargs):
        self.sent.append(content)
        self.kwargs.append(kwargs)

    async def history(self, *, limit, before):
        self.history_calls += 1
        for m in self.earlier:
            yield m

    @asynccontextmanager
    async def typing(self):
        yield


def _dm(content, channel, *, author=OWNER, reply_to=None):
    msg_id = next(_ids)
    return SimpleNamespace(
        id=msg_id, content=content, author=SimpleNamespace(id=author, bot=author == BOT), webhook_id=None,
        type=discord.MessageType.default, channel=channel, guild=None, raw_mentions=[], mentions=[],
        created_at=datetime.now(timezone.utc),
        reference=SimpleNamespace(message_id=reply_to.id) if reply_to else None,
        to_reference=lambda fail_if_not_exists=True: SimpleNamespace(message_id=msg_id,
                                                                      fail_if_not_exists=fail_if_not_exists))


def _messages(calls):
    return [json.loads(c)["message"] for c in calls]


@pytest.fixture
def cog_and_calls(fresh_import, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # assistant.json is written relative to cwd
    monkeypatch.setenv("ASSISTANT_ENABLED", "1")
    monkeypatch.setenv("ASSISTANT_USER_IDS", str(OWNER))
    monkeypatch.setenv("ASSISTANT_DAILY_CAP", "2")
    monkeypatch.setenv("ASSISTANT_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("ASSISTANT_CONFIG_DIR", str(tmp_path / "config"))
    mod = fresh_import("mythicbot.cogs.assistant")
    calls, results = [], []

    async def fake_run_claude(prompt, **kwargs):
        calls.append(prompt)
        return results.pop(0)

    monkeypatch.setattr(mod.claude_runner, "run_claude", fake_run_claude)
    cog = mod.Assistant(SimpleNamespace(user=SimpleNamespace(id=BOT)))
    return mod, cog, calls, results


def _count(tmp_path):
    return json.loads((tmp_path / "assistant.json").read_text(encoding="utf-8"))["count"]


def test_failed_and_successful_calls_are_counted_then_capped(cog_and_calls, tmp_path):
    mod, cog, calls, results = cog_and_calls
    channel = _Channel()
    results.extend([RunResult(ok=False, error="claude error_during_execution", cost_usd=0.001),
                    RunResult(ok=True, output=ANSWER, cost_usd=0.002)])

    asyncio.run(cog.on_assistant_message(_dm("fake:error", channel)))
    assert channel.sent == [mod.FAILURE] and _count(tmp_path) == 1  # a failed call still counts

    asyncio.run(cog.on_assistant_message(_dm("ping", channel)))
    assert channel.sent[-1] == "pong" and _count(tmp_path) == 2

    asyncio.run(cog.on_assistant_message(_dm("one more", channel)))
    assert channel.sent[-1] == "Daily assistant limit reached."
    assert _messages(calls) == ["fake:error", "ping"], "the capped message must not reach claude"
    assert _count(tmp_path) == 2


def test_cap_survives_a_restart(cog_and_calls, tmp_path):
    mod, cog, calls, results = cog_and_calls
    results.extend([RunResult(ok=True, output=ANSWER), RunResult(ok=True, output=ANSWER)])
    for text in ("a", "b"):
        asyncio.run(cog.on_assistant_message(_dm(text, _Channel())))
    restarted = mod.Assistant(SimpleNamespace(user=SimpleNamespace(id=BOT)))  # reloads assistant.json
    channel = _Channel()
    asyncio.run(restarted.on_assistant_message(_dm("c", channel)))
    assert channel.sent == ["Daily assistant limit reached."] and _messages(calls) == ["a", "b"]


def test_chunks_cap_at_three_messages(cog_and_calls):
    mod = cog_and_calls[0]
    assert mod._chunks("short") == ["short"]
    exact = mod._chunks("x" * 6000)
    assert [len(c) for c in exact] == [2000, 2000, 2000] and not exact[-1].endswith("…")
    long = mod._chunks("y" * 7000)
    assert len(long) == 3 and all(len(c) <= 2000 for c in long) and long[-1].endswith("…")


def test_refused_write_gets_the_access_reply_before_any_confirm_prompt(cog_and_calls):
    mod, cog, _calls, results = cog_and_calls
    invoked = []

    class _Watch:
        name = "watch"

        async def can_run(self, ctx):
            raise commands.CheckFailure("banker only")

    async def get_context(proxy):
        return SimpleNamespace(command=_Watch(), message=proxy)

    async def invoke(ctx):
        invoked.append(ctx)

    cog.bot = SimpleNamespace(user=SimpleNamespace(id=BOT), get_context=get_context, invoke=invoke)
    results.append(RunResult(ok=True, output={"kind": "command", "command": "watch", "args": ["241326"]}))
    channel = _Channel()

    asyncio.run(cog.on_assistant_message(_dm("watch item 241326", channel)))

    assert channel.sent == ["You don't have access to `!watch` here."]
    assert invoked == []


def test_prompt_carries_the_collected_history(cog_and_calls):
    _mod, cog, calls, results = cog_and_calls
    channel = _Channel()
    asked = _dm("snipe the vibrant shard", channel)
    question = _dm("What max price, in gold?", channel, author=BOT, reply_to=asked)
    channel.earlier = [question, asked]
    results.append(RunResult(ok=True, output=ANSWER))

    asyncio.run(cog.on_assistant_message(_dm("5000", channel)))

    assert json.loads(calls[0]) == {"history": [{"role": "user", "text": "snipe the vibrant shard"},
                                                {"role": "assistant", "text": "What max price, in gold?"}],
                                    "message": "5000"}


@pytest.mark.parametrize("text,expected", [("ping", "pong"), ("", "What can I help with?")])
def test_every_reply_threads_to_the_message_without_pinging(cog_and_calls, text, expected):
    mod, cog, _calls, results = cog_and_calls
    channel = _Channel()
    results.append(RunResult(ok=True, output=ANSWER))
    message = _dm(text, channel)

    asyncio.run(cog.on_assistant_message(message))

    assert channel.sent == [expected]
    kwargs = channel.kwargs[0]
    assert kwargs["reference"].message_id == message.id and kwargs["reference"].fail_if_not_exists is False
    assert kwargs["allowed_mentions"] is mod.NO_MENTIONS


def test_reply_without_read_message_history_falls_back_to_a_plain_send(cog_and_calls):
    mod, cog, _calls, results = cog_and_calls

    class _NoHistoryChannel(_Channel):
        async def send(self, content=None, **kwargs):
            if "reference" in kwargs:  # Discord refuses a reply without Read Message History (code 160002)
                raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"),
                                        {"code": 160002, "message": "Cannot reply without permission to read message history"})
            await super().send(content, **kwargs)

    channel = _NoHistoryChannel()
    results.append(RunResult(ok=True, output=ANSWER))

    asyncio.run(cog.on_assistant_message(_dm("ping", channel)))

    assert channel.sent == ["pong"] and "reference" not in channel.kwargs[0]
    assert channel.kwargs[0]["allowed_mentions"] is mod.NO_MENTIONS


def test_context_off_never_reads_history(cog_and_calls, monkeypatch):
    mod, cog, calls, results = cog_and_calls
    monkeypatch.setattr(mod.config, "ASSISTANT_CONTEXT_MINUTES", 0)
    channel = _Channel()
    results.append(RunResult(ok=True, output=ANSWER))

    asyncio.run(cog.on_assistant_message(_dm("ping", channel)))

    assert channel.history_calls == 0 and json.loads(calls[0]) == {"history": [], "message": "ping"}


def test_reset_starts_fresh_without_calling_claude(cog_and_calls, tmp_path):
    mod, cog, calls, _results = cog_and_calls
    channel = _Channel()

    asyncio.run(cog.on_assistant_message(_dm("Reset.", channel)))

    assert channel.sent == [mod.RESET_REPLY] and calls == []
    assert not (tmp_path / "assistant.json").exists(), "a reset is not a claude call and is not counted"


def test_reset_with_context_off_goes_to_claude(cog_and_calls, monkeypatch):
    mod, cog, calls, results = cog_and_calls
    monkeypatch.setattr(mod.config, "ASSISTANT_CONTEXT_MESSAGES", 0)
    results.append(RunResult(ok=True, output=ANSWER))

    asyncio.run(cog.on_assistant_message(_dm("Reset!", _Channel())))

    assert _messages(calls) == ["Reset!"]


# ---------------------------------------------------------------------------
# Startup: the real cog set loads, and a bad assistant setup disables only the assistant
# ---------------------------------------------------------------------------


@pytest.fixture
def boot(fresh_import, monkeypatch, tmp_path):
    """Build the real MyClient and load every cogs.EXTENSIONS entry, loops not started."""
    monkeypatch.chdir(tmp_path)  # state files are read and written relative to cwd
    monkeypatch.setenv("ASSISTANT_ENABLED", "1")
    monkeypatch.setenv("ASSISTANT_USER_IDS", str(OWNER))
    monkeypatch.setenv("ASSISTANT_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("ASSISTANT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(tasks.Loop, "start", lambda self, *args, **kwargs: None)
    bot = fresh_import("bot")

    async def _boot():
        client = bot.MyClient(intents=bot.intents)
        await client._async_setup_hook()
        await client.setup_hook()  # BotState.load, persistent views, load_extension for every cogs.EXTENSIONS entry
        return client

    return bot, _boot


def test_every_assistant_command_resolves_on_the_real_bot(boot):
    bot, _boot = boot
    client = asyncio.run(_boot())
    intent = importlib.import_module("mythicbot.assistant.intent")
    cogs = importlib.import_module("mythicbot.cogs")

    assert len(client.cogs) == len(cogs.EXTENSIONS)
    assert [name for name in intent.USAGE if client.get_command(name) is None] == []
    assert client.get_cog("Assistant").disabled is False


def test_unknown_assistant_command_disables_the_assistant_not_the_bot(boot, monkeypatch, caplog):
    bot, _boot = boot
    intent = importlib.import_module("mythicbot.assistant.intent")
    monkeypatch.setattr(intent, "USAGE", {**intent.USAGE, "renamedaway": "!renamedaway — gone"})
    cogs = importlib.import_module("mythicbot.cogs")

    with caplog.at_level(logging.ERROR):
        client = asyncio.run(_boot())

    assert len(client.cogs) == len(cogs.EXTENSIONS), "every other cog must still load"
    cog = client.get_cog("Assistant")
    assert cog.disabled is True
    assert any(r.levelno == logging.ERROR and "renamedaway" in r.getMessage() for r in caplog.records)
    calls = []

    async def fake_run_claude(prompt, **kwargs):
        calls.append(prompt)

    monkeypatch.setattr(cog_module(), "claude_runner", SimpleNamespace(run_claude=fake_run_claude))
    channel = _Channel()
    asyncio.run(cog.on_assistant_message(_dm("hello", channel)))
    assert channel.sent == [] and calls == []


def cog_module():
    return importlib.import_module("mythicbot.cogs.assistant")


def _load(mod, monkeypatch, work_dir, config_dir):
    monkeypatch.setattr(mod.config, "ASSISTANT_WORK_DIR", str(work_dir))
    monkeypatch.setattr(mod.config, "ASSISTANT_CONFIG_DIR", str(config_dir))
    cog = mod.Assistant(SimpleNamespace(user=SimpleNamespace(id=BOT), get_command=lambda name: object()))
    asyncio.run(cog.cog_load())
    return cog


def test_work_dir_inside_a_git_work_tree_disables_the_assistant(cog_and_calls, monkeypatch, tmp_path, caplog):
    mod = cog_and_calls[0]
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)

    with caplog.at_level(logging.ERROR):
        cog = _load(mod, monkeypatch, checkout / "deep" / "work", tmp_path / "outside-config")

    assert cog.disabled is True
    assert not (tmp_path / "outside-config" / "system_prompt.md").exists()
    assert any(r.levelno == logging.ERROR and "git work tree" in r.getMessage() for r in caplog.records)


def test_config_dir_inside_the_bot_repo_disables_the_assistant(cog_and_calls, monkeypatch, tmp_path, caplog):
    mod = cog_and_calls[0]
    repo = tmp_path / "deployed-bot"  # e.g. the Docker image's /app: the bot's own dir, no .git
    repo.mkdir()
    monkeypatch.setattr(mod, "_REPO_DIR", repo)

    with caplog.at_level(logging.ERROR):
        cog = _load(mod, monkeypatch, tmp_path / "work", repo / "assistant-config")

    assert cog.disabled is True
    assert not (repo / "assistant-config").exists()
    assert any(r.levelno == logging.ERROR and "bot's own directory" in r.getMessage() for r in caplog.records)


def test_dirs_outside_any_repo_keep_the_assistant_enabled(cog_and_calls, monkeypatch, tmp_path):
    mod = cog_and_calls[0]
    cog = _load(mod, monkeypatch, tmp_path / "work", tmp_path / "config")
    assert cog.disabled is False
    assert (tmp_path / "config" / "system_prompt.md").is_file()
