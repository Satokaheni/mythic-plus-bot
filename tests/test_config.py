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
