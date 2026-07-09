"""Tests for the Raider.io client, mapping loader, and harvest."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import eventlog
import raiderio
from raiderio import Run, _parse_completed_at, _parse_runs, fetch_character_runs, harvest, load_character_mappings


def test_parse_completed_at_utc():
    dt = _parse_completed_at("2026-04-22T06:07:47.000Z")
    assert dt == datetime(2026, 4, 22, 6, 7, 47, tzinfo=timezone.utc)
    assert dt.tzinfo is not None


def test_parse_runs_combines_recent_and_best_and_dedups():
    data = {
        "result": {
            "mythic_plus_recent_runs": [
                {"keystone_run_id": 1, "completed_at": "2026-07-08T20:00:00.000Z", "mythic_level": 12},
                {"keystone_run_id": 2, "completed_at": "2026-07-07T02:30:00.000Z", "mythic_level": 15},
            ],
            "mythic_plus_best_runs": [
                # overlaps run_id 2 (dedup) + one new (3)
                {"keystone_run_id": 2, "completed_at": "2026-07-07T02:30:00.000Z", "mythic_level": 15},
                {"keystone_run_id": 3, "completed_at": "2026-07-06T18:00:00.000Z", "mythic_level": 20},
            ],
        }
    }
    runs = _parse_runs(data)
    assert {r.run_id for r in runs} == {1, 2, 3}
    r3 = next(r for r in runs if r.run_id == 3)
    assert r3.level == 20
    assert r3.completed_at == datetime(2026, 7, 6, 18, 0, tzinfo=timezone.utc)


def test_parse_runs_skips_malformed_and_handles_empty():
    data = {
        "result": {
            "mythic_plus_recent_runs": [
                {"keystone_run_id": 1, "completed_at": "2026-07-08T20:00:00.000Z", "mythic_level": 12},
                {"keystone_run_id": 9, "mythic_level": 10},          # missing completed_at
                {"completed_at": "2026-07-08T20:00:00.000Z", "mythic_level": 10},  # missing id
            ]
        }
    }
    runs = _parse_runs(data)
    assert [r.run_id for r in runs] == [1]
    assert _parse_runs({"result": {}}) == []
    assert _parse_runs({}) == []


def test_parse_runs_handles_null_fields():
    assert _parse_runs({"result": None}) == []
    assert _parse_runs({"result": {"mythic_plus_recent_runs": None, "mythic_plus_best_runs": None}}) == []


def test_parse_runs_skips_entry_level_null_completed_at():
    data = {"result": {"mythic_plus_recent_runs": [
        {"keystone_run_id": 1, "completed_at": "2026-07-08T20:00:00.000Z", "mythic_level": 12},
        {"keystone_run_id": 5, "completed_at": None, "mythic_level": 12},  # entry-level null -> AttributeError pre-fix
    ]}}
    runs = _parse_runs(data)
    assert [r.run_id for r in runs] == [1]  # bad entry skipped, good one kept, no raise


class _FakeResp:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload or {}
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self): return self._payload


class _FakeSession:
    def __init__(self, resp=None, get_exc=None):
        self._resp = resp
        self._get_exc = get_exc
    def get(self, url):
        if self._get_exc:
            raise self._get_exc
        return self._resp


def test_fetch_character_runs_parses_ok():
    payload = {"result": {"mythic_plus_recent_runs": [
        {"keystone_run_id": 5, "completed_at": "2026-07-08T20:00:00.000Z", "mythic_level": 12}]}}
    session = _FakeSession(resp=_FakeResp(200, payload))
    runs = asyncio.run(fetch_character_runs(session, "malganis", "Reíka"))
    assert [r.run_id for r in runs] == [5]


def test_fetch_character_runs_best_effort_on_non_200():
    session = _FakeSession(resp=_FakeResp(404, {}))
    assert asyncio.run(fetch_character_runs(session, "malganis", "Nobody")) == []


def test_fetch_character_runs_best_effort_on_error():
    session = _FakeSession(get_exc=asyncio.TimeoutError())
    assert asyncio.run(fetch_character_runs(session, "malganis", "Nobody")) == []


def test_load_character_mappings(tmp_path):
    path = str(tmp_path / "character_mappings.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([{"character": "Main", "realm_slug": "malganis", "discord_id": "1", "alt_of": None},
                   {"character": "Alt", "realm_slug": "malganis", "discord_id": "1", "alt_of": "Main"}], f)
    m = load_character_mappings(path)
    assert len(m) == 2
    assert m[1]["character"] == "Alt"


def test_load_character_mappings_missing_and_corrupt(tmp_path):
    assert load_character_mappings(str(tmp_path / "nope.json")) == []
    bad = str(tmp_path / "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{ not json")
    assert load_character_mappings(bad) == []


def test_harvest_registered_only_and_dedup(tmp_path, monkeypatch):
    path = str(tmp_path / "events.jsonl")
    raiders = {111: SimpleNamespace(timezone=ZoneInfo("America/Chicago"))}  # 111 registered; 222 not
    mappings = [
        {"discord_id": "111", "realm_slug": "malganis", "character": "Main", "alt_of": None},
        {"discord_id": "222", "realm_slug": "dalaran", "character": "Ghost", "alt_of": None},
    ]
    runs_by_char = {
        "Main": [Run(1, datetime(2026, 7, 8, 20, 0, tzinfo=timezone.utc), 20)],
        "Ghost": [Run(2, datetime(2026, 7, 8, 20, 0, tzinfo=timezone.utc), 15)],
    }

    async def fake_fetch(session, realm_slug, character):
        return runs_by_char.get(character, [])

    monkeypatch.setattr(raiderio, "fetch_character_runs", fake_fetch)

    added = asyncio.run(harvest(raiders, None, mappings, events_path=path))
    assert added == 1  # only the registered user's run
    events = eventlog.read_events(path)
    assert len(events) == 1
    e = events[0]
    assert e["type"] == "raiderio_run"
    assert e["user_id"] == 111
    assert e["run_id"] == 1
    assert e["source"] == "raiderio"
    assert e["level"] == 20
    assert e["local_weekday"] is not None  # timezone applied -> real local slot

    # Idempotent: re-running adds nothing (dedup by (user_id, run_id))
    added2 = asyncio.run(harvest(raiders, None, mappings, events_path=path))
    assert added2 == 0
    assert len(eventlog.read_events(path)) == 1
