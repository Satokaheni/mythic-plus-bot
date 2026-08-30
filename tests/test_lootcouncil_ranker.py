"""Tests for lootcouncil.ranker — pure normalization and the weighted blend."""

from typing import Dict, Optional
from unittest.mock import MagicMock

import pytest

from loot import format_loot_table, format_performance_table, main, parse_weights
from lootcouncil.config import Config
from lootcouncil.models import DPS, HEALER, TANK, Character, PerformanceScore, RawMetrics, UpgradeInfo
from lootcouncil.ranker import LootRanker, LootResult, RankedCandidate, normalize_upgrades, weighted_score

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
    """The primary use case from the spec: several big upgrades -> performance wins.

    This test proves that normalize_upgrades is essential: without it (raw values),
    average would rank first despite ace's superior performance. With normalization,
    ace's smaller raw upgrade is scaled up to match average's, letting performance
    (0.95 vs 0.20) decide the order.
    """
    upgrades = normalize_upgrades({"ace": 9.0, "average": 10.0, "poor": 9.5})
    perf = {"ace": 0.95, "average": 0.20, "poor": 0.50}
    ranked = sorted(
        upgrades,
        key=lambda k: weighted_score(upgrades[k], perf[k], 0.6, 0.4),
        reverse=True,
    )
    # ace ranks first (0.92) despite average having the largest raw upgrade (10.0),
    # proving the normalization mechanic works.
    assert ranked == ["ace", "poor", "average"]


def test_a_much_bigger_upgrade_can_still_outrank_better_performance():
    upgrades = normalize_upgrades({"huge": 20.0, "tiny": 1.0})
    perf = {"huge": 0.30, "tiny": 0.90}
    assert weighted_score(upgrades["huge"], perf["huge"], 0.6, 0.4) > weighted_score(
        upgrades["tiny"], perf["tiny"], 0.6, 0.4
    )


# ---------------------------------------------------------------------------
# LootRanker.rank() integration tests
# ---------------------------------------------------------------------------


class FakeWoWAudit:
    """Fake WoWAudit for testing LootRanker without network I/O."""

    def __init__(self, roster: Optional[Dict[str, Character]] = None,
                 upgrades: Optional[Dict[int, Dict[str, UpgradeInfo]]] = None):
        self._roster = list(roster.values()) if roster else []
        self._upgrades = upgrades or {}

    def roster(self):
        return self._roster

    def upgrades_for(self, item_id: int, difficulty: int):
        return self._upgrades.get(item_id, {})


class FakePerformanceAnalyzer:
    """Fake PerformanceAnalyzer for testing LootRanker without network I/O."""

    def __init__(self, raws: Optional[Dict[str, RawMetrics]] = None):
        self._raws = raws or {}

    def aggregate_season(self, roster, refresh=False):
        return self._raws


def test_rank_normalizes_upgrades_within_each_role():
    """Upgrades are scaled within role, not globally across roles."""
    cfg = Config()
    tank = Character(name="Thrall", realm="Area 52", role=TANK, class_name="Warrior")
    dps = Character(name="Gul'dan", realm="Area 52", role=DPS, class_name="Warlock")

    roster = {
        tank.key: tank,
        dps.key: dps,
    }

    upgrades = {
        1001: {
            tank.key: UpgradeInfo(percentage=8.0, absolute=100, spec="Protection"),
            dps.key: UpgradeInfo(percentage=12.0, absolute=150, spec="Destruction"),
        }
    }

    raws = {
        tank.key: RawMetrics(fights=5, parse_total=400.0, parse_count=5, damage_taken=5000,
                              active_time_ms=1000, tmi_total=5.0, tmi_count=5),
        dps.key: RawMetrics(fights=5, parse_total=350.0, parse_count=5, damage_taken=1000,
                             active_time_ms=1000, tmi_total=2.0, tmi_count=5),
    }

    wowaudit = FakeWoWAudit(roster, upgrades)
    analyzer = FakePerformanceAnalyzer(raws)
    ranker = LootRanker(cfg, wowaudit, analyzer)

    result = ranker.rank(1001, difficulty=5)

    # Each role's top candidate should normalize to 1.0 within their role.
    assert result.by_role[TANK][0].upgrade_norm == 1.0
    assert result.by_role[DPS][0].upgrade_norm == 1.0
    # If upgrades were normalized globally, the DPS (12.0) would be 1.0 and tank (8.0) would be ~0.67.
    # Proving they're normalized within-role.


