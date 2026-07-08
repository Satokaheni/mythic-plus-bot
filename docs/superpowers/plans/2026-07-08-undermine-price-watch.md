# Undermine Price-Watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an owner-only (`BANKER_ID`) price-watch feature that polls the Undermine Exchange API hourly for region-wide WoW commodities and DMs the banker when an item's price dips into a self-adjusting "pounce" low.

**Architecture:** Three focused modules. `undermine.py` is a thin async API client with pure JSON parsers. `watchlist.py` holds the `Watch`/`Watchlist` state, all detection math as pure functions, and display formatters — fully unit-testable with no Discord or network dependency. `bot.py` wires an hourly `@tasks.loop` task, owner-gated commands, and DM delivery on top of them.

**Tech Stack:** Python 3.9+, discord.py v2+, `aiohttp` (bundled with discord.py), pytest. Persistence via a dedicated `watches.json` (atomic temp-file write), separate from `state.json`.

## Global Constraints

- Python 3.9+ compatible (use `Optional[X]`, `List[X]` from `typing`; `list[int]` annotations are acceptable — the codebase already uses them, e.g. `utils.ROLES_DICT`).
- All internal datetimes are timezone-aware UTC (`datetime.now(timezone.utc)`).
- Prices from Undermine are in **copper**; convert to `g/s/c` only for display.
- Owner gate: every price-watch command and every alert DM is restricted to the single user whose ID is `BANKER_ID` (already present in `.env`).
- Region from `UNDERMINE_REGION` env var, default `"us"`. API key from `UNDERMINE_API_KEY`.
- Undermine auth header: `Authorization: ApiKey {key}`; also send `Accept-Encoding: gzip`.
- Base URL: `https://api.undermine.exchange`.
- Follow existing patterns: `logging.getLogger("discord")`, atomic writes via `os.replace`, per-item `try/except` in loops, `is_ready()` guard at the top of `@tasks.loop` tasks.
- Detection constants (defined once in `watchlist.py`): `BASELINE_WINDOW_DAYS=14`, `MIN_HISTORY_DAYS=7`, `START_PERCENTILE=35.0`, `PERCENTILE_MIN=10.0`, `PERCENTILE_MAX=50.0`, `STARVE_DAYS=7`, `LOOSEN_STEP=5.0`, `FLOOD_ALERTS=2`, `FLOOD_DAYS=7`, `TIGHTEN_STEP=1.0`, `ADJUST_INTERVAL_HOURS=24`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `undermine.py` (create) | Async Undermine API client: `fetch_now`, `fetch_daily`, pure parsers `_parse_now`/`_parse_daily`, `NowResult` dataclass. |
| `watchlist.py` (create) | `Watch`/`Signal` dataclasses, `Watchlist` store (add/remove/get/all + save/load), pure detection (`percentile`, `median`, `evaluate`, `process_signal`, `auto_tune`), display (`format_gold`, `format_alert`, `format_watch_line`). |
| `bot.py` (modify) | `BANKER_ID` env, imports, `setup_hook` wiring, `price_watch_check` task, `!watch`/`!unwatch`/`!watches` commands. |
| `tests/test_undermine.py` (create) | Parser tests against real JSON shapes. |
| `tests/test_watchlist.py` (create) | Detection, anti-spam, auto-tune, formatting, store round-trip. |
| `CLAUDE.md`, `README.md`, `CHANGELOG.md` (modify) | Docs. |

---

## Task 1: Undermine API client

**Files:**
- Create: `undermine.py`
- Test: `tests/test_undermine.py`

**Interfaces:**
- Produces:
  - `NowResult` dataclass: `price: int` (copper), `quantity: int`
  - `_parse_now(data: dict) -> Optional[NowResult]`
  - `_parse_daily(data: dict) -> List[int]` (prices, copper, chronological ascending)
  - `async fetch_now(session: aiohttp.ClientSession, item_id: int) -> Optional[NowResult]`
  - `async fetch_daily(session: aiohttp.ClientSession, item_id: int) -> List[int]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_undermine.py`:

