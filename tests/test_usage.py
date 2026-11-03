from assistant.usage import DailyUsage


def test_cap_and_rollover(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    u = DailyUsage.load()
    assert u.allow("2026-11-03", 2)
    u.record("2026-11-03", 0.01)
    u.record("2026-11-03", 0.01)
    assert not u.allow("2026-11-03", 2)
    assert u.allow("2026-11-04", 2)            # new day resets
    u.save()
    again = DailyUsage.load()
    assert (again.day, again.count) == ("2026-11-04", 0)   # allow() on the new day already rolled it over


def test_persists_and_tolerates_corruption(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    u = DailyUsage.load()
    u.record("2026-11-03", 0.5)
    u.save()
    assert DailyUsage.load().count == 1
    (tmp_path / "assistant.json").write_text("{bad", encoding="utf-8")
    assert DailyUsage.load().count == 0
