"""Availability forecaster: events -> per-person, per-(weekday, block) probabilities."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger("discord")

HALF_LIFE = 4.0      # weeks; recency decay half-life
ALPHA = 2.0          # smoothing pseudo-count
BASE_PRIOR = 0.15    # availability prior when a person has no data

_POSITIVE_TYPES = {"run_completed", "offer_accepted", "raiderio_run"}
_NEGATIVE_TYPES = {"offer_declined"}


@dataclass(frozen=True)
class Obs:
    """One signed availability observation for a user in a local (weekday, block)."""

    user_id: int
    weekday: int
    block: int
    age_weeks: float
    sign: int  # +1 positive, -1 negative


def _age_weeks(ts_utc: datetime, now: datetime) -> float:
    return (now - ts_utc).total_seconds() / (7 * 86400)


def observations(events: List[dict], raiders: dict, now: datetime) -> List[Obs]:
    """Normalize raw events into signed per-user, per-local-slot observations.

    - run_completed: one +1 obs per roster member, slot from ts_utc + that member's tz.
    - offer_accepted / raiderio_run: +1 at the event's stored (local_weekday, local_block).
    - offer_declined: -1 at its stored slot.
    - avail_reaction and slotless/tz-less events are skipped.
    """
    out: List[Obs] = []
    for e in events:
        etype = e.get("type")
        ts_raw = e.get("ts_utc")
        if ts_raw is None:
            continue
        ts = datetime.fromisoformat(ts_raw)
        age = _age_weeks(ts, now)
        if etype == "run_completed":
            for uid in e.get("roster") or []:
                raider = raiders.get(uid)
                if raider is None or getattr(raider, "timezone", None) is None:
                    continue
                local = ts.astimezone(raider.timezone)
                out.append(Obs(uid, local.weekday(), local.hour // 2, age, 1))
        elif etype in _POSITIVE_TYPES or etype in _NEGATIVE_TYPES:
            uid = e.get("user_id")
            wd = e.get("local_weekday")
            blk = e.get("local_block")
            if uid is None or wd is None or blk is None:
                continue
            sign = 1 if etype in _POSITIVE_TYPES else -1
            out.append(Obs(uid, wd, blk, age, sign))
    return out


def _weight(age_weeks: float) -> float:
    return 0.5 ** (age_weeks / HALF_LIFE)


def _weighted_fraction(obs: List[Obs]) -> Optional[float]:
    """Recency-weighted positive fraction over the given obs, or None if empty."""
    wpos = sum(_weight(o.age_weeks) for o in obs if o.sign > 0)
    wneg = sum(_weight(o.age_weeks) for o in obs if o.sign < 0)
    total = wpos + wneg
    if total == 0:
        return None
    return wpos / total


def _base_rate(user_obs: List[Obs], weekday: int) -> float:
    """Prior: same-weekday positive fraction, else all obs, else BASE_PRIOR."""
    same_day = _weighted_fraction([o for o in user_obs if o.weekday == weekday])
    if same_day is not None:
        return same_day
    overall = _weighted_fraction(user_obs)
    return overall if overall is not None else BASE_PRIOR


def predict(user_obs: List[Obs], weekday: int, block: int) -> float:
    """P(user available at local weekday/block), recency-weighted + smoothed."""
    block_obs = [o for o in user_obs if o.weekday == weekday and o.block == block]
    wpos = sum(_weight(o.age_weeks) for o in block_obs if o.sign > 0)
    wneg = sum(_weight(o.age_weeks) for o in block_obs if o.sign < 0)
    prior = _base_rate(user_obs, weekday)
    return (wpos + ALPHA * prior) / (wpos + wneg + ALPHA)


@dataclass
class Team:
    """A role-valid roster, its mean predicted availability, and per-member probs."""

    tank: object
    healer: object
    dps: list
    mean: float
    probs: dict  # user_id -> predicted probability


def select_team(candidates: List[tuple]) -> Optional[Team]:
    """Pick 1 tank + 1 healer + 3 dps (distinct, multi-role aware) maximizing mean prob."""
    prob = {id(r): p for r, p in candidates}
    tanks = [r for r, _ in candidates if "tank" in r.roles]
    healers = [r for r, _ in candidates if "healer" in r.roles]
    dps_pool = [r for r, _ in candidates if "dps" in r.roles]

    best: Optional[Team] = None
    for tank in tanks:
        for healer in healers:
            if healer is tank:
                continue
            remaining = [r for r in dps_pool if r is not tank and r is not healer]
            if len(remaining) < 3:
                continue
            top3 = sorted(remaining, key=lambda r: prob[id(r)], reverse=True)[:3]
            mean = (prob[id(tank)] + prob[id(healer)] + sum(prob[id(r)] for r in top3)) / 5
            if best is None or mean > best.mean:
                members = [tank, healer, *top3]
                best = Team(
                    tank=tank,
                    healer=healer,
                    dps=top3,
                    mean=mean,
                    probs={r.user_id: prob[id(r)] for r in members},
                )
    return best


def can_field_team(raiders: list) -> bool:
    """Feasibility gate: can a role-valid team of 5 be assembled from these raiders?"""
    return select_team([(r, 1.0) for r in raiders]) is not None
