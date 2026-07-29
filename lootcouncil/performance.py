"""Role-aware performance scoring, plus season aggregation over Warcraft Logs.

The scoring functions in the top half are pure: they take already-collected
RawMetrics and never touch the network, so they are fully unit-testable.
"""

import json
import logging
import os
import time
from dataclasses import fields as dataclass_fields
from typing import Dict, Iterable, List, Optional, Sequence

from lootcouncil.config import CACHE_FILE, DEFAULT_ROLE_WEIGHTS, Config
from lootcouncil.models import DPS, Character, PerformanceScore, RawMetrics

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


def cache_is_fresh(cache: Optional[dict], cfg: Config, now_ts: float) -> bool:
    """A cache is fresh only if it is young AND describes the configured tier."""
    if not cache:
        return False
    if int(cache.get("zone_id", -1)) != int(cfg.season_zone_id):
        return False
    if int(cache.get("difficulty", -1)) != int(cfg.difficulty):
        return False
    age_hours = (now_ts - float(cache.get("fetched_at", 0.0))) / 3600.0
    return age_hours <= cfg.cache_ttl_hours


def metrics_to_dict(raws: Dict[str, RawMetrics]) -> Dict[str, dict]:
    names = [f.name for f in dataclass_fields(RawMetrics)]
    return {key: {n: getattr(raw, n) for n in names} for key, raw in raws.items()}


def metrics_from_dict(data: Dict[str, dict]) -> Dict[str, RawMetrics]:
    names = {f.name for f in dataclass_fields(RawMetrics)}
    out: Dict[str, RawMetrics] = {}
    for key, row in (data or {}).items():
        out[key] = RawMetrics(**{k: v for k, v in row.items() if k in names})
    return out


class PerformanceAnalyzer:
    """Walks the tier's reports once, accumulates per-character metrics, caches the result.

    `wcl` may be None when only the cache/scoring helpers are used.
    """

    def __init__(self, cfg: Config, wcl) -> None:
        self._cfg = cfg
        self._wcl = wcl

    def load_cache(self, path: str = CACHE_FILE) -> Optional[dict]:
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Ignoring unreadable cache %s: %s", path, exc)
            return None

    def save_cache(self, path: str, raws: Dict[str, RawMetrics]) -> None:
        payload = {
            "fetched_at": time.time(),
            "zone_id": self._cfg.season_zone_id,
            "difficulty": self._cfg.difficulty,
            "metrics": metrics_to_dict(raws),
        }
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, path)  # atomic, matching utils.save_state

    def aggregate_season(
        self, roster: List[Character], refresh: bool = False, cache_path: str = CACHE_FILE
    ) -> Dict[str, RawMetrics]:
        if not refresh:
            cache = self.load_cache(cache_path)
            if cache_is_fresh(cache, self._cfg, time.time()):
                logger.info("Using cached season metrics from %s", cache_path)
                return metrics_from_dict(cache.get("metrics", {}))

        raws = self._collect(roster)
        self.save_cache(cache_path, raws)
        return raws

    def _collect(self, roster: List[Character]) -> Dict[str, RawMetrics]:
        cfg = self._cfg
        raws: Dict[str, RawMetrics] = {c.key: RawMetrics() for c in roster}
        by_name = {c.name: c.key for c in roster}

        # 1. Parses come straight from zoneRankings, one query per character.
        for char in roster:
            try:
                parses = self._wcl.character_parses(
                    char.name, char.realm_slug, cfg.guild_region, cfg.season_zone_id
                )
            except Exception as exc:  # a single missing character must not kill the sweep
                logger.warning("No parse data for %s-%s: %s", char.name, char.realm, exc)
                continue
            if parses.per_encounter:
                raw = raws[char.key]
                raw.parse_total += parses.average
                raw.parse_count += 1

        # 2. Fight-level metrics come from the guild's reports.
        cutoff_ms = (time.time() - cfg.report_lookback_days * 86400) * 1000
        for code in self._report_codes(roster, cutoff_ms):
            try:
                fights = self._wcl.report_fights(code, cfg.difficulty)
            except Exception as exc:
                logger.warning("Skipping report %s: %s", code, exc)
                continue
            if not fights:
                continue
            self._accumulate_report(code, fights, raws, by_name)
        return raws

    def _report_codes(self, roster: List[Character], cutoff_ms: float) -> List[str]:
        """Union of the roster's recent reports — the guild's raid logs."""
        seen: Dict[str, None] = {}
        for char in roster:
            try:
                refs = self._wcl.recent_report_codes(
                    char.name, char.realm_slug, self._cfg.guild_region
                )
            except Exception as exc:
                logger.warning("No recent reports for %s: %s", char.name, exc)
                continue
            for ref in refs:
                if ref.start_time >= cutoff_ms:
                    seen.setdefault(ref.code, None)
        return list(seen)

    def _accumulate_report(self, code, fights, raws, by_name) -> None:
        fight_ids = [f.id for f in fights]

        # Deaths are queried per fight because death *order* is only meaningful
        # within a single pull.
        for fight in fights:
            deaths = self._table(code, [fight.id], "Deaths")
            early = set(early_death_names(deaths))
            for entry in deaths:
                key = by_name.get(entry.get("name"))
                if key is None:
                    continue
                raws[key].deaths += 1
                if entry["name"] in early:
                    raws[key].early_deaths += 1

        # Fight participation comes from DamageTaken, which lists every player
        # present in the fight.
        for entry in self._table(code, fight_ids, "DamageTaken"):
            key = by_name.get(entry.get("name"))
            if key is None:
                continue
            raw = raws[key]
            raw.fights += 1
            raw.damage_taken += int(entry.get("total") or 0)
            raw.active_time_ms += int(entry.get("activeTime") or 0)
            tmi = entry.get("tmi")
            if tmi is not None:
                raw.tmi_total += float(tmi)
                raw.tmi_count += 1

        for data_type, field_name in (("Interrupts", "interrupts"), ("Dispels", "dispels")):
            for entry in self._table(code, fight_ids, data_type):
                key = by_name.get(entry.get("name"))
                if key is None:
                    continue
                setattr(raws[key], field_name, getattr(raws[key], field_name) + int(entry.get("total") or 0))

    def _table(self, code: str, fight_ids: List[int], data_type: str) -> List[dict]:
        try:
            return self._wcl.report_table(code, fight_ids, data_type)
        except Exception as exc:
            logger.warning("%s table failed for %s: %s", data_type, code, exc)
            return []
