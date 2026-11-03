"""!help lists the Assistant only when it is enabled."""

import asyncio
from types import SimpleNamespace

import pytest


def _help_field_names(fresh_import):
    admin = fresh_import("cogs.admin")
    sent = []

    async def send(content=None, **kwargs):
        sent.append(kwargs["embed"])

    ctx = SimpleNamespace(message=SimpleNamespace(channel=SimpleNamespace(send=send)))
    asyncio.run(admin.Admin.cmd_help.callback(admin.Admin(SimpleNamespace()), ctx))
    return [field.name for field in sent[0].fields]


@pytest.mark.parametrize("enabled, listed", [("0", False), ("1", True)])
def test_assistant_field_follows_assistant_enabled(fresh_import, monkeypatch, enabled, listed):
    monkeypatch.setenv("ASSISTANT_ENABLED", enabled)
    names = _help_field_names(fresh_import)
    assert any("Assistant" in name for name in names) is listed
    assert any("Scheduling" in name for name in names) and any("Help" in name for name in names)
