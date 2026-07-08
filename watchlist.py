"""Price-watch state, buy-signal detection, and display formatting."""

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from textwrap import dedent
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

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "label": self.label,
            "percentile": self.percentile,
            "state": self.state,
            "alert_history": [t.isoformat() for t in self.alert_history],
            "last_adjusted_at": self.last_adjusted_at.isoformat() if self.last_adjusted_at else None,
            "added_at": self.added_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Watch":
        return cls(
            item_id=int(d["item_id"]),
            label=d["label"],
            percentile=float(d.get("percentile", START_PERCENTILE)),
            state=d.get("state", "idle"),
            alert_history=[datetime.fromisoformat(t) for t in d.get("alert_history", [])],
            last_adjusted_at=datetime.fromisoformat(d["last_adjusted_at"]) if d.get("last_adjusted_at") else None,
            added_at=datetime.fromisoformat(d["added_at"]),
        )


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


def auto_tune(watch: Watch, now: datetime) -> None:
    """Adjust the low-band percentile: fast-loosen when starved, slow-tighten when flooding."""
    if watch.last_adjusted_at is not None and (now - watch.last_adjusted_at) < timedelta(hours=ADJUST_INTERVAL_HOURS):
        return
    old_enough = (now - watch.added_at) >= timedelta(days=STARVE_DAYS)
    recent_starve = [t for t in watch.alert_history if t >= now - timedelta(days=STARVE_DAYS)]
    recent_flood = [t for t in watch.alert_history if t >= now - timedelta(days=FLOOD_DAYS)]
    if old_enough and not recent_starve:
        watch.percentile = min(PERCENTILE_MAX, watch.percentile + LOOSEN_STEP)
    elif len(recent_flood) >= FLOOD_ALERTS:
        watch.percentile = max(PERCENTILE_MIN, watch.percentile - TIGHTEN_STEP)
    watch.last_adjusted_at = now


class Watchlist:
    """In-memory store of watched commodities, persisted to watches.json."""

    def __init__(self) -> None:
        self._watches: dict = {}

    def add(self, item_id: int, label: str) -> Watch:
        existing = self._watches.get(item_id)
        if existing is not None:
            existing.label = label
            return existing
        watch = Watch(item_id=item_id, label=label)
        self._watches[item_id] = watch
        return watch

    def remove(self, item_id: int) -> bool:
        return self._watches.pop(item_id, None) is not None

    def get(self, item_id: int) -> Optional[Watch]:
        return self._watches.get(item_id)

    def all(self) -> List[Watch]:
        return list(self._watches.values())

    def save(self, path: str = "watches.json") -> None:
        data = {"version": 1, "watches": [w.to_dict() for w in self._watches.values()]}
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)

    @classmethod
    def load(cls, path: str = "watches.json") -> "Watchlist":
        wl = cls()
        if not os.path.exists(path):
            return wl
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Error loading %s: %s. Starting with empty watchlist.", path, exc)
            return wl
        for entry in data.get("watches", []):
            try:
                watch = Watch.from_dict(entry)
                wl._watches[watch.item_id] = watch
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("Skipping malformed watch entry in %s: %s", path, exc)
        return wl


def format_alert(watch: Watch, signal: Signal) -> str:
    """Build the buy-signal DM sent to the banker."""
    pct_below = (1 - signal.price / signal.median) * 100 if signal.median else 0
    return dedent(
        f"""
        🛎️ **Buy signal** — {watch.label} (item {watch.item_id})
        Current: **{format_gold(signal.price)}** ({pct_below:.0f}% below {format_gold(int(signal.median))} median)
        Low band (p{watch.percentile:.0f}): {format_gold(int(signal.low_band))}
        Quantity available: {signal.quantity:,}
        https://www.wowhead.com/item={watch.item_id}
        """
    ).strip()


def format_watch_line(watch: Watch, signal: Optional[Signal]) -> str:
    """Build one line describing a watch for the !watches command."""
    if signal is None or not signal.enough_history:
        return f"• **{watch.label}** (item {watch.item_id}) — p{watch.percentile:.0f}, {watch.state} — insufficient data"
    return (
        f"• **{watch.label}** (item {watch.item_id}) — "
        f"now {format_gold(signal.price)}, median {format_gold(int(signal.median))}, "
        f"low band {format_gold(int(signal.low_band))} (p{watch.percentile:.0f}), {watch.state}"
    )
