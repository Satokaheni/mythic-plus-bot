import logging

import pytest


def test_elevated_ids_are_coordinator_plus_admins(fresh_import):
    assert fresh_import("mythicbot.config").ELEVATED_IDS == {7, 10}


def test_admin_id_list_skips_blanks(fresh_import, monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "10, ,11,")
    assert fresh_import("mythicbot.config").ADMINS == [10, 11]


def test_missing_required_env_raises(fresh_import, monkeypatch):
    monkeypatch.delenv("BANKER_ID")
    with pytest.raises(RuntimeError, match="BANKER_ID"):
        fresh_import("mythicbot.config")


def test_assistant_defaults_off_and_parses_ids(fresh_import, monkeypatch):
    monkeypatch.delenv("ASSISTANT_ENABLED", raising=False)
    monkeypatch.setenv("ASSISTANT_USER_IDS", "5, x,6")
    cfg = fresh_import("mythicbot.config")
    assert cfg.ASSISTANT_ENABLED is False and cfg.ASSISTANT_USER_IDS == frozenset({5, 6})


def test_assistant_context_defaults(fresh_import, monkeypatch):
    monkeypatch.delenv("ASSISTANT_CONTEXT_MINUTES", raising=False)
    monkeypatch.delenv("ASSISTANT_CONTEXT_MESSAGES", raising=False)
    cfg = fresh_import("mythicbot.config")
    assert (cfg.ASSISTANT_CONTEXT_MINUTES, cfg.ASSISTANT_CONTEXT_MESSAGES) == (30, 10)


def test_assistant_context_bad_value_falls_back_with_a_warning(fresh_import, monkeypatch, caplog):
    monkeypatch.setenv("ASSISTANT_CONTEXT_MINUTES", "half an hour")
    monkeypatch.setenv("ASSISTANT_CONTEXT_MESSAGES", "0")
    with caplog.at_level(logging.WARNING):
        cfg = fresh_import("mythicbot.config")
    assert cfg.ASSISTANT_CONTEXT_MINUTES == 30 and cfg.ASSISTANT_CONTEXT_MESSAGES == 0
    assert any("ASSISTANT_CONTEXT_MINUTES" in r.getMessage() for r in caplog.records)