def test_rank_filters_out_roster_mismatches():
    """Wishlist entries whose key is not in the roster are dropped."""
    cfg = Config()
    roster_member = Character(name="Arthas", realm="Area 52", role=TANK, class_name="Deathknight")

    roster = {roster_member.key: roster_member}

    upgrades = {
        1001: {
            roster_member.key: UpgradeInfo(percentage=5.0, absolute=50, spec="Blood"),
            "nonexistent-area52": UpgradeInfo(percentage=8.0, absolute=80, spec="Frost"),
        }
    }

    raws = {
        roster_member.key: RawMetrics(fights=3, parse_total=225.0, parse_count=3, damage_taken=3000,
                                       active_time_ms=1000, tmi_total=3.0, tmi_count=3),
    }

    wowaudit = FakeWoWAudit(roster, upgrades)
    analyzer = FakePerformanceAnalyzer(raws)
    ranker = LootRanker(cfg, wowaudit, analyzer)

    result = ranker.rank(1001, difficulty=5)

    # Only the roster member should be in the result.
    assert len(result.by_role[TANK]) == 1
    assert result.by_role[TANK][0].key == roster_member.key


def test_rank_handles_missing_performance_score():
    """A roster member with no performance data gets 0.0 score and low_confidence=True."""
    cfg = Config()
    good = Character(name="Uther", realm="Area 52", role=HEALER, class_name="Paladin")
    missing = Character(name="Malfurion", realm="Area 52", role=HEALER, class_name="Druid")

    roster = {good.key: good, missing.key: missing}

    upgrades = {
        2001: {
            good.key: UpgradeInfo(percentage=6.0, absolute=60, spec="Holy"),
            missing.key: UpgradeInfo(percentage=7.0, absolute=70, spec="Restoration"),
        }
    }

    raws = {
        good.key: RawMetrics(fights=10, parse_total=850.0, parse_count=10, damage_taken=1000,
                              active_time_ms=1000, tmi_total=10.0, tmi_count=10),
        # missing.key intentionally absent - will trigger the missing performance data path
    }

    wowaudit = FakeWoWAudit(roster, upgrades)
    analyzer = FakePerformanceAnalyzer(raws)
    ranker = LootRanker(cfg, wowaudit, analyzer)

    result = ranker.rank(2001, difficulty=5)

    missing_cand = next(c for c in result.by_role[HEALER] if c.key == missing.key)
    assert missing_cand.performance == 0.0
    assert missing_cand.low_confidence is True
    assert missing_cand.components == {}


def test_rank_returns_empty_result_when_nobody_wishlists():
    """When no one wishlists the item, rank() returns a LootResult with is_empty=True."""
    cfg = Config()
    raider = Character(name="Illidan", realm="Area 52", role=DPS, class_name="Demon Hunter")
    roster = {raider.key: raider}
    # No upgrades for item 9999
    upgrades = {}

    scores = {
        raider.key: PerformanceScore(score=0.9, components={}, fights=5, low_confidence=False),
    }

    wowaudit = FakeWoWAudit(roster, upgrades)
    analyzer = FakePerformanceAnalyzer(scores)
    ranker = LootRanker(cfg, wowaudit, analyzer)

    result = ranker.rank(9999, difficulty=5)

    assert result.is_empty is True
    assert result.by_role == {}


