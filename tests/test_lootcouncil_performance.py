"""Tests for lootcouncil.performance — pure scoring, death order, cache freshness,
and network aggregation (_collect / _report_codes / _accumulate_report / _table)."""

import json
import time

import pytest

from lootcouncil.config import Config
from lootcouncil.models import DPS, HEALER, TANK, Character, RawMetrics
from lootcouncil.performance import (
    PerformanceAnalyzer,
    cache_is_fresh,
    component_values,
    early_death_names,
    metrics_from_dict,
    metrics_to_dict,
    rank_normalize,
    score_cohort,
)
from lootcouncil.warcraftlogs import Fight, ParseData, ReportRef

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
    base = dict(fights=10, parse_total=700.0, parse_count=10, deaths=1, early_deaths=0,
                interrupts=0, dispels=0, damage_taken=1000, active_time_ms=1000,
                tmi_total=10.0, tmi_count=10)
    metrics = RawMetrics(**base)
    scores_unknown = score_cohort({"x": metrics}, {"x": "sponge"}, Config())
    scores_dps = score_cohort({"x": metrics}, {"x": DPS}, Config())
    assert scores_unknown["x"].score == pytest.approx(scores_dps["x"].score)


def test_damage_inversion_isolated():
    """Lower damage_taken must score higher when damage is the only differing component."""
    base = dict(fights=10, parse_total=700.0, parse_count=10, deaths=1, early_deaths=0,
                interrupts=0, dispels=0, active_time_ms=1000, tmi_total=10.0, tmi_count=10)
    low_damage = RawMetrics(damage_taken=1000, **base)
    high_damage = RawMetrics(damage_taken=3000, **base)
    scores = score_cohort({"low": low_damage, "high": high_damage}, {"low": DPS, "high": DPS},
                          Config())
    assert scores["low"].score > scores["high"].score
    assert scores["low"].components["damage"] == pytest.approx(1.0)
    assert scores["high"].components["damage"] == pytest.approx(0.0)


def test_survivability_inversion_isolated_tank():
    """Lower tmi_avg must score higher for TANK when survivability is the only differing component."""
    base = dict(fights=10, parse_total=500.0, parse_count=10, deaths=0, early_deaths=0,
                interrupts=0, dispels=0, damage_taken=50000, active_time_ms=1000)
    low_tmi = RawMetrics(tmi_total=10.0, tmi_count=10, **base)
    high_tmi = RawMetrics(tmi_total=50.0, tmi_count=10, **base)
    scores = score_cohort({"low": low_tmi, "high": high_tmi}, {"low": TANK, "high": TANK},
                          Config())
    assert scores["low"].score > scores["high"].score
    assert scores["low"].components["survivability"] == pytest.approx(1.0)
    assert scores["high"].components["survivability"] == pytest.approx(0.0)


def test_score_cohort_on_empty_input():
    assert score_cohort({}, {}, Config()) == {}


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------

HOUR = 3600.0


def test_cache_is_fresh_within_the_ttl():
    cache = {"fetched_at": 1000.0, "zone_id": 42, "difficulty": 5}
    cfg = Config(season_zone_id=42, difficulty=5, cache_ttl_hours=24)
    assert cache_is_fresh(cache, cfg, now_ts=1000.0 + 23 * HOUR) is True


def test_cache_is_stale_past_the_ttl():
    cache = {"fetched_at": 1000.0, "zone_id": 42, "difficulty": 5}
    cfg = Config(season_zone_id=42, difficulty=5, cache_ttl_hours=24)
    assert cache_is_fresh(cache, cfg, now_ts=1000.0 + 25 * HOUR) is False


def test_cache_is_stale_when_the_tier_or_difficulty_changed():
    cfg = Config(season_zone_id=42, difficulty=5, cache_ttl_hours=24)
    assert cache_is_fresh({"fetched_at": 1000.0, "zone_id": 99, "difficulty": 5}, cfg, 1000.0) is False
    assert cache_is_fresh({"fetched_at": 1000.0, "zone_id": 42, "difficulty": 4}, cfg, 1000.0) is False


