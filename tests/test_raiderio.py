"""Tests for the Raider.io client, mapping loader, and harvest."""

from datetime import datetime, timezone

from raiderio import Run, _parse_completed_at, _parse_runs


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