def test_rank_produces_different_orderings_by_loot_score_and_performance():
    """by_role and performance_by_role can produce different orderings."""
    cfg = Config()
    # Construct two candidates where ranking by loot_score differs from ranking by performance.
    high_upgrade_low_perf = Character(name="Sargeras", realm="Area 52", role=DPS, class_name="Demon")
    low_upgrade_high_perf = Character(name="Velen", realm="Area 52", role=DPS, class_name="Priest")

    roster = {
        high_upgrade_low_perf.key: high_upgrade_low_perf,
        low_upgrade_high_perf.key: low_upgrade_high_perf,
    }

    upgrades = {
        3001: {
            high_upgrade_low_perf.key: UpgradeInfo(percentage=20.0, absolute=200, spec="Demonology"),
            low_upgrade_high_perf.key: UpgradeInfo(percentage=5.0, absolute=50, spec="Shadow"),
        }
    }

    raws = {
        high_upgrade_low_perf.key: RawMetrics(
            fights=5, parse_total=150.0, parse_count=5, damage_taken=1000,
            active_time_ms=1000, tmi_total=2.0, tmi_count=5
        ),
        low_upgrade_high_perf.key: RawMetrics(
            fights=10, parse_total=950.0, parse_count=10, damage_taken=500,
            active_time_ms=1000, tmi_total=1.0, tmi_count=10
        ),
    }

    wowaudit = FakeWoWAudit(roster, upgrades)
    analyzer = FakePerformanceAnalyzer(raws)
    ranker = LootRanker(cfg, wowaudit, analyzer)

    result = ranker.rank(3001, difficulty=5)

    # by_role should rank by loot_score (upgrade-heavy with 0.6 weight).
    # performance_by_role should rank by performance.
    by_loot_order = [c.key for c in result.by_role[DPS]]
    by_perf_order = [c.key for c in result.performance_by_role[DPS]]

    # The orderings should differ: loot_score favors the big upgrade,
    # performance favors the high-perf candidate.
    assert by_loot_order != by_perf_order


# ---------------------------------------------------------------------------
# CLI formatting
# ---------------------------------------------------------------------------


def _candidate(name, role=DPS, upgrade=5.0, norm=1.0, perf=0.8, low=False):
    return RankedCandidate(
        key=f"{name.lower()}-illidan",
        name=name,
        role=role,
        spec="Fury",
        upgrade_pct=upgrade,
        upgrade_norm=norm,
        performance=perf,
        components={"parse": 0.9, "deaths": 0.8, "damage": 0.7, "utility": 0.0, "survivability": 0.0},
        loot_score=0.6 * norm + 0.4 * perf,
        low_confidence=low,
    )


def _result(rows):
    by_role = {}
    for row in rows:
        by_role.setdefault(row.role, []).append(row)
    return LootResult(
        item_id=215147,
        difficulty=5,
        weight_upgrade=0.6,
        weight_performance=0.4,
        by_role=by_role,
        performance_by_role=by_role,
    )


def test_parse_weights_accepts_a_comma_pair():
    assert parse_weights("0.7,0.3") == (0.7, 0.3)


def test_parse_weights_rejects_malformed_input():
    for bad in ("0.7", "a,b", "", "0.1,0.2,0.3"):
        with pytest.raises(ValueError):
            parse_weights(bad)


def test_loot_table_lists_candidates_best_first_with_the_weights_in_the_header():
    result = _result([_candidate("Ace", perf=0.9, norm=1.0), _candidate("Rookie", perf=0.2, norm=0.9)])
    out = format_loot_table(result)
    assert "215147" in out
    # Strengthen: assert both weights appear on the header line specifically, not elsewhere
    for line in out.split("\n"):
        if "Weights:" in line:
            assert "0.6" in line and "0.4" in line
            break
    else:
        pytest.fail("Weights: header line not found")
    assert out.index("Ace") < out.index("Rookie")


def test_loot_table_marks_low_confidence_rows():
    out = format_loot_table(_result([_candidate("Thin", low=True)]))
    assert "low confidence" in out.lower()


def test_loot_table_reports_the_empty_case_clearly():
    empty = LootResult(item_id=999, difficulty=5, weight_upgrade=0.6, weight_performance=0.4)
    out = format_loot_table(empty)
    assert "no candidates" in out.lower()


def test_tables_group_by_role():
    out = format_loot_table(_result([_candidate("Tanky", role=TANK), _candidate("Stabby", role=DPS)]))
    assert "TANK" in out.upper()
    assert "DPS" in out.upper()


def test_performance_table_shows_the_component_breakdown():
    out = format_performance_table(_result([_candidate("Ace")]))
    assert "parse" in out.lower()
    assert "deaths" in out.lower()


# ---------------------------------------------------------------------------
# main(argv) — CLI integration tests
# ---------------------------------------------------------------------------


