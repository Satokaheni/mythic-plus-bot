"""Price-watch state, buy-signal detection, and display formatting."""

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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

# Only send buy alerts during waking hours (local CST hour, inclusive): 10:00 AM–11:59 PM.
ALERT_START_HOUR = 10
ALERT_END_HOUR = 23


def in_alert_window(now_cst: datetime) -> bool:
    """True if `now_cst` (a Central-time datetime) is within the alert window (10 AM–11:59 PM)."""
    return ALERT_START_HOUR <= now_cst.hour <= ALERT_END_HOUR


def bulk_price(auctions, target_qty: int) -> tuple:
    """Volume-weighted price to actually acquire `target_qty` units off the ladder.

    Walks the auction lots cheapest-first and blends their prices over exactly
    `target_qty` units. Returns ``(fillable, vwap, units_available)``:

    - ``units_available`` — total units listed across all lots.
    - ``fillable`` — whether at least ``target_qty`` units exist (the depth gate).
    - ``vwap`` — total cost to buy ``target_qty`` units divided by ``target_qty``
      ("the overall bulk buy price"); ``0.0`` when not fillable.

    A thin cheapest lot no longer dominates: if only 20 units sit at the floor and
    you want 100, the VWAP reflects the more expensive lots you'd have to buy too.
    """
    units_available = sum(qty for _, qty in auctions)
    if target_qty <= 0 or units_available < target_qty:
        return (False, 0.0, units_available)
    remaining = target_qty
    cost = 0
    for price, qty in sorted(auctions):
        take = min(qty, remaining)
        cost += take * price
        remaining -= take
        if remaining <= 0:
            break
    return (True, cost / target_qty, units_available)