def test_cache_is_stale_when_empty_or_malformed():
    cfg = Config(season_zone_id=42, difficulty=5)
    assert cache_is_fresh(None, cfg, 1000.0) is False
    assert cache_is_fresh({}, cfg, 1000.0) is False


def test_metrics_roundtrip_through_dicts():
    raws = {"thrall-malganis": RawMetrics(fights=7, deaths=2, parse_total=500.0, parse_count=7)}
    restored = metrics_from_dict(metrics_to_dict(raws))
    assert restored["thrall-malganis"].fights == 7
    assert restored["thrall-malganis"].deaths == 2
    assert restored["thrall-malganis"].parse_avg == pytest.approx(500.0 / 7)


def test_metrics_from_dict_ignores_unknown_fields():
    restored = metrics_from_dict({"a": {"fights": 3, "bogus_field": 1}})
    assert restored["a"].fights == 3


def test_save_and_load_cache_roundtrip(tmp_path):
    cfg = Config(season_zone_id=42, difficulty=5)
    analyzer = PerformanceAnalyzer(cfg, wcl=None)
    path = str(tmp_path / "cache.json")
    analyzer.save_cache(path, {"thrall-malganis": RawMetrics(fights=4)})

    loaded = analyzer.load_cache(path)
    assert loaded["zone_id"] == 42
    assert loaded["difficulty"] == 5
    assert loaded["metrics"]["thrall-malganis"]["fights"] == 4

    on_disk = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    assert "fetched_at" in on_disk


def test_load_cache_returns_none_when_absent_or_corrupt(tmp_path):
    analyzer = PerformanceAnalyzer(Config(), wcl=None)
    assert analyzer.load_cache(str(tmp_path / "nope.json")) is None

    bad = tmp_path / "bad.json"
    bad.write_text("{ not json }", encoding="utf-8")
    assert analyzer.load_cache(str(bad)) is None


def test_aggregate_season_uses_a_fresh_cache_without_calling_wcl(tmp_path):
    cfg = Config(season_zone_id=42, difficulty=5, cache_ttl_hours=24)

    class ExplodingClient:
        def __getattr__(self, name):
            raise AssertionError(f"WCL must not be called on a cache hit (tried {name})")

    path = str(tmp_path / "cache.json")
    PerformanceAnalyzer(cfg, wcl=None).save_cache(path, {"thrall-malganis": RawMetrics(fights=9)})

    analyzer = PerformanceAnalyzer(cfg, wcl=ExplodingClient())
    raws = analyzer.aggregate_season(roster=[], cache_path=path)
    assert raws["thrall-malganis"].fights == 9


# ---------------------------------------------------------------------------
# network aggregation (_collect / _report_codes / _accumulate_report / _table)
# ---------------------------------------------------------------------------

_RAISE = object()  # sentinel: report_table should raise instead of returning rows


class FakeWCL:
    """Hand-written fake standing in for WarcraftLogsClient in aggregation tests.

    `tables` maps (code, tuple(fight_ids), data_type) -> rows, or the _RAISE
    sentinel to simulate that call raising. `parses`/`recent` map a character
    name to its return value; `raise_on_parse`/`raise_on_recent` are sets of
    names whose call should raise instead of returning.
    """

    def __init__(
        self, reports=None, tables=None, parses=None, recent=None, raise_on_parse=None, raise_on_recent=None
    ):
        self._reports = reports or {}
        self._tables = tables or {}
        self._parses = parses or {}
        self._recent = recent or {}
        self._raise_on_parse = raise_on_parse or set()
        self._raise_on_recent = raise_on_recent or set()

    def character_parses(self, name, server_slug, region, zone_id):
        if name in self._raise_on_parse:
            raise RuntimeError(f"parses failed for {name}")
        return self._parses.get(name, ParseData())

    def recent_report_codes(self, name, server_slug, region):
        if name in self._raise_on_recent:
            raise RuntimeError(f"recent reports failed for {name}")
        return self._recent.get(name, [])

    def report_fights(self, code, difficulty):
        return self._reports.get(code, [])

    def report_table(self, code, fight_ids, data_type):
        value = self._tables.get((code, tuple(fight_ids), data_type), [])
        if value is _RAISE:
            raise RuntimeError(f"table failed for {code} {fight_ids} {data_type}")
        return value


