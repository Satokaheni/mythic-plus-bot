"""Tests for the WoW Token sell-alert logic."""

from tokenwatch import TokenWatch


def test_defaults_are_disabled():
    w = TokenWatch()
    assert w.threshold is None
    assert w.last_alert is None


def test_to_dict_from_dict_round_trip():
    w = TokenWatch(threshold=2_900_000_000, last_alert=3_000_000_000)
    assert TokenWatch.from_dict(w.to_dict()) == w


def test_round_trip_with_none_fields():
    w = TokenWatch()
    assert TokenWatch.from_dict(w.to_dict()) == w


def test_save_and_load(tmp_path):
    path = str(tmp_path / "token_watch.json")
    original = TokenWatch(threshold=2_900_000_000, last_alert=3_000_000_000)
    original.save(path)
    assert TokenWatch.load(path) == original


def test_load_missing_file_returns_disabled(tmp_path):
    assert TokenWatch.load(str(tmp_path / "does_not_exist.json")) == TokenWatch()


def test_load_corrupt_file_returns_disabled(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{ not json at all", encoding="utf-8")
    assert TokenWatch.load(str(path)) == TokenWatch()


def test_load_infinite_threshold_returns_disabled(tmp_path):
    path = tmp_path / "inf.json"
    path.write_text('{"token_watch": {"threshold": Infinity}}', encoding="utf-8")
    assert TokenWatch.load(str(path)) == TokenWatch()
