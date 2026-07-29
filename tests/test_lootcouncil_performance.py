"""Tests for lootcouncil.performance — pure scoring, death order, cache freshness."""

import pytest

from lootcouncil.config import Config
from lootcouncil.models import DPS, HEALER, TANK, RawMetrics
from lootcouncil.performance import (
    component_values,
    early_death_names,
    rank_normalize,
    score_cohort,
)

# ---------------------------------------------------------------------------
# death order
# ---------------------------------------------------------------------------


def test_early_death_names_returns_the_first_two_by_timestamp():
    entries = [
        {"name": "Grom", "timestamp": 5000},
        {"name": "Thrall", "timestamp": 1000},
        {"name": "Sylvanas", "timestamp": 3000},
    ]
    assert early_death_names(entries) == ["Thrall", "Sylvanas"]


def test_early_death_names_handles_fewer_than_the_limit():
    assert early_death_names([{"name": "Thrall", "timestamp": 1}]) == ["Thrall"]
    assert early_death_names([]) == []


def test_early_death_names_respects_a_custom_limit():
    entries = [{"name": f"P{i}", "timestamp": i} for i in range(5)]
    assert early_death_names(entries, limit=3) == ["P0", "P1", "P2"]


def test_early_death_names_skips_entries_without_a_name():
    entries = [{"timestamp": 1}, {"name": "Thrall", "timestamp": 2}]
    assert early_death_names(entries) == ["Thrall"]


def test_early_death_names_treats_missing_timestamp_as_zero():
    entries = [{"name": "NoTs"}, {"name": "Late", "timestamp": 900}]
    assert early_death_names(entries) == ["NoTs", "Late"]


# ---------------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------------


def test_rank_normalize_direct_maps_best_to_one():
    assert rank_normalize(10, [0, 5, 10], invert=False) == 1.0
    assert rank_normalize(0, [0, 5, 10], invert=False) == 0.0
    assert rank_normalize(5, [0, 5, 10], invert=False) == 0.5


def test_rank_normalize_inverted_maps_lowest_to_one():
    assert rank_normalize(0, [0, 5, 10], invert=True) == 1.0
    assert rank_normalize(10, [0, 5, 10], invert=True) == 0.0


def test_rank_normalize_returns_one_when_the_cohort_is_flat():
    # Everyone equal -> nobody penalized, in both directions.
    assert rank_normalize(4, [4, 4, 4], invert=True) == 1.0
    assert rank_normalize(4, [4, 4, 4], invert=False) == 1.0


def test_rank_normalize_handles_an_empty_cohort():
    assert rank_normalize(4, [], invert=True) == 1.0


# ---------------------------------------------------------------------------
# component extraction
# ---------------------------------------------------------------------------


def test_component_values_are_per_fight_rates():
    raw = RawMetrics(
        fights=4,
        parse_total=320.0,
        parse_count=4,
        deaths=2,
        early_deaths=1,
        interrupts=6,
        dispels=2,
        damage_taken=8000,
        active_time_ms=4000,
        tmi_total=40.0,
        tmi_count=4,
    )
    vals = component_values(raw)
    assert vals["parse"] == 80.0
    assert vals["deaths"] == pytest.approx(0.75)  # (2 + 1) / 4
    assert vals["utility"] == pytest.approx(2.0)  # (6 + 2) / 4
    assert vals["damage"] == pytest.approx(2.0)  # 8000 / 4000
    assert vals["survivability"] == 10.0


def test_component_values_guard_against_zero_fights_and_time():
    vals = component_values(RawMetrics())
    assert vals["deaths"] == 0.0
    assert vals["utility"] == 0.0
    assert vals["damage"] == 0.0


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def _dps_pair():
    """Two DPS identical except that 'good' parses higher and dies less."""
    good = RawMetrics(fights=10, parse_total=900.0, parse_count=10, deaths=1, early_deaths=0,
                      damage_taken=1000, active_time_ms=1000, tmi_total=10.0, tmi_count=10)
    bad = RawMetrics(fights=10, parse_total=300.0, parse_count=10, deaths=8, early_deaths=4,
                     damage_taken=3000, active_time_ms=1000, tmi_total=30.0, tmi_count=10)
    return good, bad