```python
"""Tests for the Undermine Exchange API client parsers."""

from undermine import NowResult, _parse_daily, _parse_now


def test_parse_now_extracts_price_and_quantity():
    data = {"result": {"lastSeen": "2026-07-08T15:33:06Z", "price": 1100, "quantity": 446461, "auctions": []}}
    assert _parse_now(data) == NowResult(price=1100, quantity=446461)


def test_parse_now_returns_none_when_not_listed():
    # An item not currently for sale has no "price" key, only a seen timestamp.
    data = {"result": {"lastSeen": "2026-07-08T15:33:06Z"}}
    assert _parse_now(data) is None


def test_parse_daily_returns_prices_in_order():
    data = {
        "result": {
            "daily": [
                {"day": "2022-09-04", "price": 1800, "quantity": 644421},
                {"day": "2022-09-05", "price": 1100, "quantity": 727189},
                {"day": "2022-09-06", "price": 900, "quantity": 708004},
            ]
        }
    }
    assert _parse_daily(data) == [1800, 1100, 900]


def test_parse_daily_handles_empty():
    assert _parse_daily({"result": {}}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_undermine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'undermine'`

- [ ] **Step 3: Write minimal implementation**

Create `undermine.py`:

```python
"""Async client for the Undermine Exchange commodities API."""

import logging
import os
from dataclasses import dataclass
from typing import List, Optional

import aiohttp

logger = logging.getLogger("discord")

BASE_URL = "https://api.undermine.exchange"
UNDERMINE_API_KEY = os.getenv("UNDERMINE_API_KEY", "")
UNDERMINE_REGION = os.getenv("UNDERMINE_REGION", "us")


@dataclass(frozen=True)
class NowResult:
    """Current market snapshot for a commodity: min price (copper) and total quantity."""

    price: int
    quantity: int


def _parse_now(data: dict) -> Optional[NowResult]:
    """Parse a commodities now.json payload. Returns None when the item is not listed."""
    result = data.get("result", {})
    if "price" not in result:
        return None
    return NowResult(price=int(result["price"]), quantity=int(result.get("quantity", 0)))


def _parse_daily(data: dict) -> List[int]:
    """Parse a commodities daily.json payload into a chronological list of prices (copper)."""
    result = data.get("result", {})
    return [int(entry["price"]) for entry in result.get("daily", []) if "price" in entry]


async def _get_json(session: aiohttp.ClientSession, path: str) -> Optional[dict]:
    headers = {"Authorization": f"ApiKey {UNDERMINE_API_KEY}", "Accept-Encoding": "gzip"}
    try:
        async with session.get(f"{BASE_URL}{path}", headers=headers) as resp:
            if resp.status != 200:
                logger.warning("Undermine API %s returned status %s", path, resp.status)
                return None
            return await resp.json()
    except aiohttp.ClientError as exc:
        logger.warning("Undermine API request failed for %s: %s", path, exc)
        return None


async def fetch_now(session: aiohttp.ClientSession, item_id: int) -> Optional[NowResult]:
    """Fetch the current price/quantity for a region-wide commodity."""
    data = await _get_json(session, f"/v1/region/{UNDERMINE_REGION}/commodities/{item_id}/now.json")
    return _parse_now(data) if data else None


async def fetch_daily(session: aiohttp.ClientSession, item_id: int) -> List[int]:
    """Fetch the daily price history (copper, chronological) for a region-wide commodity."""
    data = await _get_json(session, f"/v1/region/{UNDERMINE_REGION}/commodities/{item_id}/daily.json")
    return _parse_daily(data) if data else []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_undermine.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add undermine.py tests/test_undermine.py
git commit -m "feat: add Undermine Exchange commodities API client"
```

---

## Task 2: Price helpers — gold formatting, percentile, median

**Files:**
- Create: `watchlist.py`
- Test: `tests/test_watchlist.py`

**Interfaces:**
- Produces:
  - `format_gold(copper: int) -> str`
  - `percentile(values: List[float], p: float) -> float`
  - `median(values: List[float]) -> float`
  - Module-level detection constants (see Global Constraints).

- [ ] **Step 1: Write the failing test**

Create `tests/test_watchlist.py`:

