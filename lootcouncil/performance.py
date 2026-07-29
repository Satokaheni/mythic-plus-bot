"""Role-aware performance scoring, plus season aggregation over Warcraft Logs.

The scoring functions in the top half are pure: they take already-collected
RawMetrics and never touch the network, so they are fully unit-testable.
"""

import logging
from typing import Dict, Iterable, List, Sequence

from lootcouncil.config import DEFAULT_ROLE_WEIGHTS, Config
from lootcouncil.models import DPS, PerformanceScore, RawMetrics

logger = logging.getLogger("lootcouncil")

# Components where a lower raw value means a better player.
INVERTED_COMPONENTS = ("deaths", "damage", "survivability")
COMPONENTS = ("parse", "deaths", "damage", "utility", "survivability")


def early_death_names(entries: Iterable[dict], limit: int = 2) -> List[str]:
    """Names of the first `limit` players to die in one fight, earliest first.

    This is the "one of the first two to die in a pull" signal: sort the WCL
    Deaths table by timestamp and take the head.
    """
    named = [e for e in entries if e.get("name")]
    named.sort(key=lambda e: e.get("timestamp") or 0)
    return [e["name"] for e in named[:limit]]


def rank_normalize(value: float, cohort: Sequence[float], invert: bool) -> float:
    """Min-max a value against its cohort onto 0..1 where 1.0 is always best.

    A flat or empty cohort returns 1.0 — if everyone is equal, nobody is penalized.
    """
    if not cohort:
        return 1.0
    lo, hi = min(cohort), max(cohort)
    if hi == lo:
        return 1.0
    scaled = (value - lo) / (hi - lo)
    return 1.0 - scaled if invert else scaled


def component_values(raw: RawMetrics) -> Dict[str, float]:
    """Raw per-fight component values, before cohort normalization."""
    fights = raw.fights or 0
    return {
        "parse": raw.parse_avg,
        "deaths": (raw.deaths + raw.early_deaths) / fights if fights else 0.0,
        "damage": raw.damage_taken / raw.active_time_ms if raw.active_time_ms else 0.0,
        "utility": (raw.interrupts + raw.dispels) / fights if fights else 0.0,
        "survivability": raw.tmi_avg,
    }


def score_cohort(
    raws: Dict[str, RawMetrics], roles: Dict[str, str], cfg: Config
) -> Dict[str, PerformanceScore]:
    """Score every character, normalizing each component within its own role cohort."""
    if not raws:
        return {}

    values = {key: component_values(raw) for key, raw in raws.items()}
    keys_by_role: Dict[str, List[str]] = {}
    for key in raws:
        keys_by_role.setdefault(roles.get(key, DPS), []).append(key)

    scores: Dict[str, PerformanceScore] = {}
    for role, keys in keys_by_role.items():
        weights = cfg.role_weights.get(role) or DEFAULT_ROLE_WEIGHTS[DPS]
        cohorts = {c: [values[k][c] for k in keys] for c in COMPONENTS}
        for key in keys:
            components: Dict[str, float] = {}
            for component in COMPONENTS:
                if component == "parse":
                    # Already a 0-100 spec-normalized percentile; no cohort needed.
                    components[component] = max(0.0, min(1.0, values[key][component] / 100.0))
                else:
                    components[component] = rank_normalize(
                        values[key][component],
                        cohorts[component],
                        invert=component in INVERTED_COMPONENTS,
                    )
            total = sum(weights.get(c, 0.0) * components[c] for c in COMPONENTS)
            scores[key] = PerformanceScore(
                score=max(0.0, min(1.0, total)),
                components=components,
                fights=raws[key].fights,
                low_confidence=raws[key].fights < cfg.min_fights,
            )
    return scores
