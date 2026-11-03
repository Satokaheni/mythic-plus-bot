"""Shared pytest fixtures for the Mythic+ bot test suite."""

import importlib
import logging
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import dotenv
import pytest

from mythicbot.scheduling.raider import Raider
from mythicbot.scheduling.schedule import Schedule


@pytest.fixture
def make_raider():
    """Fixture-factory that returns a Raider built from a FakeMember SimpleNamespace.

    FakeMember is ONLY passed to Raider.__init__ — never directly to Schedule.__init__.
    Schedule.__init__ always receives a Raider object.
    """

    def _factory(user_id=1, roles=None, class_play="warrior", tz="US/Eastern", name="TestUser"):
        if roles is None:
            roles = ["tank"]
        member = SimpleNamespace(
            id=user_id,
            mention=f"<@{user_id}>",
            display_name=name,
        )
        return Raider(member, class_play, roles, tz)

    return _factory


@pytest.fixture
def make_schedule(make_raider):
    """Fixture-factory that returns a Schedule with the given raider as organizer.

    The organizer is always in the team (1 signup) after construction.
    start_time must be timezone-aware; defaults to now(UTC) + 24 hours.
    date_str is derived from start_time.strftime("%Y-%m-%d").
    """

    def _factory(raider=None, start_time=None, run_type="one", level="10"):
        if raider is None:
            raider = make_raider()
        if start_time is None:
            start_time = datetime.now(timezone.utc) + timedelta(hours=24)
        date_str = start_time.strftime("%Y-%m-%d")
        return Schedule(raider, level, date_str, start_time, run_type)

    return _factory


@pytest.fixture
def make_full_schedule(make_raider, make_schedule):
    """Fixture-factory that returns a Schedule with all 5 slots filled.

    Accepts an optional list of exactly 5 Raiders with roles:
    [tank], [healer], [dps], [dps], [dps].
    Default raiders use user_ids 1-5.

    Note: raider_signup does not call raider.add_run(), so each raider's
    current_runs is empty after construction. Tests that need current_runs
    populated must call raider.add_run(sched) explicitly.
    """

    def _factory(raiders=None):
        if raiders is None:
            raiders = [
                make_raider(user_id=1, roles=["tank"], name="Tank"),
                make_raider(user_id=2, roles=["healer"], name="Healer"),
                make_raider(user_id=3, roles=["dps"], name="DPS1"),
                make_raider(user_id=4, roles=["dps"], name="DPS2"),
                make_raider(user_id=5, roles=["dps"], name="DPS3"),
            ]
        assert len(raiders) == 5
        sched = make_schedule(raider=raiders[0])
        for r in raiders[1:]:
            sched.raider_signup(r)
        return sched

    return _factory


_REQUIRED_ENV = {
    "CLIENT_KEY": "x", "AVAIL_CHANNEL_ID": "1", "KEY_CHANNEL_ID": "2", "GUILD_ID": "3", "TANK_ROLE_ID": "4",
    "HEALER_ROLE_ID": "5", "DPS_ROLE_ID": "6", "COORDINATOR_ID": "7", "BANKER_ID": "8", "BLIZZ_CLIENT_ID": "x",
    "BLIZZ_CLIENT_SECRET": "x", "MYTHIC_PLUS_ID": "9", "ADMIN_ID": "10",
}
_FRESH = ("bot", "mythicbot.config", "mythicbot.messages", "mythicbot.state")
_FRESH_PACKAGES = ("mythicbot.cogs", "mythicbot.assistant")


def _forget_bot_modules():
    for name in list(sys.modules):
        if name in _FRESH or name in _FRESH_PACKAGES or name.startswith(tuple(p + "." for p in _FRESH_PACKAGES)):
            sys.modules.pop(name, None)
            # `from mythicbot import config` reads the parent package's attribute before sys.modules,
            # so drop that too or the next import would hand back the stale module.
            parent, _, leaf = name.rpartition(".")
            if parent in sys.modules and getattr(sys.modules[parent], leaf, None) is not None:
                delattr(sys.modules[parent], leaf)


@pytest.fixture
def fresh_import(monkeypatch, tmp_path):
    """Import an env-reading module (bot, config, ...) fresh, with fake env; undo its log handlers."""
    for name, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "bot.log"))
    monkeypatch.setenv("BLIZZ_REGION", "us")
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level

    def _import(name: str):
        _forget_bot_modules()
        return importlib.import_module(name)

    yield _import
    _forget_bot_modules()
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers, root.level = handlers, level


@pytest.fixture
def bot_module(fresh_import):
    return fresh_import("bot")
