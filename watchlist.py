"""Price-watch state, buy-signal detection, and display formatting."""

import logging
import math
from typing import List

logger = logging.getLogger("discord")

# Detection tuning constants.
BASELINE_WINDOW_DAYS = 14
MIN_HISTORY_DAYS = 7
START_PERCENTILE = 35.0
PERCENTILE_MIN = 10.0
PERCENTILE_MAX = 50.0
STARVE_DAYS = 7
LOOSEN_STEP = 5.0
FLOOD_ALERTS = 2
FLOOD_DAYS = 7
TIGHTEN_STEP = 1.0
ADJUST_INTERVAL_HOURS = 24


def format_gold(copper: int) -> str:
    """Format a copper amount as a compact WoW gold/silver/copper string."""
    gold, rem = divmod(int(copper), 10000)
    silver, copp = divmod(rem, 100)
    parts = []
    if gold:
        parts.append(f"{gold:,}g")
    if silver:
        parts.append(f"{silver}s")
    if copp or not parts:
        parts.append(f"{copp}c")
    return " ".join(parts)


def percentile(values: List[float], p: float) -> float:
    """Linear-interpolation percentile (numpy default method). `values` need not be sorted."""
    if not values:
        raise ValueError("percentile of empty sequence")
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(s[int(k)])
    return s[lo] * (hi - k) + s[hi] * (k - lo)


def median(values: List[float]) -> float:
    """Median = 50th percentile."""
    return percentile(values, 50)
