"""Tests for the WoW Token sell-alert logic."""

from tokenwatch import TokenWatch, evaluate, should_rearm


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


G = 10_000                    # copper per gold
THRESHOLD = 290_000 * G       # 290,000g


def test_disabled_watch_never_fires():
    assert evaluate(TokenWatch(), 999_999 * G) is None


def test_below_threshold_is_silent():
    assert evaluate(TokenWatch(threshold=THRESHOLD), 285_000 * G) is None


def test_first_crossing_fires():
    assert evaluate(TokenWatch(threshold=THRESHOLD), 300_000 * G) == 300_000 * G


def test_exactly_at_threshold_fires():
    assert evaluate(TokenWatch(threshold=THRESHOLD), THRESHOLD) == THRESHOLD


def test_sub_step_rise_is_silent():
    w = TokenWatch(threshold=THRESHOLD, last_alert=300_000 * G)
    assert evaluate(w, 305_000 * G) is None


def test_full_step_rise_fires():
    w = TokenWatch(threshold=THRESHOLD, last_alert=300_000 * G)
    assert evaluate(w, 310_000 * G) == 310_000 * G


def test_dip_and_reclimb_below_last_alert_stays_silent():
    w = TokenWatch(threshold=THRESHOLD, last_alert=310_000 * G)
    assert evaluate(w, 302_000 * G) is None   # dipped, still above threshold
    assert evaluate(w, 315_000 * G) is None   # reclimbed, but under 310k + 10k


def test_evaluate_does_not_mutate_the_watch():
    w = TokenWatch(threshold=THRESHOLD, last_alert=300_000 * G)
    evaluate(w, 320_000 * G)
    assert w.last_alert == 300_000 * G


def test_should_rearm_only_when_alerted_and_below_threshold():
    assert should_rearm(TokenWatch(threshold=THRESHOLD, last_alert=300_000 * G), 285_000 * G) is True
    # already armed - nothing to reset
    assert should_rearm(TokenWatch(threshold=THRESHOLD, last_alert=None), 285_000 * G) is False
    # still above threshold
    assert should_rearm(TokenWatch(threshold=THRESHOLD, last_alert=300_000 * G), 295_000 * G) is False
    # disabled
    assert should_rearm(TokenWatch(), 100 * G) is False


def test_worked_sequence_from_the_spec():
    """Walk the spec's worked example end to end, driving the ratchet exactly as the task does."""
    w = TokenWatch(threshold=THRESHOLD)
    steps = [
        (285_000, None),      # below threshold
        (300_000, 300_000),   # crosses -> DM
        (305_000, None),      # only +5,000g
        (310_000, 310_000),   # new high -> DM
        (302_000, None),      # dip, still above threshold
        (315_000, None),      # under 310k + 10k
        (320_000, 320_000),   # new high -> DM
        (288_000, None),      # drops below threshold -> re-arms
        (295_000, 295_000),   # crosses again -> DM
    ]
    for gold, expected in steps:
        price = gold * G
        if should_rearm(w, price):
            w.last_alert = None
        fired = evaluate(w, price)
        assert fired == (expected * G if expected is not None else None), f"at {gold:,}g"
        if fired is not None:
            w.last_alert = fired