```python
"""Tests for the price-watch state, detection, and formatting logic."""

from watchlist import format_gold, median, percentile


def test_format_gold_full_denominations():
    assert format_gold(11234) == "1g 12s 34c"


def test_format_gold_drops_empty_leading_units():
    assert format_gold(1100) == "11s"
    assert format_gold(10000) == "1g"


def test_format_gold_zero():
    assert format_gold(0) == "0c"


def test_percentile_linear_interpolation():
    assert percentile([1, 2, 3, 4], 25) == 1.75
    assert percentile([1, 2, 3, 4], 50) == 2.5


def test_percentile_single_value():
    assert percentile([500], 25) == 500.0


def test_median_matches_p50():
    assert median([1, 2, 3, 4]) == 2.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_watchlist.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'watchlist'`

- [ ] **Step 3: Write minimal implementation**

Create `watchlist.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_watchlist.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add watchlist.py tests/test_watchlist.py
git commit -m "feat: add gold formatting and percentile helpers"
```

---

## Task 3: Watch/Signal dataclasses and buy-signal evaluation

**Files:**
- Modify: `watchlist.py`
- Test: `tests/test_watchlist.py`

**Interfaces:**
- Consumes: `percentile`, `median`, detection constants (Task 2).
- Produces:
  - `Watch` dataclass: `item_id: int`, `label: str`, `percentile: float = START_PERCENTILE`, `state: str = "idle"`, `alert_history: List[datetime]`, `last_adjusted_at: Optional[datetime] = None`, `added_at: datetime`
  - `Signal` dataclass: `fired: bool`, `enough_history: bool`, `price: int`, `median: float`, `low_band: float`, `quantity: int`
  - `evaluate(now_price: int, quantity: int, daily_prices: List[int], watch: Watch) -> Signal`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_watchlist.py`:

```python
from datetime import datetime, timezone

from watchlist import Watch, evaluate


def _watch(percentile=35.0):
    return Watch(item_id=21877, label="Netherweave Cloth", percentile=percentile)


def test_evaluate_insufficient_history():
    sig = evaluate(1000, 500, [1000, 900, 1100], _watch())  # only 3 days < MIN_HISTORY_DAYS
    assert sig.enough_history is False
    assert sig.fired is False


def test_evaluate_fires_on_dip_into_low_band():
    # 14 flat days at 1000, then a dip to 700 — 700 is below the p35 band.
    history = [1000] * 13 + [800]
    sig = evaluate(700, 500, history, _watch())
    assert sig.enough_history is True
    assert sig.fired is True
    assert sig.median == 1000


def test_evaluate_does_not_fire_when_price_is_typical():
    history = [1000] * 14
    sig = evaluate(1000, 500, history, _watch())
    assert sig.enough_history is True
    assert sig.fired is False


def test_evaluate_uses_only_last_window_days():
    # 30 days: first 16 are cheap noise, last 14 are expensive. Window is the last 14.
    history = [100] * 16 + [1000] * 14
    sig = evaluate(950, 500, history, _watch())
    assert sig.median == 1000  # noise outside the window is ignored
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_watchlist.py -k evaluate -v`
Expected: FAIL with `ImportError: cannot import name 'Watch'`

- [ ] **Step 3: Write minimal implementation**

Add imports at the top of `watchlist.py` (merge with existing import block):

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional
```

Then append to `watchlist.py`:

```python
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
    return Signal(now_price <= low, True, now_price, m, low, quantity)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_watchlist.py -v`
Expected: PASS (all Task 2 + Task 3 tests)

- [ ] **Step 5: Commit**

```bash
git add watchlist.py tests/test_watchlist.py
git commit -m "feat: add Watch/Signal model and buy-signal evaluation"
```

---

## Task 4: Anti-spam signal processing

**Files:**
- Modify: `watchlist.py`
- Test: `tests/test_watchlist.py`

**Interfaces:**
- Consumes: `Watch`, `Signal`, `evaluate` (Task 3).
- Produces: `process_signal(watch: Watch, signal: Signal, now: datetime) -> bool` — mutates `watch.state`/`watch.alert_history`; returns `True` iff a DM should be sent now.

Behavior: fire once on entry into the low band (idle→alerted, record timestamp); stay silent while alerted; re-arm to idle only when price recovers above the median.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_watchlist.py`:

```python
from watchlist import Signal, process_signal