def suggest_buy(auctions, ceiling_price: float, budget_copper: int) -> tuple:
    """Walk the auction ladder cheapest-first, buying lots priced at or below `ceiling_price`
    until the budget runs out or the price crosses the ceiling. Returns (units, cost_copper)."""
    units = 0
    cost = 0
    remaining = budget_copper
    for price, qty in sorted(auctions):
        if price > ceiling_price or remaining < price:
            break
        take = min(qty, remaining // price)
        if take <= 0:
            break
        units += take
        spent = take * price
        cost += spent
        remaining -= spent
        if take < qty:  # budget exhausted within this price tier
            break
    return units, cost


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
    # Bulk order size this item's buy signal targets. None -> use the global default.
    target_qty: Optional[int] = None
    percentile: float = START_PERCENTILE
    state: str = "idle"  # "idle" -> armed; "alerted" -> already pinged this dip
    alert_history: List[datetime] = field(default_factory=list)
    last_adjusted_at: Optional[datetime] = None
    added_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "label": self.label,
            "target_qty": self.target_qty,
            "percentile": self.percentile,
            "state": self.state,
            "alert_history": [t.isoformat() for t in self.alert_history],
            "last_adjusted_at": self.last_adjusted_at.isoformat() if self.last_adjusted_at else None,
            "added_at": self.added_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Watch":
        raw_target = d.get("target_qty")
        return cls(
            item_id=int(d["item_id"]),
            label=d["label"],
            target_qty=int(raw_target) if raw_target is not None else None,
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
    price: int  # the watched price: bulk VWAP to fill target_qty (falls back to the min lot)
    median: float
    low_band: float
    quantity: int
    auctions: tuple = ()  # cheapest-first (price, quantity) lots, for the budget buy estimate
    fillable: bool = True  # whether target_qty units are actually available (depth gate)
    units_available: int = 0
    target_qty: int = 1


def evaluate(
    now_price: int,
    quantity: int,
    daily_prices: List[int],
    watch: Watch,
    auctions: tuple = (),
    target_qty: int = 1,
) -> Signal:
    """Decide whether the *bulk* price to fill ``target_qty`` units sits in the low band.

    Instead of the single cheapest lot, the detector watches the VWAP to actually
    acquire ``target_qty`` units by walking the auction ladder (`bulk_price`). If fewer
    than ``target_qty`` units are listed there's no bulk opportunity — the depth gate
    fails and nothing fires. When no ladder is supplied the detector degrades to the
    legacy cheapest-lot behavior so existing callers keep working.
    """
    target = target_qty if target_qty and target_qty > 0 else 1
    window = daily_prices[-BASELINE_WINDOW_DAYS:]

    if auctions:
        fillable, vwap, units_available = bulk_price(auctions, target)
        watched_price = int(round(vwap)) if fillable else now_price
    else:
        # No ladder: fall back to the cheapest-lot price, no depth gate.
        fillable, units_available = True, quantity
        watched_price = now_price

    if len(window) < MIN_HISTORY_DAYS:
        return Signal(False, False, watched_price, 0.0, 0.0, quantity, auctions, fillable, units_available, target)
    m = median(window)
    low = percentile(window, watch.percentile)
    fired = fillable and watched_price < low
    return Signal(fired, True, watched_price, m, low, quantity, auctions, fillable, units_available, target)


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

    def add(self, item_id: int, label: str, target_qty: Optional[int] = None) -> Watch:
        existing = self._watches.get(item_id)
        if existing is not None:
            existing.label = label
            if target_qty is not None:  # only overwrite the target when one was given
                existing.target_qty = target_qty
            return existing
        watch = Watch(item_id=item_id, label=label, target_qty=target_qty)
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


def _take_x_target(args: List[str], i: int):
    """If args[i] is a glued ``-x<qty>`` flag, return ``(qty, next_index)``.

    Returns ``(None, i)`` when args[i] is not a ``-x`` flag, or ``(False, i)`` on a
    malformed one. The quantity MUST be glued to ``-x`` (``-x100``, not ``-x 100``):
    a spaced form in the multi-item run would silently swallow the following item id
    as the quantity, which is exactly how a real command lost an item.
    """
    if i >= len(args):
        return (None, i)
    a = args[i]
    if a.startswith("-x"):
        rest = a[2:]
        if rest.isdigit() and int(rest) > 0:
            return (int(rest), i + 1)
        return (False, i)  # bare "-x", "-x 100" (space), non-numeric, or non-positive
    return (None, i)


def parse_watch_command(args: List[str]) -> dict:
    """Parse the arguments of a ``!watch`` command into an intent dict.

    Grammar (the ``-x`` quantity must be glued: ``-x100``, never ``-x 100``):
      ``!watch <id> [-x<qty>] [label]``
                          -> {"kind": "single", "item_id", "label", "target_qty"}
      ``!watch <id> [-x<qty>] <id> [-x<qty>] ...``
                          -> {"kind": "multi", "items": [(item_id, target_qty|None), ...]}
      anything unusable   -> {"kind": "error", "reason": ...}

    Each ``-x`` binds to the item id immediately before it. Two or more ids form the
    multi-item form (no custom labels — each is auto-labelled); a single id may carry a
    trailing free-text label. ``label``/``target_qty`` are ``None`` when omitted (the
    caller supplies defaults). Error reasons: ``"bad_flag"`` (malformed ``-x``),
    ``"multi_label"`` (free text alongside several ids), ``"no_item"`` (no leading id).
    """
    items: list = []  # (item_id, target_qty|None)
    i = 0
    while i < len(args) and args[i].isdigit():
        item_id = int(args[i])
        i += 1
        target, i = _take_x_target(args, i)
        if target is False:  # malformed -x flag (e.g. a spaced "-x 100")
            return {"kind": "error", "reason": "bad_flag"}
        items.append((item_id, target))

    if not items:
        return {"kind": "error", "reason": "no_item"}

    trailing = args[i:]  # whatever's left after the run of ids

    # Two or more ids -> multi. A leftover token is ambiguous across items.
    if len(items) >= 2:
        if trailing:
            reason = "bad_flag" if trailing[0].startswith("-x") else "multi_label"
            return {"kind": "error", "reason": reason}
        return {"kind": "multi", "items": items}

    # Single id: the remainder (if any) is its label. A stray -x here is malformed.
    item_id, target_qty = items[0]
    if trailing and trailing[0].startswith("-x"):
        return {"kind": "error", "reason": "bad_flag"}
    label = " ".join(trailing) if trailing else None
    return {"kind": "single", "item_id": item_id, "label": label, "target_qty": target_qty}


def format_alert(watch: Watch, signal: Signal, budget_copper: int = 0) -> str:
    """Build the buy-signal DM sent to the banker.

    When `budget_copper` > 0, add a line suggesting how much to buy within budget by walking the
    auction ladder up to the low band (the pounce threshold that fired the alert).
    """
    pct_below = (1 - signal.price / signal.median) * 100 if signal.median else 0
    fill_note = f" to fill {signal.target_qty:,}" if signal.target_qty > 1 else ""
    lines = [
        f"🛎️ **Buy signal** — {watch.label} (item {watch.item_id})",
        f"Bulk price{fill_note}: **{format_gold(signal.price)}** "
        f"({pct_below:.0f}% below {format_gold(int(signal.median))} median)",
        f"Low band (p{watch.percentile:.0f}): {format_gold(int(signal.low_band))}",
    ]
    if budget_copper > 0:
        units, cost = suggest_buy(signal.auctions, signal.low_band, budget_copper)
        if units > 0:
            lines.append(
                f"💰 **Buy up to {units:,} for {format_gold(cost)}** (budget: {format_gold(budget_copper)})"
            )
    lines.append(f"Quantity available: {signal.quantity:,}")
    lines.append(f"https://www.wowhead.com/item={watch.item_id}")
    return "\n".join(lines)


def format_watch_line(watch: Watch, signal: Optional[Signal]) -> str:
    """Build one line describing a watch for the !watches command."""
    if signal is None or not signal.enough_history:
        return f"• **{watch.label}** (item {watch.item_id}) — p{watch.percentile:.0f}, {watch.state} — insufficient data"
    if not signal.fillable:
        return (
            f"• **{watch.label}** (item {watch.item_id}) — p{watch.percentile:.0f}, {watch.state} — "
            f"insufficient depth ({signal.units_available:,}/{signal.target_qty:,})"
        )
    return (
        f"• **{watch.label}** (item {watch.item_id}) — "
        f"bulk {format_gold(signal.price)} (fill {signal.target_qty:,}), median {format_gold(int(signal.median))}, "
        f"low band {format_gold(int(signal.low_band))} (p{watch.percentile:.0f}), {watch.state}"
    )
