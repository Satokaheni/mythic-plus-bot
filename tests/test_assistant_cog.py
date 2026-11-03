"""Assistant cog: daily cap, usage counting, and answer chunking (claude_runner stubbed)."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import discord
import pytest

from assistant.claude_runner import RunResult

OWNER, BOT = 5, 99
ANSWER = {"kind": "answer", "text": "pong"}


class _Channel:
    type = discord.ChannelType.private

    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)

    @asynccontextmanager
    async def typing(self):
        yield


def _dm(content, channel):
    return SimpleNamespace(content=content, author=SimpleNamespace(id=OWNER, bot=False), webhook_id=None,
                           type=discord.MessageType.default, channel=channel, guild=None, raw_mentions=[], mentions=[])


@pytest.fixture
def cog_and_calls(fresh_import, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # assistant.json is written relative to cwd
    monkeypatch.setenv("ASSISTANT_ENABLED", "1")
    monkeypatch.setenv("ASSISTANT_USER_IDS", str(OWNER))
    monkeypatch.setenv("ASSISTANT_DAILY_CAP", "2")
    monkeypatch.setenv("ASSISTANT_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("ASSISTANT_CONFIG_DIR", str(tmp_path / "config"))
    mod = fresh_import("cogs.assistant")
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
    assert calls == ["fake:error", "ping"], "the capped message must not reach claude"
    assert _count(tmp_path) == 2


def test_cap_survives_a_restart(cog_and_calls, tmp_path):
    mod, cog, calls, results = cog_and_calls
    results.extend([RunResult(ok=True, output=ANSWER), RunResult(ok=True, output=ANSWER)])
    for text in ("a", "b"):
        asyncio.run(cog.on_assistant_message(_dm(text, _Channel())))
    restarted = mod.Assistant(SimpleNamespace(user=SimpleNamespace(id=BOT)))  # reloads assistant.json
    channel = _Channel()
    asyncio.run(restarted.on_assistant_message(_dm("c", channel)))
    assert channel.sent == ["Daily assistant limit reached."] and calls == ["a", "b"]


def test_chunks_cap_at_three_messages(cog_and_calls):
    mod = cog_and_calls[0]
    assert mod._chunks("short") == ["short"]
    exact = mod._chunks("x" * 6000)
    assert [len(c) for c in exact] == [2000, 2000, 2000] and not exact[-1].endswith("…")
    long = mod._chunks("y" * 7000)
    assert len(long) == 3 and all(len(c) <= 2000 for c in long) and long[-1].endswith("…")