def _now():
    return datetime(2026, 7, 8, tzinfo=timezone.utc)


def test_process_signal_alerts_once_on_entry():
    w = _watch()
    sig = Signal(fired=True, enough_history=True, price=700, median=1000, low_band=800, quantity=5)
    assert process_signal(w, sig, _now()) is True
    assert w.state == "alerted"
    assert len(w.alert_history) == 1


def test_process_signal_silent_while_still_low():
    w = _watch()
    w.state = "alerted"
    sig = Signal(fired=True, enough_history=True, price=700, median=1000, low_band=800, quantity=5)
    assert process_signal(w, sig, _now()) is False
    assert w.alert_history == []


def test_process_signal_rearms_above_median():
    w = _watch()
    w.state = "alerted"
    sig = Signal(fired=False, enough_history=True, price=1100, median=1000, low_band=800, quantity=5)
    assert process_signal(w, sig, _now()) is False
    assert w.state == "idle"


def test_process_signal_stays_alerted_between_low_band_and_median():
    w = _watch()
    w.state = "alerted"
    sig = Signal(fired=False, enough_history=True, price=900, median=1000, low_band=800, quantity=5)
    assert process_signal(w, sig, _now()) is False
    assert w.state == "alerted"


def test_process_signal_ignores_insufficient_history():
    w = _watch()
    sig = Signal(fired=False, enough_history=False, price=700, median=0.0, low_band=0.0, quantity=5)
    assert process_signal(w, sig, _now()) is False
    assert w.state == "idle"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_watchlist.py -k process_signal -v`
Expected: FAIL with `ImportError: cannot import name 'process_signal'`

- [ ] **Step 3: Write minimal implementation**

Append to `watchlist.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_watchlist.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add watchlist.py tests/test_watchlist.py
git commit -m "feat: add anti-spam signal processing"
```

---

## Task 5: Adaptive threshold auto-tuning

**Files:**
- Modify: `watchlist.py`
- Test: `tests/test_watchlist.py`

**Interfaces:**
- Consumes: `Watch`, detection constants (Task 3).
- Produces: `auto_tune(watch: Watch, now: datetime) -> None` — mutates `watch.percentile` and `watch.last_adjusted_at`.

Behavior (evaluated at most once per `ADJUST_INTERVAL_HOURS`): if the watch is at least `STARVE_DAYS` old and has no alert in the last `STARVE_DAYS` days, loosen `+LOOSEN_STEP`; else if it has `FLOOD_ALERTS`+ alerts in the last `FLOOD_DAYS` days, tighten `-TIGHTEN_STEP`. Clamp to `[PERCENTILE_MIN, PERCENTILE_MAX]`. The age guard prevents a brand-new watch from loosening before it has had a real chance to fire.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_watchlist.py`:

```python
from datetime import timedelta

from watchlist import auto_tune


def test_auto_tune_loosens_when_starved():
    now = _now()
    w = Watch(item_id=1, label="x", percentile=35.0, added_at=now - timedelta(days=30))
    auto_tune(w, now)
    assert w.percentile == 40.0  # +5, no alerts ever
    assert w.last_adjusted_at == now


def test_auto_tune_skips_new_watch():
    now = _now()
    w = Watch(item_id=1, label="x", percentile=35.0, added_at=now - timedelta(days=2))
    auto_tune(w, now)
    assert w.percentile == 35.0  # too young to loosen


def test_auto_tune_tightens_when_flooded():
    now = _now()
    w = Watch(item_id=1, label="x", percentile=35.0, added_at=now - timedelta(days=30))
    w.alert_history = [now - timedelta(days=1), now - timedelta(days=2)]  # 2 in last 7 days
    auto_tune(w, now)
    assert w.percentile == 34.0  # -1


def test_auto_tune_respects_24h_interval():
    now = _now()
    w = Watch(item_id=1, label="x", percentile=35.0, added_at=now - timedelta(days=30))
    w.last_adjusted_at = now - timedelta(hours=5)
    auto_tune(w, now)
    assert w.percentile == 35.0  # too soon, unchanged


def test_auto_tune_clamps_to_max():
    now = _now()
    w = Watch(item_id=1, label="x", percentile=48.0, added_at=now - timedelta(days=30))
    auto_tune(w, now)
    assert w.percentile == 50.0  # 48 + 5 clamped to 50


def test_auto_tune_clamps_to_min():
    now = _now()
    w = Watch(item_id=1, label="x", percentile=10.0, added_at=now - timedelta(days=30))
    w.alert_history = [now - timedelta(days=1), now - timedelta(days=2)]
    auto_tune(w, now)
    assert w.percentile == 10.0  # already at floor
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_watchlist.py -k auto_tune -v`
Expected: FAIL with `ImportError: cannot import name 'auto_tune'`