def test_score_cohort_ranks_the_better_dps_higher():
    good, bad = _dps_pair()
    scores = score_cohort({"good": good, "bad": bad}, {"good": DPS, "bad": DPS}, Config())
    assert scores["good"].score > scores["bad"].score


def test_score_is_bounded_zero_to_one():
    good, bad = _dps_pair()
    scores = score_cohort({"good": good, "bad": bad}, {"good": DPS, "bad": DPS}, Config())
    for s in scores.values():
        assert 0.0 <= s.score <= 1.0


def test_more_deaths_lowers_the_score_all_else_equal():
    base = dict(fights=10, parse_total=700.0, parse_count=10, damage_taken=1000,
                active_time_ms=1000, tmi_total=10.0, tmi_count=10)
    steady = RawMetrics(deaths=0, early_deaths=0, **base)
    dying = RawMetrics(deaths=9, early_deaths=5, **base)
    scores = score_cohort({"steady": steady, "dying": dying}, {"steady": DPS, "dying": DPS}, Config())
    assert scores["steady"].score > scores["dying"].score


def test_roles_are_normalized_independently():
    # The tank takes far more damage than the healer but must not be punished for it:
    # each is the only member of its own cohort, so both normalize to 1.0 on damage.
    tank = RawMetrics(fights=10, parse_total=500.0, parse_count=10, damage_taken=50000,
                      active_time_ms=1000, tmi_total=50.0, tmi_count=10)
    healer = RawMetrics(fights=10, parse_total=500.0, parse_count=10, damage_taken=100,
                        active_time_ms=1000, tmi_total=5.0, tmi_count=10)
    scores = score_cohort({"t": tank, "h": healer}, {"t": TANK, "h": HEALER}, Config())
    assert scores["t"].components["damage"] == 1.0
    assert scores["h"].components["damage"] == 1.0


def test_utility_is_ignored_for_dps_and_counted_for_healers():
    cfg = Config()
    base = dict(fights=10, parse_total=700.0, parse_count=10, damage_taken=1000,
                active_time_ms=1000, tmi_total=10.0, tmi_count=10)
    quiet = RawMetrics(interrupts=0, dispels=0, **base)
    busy = RawMetrics(interrupts=20, dispels=20, **base)

    dps_scores = score_cohort({"q": quiet, "b": busy}, {"q": DPS, "b": DPS}, cfg)
    assert dps_scores["q"].score == pytest.approx(dps_scores["b"].score)

    heal_scores = score_cohort({"q": quiet, "b": busy}, {"q": HEALER, "b": HEALER}, cfg)
    assert heal_scores["b"].score > heal_scores["q"].score


def test_low_confidence_flag_tracks_min_fights():
    cfg = Config(min_fights=3)
    thin = RawMetrics(fights=2, parse_total=180.0, parse_count=2)
    thick = RawMetrics(fights=3, parse_total=270.0, parse_count=3)
    scores = score_cohort({"thin": thin, "thick": thick}, {"thin": DPS, "thick": DPS}, cfg)
    assert scores["thin"].low_confidence is True
    assert scores["thick"].low_confidence is False
    assert scores["thin"].fights == 2


def test_components_are_reported_for_transparency():
    good, bad = _dps_pair()
    scores = score_cohort({"good": good, "bad": bad}, {"good": DPS, "bad": DPS}, Config())
    assert set(scores["good"].components) == {"parse", "deaths", "damage", "utility", "survivability"}


def test_unknown_role_falls_back_to_dps_weights():
    good, _ = _dps_pair()
    scores = score_cohort({"g": good}, {"g": "sponge"}, Config())
    assert 0.0 <= scores["g"].score <= 1.0


def test_score_cohort_on_empty_input():
    assert score_cohort({}, {}, Config()) == {}