def test_main_exits_2_on_bad_weights(monkeypatch, capsys, tmp_path):
    """main() exits 2 and prints error to stderr when --weights is invalid (e.g. sum to zero)."""
    monkeypatch.chdir(tmp_path)
    # Use weights that sum to zero, which the fixed parse_weights now rejects
    exit_code = main(["215147", "--weights=0,0"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert captured.out == ""  # Error goes to stderr, not stdout


def test_main_exits_2_on_config_validation_failure(monkeypatch, capsys, tmp_path):
    """main() exits 2 when Config.validate() fails (e.g. no loot_config.json)."""
    monkeypatch.chdir(tmp_path)
    # No loot_config.json in tmp_path, so Config.load().validate() will raise
    exit_code = main(["215147"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "error:" in captured.err


def test_main_exits_0_on_success(monkeypatch, capsys, tmp_path):
    """main() exits 0 and prints both tables to stdout on success."""
    monkeypatch.chdir(tmp_path)

    # Create a minimal valid config
    valid_cfg = Config()
    valid_cfg.guild_name = "Test Guild"
    valid_cfg.guild_server_slug = "area-52"
    valid_cfg.season_zone_id = 1
    valid_cfg.weight_upgrade = 0.6
    valid_cfg.weight_performance = 0.4

    # Create a fake LootResult with one candidate
    fake_result = LootResult(
        item_id=215147,
        difficulty=5,
        weight_upgrade=0.6,
        weight_performance=0.4,
        by_role={DPS: [_candidate("TestChar", perf=0.8, norm=1.0)]},
        performance_by_role={DPS: [_candidate("TestChar", perf=0.8, norm=1.0)]},
    )

    # Patch Config.load to return our valid config
    monkeypatch.setattr("loot.Config.load", lambda: valid_cfg)

    # Patch LootRanker to return our fake result
    fake_ranker = MagicMock()
    fake_ranker.rank.return_value = fake_result

    def fake_loot_ranker_init(self, cfg, wowaudit, analyzer):
        pass

    monkeypatch.setattr("loot.LootRanker.__init__", fake_loot_ranker_init)
    monkeypatch.setattr("loot.LootRanker.rank", lambda self, item_id, difficulty, refresh: fake_result)

    exit_code = main(["215147"])
    assert exit_code == 0

    captured = capsys.readouterr()
    # Should contain loot table and performance table headings and the character name
    assert "Loot ranking" in captured.out
    assert "Performance ranking" in captured.out
    assert "TestChar" in captured.out


def test_main_exits_1_when_ranking_raises(monkeypatch, capsys, tmp_path):
    """main() exits 1 and prints error to stderr when LootRanker.rank() raises."""
    monkeypatch.chdir(tmp_path)

    valid_cfg = Config()
    valid_cfg.guild_name = "Test Guild"
    valid_cfg.guild_server_slug = "area-52"
    valid_cfg.season_zone_id = 1

    monkeypatch.setattr("loot.Config.load", lambda: valid_cfg)

    # Patch LootRanker to raise RuntimeError
    def fake_loot_ranker_init(self, cfg, wowaudit, analyzer):
        pass

    def fake_rank_raises(*args, **kwargs):
        raise RuntimeError("Simulated ranking error")

    monkeypatch.setattr("loot.LootRanker.__init__", fake_loot_ranker_init)
    monkeypatch.setattr("loot.LootRanker.rank", fake_rank_raises)

    exit_code = main(["215147"])
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert "Simulated ranking error" in captured.err


def test_main_exits_0_on_empty_result(monkeypatch, capsys, tmp_path):
    """main() exits 0 on an empty LootResult (no candidates) and prints message to stdout."""
    monkeypatch.chdir(tmp_path)

    valid_cfg = Config()
    valid_cfg.guild_name = "Test Guild"
    valid_cfg.guild_server_slug = "area-52"
    valid_cfg.season_zone_id = 1
    valid_cfg.weight_upgrade = 0.6
    valid_cfg.weight_performance = 0.4

    # Create empty LootResult
    empty_result = LootResult(
        item_id=215147,
        difficulty=5,
        weight_upgrade=0.6,
        weight_performance=0.4,
    )

    monkeypatch.setattr("loot.Config.load", lambda: valid_cfg)

    def fake_loot_ranker_init(self, cfg, wowaudit, analyzer):
        pass

    monkeypatch.setattr("loot.LootRanker.__init__", fake_loot_ranker_init)
    monkeypatch.setattr("loot.LootRanker.rank", lambda self, item_id, difficulty, refresh: empty_result)

    exit_code = main(["215147"])
    assert exit_code == 0

    captured = capsys.readouterr()
    # Should contain the "no candidates" message
    assert "no candidates" in captured.out.lower()