- [ ] **Step 3: Write minimal implementation**

Append to `watchlist.py`:

```python
from datetime import timedelta  # add to the datetime import at the top of the file


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
```

Note: `from datetime import ...` at the top of `watchlist.py` must include `timedelta` (merge with the existing datetime import line rather than adding a duplicate import).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_watchlist.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add watchlist.py tests/test_watchlist.py
git commit -m "feat: add adaptive threshold auto-tuning"
```

---

## Task 6: Watchlist store with save/load

**Files:**
- Modify: `watchlist.py`
- Test: `tests/test_watchlist.py`

**Interfaces:**
- Consumes: `Watch` (Task 3).
- Produces:
  - `Watchlist` class: `add(item_id: int, label: str) -> Watch`, `remove(item_id: int) -> bool`, `get(item_id: int) -> Optional[Watch]`, `all() -> List[Watch]`, `save(path: str = "watches.json") -> None`, `Watchlist.load(path: str = "watches.json") -> "Watchlist"`
  - `Watch.to_dict() -> dict`, `Watch.from_dict(d: dict) -> Watch` (classmethod)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_watchlist.py`:

```python
from watchlist import Watchlist


def test_add_creates_and_updates():
    wl = Watchlist()
    w = wl.add(21877, "Netherweave Cloth")
    assert w.item_id == 21877
    # Adding the same id again updates the label, not a duplicate.
    wl.add(21877, "Netherweave (renamed)")
    assert len(wl.all()) == 1
    assert wl.get(21877).label == "Netherweave (renamed)"


def test_remove():
    wl = Watchlist()
    wl.add(1, "a")
    assert wl.remove(1) is True
    assert wl.remove(1) is False
    assert wl.all() == []


def test_save_load_round_trip(tmp_path):
    path = str(tmp_path / "watches.json")
    wl = Watchlist()
    w = wl.add(21877, "Netherweave Cloth")
    w.percentile = 42.0
    w.state = "alerted"
    w.alert_history = [datetime(2026, 7, 1, tzinfo=timezone.utc)]
    w.last_adjusted_at = datetime(2026, 7, 2, tzinfo=timezone.utc)
    wl.save(path)

    loaded = Watchlist.load(path)
    lw = loaded.get(21877)
    assert lw.label == "Netherweave Cloth"
    assert lw.percentile == 42.0
    assert lw.state == "alerted"
    assert lw.alert_history == [datetime(2026, 7, 1, tzinfo=timezone.utc)]
    assert lw.last_adjusted_at == datetime(2026, 7, 2, tzinfo=timezone.utc)


def test_load_missing_file_is_empty():
    assert Watchlist.load(str("does_not_exist_watches.json")).all() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_watchlist.py -k "add or remove or round_trip or missing_file" -v`
Expected: FAIL with `ImportError: cannot import name 'Watchlist'`

- [ ] **Step 3: Write minimal implementation**

Add `import json` and `import os` to the top of `watchlist.py` (merge with existing imports). Then add `to_dict`/`from_dict` methods to the `Watch` dataclass:

```python
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
```

Then append the store class to `watchlist.py`:

```python
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
            for entry in data.get("watches", []):
                watch = Watch.from_dict(entry)
                wl._watches[watch.item_id] = watch
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Error loading %s: %s. Starting with empty watchlist.", path, exc)
        return wl
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_watchlist.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add watchlist.py tests/test_watchlist.py
git commit -m "feat: add Watchlist store with atomic save/load"
```