def _char(name, realm="Mal'Ganis", role=DPS):
    return Character(name=name, realm=realm, role=role, class_name="Shaman")


def test_collect_counts_true_fights_not_reports(tmp_path):
    """Regression for Finding 1: two reports, three total fights participated in.

    Both the per-fight single-ID DamageTaken rows (what the fixed code queries)
    and the whole-report batched row (what the old buggy code queried instead)
    are present in the fake, so this test discriminates the two implementations
    — it must fail if `_accumulate_report` goes back to batching DamageTaken
    across the report's full `fight_ids` list.
    """
    now_ms = time.time() * 1000
    thrall = _char("Thrall")
    roster = [thrall]

    reports = {
        "R1": [
            Fight(id=1, name="Boss1", encounter_id=1, difficulty=5, kill=True),
            Fight(id=2, name="Boss2", encounter_id=2, difficulty=5, kill=True),
        ],
        "R2": [Fight(id=3, name="Boss3", encounter_id=3, difficulty=5, kill=True)],
    }
    tables = {
        ("R1", (1,), "Deaths"): [],
        ("R1", (2,), "Deaths"): [],
        ("R2", (3,), "Deaths"): [],
        ("R1", (1,), "DamageTaken"): [{"name": "Thrall", "total": 100, "activeTime": 1000}],
        ("R1", (2,), "DamageTaken"): [{"name": "Thrall", "total": 150, "activeTime": 1000}],
        ("R2", (3,), "DamageTaken"): [{"name": "Thrall", "total": 200, "activeTime": 1000}],
        # The whole-report batched call an un-fixed implementation would make instead
        # of per-fight calls — WCL aggregates this to one row per actor regardless of
        # how many of the requested fights they were in.
        ("R1", (1, 2), "DamageTaken"): [{"name": "Thrall", "total": 999, "activeTime": 9999}],
        ("R1", (1, 2), "Interrupts"): [],
        ("R1", (1, 2), "Dispels"): [],
        ("R2", (3,), "Interrupts"): [],
        ("R2", (3,), "Dispels"): [],
    }
    recent = {
        "Thrall": [
            ReportRef(code="R1", zone_name="Z", start_time=int(now_ms)),
            ReportRef(code="R2", zone_name="Z", start_time=int(now_ms)),
        ]
    }

    wcl = FakeWCL(reports=reports, tables=tables, recent=recent)
    analyzer = PerformanceAnalyzer(Config(), wcl)

    cache_path = str(tmp_path / "cache.json")
    raws = analyzer.aggregate_season(roster, refresh=True, cache_path=cache_path)

    assert raws[thrall.key].fights == 3


