import pytest


def test_elevated_ids_are_coordinator_plus_admins(fresh_import):
    assert fresh_import("config").ELEVATED_IDS == {7, 10}


def test_admin_id_list_skips_blanks(fresh_import, monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "10, ,11,")
    assert fresh_import("config").ADMINS == [10, 11]


def test_missing_required_env_raises(fresh_import, monkeypatch):
    monkeypatch.delenv("BANKER_ID")
    with pytest.raises(RuntimeError, match="BANKER_ID"):
        fresh_import("config")