---

## Task 7: Display formatters for alert DM and watch list

**Files:**
- Modify: `watchlist.py`
- Test: `tests/test_watchlist.py`

**Interfaces:**
- Consumes: `Watch`, `Signal`, `format_gold` (earlier tasks).
- Produces:
  - `format_alert(watch: Watch, signal: Signal) -> str`
  - `format_watch_line(watch: Watch, signal: Optional[Signal]) -> str`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_watchlist.py`:

```python
from watchlist import format_alert, format_watch_line


def test_format_alert_contains_key_facts():
    w = _watch()
    sig = Signal(fired=True, enough_history=True, price=700, median=1000.0, low_band=800.0, quantity=1234)
    text = format_alert(w, sig)
    assert "Netherweave Cloth" in text
    assert "21877" in text
    assert "30% below" in text  # (1 - 700/1000) * 100
    assert "1,234" in text
    assert "wowhead.com/item=21877" in text


def test_format_watch_line_insufficient_data():
    w = _watch()
    line = format_watch_line(w, None)
    assert "insufficient data" in line
    assert "21877" in line


def test_format_watch_line_with_signal():
    w = _watch()
    sig = Signal(fired=False, enough_history=True, price=1000, median=1000.0, low_band=800.0, quantity=5)
    line = format_watch_line(w, sig)
    assert "Netherweave Cloth" in line
    assert "p35" in line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_watchlist.py -k format_alert -v`
Expected: FAIL with `ImportError: cannot import name 'format_alert'`

- [ ] **Step 3: Write minimal implementation**

Add `from textwrap import dedent` to the top of `watchlist.py` (merge with existing imports). Then append:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_watchlist.py -v`
Expected: PASS (entire watchlist suite)

- [ ] **Step 5: Commit**

```bash
git add watchlist.py tests/test_watchlist.py
git commit -m "feat: add alert and watch-list display formatters"
```

---

## Task 8: Wire BANKER_ID env, imports, and setup_hook

**Files:**
- Modify: `bot.py` (env block near line 45-48; imports near line 15-18; `setup_hook` near line 1046-1077)

**Interfaces:**
- Consumes: `Watchlist` (Task 6), `undermine`/`watchlist` modules.
- Produces: `self.watchlist: Watchlist` on `MyClient`; module-level `BANKER_ID: int`; a started `price_watch_check` task (implemented in Task 9 — here it is a stub so `setup_hook` can start it).

- [ ] **Step 1: Add module imports**

In `bot.py`, after the existing `from views import ...` line (line 18), add:

```python
import undermine
import watchlist
from watchlist import Watchlist
```

- [ ] **Step 2: Add the BANKER_ID env var**

In `bot.py`, after `COORDINATOR_ID = int(_require_env("COORDINATOR_ID"))` (line 45), add:

```python
BANKER_ID = int(_require_env("BANKER_ID"))
```

- [ ] **Step 3: Add a stub task so setup_hook can start it**

In `bot.py`, immediately before the `weekly_avail_reset` task definition (near line 794, i.e. after `hourly_check` ends), add a stub task (fully implemented in Task 9):

```python
    @tasks.loop(hours=1)
    async def price_watch_check(self):
        """Hourly Undermine price sweep (implemented in Task 9)."""
        if not self.is_ready():
            return
```

- [ ] **Step 4: Load the watchlist and start the task in setup_hook**

In `bot.py` `setup_hook`, after `self.elevated_ids = ELEVATED_IDS` (line 1059), add:

```python
        self.watchlist = Watchlist.load()
```

And after the `weekly_avail_reset` start block (line 1077), add:

```python
        # Start hourly Undermine price-watch sweep
        if not self.price_watch_check.is_running():
            self.price_watch_check.start()
```

- [ ] **Step 5: Verify the module compiles and tests still pass**

