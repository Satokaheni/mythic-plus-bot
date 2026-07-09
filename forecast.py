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
                out.append(Obs(uid, local.weekday(), (local.hour + 1) // 2, age, 1))
        elif etype in _POSITIVE_TYPES or etype in _NEGATIVE_TYPES:
            uid = e.get("user_id")
            wd = e.get("local_weekday")
            blk = e.get("local_block")
            if uid is None or wd is None or blk is None:
                continue
            sign = 1 if etype in _POSITIVE_TYPES else -1
            out.append(Obs(uid, wd, blk, age, sign))
    return out
