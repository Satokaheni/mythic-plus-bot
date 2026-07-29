"""Blend upgrade size with role-aware performance into a single loot ranking."""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from lootcouncil.config import Config
from lootcouncil.models import Character, PerformanceScore
from lootcouncil.performance import PerformanceAnalyzer, score_cohort

logger = logging.getLogger("lootcouncil")


def normalize_upgrades(pcts: Dict[str, float]) -> Dict[str, float]:
    """Scale upgrade percentages onto 0..1 *within the candidate set*.

    This is what makes performance the decider when several candidates all have
    a big upgrade: their normalized values all compress toward 1.0.
    """
    if not pcts:
        return {}
    best = max(pcts.values())
    if best <= 0:
        return {key: 0.0 for key in pcts}
    return {key: value / best for key, value in pcts.items()}


def weighted_score(
    upgrade_norm: float, performance: float, weight_upgrade: float, weight_performance: float
) -> float:
    return weight_upgrade * upgrade_norm + weight_performance * performance


@dataclass(frozen=True)
class RankedCandidate:
    key: str
    name: str
    role: str
    spec: str
    upgrade_pct: float
    upgrade_norm: float
    performance: float
    components: Dict[str, float]
    loot_score: float
    low_confidence: bool


@dataclass(frozen=True)
class LootResult:
    item_id: int
    difficulty: int
    weight_upgrade: float
    weight_performance: float
    by_role: Dict[str, List[RankedCandidate]] = field(default_factory=dict)
    performance_by_role: Dict[str, List[RankedCandidate]] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not any(self.by_role.values())


class LootRanker:
    """Ties the two data sources together and produces both rankings."""

    def __init__(self, cfg: Config, wowaudit, analyzer: PerformanceAnalyzer) -> None:
        self._cfg = cfg
        self._wowaudit = wowaudit
        self._analyzer = analyzer

    def rank(self, item_id: int, difficulty: Optional[int] = None, refresh: bool = False) -> LootResult:
        cfg = self._cfg
        difficulty = cfg.difficulty if difficulty is None else difficulty

        roster: List[Character] = self._wowaudit.roster()
        by_key = {c.key: c for c in roster}
        upgrades = self._wowaudit.upgrades_for(item_id, difficulty)

        candidates = {k: v for k, v in upgrades.items() if k in by_key}
        if not candidates:
            return LootResult(item_id, difficulty, cfg.weight_upgrade, cfg.weight_performance)

        raws = self._analyzer.aggregate_season(roster, refresh=refresh)
        roles = {c.key: c.role for c in roster}
        scores: Dict[str, PerformanceScore] = score_cohort(raws, roles, cfg)

        # Upgrades normalize within role, because the rankings are per-role.
        keys_by_role: Dict[str, List[str]] = {}
        for key in candidates:
            keys_by_role.setdefault(by_key[key].role, []).append(key)

        result_by_role: Dict[str, List[RankedCandidate]] = {}
        for role, keys in keys_by_role.items():
            norms = normalize_upgrades({k: candidates[k].percentage for k in keys})
            rows: List[RankedCandidate] = []
            for key in keys:
                score = scores.get(key)
                perf = score.score if score else 0.0
                rows.append(
                    RankedCandidate(
                        key=key,
                        name=by_key[key].name,
                        role=role,
                        spec=candidates[key].spec,
                        upgrade_pct=candidates[key].percentage,
                        upgrade_norm=norms[key],
                        performance=perf,
                        components=score.components if score else {},
                        loot_score=weighted_score(
                            norms[key], perf, cfg.weight_upgrade, cfg.weight_performance
                        ),
                        low_confidence=score.low_confidence if score else True,
                    )
                )
            result_by_role[role] = sorted(rows, key=lambda r: r.loot_score, reverse=True)

        performance_by_role = {
            role: sorted(rows, key=lambda r: r.performance, reverse=True)
            for role, rows in result_by_role.items()
        }

        return LootResult(
            item_id=item_id,
            difficulty=difficulty,
            weight_upgrade=cfg.weight_upgrade,
            weight_performance=cfg.weight_performance,
            by_role=result_by_role,
            performance_by_role=performance_by_role,
        )