Run: `python -m py_compile bot.py && python -m pytest -q`
Expected: no compile errors; existing suite passes.

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "feat: wire BANKER_ID, watchlist load, and price-watch task"
```

---

## Task 9: Implement the price_watch_check sweep

**Files:**
- Modify: `bot.py` (the `price_watch_check` stub from Task 8)

**Interfaces:**
- Consumes: `undermine.fetch_now`, `undermine.fetch_daily`, `watchlist.evaluate`, `watchlist.process_signal`, `watchlist.auto_tune`, `watchlist.format_alert`, `self.watchlist`, `BANKER_ID`.
- Produces: hourly DM alerts to the banker; persisted `watches.json`.

- [ ] **Step 1: Add the aiohttp import**

In `bot.py`, add to the import block near the top:

```python
import aiohttp
```

- [ ] **Step 2: Replace the stub body with the full sweep**

Replace the `price_watch_check` stub (from Task 8) with:

```python
    @tasks.loop(hours=1)
    async def price_watch_check(self):
        """Hourly sweep of watched commodities; DMs the banker on a buy signal."""
        if not self.is_ready():
            logger.warning("price_watch_check: Bot not ready yet, skipping this iteration")
            return

        watches = self.watchlist.all()
        if not watches:
            return

        now = datetime.now(timezone.utc)
        banker = None
        async with aiohttp.ClientSession() as session:
            for watch in watches:
                try:
                    now_result = await undermine.fetch_now(session, watch.item_id)
                    if now_result is None:
                        continue
                    daily = await undermine.fetch_daily(session, watch.item_id)
                    signal = watchlist.evaluate(now_result.price, now_result.quantity, daily, watch)
                    if watchlist.process_signal(watch, signal, now):
                        if banker is None:
                            banker = await self.fetch_user(BANKER_ID)
                        try:
                            await banker.send(watchlist.format_alert(watch, signal))
                        except discord.HTTPException:
                            logger.warning("price_watch_check: could not DM banker for item %s", watch.item_id)
                    watchlist.auto_tune(watch, now)
                except Exception as exc:  # noqa: BLE001 - one bad item must not kill the sweep
                    logger.warning("price_watch_check failed for item %s: %s", watch.item_id, exc)

        self.watchlist.save()
```

- [ ] **Step 3: Verify compile and tests**

Run: `python -m py_compile bot.py && python -m pytest -q`
Expected: no compile errors; suite passes.

- [ ] **Step 4: Manual smoke check (optional but recommended)**

With a valid `.env`, add a temporary watch to `watches.json` for item `21877` and run the bot briefly; confirm the task logs no errors and that `watches.json` is rewritten. (No assertion — this is an integration sanity check.)

- [ ] **Step 5: Commit**

```bash
git add bot.py
git commit -m "feat: implement hourly price-watch sweep with banker alerts"
```

---

## Task 10: Owner-gated commands (!watch / !unwatch / !watches)

**Files:**
- Modify: `bot.py` `on_message` (near line 1123 onward)

**Interfaces:**
- Consumes: `self.watchlist`, `BANKER_ID`, `undermine`, `watchlist`, `aiohttp`.
- Produces: three commands, all restricted to `BANKER_ID`, replies via DM, command message deleted when sent in a guild channel.

- [ ] **Step 1: Add the command handlers**

In `bot.py` `on_message`, after the `if message.author.id == self.user.id: return` guard (line 1126) and before the `!keys` block, add:

```python
        if message.content.startswith("!watch ") and message.author.id == BANKER_ID:
            parts = message.content.split(maxsplit=2)
            if message.guild is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
            if len(parts) < 2 or not parts[1].isdigit():
                await message.author.send("Usage: `!watch <itemId> [label]`")
                return
            item_id = int(parts[1])
            label = parts[2] if len(parts) > 2 else f"Item {item_id}"
            self.watchlist.add(item_id, label)
            self.watchlist.save()
            await message.author.send(f"👁️ Now watching **{label}** (item {item_id}).")
            return

        if message.content.startswith("!unwatch ") and message.author.id == BANKER_ID:
            parts = message.content.split()
            if message.guild is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
            if len(parts) < 2 or not parts[1].isdigit():
                await message.author.send("Usage: `!unwatch <itemId>`")
                return
            item_id = int(parts[1])
            if self.watchlist.remove(item_id):
                self.watchlist.save()
                await message.author.send(f"🚫 Stopped watching item {item_id}.")
            else:
                await message.author.send(f"Item {item_id} was not being watched.")
            return

        if message.content == "!watches" and message.author.id == BANKER_ID:
            if message.guild is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
            watches = self.watchlist.all()
            if not watches:
                await message.author.send("No items are being watched. Add one with `!watch <itemId> [label]`.")
                return
            lines = []
            async with aiohttp.ClientSession() as session:
                for watch in watches:
                    try:
                        now_result = await undermine.fetch_now(session, watch.item_id)
                        daily = await undermine.fetch_daily(session, watch.item_id)
                        sig = (
                            watchlist.evaluate(now_result.price, now_result.quantity, daily, watch)
                            if now_result
                            else None
                        )
                    except Exception:  # noqa: BLE001 - display best-effort
                        sig = None
                    lines.append(watchlist.format_watch_line(watch, sig))
            await message.author.send("**Watched items:**\n" + "\n".join(lines))
            return
