"""Tests for lootcouncil.ranker — pure normalization and the weighted blend."""

import pytest

from lootcouncil.ranker import normalize_upgrades, weighted_score  # noqa: I001

# ---------------------------------------------------------------------------
# normalize_upgrades
# ---------------------------------------------------------------------------


def test_normalize_upgrades_divides_by_the_set_maximum():
    out = normalize_upgrades({"a": 2.0, "b": 8.0, "c": 4.0})
    assert out["b"] == 1.0
    assert out["a"] == 0.25
    assert out["c"] == 0.5


def test_normalize_upgrades_single_candidate_is_one():
    assert normalize_upgrades({"solo": 3.3}) == {"solo": 1.0}


def test_normalize_upgrades_ties_all_map_to_one():
    out = normalize_upgrades({"a": 5.0, "b": 5.0, "c": 5.0})
    assert out == {"a": 1.0, "b": 1.0, "c": 1.0}


def test_normalize_upgrades_handles_empty_and_nonpositive():
    assert normalize_upgrades({}) == {}
    assert normalize_upgrades({"a": 0.0, "b": 0.0}) == {"a": 0.0, "b": 0.0}


# ---------------------------------------------------------------------------
# weighted_score
# ---------------------------------------------------------------------------


def test_weighted_score_blends_with_the_configured_weights():
    assert weighted_score(1.0, 0.5, 0.6, 0.4) == pytest.approx(0.8)
    assert weighted_score(0.0, 1.0, 0.6, 0.4) == pytest.approx(0.4)


def test_weighted_score_is_monotonic_in_both_inputs():
    assert weighted_score(0.9, 0.5, 0.6, 0.4) > weighted_score(0.5, 0.5, 0.6, 0.4)
    assert weighted_score(0.5, 0.9, 0.6, 0.4) > weighted_score(0.5, 0.5, 0.6, 0.4)


def test_performance_decides_when_upgrades_are_equally_large():
    """The primary use case from the spec: several big upgrades -> performance wins."""
    upgrades = normalize_upgrades({"ace": 9.8, "average": 9.9, "poor": 9.7})
    perf = {"ace": 0.95, "average": 0.55, "poor": 0.20}
    ranked = sorted(
        upgrades,
        key=lambda k: weighted_score(upgrades[k], perf[k], 0.6, 0.4),
        reverse=True,
    )
    assert ranked == ["ace", "average", "poor"]


def test_a_much_bigger_upgrade_can_still_outrank_better_performance():
    upgrades = normalize_upgrades({"huge": 20.0, "tiny": 1.0})
    perf = {"huge": 0.30, "tiny": 0.90}
    assert weighted_score(upgrades["huge"], perf["huge"], 0.6, 0.4) > weighted_score(
        upgrades["tiny"], perf["tiny"], 0.6, 0.4
    )
