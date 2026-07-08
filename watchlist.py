"""Price-watch state, buy-signal detection, and display formatting."""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

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


@dataclass
class Watch:
    """A single watched commodity and its adaptive detection state."""

    item_id: int
    label: str
    percentile: float = START_PERCENTILE
    state: str = "idle"  # "idle" -> armed; "alerted" -> already pinged this dip
    alert_history: List[datetime] = field(default_factory=list)
    last_adjusted_at: Optional[datetime] = None
    added_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Signal:
    """Result of evaluating a watch against current + historical prices."""

    fired: bool
    enough_history: bool
    price: int
    median: float
    low_band: float
    quantity: int


def evaluate(now_price: int, quantity: int, daily_prices: List[int], watch: Watch) -> Signal:
    """Decide whether the current price sits in the item's recent low band."""
    window = daily_prices[-BASELINE_WINDOW_DAYS:]
    if len(window) < MIN_HISTORY_DAYS:
        return Signal(False, False, now_price, 0.0, 0.0, quantity)
    m = median(window)
    low = percentile(window, watch.percentile)
    return Signal(now_price < low, True, now_price, m, low, quantity)


def process_signal(watch: Watch, signal: Signal, now: datetime) -> bool:
    """Apply anti-spam rules. Returns True iff an alert DM should be sent now."""
    if not signal.enough_history:
        return False
    if signal.fired:
        if watch.state == "idle":
            watch.state = "alerted"
            watch.alert_history.append(now)
            return True
        return False
    # Not firing: re-arm only once the price recovers above the median.
    if signal.price > signal.median:
        watch.state = "idle"
    return False