```

- [ ] **Step 2: Verify compile and tests**

Run: `python -m py_compile bot.py && python -m pytest -q`
Expected: no compile errors; suite passes.

- [ ] **Step 3: Manual smoke check (optional)**

With the bot running and logged in as the `BANKER_ID` user, DM `!watch 21877 Netherweave Cloth`, then `!watches`, then `!unwatch 21877`. Confirm the expected DMs and that `watches.json` updates.

- [ ] **Step 4: Commit**

```bash
git add bot.py
git commit -m "feat: add owner-gated !watch/!unwatch/!watches commands"
```

---

## Task 11: Documentation

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `CHANGELOG.md`

- [ ] **Step 1: Update CLAUDE.md**

- Add `undermine.py`, `watchlist.py` to the File Map, and `watches.json` to State Persistence notes.
- Add a "Price Watch (Undermine)" subsection under Key Workflows describing: owner-only (`BANKER_ID`), region-wide commodities, hourly `price_watch_check` sweep, rolling-percentile buy signal with per-item adaptive threshold (start 35, +5 starve / −1 flood, clamp 10–50), anti-spam re-arm above median.
- Add `!watch`, `!unwatch`, `!watches` to the Commands table (Who = Banker).
- Add `UNDERMINE_API_KEY`, `UNDERMINE_REGION`, `BANKER_ID` to Environment Variables.

- [ ] **Step 2: Update README.md**

Add a "Price Watch" feature section: what it does, the three commands, and the required env vars (`UNDERMINE_API_KEY`, optional `UNDERMINE_REGION` default `us`, `BANKER_ID`).

- [ ] **Step 3: Update CHANGELOG.md**

Add a new version entry (bump minor from `1.0.7` → `1.1.0`) with an `### Improvements` section:

```markdown
## [1.1.0] - 2026-07-08

### Improvements
- Added an owner-only Undermine Exchange price watch: `!watch`, `!unwatch`, and `!watches` let the banker track region-wide commodities and receive a DM when a price dips into a self-adjusting "pounce" low.
```

Also update `BOT_VERSION = "1.0.7"` → `"1.1.0"` in `bot.py` (near line 53). Do **not** edit `version.txt` — the bot writes it on next startup.

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest -q`
Expected: full suite passes.

```bash
git add CLAUDE.md README.md CHANGELOG.md bot.py
git commit -m "docs: document Undermine price-watch feature (v1.1.0)"
```

---

## Self-Review Notes

- **Spec coverage:** Config (Task 1/8), API client (Task 1), rolling-percentile detection (Task 3), anti-spam (Task 4), adaptive threshold (Task 5), store/persistence (Task 6), alert + list display (Task 7), hourly loop (Task 9), owner-gated commands (Task 10), errors/edge cases (Task 1 parsers + Task 9 try/except), tests (Tasks 1-7), docs (Task 11). All spec sections map to a task.
- **Refinement beyond spec:** `auto_tune` adds an "old enough" (≥ `STARVE_DAYS`) guard so brand-new watches don't loosen before they've had a real chance to fire. Spec updated to reflect this.
- **Type consistency:** `NowResult(price, quantity)`, `Signal(fired, enough_history, price, median, low_band, quantity)`, and `Watch` fields are used identically across tasks 3-10. `evaluate` always takes `(now_price, quantity, daily_prices, watch)`.
