"""Tests for utils.py — save/load state, deserialization, pickle migration."""

import json
import pickle
from datetime import datetime, timezone

import pytest

from raider import Raider
from utils import GREEN, RED, YELLOW, _deserialize, load_state, save_state


# ---------------------------------------------------------------------------
# save_state / load_state
# ---------------------------------------------------------------------------


def test_save_load_roundtrip(tmp_path, monkeypatch, make_raider, make_schedule):
    monkeypatch.chdir(tmp_path)

    raider = make_raider(user_id=1)
    sched = make_schedule(raider=raider)
    raider.add_run(sched)

    msg_id = 100
    raiders = {raider.user_id: raider}
    schedules = {msg_id: sched}
    availability = {GREEN: [raider], YELLOW: [], RED: []}

    save_state(raiders, schedules, availability, 42, {}, {})
    r_out, s_out, av_out, av_id_out, dm_map_out, dm_ts_out = load_state()

    assert 1 in r_out
    assert msg_id in s_out
    assert av_id_out == 42
    assert len(av_out[GREEN]) == 1
    assert av_out[GREEN][0].user_id == raider.user_id


def test_load_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = load_state()
    assert result == ({}, {}, {}, 0, {}, {})


def test_load_corrupt_json_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "state.json").write_text("{ not valid json }", encoding="utf-8")
    result = load_state()
    assert result == ({}, {}, {}, 0, {}, {})


def test_load_key_error_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Schedule.from_dict uses direct indexing for "date_scheduled", "full", etc.
    # Omitting those keys triggers a KeyError inside _deserialize → defaults.
    bad_data = {
        "version": 2,
        "raiders": {},
        "schedules": {"1": {"level": "10"}},  # missing "date_scheduled", "full", etc.
        "availability": {},
        "availability_message_id": 0,
        "dm_map": {},
        "dm_timestamps": {},
    }
    (tmp_path / "state.json").write_text(json.dumps(bad_data), encoding="utf-8")
    result = load_state()
    assert result == ({}, {}, {}, 0, {}, {})


def test_load_value_error_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bad_data = {
        "version": 2,
        "raiders": {},
        "schedules": {},
        "availability": {},
        "availability_message_id": 0,
        "dm_map": {},
        "dm_timestamps": {"1": {"100": "NOT_A_VALID_ISOFORMAT"}},
    }
    (tmp_path / "state.json").write_text(json.dumps(bad_data), encoding="utf-8")
    result = load_state()
    assert result == ({}, {}, {}, 0, {}, {})


def test_availability_roundtrip(tmp_path, monkeypatch, make_raider):
    monkeypatch.chdir(tmp_path)

    green_raider = make_raider(user_id=1)
    yellow_raider = make_raider(user_id=2, roles=["healer"])
    red_raider = make_raider(user_id=3, roles=["dps"])

    raiders = {1: green_raider, 2: yellow_raider, 3: red_raider}
    availability = {GREEN: [green_raider], YELLOW: [yellow_raider], RED: [red_raider]}

    save_state(raiders, {}, availability, 0, {}, {})
    _, _, av_out, _, _, _ = load_state()

    assert len(av_out[GREEN]) == 1 and av_out[GREEN][0].user_id == 1
    assert len(av_out[YELLOW]) == 1 and av_out[YELLOW][0].user_id == 2
    assert len(av_out[RED]) == 1 and av_out[RED][0].user_id == 3


def test_dm_map_roundtrip(tmp_path, monkeypatch, make_raider):
    monkeypatch.chdir(tmp_path)

    raider = make_raider(user_id=1)
    dm_map = {1: {100: 200, 101: 202}}

    save_state({1: raider}, {}, {GREEN: [], YELLOW: [], RED: []}, 0, dm_map, {})
    _, _, _, _, dm_map_out, _ = load_state()

    assert dm_map_out == dm_map


def test_dm_timestamps_roundtrip(tmp_path, monkeypatch, make_raider):
    monkeypatch.chdir(tmp_path)

    raider = make_raider(user_id=1)
    ts = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
    dm_timestamps = {1: {100: ts}}

    save_state({1: raider}, {}, {GREEN: [], YELLOW: [], RED: []}, 0, {}, dm_timestamps)
    _, _, _, _, _, dm_ts_out = load_state()

    assert 1 in dm_ts_out
    assert 100 in dm_ts_out[1]
    assert dm_ts_out[1][100] == ts


# ---------------------------------------------------------------------------
# _deserialize
# ---------------------------------------------------------------------------


def test_deserialize_cross_references(make_raider, make_schedule):
    """Raider.current_runs and denied_runs are wired to the correct Schedule objects."""
    raider = make_raider(user_id=1)
    sched = make_schedule(raider=raider)
    raider.add_run(sched)

    msg_id = 100
    schedule_to_id = {id(sched): msg_id}

    data = {
        "version": 2,
        "raiders": {str(raider.user_id): raider.to_dict(schedule_to_id)},
        "schedules": {str(msg_id): sched.to_dict(msg_id)},
        "availability": {},
        "availability_message_id": 0,
        "dm_map": {},
        "dm_timestamps": {},
    }

    raiders_out, schedules_out, *_ = _deserialize(data)

    assert msg_id in schedules_out
    assert schedules_out[msg_id] in raiders_out[1].current_runs


def test_deserialize_missing_schedule_id_skipped(make_raider):
    """A stale schedule ID in raider data that has no matching schedule is silently dropped."""
    raider = make_raider(user_id=1)
    stale_mid = 9999

    # Build the data dict directly to test the deserialization contract
    # without relying on to_dict — this ensures from_dict handles stale IDs
    # regardless of how the data was serialized.
    data = {
        "version": 2,
        "raiders": {
            "1": {
                "user_id": 1,
                "mention": "<@1>",
                "name": "TestUser",
                "class_play": "warrior",
                "roles": ["tank"],
                "timezone": "US/Eastern",
                "current_runs": [stale_mid],
                "denied_runs": [],
            }
        },
        "schedules": {},  # stale_mid not present
        "availability": {},
        "availability_message_id": 0,
        "dm_map": {},
        "dm_timestamps": {},
    }

    raiders_out, schedules_out, *_ = _deserialize(data)

    assert 1 in raiders_out
    assert raiders_out[1].current_runs == set()


# ---------------------------------------------------------------------------
# _migrate_from_pickle
# ---------------------------------------------------------------------------


def test_migrate_from_pickle(tmp_path, monkeypatch, make_raider):
    """Valid state.pkl is loaded, re-saved as state.json, and correct state returned."""
    monkeypatch.chdir(tmp_path)

    raider = make_raider(user_id=1)
    state = {
        "raiders": {raider.user_id: raider},
        "schedules": {},
        "availability": {GREEN: [raider], YELLOW: [], RED: []},
        "availability_message_id": 7,
        "dm_map": {},
        "dm_timestamps": {},
    }
    with open("state.pkl", "wb") as f:
        pickle.dump(state, f)

    # load_state should detect state.pkl (no state.json) and migrate
    raiders_out, schedules_out, av_out, av_id_out, _, _ = load_state()

    assert raider.user_id in raiders_out
    assert av_id_out == 7
    assert (tmp_path / "state.json").exists()