def test_accumulate_report_early_death_attribution():
    """A character among the first two to die gets early_deaths; a later death doesn't."""
    thrall = _char("Thrall")
    jaina = _char("Jaina", role=HEALER)
    roster = [thrall, jaina]
    by_name = {c.name: c.key for c in roster}
    raws = {c.key: RawMetrics() for c in roster}

    fights = [Fight(id=1, name="Boss", encounter_id=1, difficulty=5, kill=True)]
    tables = {
        ("R1", (1,), "Deaths"): [
            {"name": "Thrall", "timestamp": 1000},
            {"name": "Grom", "timestamp": 2000},  # not on roster, ignored
            {"name": "Jaina", "timestamp": 5000},
        ],
        ("R1", (1,), "DamageTaken"): [
            {"name": "Thrall", "total": 100, "activeTime": 1000},
            {"name": "Jaina", "total": 50, "activeTime": 1000},
        ],
        ("R1", (1,), "Interrupts"): [],
        ("R1", (1,), "Dispels"): [],
    }
    analyzer = PerformanceAnalyzer(Config(), FakeWCL(tables=tables))

    analyzer._accumulate_report("R1", fights, raws, by_name)

    assert raws[thrall.key].deaths == 1
    assert raws[thrall.key].early_deaths == 1
    assert raws[jaina.key].deaths == 1
    assert raws[jaina.key].early_deaths == 0


def test_accumulate_report_skips_fight_atomically_on_partial_failure():
    """Finding 2 regression: a fight whose DamageTaken call raises leaves no partial
    mutation — not even the Deaths half that succeeded — while a healthy sibling
    fight in the same report still accumulates normally."""
    thrall = _char("Thrall")
    roster = [thrall]
    by_name = {c.name: c.key for c in roster}
    raws = {c.key: RawMetrics() for c in roster}

    fights = [
        Fight(id=10, name="Healthy", encounter_id=1, difficulty=5, kill=True),
        Fight(id=11, name="Broken", encounter_id=2, difficulty=5, kill=True),
    ]
    tables = {
        ("R1", (10,), "Deaths"): [],
        ("R1", (10,), "DamageTaken"): [{"name": "Thrall", "total": 500, "activeTime": 1000}],
        ("R1", (11,), "Deaths"): [{"name": "Thrall", "timestamp": 42}],
        ("R1", (11,), "DamageTaken"): _RAISE,
        ("R1", (10, 11), "Interrupts"): [],
        ("R1", (10, 11), "Dispels"): [],
    }
    analyzer = PerformanceAnalyzer(Config(), FakeWCL(tables=tables))

    analyzer._accumulate_report("R1", fights, raws, by_name)

    raw = raws[thrall.key]
    assert raw.fights == 1
    assert raw.damage_taken == 500
    assert raw.deaths == 0
    assert raw.early_deaths == 0


def test_report_codes_dedupes_and_excludes_stale_reports():
    now_ms = time.time() * 1000
    old_ms = now_ms - 90 * 86400 * 1000  # 90 days ago
    roster = [_char("Thrall"), _char("Jaina")]

    recent = {
        "Thrall": [
            ReportRef(code="R1", zone_name="Z", start_time=int(now_ms)),
            ReportRef(code="R2", zone_name="Z", start_time=int(old_ms)),
        ],
        "Jaina": [
            ReportRef(code="R1", zone_name="Z", start_time=int(now_ms)),
            ReportRef(code="R3", zone_name="Z", start_time=int(now_ms)),
        ],
    }
    analyzer = PerformanceAnalyzer(Config(report_lookback_days=60), FakeWCL(recent=recent))

    cutoff_ms = (time.time() - 60 * 86400) * 1000
    codes = analyzer._report_codes(roster, cutoff_ms)

    assert set(codes) == {"R1", "R3"}
    assert len(codes) == 2  # R1 is de-duplicated despite appearing for both characters


def test_collect_skips_character_whose_parses_call_raises():
    thrall = _char("Thrall")
    grom = _char("Grom")
    roster = [thrall, grom]

    parses = {"Thrall": ParseData(average=85.0, per_encounter={1: 85.0})}
    analyzer = PerformanceAnalyzer(Config(), FakeWCL(parses=parses, raise_on_parse={"Grom"}))

    raws = analyzer._collect(roster)

    assert raws[thrall.key].parse_total == pytest.approx(85.0)
    assert raws[thrall.key].parse_count == 1
    assert raws[grom.key].parse_total == 0.0
    assert raws[grom.key].parse_count == 0
