# Blizzard Auction Sniper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any guild member track per-realm WoW items/pets across all region realms and get DM'd when the cheapest listing anywhere drops below their target price (recipes also CC the banker).

**Architecture:** A new `blizzard.py` async client (OAuth + Auction House fetches, mirroring `undermine.py`), a new `snipelist.py` for state + pure cross-realm detection (mirroring `watchlist.py`), and `bot.py` wiring (a 30-minute sweep task + `!snipe`/`!snipepet`/`!unsnipe`/`!snipes` commands). State persists to `snipes.json`.

**Tech Stack:** Python 3.9+, discord.py v2, aiohttp, pytest, ruff.

## Global Constraints

- Python **3.9+** compatible. Use `typing` imports (`Dict`, `List`, `Optional`, `Tuple`) to match `watchlist.py`/`raider.py` style, not `X | None`.
- Prices are stored in **copper** everywhere; convert to gold only for display via `watchlist.format_gold`.
- JSON persistence uses an **atomic temp-file write + `os.replace`** (same pattern as `save_state`/`Watchlist.save`). State file is `snipes.json`, kept **separate** from `state.json`.
- Background tasks are **best-effort**: `is_ready()` guard up front, broad `try/except` so one bad realm/DM never kills the loop (same discipline as `price_watch_check`).
- `logging.getLogger("discord")` for logging. No secrets in logs.
- `bot.py` is **not importable in tests** (it calls `client.run()` at import and requires full env). All unit-testable logic therefore lives in `snipelist.py` and `blizzard.py` pure functions. `bot.py` glue is exact code, verified by `python -m py_compile`.
- ruff config: line-length 120, `select = E,F,W,I`, `ignore = E501`. Keep imports sorted.
- Commit messages: end with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Prefix git commands with `rtk` per the repo's global convention.
- **Blizzard API facts (verified in the design spike):** token via `POST https://oauth.battle.net/token` (HTTP Basic client id:secret, `grant_type=client_credentials`), ~24h lifetime. Data host `https://{region}.api.blizzard.com`. Namespaces: `dynamic-{region}` for AH, `static-{region}` for item/realm/pet metadata. Item auction: `{id, item:{id}, buyout, quantity, time_left}`. Pet auction: `item.id == 82800` with `pet_species_id`. Recipe = item's `item_class.id == 9`.

---

## File Structure

- **Create `snipelist.py`** — `Subscriber`/`Snipe`/`Snipelist`, pure detection (`parse_gold`, `best_price_for`, `cheapest`, `apply_anti_spam`, `plan_alerts`), formatters, persistence.
- **Create `blizzard.py`** — pure parsers (`_parse_token`, `_parse_realm_index`, `_parse_auctions`, `_parse_item_info`, `_parse_realm_name`) + `BlizzardClient` async wrapper (token cache, fetches, name caches).
- **Create `tests/test_snipelist.py`** — all `snipelist.py` logic.
- **Create `tests/test_blizzard.py`** — the pure parsers.
- **Modify `bot.py`** — config (`BLIZZ_*`), instantiate client + snipelist in `MyClient.__init__`, load `snipes.json`, the `auction_snipe_check` task, the four commands, start the task in `setup_hook`.
- **Modify docs** — `CLAUDE.md`, `README.md`, `CHANGELOG.md`.

---

### Task 1: `snipelist.py` — pure detection helpers

**Files:**
- Create: `snipelist.py`
- Test: `tests/test_snipelist.py`

**Interfaces:**
- Produces:
  - `PET_ITEM_ID = 82800`
  - `parse_gold(text: str) -> Optional[int]`
  - `best_price_for(auctions: List[dict], kind: str, key_id: int) -> Optional[Tuple[int, int]]` → `(price, qty)`
  - `cheapest(realm_prices: Dict[int, Tuple[int, int]]) -> Optional[Tuple[int, int, int]]` → `(price, qty, realm_id)`
  - `apply_anti_spam(fired: bool, state: str) -> Tuple[bool, str]` → `(should_dm, new_state)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_snipelist.py
"""Tests for the auction-snipe state, detection, and formatting logic."""

from snipelist import PET_ITEM_ID, apply_anti_spam, best_price_for, cheapest, parse_gold


def test_parse_gold_whole_and_decimal():
    assert parse_gold("100") == 1_000_000        # 100g -> copper
    assert parse_gold("2.5") == 25_000           # 2.5g -> copper


def test_parse_gold_rejects_bad_input():
    assert parse_gold("abc") is None
    assert parse_gold("0") is None
    assert parse_gold("-5") is None


def test_best_price_for_item_picks_cheapest_buyout():
    auctions = [
        {"item": {"id": 111}, "buyout": 5000, "quantity": 1},
        {"item": {"id": 111}, "buyout": 3000, "quantity": 2},
        {"item": {"id": 222}, "buyout": 10, "quantity": 1},
    ]
    assert best_price_for(auctions, "item", 111) == (3000, 2)


def test_best_price_for_ignores_bid_only_and_missing():
    auctions = [
        {"item": {"id": 111}, "bid": 100, "quantity": 1},   # no buyout
        {"item": {"id": 111}, "buyout": 0, "quantity": 1},  # zero buyout
    ]
    assert best_price_for(auctions, "item", 111) is None


def test_best_price_for_pet_matches_species():
    auctions = [
        {"item": {"id": PET_ITEM_ID, "pet_species_id": 3022}, "buyout": 9000, "quantity": 1},
        {"item": {"id": PET_ITEM_ID, "pet_species_id": 9999}, "buyout": 10, "quantity": 1},
        {"item": {"id": 3022}, "buyout": 1, "quantity": 1},  # a normal item, not the pet
    ]
    assert best_price_for(auctions, "pet", 3022) == (9000, 1)


def test_cheapest_across_realms():
    assert cheapest({101: (5000, 1), 102: (3000, 4), 103: (8000, 2)}) == (3000, 4, 102)
    assert cheapest({}) is None


def test_apply_anti_spam_state_machine():
    assert apply_anti_spam(True, "armed") == (True, "alerted")    # fire -> DM once
    assert apply_anti_spam(True, "alerted") == (False, "alerted")  # stay silent
    assert apply_anti_spam(False, "alerted") == (False, "armed")   # re-arm
    assert apply_anti_spam(False, "armed") == (False, "armed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_snipelist.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'snipelist'`

- [ ] **Step 3: Write minimal implementation**

```python
# snipelist.py
"""Server-specific auction-snipe state, cross-realm detection, and formatting."""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from watchlist import format_gold  # reuse the copper -> g/s/c formatter

logger = logging.getLogger("discord")

PET_ITEM_ID = 82800  # AH "caged battle pet" item id; pets are keyed by species id


def parse_gold(text: str) -> Optional[int]:
    """Parse a whole/decimal gold amount into copper. None if invalid or non-positive."""
    try:
        gold = float(text)
    except (TypeError, ValueError):
        return None
    if gold <= 0:
        return None
    return int(round(gold * 10000))


def best_price_for(auctions: List[dict], kind: str, key_id: int) -> Optional[Tuple[int, int]]:
    """Cheapest (buyout_price, quantity) for a key within one realm's auctions, or None.

    Items match on item.id; pets match item.id == PET_ITEM_ID and pet_species_id.
    Bid-only auctions (no positive buyout) are ignored.
    """
    best: Optional[Tuple[int, int]] = None
    for a in auctions:
        item = a.get("item", {})
        if kind == "pet":
            if item.get("id") != PET_ITEM_ID or item.get("pet_species_id") != key_id:
                continue
        elif item.get("id") != key_id:
            continue
        price = a.get("buyout")
        if not price:  # None or 0 -> bid-only / not purchasable
            continue
        qty = int(a.get("quantity", 1))
        if best is None or price < best[0]:
            best = (int(price), qty)
    return best


def cheapest(realm_prices: Dict[int, Tuple[int, int]]) -> Optional[Tuple[int, int, int]]:
    """Given {realm_id: (price, qty)}, return (price, qty, realm_id) at the minimum price."""
    best: Optional[Tuple[int, int, int]] = None
    for realm_id, (price, qty) in realm_prices.items():
        if best is None or price < best[0]:
            best = (price, qty, realm_id)
    return best


def apply_anti_spam(fired: bool, state: str) -> Tuple[bool, str]:
    """Anti-spam state machine shared by a subscriber and the banker CC.

    Returns (should_dm, new_state). Fire while armed -> DM once, go alerted.
    Not firing re-arms. Never DMs twice for the same continuous dip.
    """
    if fired:
        if state == "armed":
            return True, "alerted"
        return False, "alerted"
    return False, "armed"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_snipelist.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
rtk git add snipelist.py tests/test_snipelist.py
rtk git commit -m "feat: snipelist detection helpers (parse_gold, best_price_for, cheapest, anti-spam)"
```

---

### Task 2: `snipelist.py` — data model + persistence

**Files:**
- Modify: `snipelist.py`
- Test: `tests/test_snipelist.py`

**Interfaces:**
- Produces:
  - `Subscriber(target_copper: int, state: str = "armed", added_at: datetime)` with `to_dict`/`from_dict`
  - `Snipe(kind, key_id, label, is_recipe=False, subscribers: Dict[int, Subscriber], banker_state="armed", last_realm=None, last_price=None)` with `to_dict`/`from_dict`
  - `Snipelist` with `subscribe(owner_id, kind, key_id, target_copper, label, is_recipe) -> Snipe`, `unsubscribe(owner_id, kind, key_id) -> bool`, `all() -> List[Snipe]`, `for_owner(owner_id) -> List[Snipe]`, `watched_keys() -> List[Tuple[str,int]]`, `get(kind, key_id) -> Optional[Snipe]`, `save(path)`, classmethod `load(path) -> Snipelist`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_snipelist.py
from datetime import datetime, timezone

from snipelist import Snipe, Snipelist, Subscriber


def test_subscribe_creates_one_record_many_subscribers():
    sl = Snipelist()
    sl.subscribe(1, "item", 111, 5000, "Widget", is_recipe=False)
    sl.subscribe(2, "item", 111, 8000, "Widget", is_recipe=False)
    assert len(sl.all()) == 1                       # one record
    snipe = sl.get("item", 111)
    assert set(snipe.subscribers) == {1, 2}         # two subscribers
    assert snipe.subscribers[1].target_copper == 5000
    assert snipe.subscribers[2].target_copper == 8000
    assert sl.watched_keys() == [("item", 111)]     # deduped to one fetch key


def test_resubscribe_updates_own_target_in_place():
    sl = Snipelist()
    sl.subscribe(1, "item", 111, 5000, "Widget", is_recipe=False)
    sl.subscribe(1, "item", 111, 4000, "Widget", is_recipe=False)
    assert sl.get("item", 111).subscribers[1].target_copper == 4000
    assert len(sl.get("item", 111).subscribers) == 1


def test_unsubscribe_removes_only_caller_keeps_others():
    sl = Snipelist()
    sl.subscribe(1, "item", 111, 5000, "Widget", is_recipe=False)
    sl.subscribe(2, "item", 111, 8000, "Widget", is_recipe=False)
    assert sl.unsubscribe(1, "item", 111) is True
    snipe = sl.get("item", 111)
    assert set(snipe.subscribers) == {2}            # record survives for #2
    assert sl.unsubscribe(1, "item", 111) is False  # already gone


def test_unsubscribe_last_deletes_record():
    sl = Snipelist()
    sl.subscribe(2, "item", 111, 8000, "Widget", is_recipe=False)
    assert sl.unsubscribe(2, "item", 111) is True
    assert sl.get("item", 111) is None
    assert sl.all() == []


def test_for_owner_only_returns_subscribed():
    sl = Snipelist()
    sl.subscribe(1, "item", 111, 5000, "A", is_recipe=False)
    sl.subscribe(2, "item", 222, 5000, "B", is_recipe=False)
    assert [s.key_id for s in sl.for_owner(1)] == [111]


def test_save_load_round_trip(tmp_path):
    path = str(tmp_path / "snipes.json")
    sl = Snipelist()
    sl.subscribe(1, "item", 111, 5000, "Widget", is_recipe=True)
    sl.subscribe(2, "pet", 3022, 9000, "Critter", is_recipe=False)
    snipe = sl.get("item", 111)
    snipe.banker_state = "alerted"
    snipe.last_realm, snipe.last_price = 121, 4200
    sl.save(path)

    loaded = Snipelist.load(path)
    a = loaded.get("item", 111)
    assert a.is_recipe is True
    assert a.banker_state == "alerted"
    assert a.last_realm == 121 and a.last_price == 4200
    assert a.subscribers[1].target_copper == 5000
    assert loaded.get("pet", 3022).subscribers[2].target_copper == 9000


def test_load_missing_file_is_empty():
    assert Snipelist.load("does_not_exist_snipes.json").all() == []


def test_load_skips_malformed_entry(tmp_path):
    import json
    path = str(tmp_path / "snipes.json")
    valid = Snipe("item", 111, "Widget", subscribers={1: Subscriber(5000)}).to_dict()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "snipes": [{"junk": True}, valid]}, f)
    sl = Snipelist.load(path)
    assert len(sl.all()) == 1 and sl.get("item", 111) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_snipelist.py -q`
Expected: FAIL — `ImportError: cannot import name 'Snipe'`

- [ ] **Step 3: Write minimal implementation**

Append to `snipelist.py`:

```python
@dataclass
class Subscriber:
    """One member's subscription to a snipe: their target price + anti-spam state."""

    target_copper: int
    state: str = "armed"  # "armed" | "alerted"
    added_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {"target_copper": self.target_copper, "state": self.state, "added_at": self.added_at.isoformat()}

    @classmethod
    def from_dict(cls, d: dict) -> "Subscriber":
        return cls(int(d["target_copper"]), d.get("state", "armed"), datetime.fromisoformat(d["added_at"]))


@dataclass
class Snipe:
    """One tracked item/pet (one record), shared by all its subscribers."""

    kind: str  # "item" | "pet"
    key_id: int  # item id, or pet species id
    label: str
    is_recipe: bool = False
    subscribers: Dict[int, Subscriber] = field(default_factory=dict)
    banker_state: str = "armed"  # recipe-CC anti-spam (record-level)
    last_realm: Optional[int] = None
    last_price: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "key_id": self.key_id,
            "label": self.label,
            "is_recipe": self.is_recipe,
            "subscribers": {str(uid): s.to_dict() for uid, s in self.subscribers.items()},
            "banker_state": self.banker_state,
            "last_realm": self.last_realm,
            "last_price": self.last_price,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Snipe":
        return cls(
            kind=d["kind"],
            key_id=int(d["key_id"]),
            label=d["label"],
            is_recipe=bool(d.get("is_recipe", False)),
            subscribers={int(uid): Subscriber.from_dict(s) for uid, s in d.get("subscribers", {}).items()},
            banker_state=d.get("banker_state", "armed"),
            last_realm=d.get("last_realm"),
            last_price=d.get("last_price"),
        )


class Snipelist:
    """In-memory store of snipes (one per item), persisted to snipes.json."""

    def __init__(self) -> None:
        self._snipes: Dict[Tuple[str, int], Snipe] = {}

    def subscribe(self, owner_id: int, kind: str, key_id: int, target_copper: int, label: str, is_recipe: bool) -> Snipe:
        key = (kind, key_id)
        snipe = self._snipes.get(key)
        if snipe is None:
            snipe = Snipe(kind=kind, key_id=key_id, label=label, is_recipe=is_recipe)
            self._snipes[key] = snipe
        else:
            snipe.label = label
            snipe.is_recipe = is_recipe
        sub = snipe.subscribers.get(owner_id)
        if sub is not None:
            sub.target_copper = target_copper
        else:
            snipe.subscribers[owner_id] = Subscriber(target_copper=target_copper)
        return snipe

    def unsubscribe(self, owner_id: int, kind: str, key_id: int) -> bool:
        key = (kind, key_id)
        snipe = self._snipes.get(key)
        if snipe is None or owner_id not in snipe.subscribers:
            return False
        del snipe.subscribers[owner_id]
        if not snipe.subscribers:
            del self._snipes[key]
        return True

    def all(self) -> List[Snipe]:
        return list(self._snipes.values())

    def for_owner(self, owner_id: int) -> List[Snipe]:
        return [s for s in self._snipes.values() if owner_id in s.subscribers]

    def watched_keys(self) -> List[Tuple[str, int]]:
        return list(self._snipes.keys())

    def get(self, kind: str, key_id: int) -> Optional[Snipe]:
        return self._snipes.get((kind, key_id))

    def save(self, path: str = "snipes.json") -> None:
        data = {"version": 1, "snipes": [s.to_dict() for s in self._snipes.values()]}
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)

    @classmethod
    def load(cls, path: str = "snipes.json") -> "Snipelist":
        sl = cls()
        if not os.path.exists(path):
            return sl
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Error loading %s: %s. Starting with empty snipelist.", path, exc)
            return sl
        for entry in data.get("snipes", []):
            try:
                snipe = Snipe.from_dict(entry)
                sl._snipes[(snipe.kind, snipe.key_id)] = snipe
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("Skipping malformed snipe entry in %s: %s", path, exc)
        return sl
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_snipelist.py -q`
Expected: PASS (all Task 1 + Task 2 tests)

- [ ] **Step 5: Commit**

```bash
rtk git add snipelist.py tests/test_snipelist.py
rtk git commit -m "feat: snipe data model (Subscriber/Snipe/Snipelist) + snipes.json persistence"
```

---

### Task 3: `snipelist.py` — alert planning (`plan_alerts`)

**Files:**
- Modify: `snipelist.py`
- Test: `tests/test_snipelist.py`

**Interfaces:**
- Produces:
  - `AlertPlan(recipient_id: int, is_banker: bool, target_copper: Optional[int])`
  - `plan_alerts(snipe: Snipe, best: Optional[Tuple[int, int, int]], banker_id: int) -> List[AlertPlan]` — mutates subscriber states, `banker_state`, and `last_realm`/`last_price`; returns the DMs to send this cycle.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_snipelist.py
from snipelist import AlertPlan, plan_alerts

BANKER = 999


def _snipe(is_recipe=False, subs=None):
    s = Snipe("item", 111, "Widget", is_recipe=is_recipe)
    for uid, tgt in (subs or {}).items():
        s.subscribers[uid] = Subscriber(tgt)
    return s


def test_plan_alerts_fires_only_subscribers_below_target():
    s = _snipe(subs={1: 5000, 2: 8000})     # best 6000 -> #2 fires, #1 doesn't
    plans = plan_alerts(s, (6000, 3, 121), BANKER)
    assert plans == [AlertPlan(2, False, 8000)]
    assert s.subscribers[2].state == "alerted"
    assert s.subscribers[1].state == "armed"
    assert s.last_realm == 121 and s.last_price == 6000


def test_plan_alerts_silent_while_alerted_then_rearms():
    s = _snipe(subs={2: 8000})
    plan_alerts(s, (6000, 1, 121), BANKER)                 # first fire
    assert plan_alerts(s, (5000, 1, 121), BANKER) == []    # still below -> silent
    plan_alerts(s, (9000, 1, 121), BANKER)                 # above target -> re-arm
    assert s.subscribers[2].state == "armed"
    assert plan_alerts(s, (6000, 1, 121), BANKER) == [AlertPlan(2, False, 8000)]  # fires again


def test_plan_alerts_recipe_ccs_banker():
    s = _snipe(is_recipe=True, subs={1: 5000})
    plans = plan_alerts(s, (4000, 2, 121), BANKER)
    assert AlertPlan(1, False, 5000) in plans
    assert AlertPlan(BANKER, True, None) in plans
    assert s.banker_state == "alerted"


def test_plan_alerts_non_recipe_never_ccs_banker():
    s = _snipe(is_recipe=False, subs={1: 5000})
    plans = plan_alerts(s, (4000, 2, 121), BANKER)
    assert plans == [AlertPlan(1, False, 5000)]


def test_plan_alerts_banker_cc_dedup_when_banker_is_subscriber():
    # Banker subscribes and fires as a subscriber -> no separate CC.
    s = _snipe(is_recipe=True, subs={BANKER: 5000})
    plans = plan_alerts(s, (4000, 1, 121), BANKER)
    assert plans == [AlertPlan(BANKER, False, 5000)]  # only the subscriber DM
    assert s.banker_state == "alerted"                # state still advances


def test_plan_alerts_no_listing_rearms_all():
    s = _snipe(is_recipe=True, subs={1: 5000})
    plan_alerts(s, (4000, 1, 121), BANKER)  # fire
    plan_alerts(s, None, BANKER)            # nothing listed anywhere
    assert s.subscribers[1].state == "armed"
    assert s.banker_state == "armed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_snipelist.py -q`
Expected: FAIL — `ImportError: cannot import name 'AlertPlan'`

- [ ] **Step 3: Write minimal implementation**

Append to `snipelist.py`:

```python
@dataclass(frozen=True)
class AlertPlan:
    """One DM the sweep should send this cycle."""

    recipient_id: int
    is_banker: bool
    target_copper: Optional[int]  # the subscriber's target; None for a banker CC


def plan_alerts(snipe: Snipe, best: Optional[Tuple[int, int, int]], banker_id: int) -> List[AlertPlan]:
    """Given a record's cheapest-anywhere `best` (price, qty, realm_id) or None, decide the DMs.

    Mutates each subscriber's state, the record's banker_state, and last_realm/last_price.
    Returns the list of AlertPlans to send. One DM per genuine dip per recipient.
    """
    plans: List[AlertPlan] = []
    if best is not None:
        snipe.last_price, _, snipe.last_realm = best[0], best[1], best[2]

    alerted_ids = set()
    for uid, sub in snipe.subscribers.items():
        fired = best is not None and best[0] < sub.target_copper
        should_dm, sub.state = apply_anti_spam(fired, sub.state)
        if should_dm:
            plans.append(AlertPlan(uid, False, sub.target_copper))
            alerted_ids.add(uid)

    # Recipes also CC the banker, once per dip, when the price beats at least one target.
    if snipe.is_recipe and snipe.subscribers:
        max_target = max(s.target_copper for s in snipe.subscribers.values())
        banker_fired = best is not None and best[0] < max_target
        should_cc, snipe.banker_state = apply_anti_spam(banker_fired, snipe.banker_state)
        if should_cc and banker_id not in alerted_ids:
            plans.append(AlertPlan(banker_id, True, None))
    return plans
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_snipelist.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
rtk git add snipelist.py tests/test_snipelist.py
rtk git commit -m "feat: snipe alert planning (per-subscriber DMs + recipe banker CC with dedup)"
```

---

### Task 4: `snipelist.py` — formatters

**Files:**
- Modify: `snipelist.py`
- Test: `tests/test_snipelist.py`

**Interfaces:**
- Produces:
  - `format_alert(snipe: Snipe, best: Tuple[int, int, int], realm_name: str, target_copper: int) -> str`
  - `format_banker_alert(snipe: Snipe, best: Tuple[int, int, int], realm_name: str, wanters: List[Tuple[str, int]]) -> str`
  - `format_snipe_line(snipe: Snipe, subscriber: Subscriber) -> str`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_snipelist.py
from snipelist import format_alert, format_banker_alert, format_snipe_line


def test_format_alert_has_key_facts():
    s = Snipe("item", 111, "Widget")
    text = format_alert(s, (4000, 3, 121), "Illidan", target_copper=5000)
    assert "Widget" in text
    assert "item:111" in text
    assert "Illidan" in text
    assert "20% below" in text            # (1 - 4000/5000) * 100
    assert "wowhead.com/item=111" in text


def test_format_banker_alert_lists_wanters():
    s = Snipe("item", 111, "Recipe: Widget", is_recipe=True)
    text = format_banker_alert(s, (4000, 2, 121), "Illidan", wanters=[("<@1>", 5000), ("<@2>", 8000)])
    assert "Illidan" in text
    assert "<@1>" in text and "<@2>" in text
    assert "wowhead.com/item=111" in text


def test_format_snipe_line_seen_and_unseen():
    s = Snipe("pet", 3022, "Critter", last_price=9000)
    line = format_snipe_line(s, Subscriber(10000, state="armed"))
    assert "Critter" in line and "pet:3022" in line and "armed" in line
    s2 = Snipe("item", 111, "Widget")  # never seen
    assert "not seen" in format_snipe_line(s2, Subscriber(5000))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_snipelist.py -q`
Expected: FAIL — `ImportError: cannot import name 'format_alert'`

- [ ] **Step 3: Write minimal implementation**

Append to `snipelist.py`:

```python
def _kind_tag(snipe: Snipe) -> str:
    return f"pet:{snipe.key_id}" if snipe.kind == "pet" else f"item:{snipe.key_id}"


def _wowhead_link(snipe: Snipe) -> str:
    if snipe.kind == "pet":
        return f"https://www.wowhead.com/battle-pet={snipe.key_id}"
    return f"https://www.wowhead.com/item={snipe.key_id}"


def format_alert(snipe: Snipe, best: Tuple[int, int, int], realm_name: str, target_copper: int) -> str:
    """DM sent to a subscriber whose target was beaten."""
    price, qty, _realm = best
    pct_below = (1 - price / target_copper) * 100 if target_copper else 0
    return "\n".join([
        f"🎯 **Snipe hit** — {snipe.label} ({_kind_tag(snipe)})",
        f"Your target: {format_gold(target_copper)}",
        f"**Cheapest: {format_gold(price)}** on **{realm_name}** — "
        f"{qty:,} available ({pct_below:.0f}% below your target)",
        _wowhead_link(snipe),
    ])


def format_banker_alert(snipe: Snipe, best: Tuple[int, int, int], realm_name: str, wanters: List[Tuple[str, int]]) -> str:
    """DM sent to the banker on a recipe alert; `wanters` is (mention, target_copper) pairs."""
    price, qty, _realm = best
    lines = [
        f"🏦 **Recipe snipe** — {snipe.label} ({_kind_tag(snipe)})",
        f"**Cheapest: {format_gold(price)}** on **{realm_name}** — {qty:,} available",
        "Wanted by:",
    ]
    lines += [f"• {who} (target {format_gold(tgt)})" for who, tgt in wanters]
    lines.append(_wowhead_link(snipe))
    return "\n".join(lines)


def format_snipe_line(snipe: Snipe, subscriber: Subscriber) -> str:
    """One row for the !snipes list (the caller's own view of a record)."""
    seen = f"last seen {format_gold(snipe.last_price)}" if snipe.last_price is not None else "not seen yet"
    return (
        f"• **{snipe.label}** ({_kind_tag(snipe)}) — "
        f"target {format_gold(subscriber.target_copper)}, {subscriber.state}, {seen}"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_snipelist.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
rtk git add snipelist.py tests/test_snipelist.py
rtk git commit -m "feat: snipe alert + list formatters"
```

---

### Task 5: `blizzard.py` — pure parsers

**Files:**
- Create: `blizzard.py`
- Test: `tests/test_blizzard.py`

**Interfaces:**
- Produces:
  - `ItemInfo(name: str, is_recipe: bool)`
  - `_parse_token(data: dict) -> Tuple[str, int]` → `(access_token, expires_in)`
  - `_parse_realm_index(data: dict) -> List[int]`
  - `_parse_auctions(data: dict) -> List[dict]`
  - `_parse_item_info(data: dict) -> ItemInfo`
  - `_parse_realm_name(data: dict) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_blizzard.py
"""Tests for the pure parsers of the Blizzard Game Data client."""

from blizzard import _parse_auctions, _parse_item_info, _parse_realm_index, _parse_realm_name, _parse_token


def test_parse_token():
    assert _parse_token({"access_token": "abc", "token_type": "bearer", "expires_in": 86399}) == ("abc", 86399)


def test_parse_realm_index_extracts_ids():
    data = {"connected_realms": [
        {"href": "https://us.api.blizzard.com/data/wow/connected-realm/121?namespace=dynamic-us"},
        {"href": "https://us.api.blizzard.com/data/wow/connected-realm/1146?namespace=dynamic-us"},
    ]}
    assert _parse_realm_index(data) == [121, 1146]


def test_parse_auctions_returns_list():
    data = {"auctions": [{"id": 1, "item": {"id": 111}, "buyout": 5000, "quantity": 1}]}
    assert _parse_auctions(data) == data["auctions"]
    assert _parse_auctions({}) == []


def test_parse_item_info_recipe_flag():
    recipe = {"name": "Recipe: Widget", "item_class": {"id": 9, "name": "Recipe"}}
    assert _parse_item_info(recipe) == __import__("blizzard").ItemInfo("Recipe: Widget", True)
    mount = {"name": "Reins of Something", "item_class": {"id": 15, "name": "Miscellaneous"}}
    info = _parse_item_info(mount)
    assert info.name == "Reins of Something" and info.is_recipe is False


def test_parse_realm_name():
    assert _parse_realm_name({"realms": [{"name": "Illidan"}, {"name": "Other"}]}) == "Illidan"
    assert _parse_realm_name({"realms": []}) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_blizzard.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'blizzard'`

- [ ] **Step 3: Write minimal implementation**

```python
# blizzard.py
"""Async client for Blizzard's WoW Game Data Auction House API (per-realm listings)."""

import base64
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger("discord")

OAUTH_URL = "https://oauth.battle.net/token"
RECIPE_ITEM_CLASS_ID = 9
NOT_MODIFIED = object()  # sentinel returned by get_realm_auctions on a 304


def _region() -> str:
    return os.getenv("BLIZZ_REGION", "us")


def _api_host() -> str:
    return f"https://{_region()}.api.blizzard.com"


@dataclass(frozen=True)
class ItemInfo:
    name: str
    is_recipe: bool


def _parse_token(data: dict) -> Tuple[str, int]:
    return data["access_token"], int(data.get("expires_in", 0))


def _parse_realm_index(data: dict) -> List[int]:
    ids: List[int] = []
    for cr in data.get("connected_realms", []):
        m = re.search(r"/connected-realm/(\d+)", cr.get("href", ""))
        if m:
            ids.append(int(m.group(1)))
    return ids


def _parse_auctions(data: dict) -> List[dict]:
    return data.get("auctions", [])


def _parse_item_info(data: dict) -> ItemInfo:
    name = data.get("name", "")
    is_recipe = data.get("item_class", {}).get("id") == RECIPE_ITEM_CLASS_ID
    return ItemInfo(name=name, is_recipe=is_recipe)


def _parse_realm_name(data: dict) -> str:
    realms = data.get("realms", [])
    return realms[0].get("name", "") if realms else ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_blizzard.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
rtk git add blizzard.py tests/test_blizzard.py
rtk git commit -m "feat: blizzard Game Data API pure parsers (token, realms, auctions, item class)"
```

---

### Task 6: `blizzard.py` — `BlizzardClient` async wrapper

**Files:**
- Modify: `blizzard.py`

**Interfaces:**
- Consumes: the pure parsers from Task 5, `aiohttp`.
- Produces: `BlizzardClient` with:
  - `async ensure_token(session) -> str`
  - `async list_connected_realms(session) -> List[int]`
  - `async get_realm_auctions(session, realm_id, if_modified_since=None) -> object` → `NOT_MODIFIED`, or `Tuple[List[dict], Optional[str]]` (auctions, last_modified)
  - `async item_info(session, item_id) -> ItemInfo` (cached)
  - `async pet_name(session, species_id) -> str` (cached)
  - `async realm_name(session, connected_realm_id) -> str` (cached)

*(Async HTTP wrappers are integration-verified, not unit-tested — the parsing they rely on is covered in Task 5. Verify with `py_compile`.)*

- [ ] **Step 1: Add the client class**

Append to `blizzard.py`:

```python
class BlizzardClient:
    """Holds the OAuth token + name caches for the Auction House sweep.

    One instance is created on the bot and reused; pass a live aiohttp session to each call.
    """

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0  # monotonic seconds
        self._item_cache: Dict[int, ItemInfo] = {}
        self._pet_cache: Dict[int, str] = {}
        self._realm_name_cache: Dict[int, str] = {}

    async def ensure_token(self, session: aiohttp.ClientSession) -> str:
        if self._token and time.monotonic() < self._token_expiry:
            return self._token
        cid = os.getenv("BLIZZ_CLIENT_ID", "")
        secret = os.getenv("BLIZZ_CLIENT_SECRET", "")
        basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
        async with session.post(
            OAUTH_URL,
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {basic}"},
        ) as resp:
            resp.raise_for_status()
            token, expires_in = _parse_token(await resp.json())
        self._token = token
        self._token_expiry = time.monotonic() + max(0, expires_in - 300)  # refresh 5 min early
        return token

    async def _get(self, session, path, namespace, headers=None):
        token = await self.ensure_token(session)
        h = {"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"}
        if headers:
            h.update(headers)
        url = f"{_api_host()}{path}?namespace={namespace}-{_region()}&locale=en_US"
        return await session.get(url, headers=h)

    async def list_connected_realms(self, session: aiohttp.ClientSession) -> List[int]:
        async with await self._get(session, "/data/wow/connected-realm/index", "dynamic") as resp:
            resp.raise_for_status()
            return _parse_realm_index(await resp.json())

    async def get_realm_auctions(self, session, realm_id, if_modified_since=None):
        extra = {"If-Modified-Since": if_modified_since} if if_modified_since else None
        async with await self._get(
            session, f"/data/wow/connected-realm/{realm_id}/auctions", "dynamic", extra
        ) as resp:
            if resp.status == 304:
                return NOT_MODIFIED
            resp.raise_for_status()
            data = await resp.json()
            return _parse_auctions(data), resp.headers.get("Last-Modified")

    async def item_info(self, session: aiohttp.ClientSession, item_id: int) -> ItemInfo:
        if item_id in self._item_cache:
            return self._item_cache[item_id]
        async with await self._get(session, f"/data/wow/item/{item_id}", "static") as resp:
            resp.raise_for_status()
            info = _parse_item_info(await resp.json())
        self._item_cache[item_id] = info
        return info

    async def pet_name(self, session: aiohttp.ClientSession, species_id: int) -> str:
        if species_id in self._pet_cache:
            return self._pet_cache[species_id]
        async with await self._get(session, f"/data/wow/pet/{species_id}", "static") as resp:
            resp.raise_for_status()
            name = (await resp.json()).get("name", f"Pet {species_id}")
        self._pet_cache[species_id] = name
        return name

    async def realm_name(self, session: aiohttp.ClientSession, connected_realm_id: int) -> str:
        if connected_realm_id in self._realm_name_cache:
            return self._realm_name_cache[connected_realm_id]
        async with await self._get(
            session, f"/data/wow/connected-realm/{connected_realm_id}", "dynamic"
        ) as resp:
            resp.raise_for_status()
            name = _parse_realm_name(await resp.json()) or f"Realm {connected_realm_id}"
        self._realm_name_cache[connected_realm_id] = name
        return name
```

- [ ] **Step 2: Verify it compiles and tests still pass**

Run: `python -m py_compile blizzard.py && python -m pytest tests/test_blizzard.py -q`
Expected: no compile errors; PASS

- [ ] **Step 3: Commit**

```bash
rtk git add blizzard.py
rtk git commit -m "feat: BlizzardClient async wrapper (token cache, realm/auction/name fetches)"
```

---

### Task 7: `bot.py` — config, client wiring, snipes.json load

**Files:**
- Modify: `bot.py` (env block near `BANKER_BUDGET_COPPER`; `MyClient.__init__` where `self.watchlist` is created)

**Interfaces:**
- Consumes: `blizzard.BlizzardClient`, `snipelist.Snipelist`.
- Produces: `self.blizzard`, `self.snipelist` on `MyClient`; `BLIZZ_REGION`, `SNIPE_SWEEP_MINUTES` module constants.

- [ ] **Step 1: Add imports + config**

In `bot.py`, add to the imports (near `import watchlist`):

```python
import blizzard
import snipelist as snipelist_mod
```

After the `BANKER_BULK_QTY` line in the config block, add:

```python
# Blizzard Game Data API — server-specific auction sniper (see snipes.json).
BLIZZ_CLIENT_ID = _require_env("BLIZZ_CLIENT_ID")
BLIZZ_CLIENT_SECRET = _require_env("BLIZZ_CLIENT_SECRET")
os.environ.setdefault("BLIZZ_REGION", os.getenv("BLIZZ_REGION", "us"))
SNIPE_SWEEP_MINUTES = 30
```

- [ ] **Step 2: Instantiate on the client**

Find where `MyClient.__init__` sets `self.watchlist = Watchlist.load()` and add beside it:

```python
        self.blizzard = blizzard.BlizzardClient()
        self.snipelist = snipelist_mod.Snipelist.load()
        self._snipe_realm_ids: list = []  # cached connected-realm ids
        self._snipe_price_cache: dict = {}  # realm_id -> {(kind,key_id): (price, qty)}
        self._snipe_realm_modified: dict = {}  # realm_id -> Last-Modified str
```

- [ ] **Step 3: Verify it compiles**

Run: `python -m py_compile bot.py`
Expected: no errors. (Requires `BLIZZ_CLIENT_ID`/`SECRET` in `.env` at runtime — already present.)

- [ ] **Step 4: Commit**

```bash
rtk git add bot.py
rtk git commit -m "feat: wire BlizzardClient + Snipelist into the bot client and config"
```

---

### Task 8: `bot.py` — snipe commands

**Files:**
- Modify: `bot.py` (`on_message`, alongside the `!watch` handlers)

**Interfaces:**
- Consumes: `self.snipelist`, `self.blizzard`, `snipelist_mod.parse_gold`, `BANKER_ID`.
- Produces: `!snipe`, `!snipepet`, `!unsnipe`, `!snipes` message handlers.

- [ ] **Step 1: Add the command handlers**

In `on_message`, after the `!watches` block, add (uses an `aiohttp` session for the name/recipe lookup):

```python
        if message.content.startswith("!snipe ") and not message.content.startswith("!snipepet "):
            args = message.content.split()[1:]
            if len(args) < 2 or not args[0].isdigit():
                await message.channel.send("Usage: `!snipe <itemId> <maxGold> [label]`")
                return
            item_id = int(args[0])
            target = snipelist_mod.parse_gold(args[1])
            if target is None:
                await message.channel.send("Max price must be a positive number of gold, e.g. `!snipe 194123 5000`.")
                return
            async with aiohttp.ClientSession() as session:
                try:
                    info = await self.blizzard.item_info(session, item_id)
                    label = " ".join(args[2:]) if len(args) > 2 else info.name or f"Item {item_id}"
                    is_recipe = info.is_recipe
                except Exception as exc:  # noqa: BLE001
                    logger.warning("snipe item_info failed for %s: %s", item_id, exc)
                    label = " ".join(args[2:]) if len(args) > 2 else f"Item {item_id}"
                    is_recipe = False
            self.snipelist.subscribe(message.author.id, "item", item_id, target, label, is_recipe)
            self.snipelist.save()
            note = " (recipe — the banker is also alerted)" if is_recipe else ""
            await message.channel.send(
                f"🎯 Sniping **{label}** (item {item_id}) under {snipelist_mod.format_gold(target)}{note}."
            )
            return

        if message.content.startswith("!snipepet "):
            args = message.content.split()[1:]
            if len(args) < 2 or not args[0].isdigit():
                await message.channel.send("Usage: `!snipepet <speciesId> <maxGold> [label]`")
                return
            species_id = int(args[0])
            target = snipelist_mod.parse_gold(args[1])
            if target is None:
                await message.channel.send("Max price must be a positive number of gold.")
                return
            async with aiohttp.ClientSession() as session:
                try:
                    name = await self.blizzard.pet_name(session, species_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("snipe pet_name failed for %s: %s", species_id, exc)
                    name = f"Pet {species_id}"
            label = " ".join(args[2:]) if len(args) > 2 else name
            self.snipelist.subscribe(message.author.id, "pet", species_id, target, label, False)
            self.snipelist.save()
            await message.channel.send(
                f"🎯 Sniping pet **{label}** (species {species_id}) under {snipelist_mod.format_gold(target)}."
            )
            return

        if message.content.startswith("!unsnipe "):
            args = message.content.split()[1:]
            if not args or not all(a.isdigit() for a in args):
                await message.channel.send("Usage: `!unsnipe <id ...>`")
                return
            removed = []
            for a in args:
                iid = int(a)
                # a bare id may be an item or a pet the caller subscribes to; try both
                if self.snipelist.unsubscribe(message.author.id, "item", iid):
                    removed.append(iid)
                elif self.snipelist.unsubscribe(message.author.id, "pet", iid):
                    removed.append(iid)
            if removed:
                self.snipelist.save()
                await message.channel.send(f"🚫 Stopped sniping: {', '.join(str(i) for i in removed)}.")
            else:
                await message.channel.send("You weren't sniping any of those.")
            return

        if message.content == "!snipes":
            snipes = self.snipelist.for_owner(message.author.id)
            if not snipes:
                await message.channel.send("You aren't sniping anything. Add one with `!snipe <itemId> <maxGold>`.")
                return
            lines = [snipelist_mod.format_snipe_line(s, s.subscribers[message.author.id]) for s in snipes]
            await message.author.send("**Your snipes:**\n" + "\n".join(lines))
            return
```

- [ ] **Step 2: Verify it compiles**

Run: `python -m py_compile bot.py`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
rtk git add bot.py
rtk git commit -m "feat: !snipe / !snipepet / !unsnipe / !snipes commands"
```

---

### Task 9: `bot.py` — the 30-minute sweep task

**Files:**
- Modify: `bot.py` (new task method; start it in `setup_hook`)

**Interfaces:**
- Consumes: `self.blizzard`, `self.snipelist`, `snipelist_mod.best_price_for`/`cheapest`/`plan_alerts`/`format_alert`/`format_banker_alert`, `BANKER_ID`, `SNIPE_SWEEP_MINUTES`.

- [ ] **Step 1: Add the sweep task**

Add this method to `MyClient` (near `price_watch_check`):

```python
    @tasks.loop(minutes=SNIPE_SWEEP_MINUTES)
    async def auction_snipe_check(self):
        """Sweep every region realm; DM subscribers when a snipe's cheapest price beats target."""
        if not self.is_ready():
            logger.warning("auction_snipe_check: not ready, skipping")
            return
        keys = self.snipelist.watched_keys()
        if not keys:
            return

        async with aiohttp.ClientSession() as session:
            try:
                if not self._snipe_realm_ids:
                    self._snipe_realm_ids = await self.blizzard.list_connected_realms(session)
            except Exception as exc:  # noqa: BLE001
                logger.warning("auction_snipe_check: realm list failed: %s", exc)
                return

            # Refresh each realm's watched-item prices (conditional; 304 -> reuse cache).
            for realm_id in self._snipe_realm_ids:
                try:
                    result = await self.blizzard.get_realm_auctions(
                        session, realm_id, self._snipe_realm_modified.get(realm_id)
                    )
                    if result is blizzard.NOT_MODIFIED:
                        continue
                    auctions, last_modified = result
                    prices = {}
                    for kind, key_id in keys:
                        bp = snipelist_mod.best_price_for(auctions, kind, key_id)
                        if bp is not None:
                            prices[(kind, key_id)] = bp
                    self._snipe_price_cache[realm_id] = prices
                    self._snipe_realm_modified[realm_id] = last_modified
                except Exception as exc:  # noqa: BLE001 - one bad realm must not kill the sweep
                    logger.warning("auction_snipe_check: realm %s failed: %s", realm_id, exc)

            # Aggregate cheapest-anywhere per key and decide alerts.
            for snipe in self.snipelist.all():
                try:
                    key = (snipe.kind, snipe.key_id)
                    realm_prices = {
                        rid: prices[key] for rid, prices in self._snipe_price_cache.items() if key in prices
                    }
                    best = snipelist_mod.cheapest(realm_prices)
                    plans = snipelist_mod.plan_alerts(snipe, best, BANKER_ID)
                    if not plans or best is None:
                        continue
                    realm = await self.blizzard.realm_name(session, best[2])
                    for plan in plans:
                        await self._send_snipe_dm(session, snipe, best, realm, plan)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("auction_snipe_check: snipe %s failed: %s", snipe.key_id, exc)

        self.snipelist.save()

    async def _send_snipe_dm(self, session, snipe, best, realm, plan):
        try:
            user = await self.fetch_user(plan.recipient_id)
            if plan.is_banker:
                wanters = [(f"<@{uid}>", s.target_copper) for uid, s in snipe.subscribers.items()]
                await user.send(snipelist_mod.format_banker_alert(snipe, best, realm, wanters))
            else:
                await user.send(snipelist_mod.format_alert(snipe, best, realm, plan.target_copper))
        except discord.HTTPException:
            logger.warning("auction_snipe_check: could not DM %s", plan.recipient_id)
```

- [ ] **Step 2: Start the task in `setup_hook`**

In `setup_hook`, next to the other `if not self.<task>.is_running(): self.<task>.start()` blocks, add:

```python
        if not self.auction_snipe_check.is_running():
            self.auction_snipe_check.start()
```

- [ ] **Step 3: Verify it compiles + full suite passes**

Run: `python -m py_compile bot.py && python -m pytest -q`
Expected: no compile errors; all tests PASS.

- [ ] **Step 4: Commit**

```bash
rtk git add bot.py
rtk git commit -m "feat: 30-minute auction_snipe_check sweep task (conditional fetch + per-subscriber/banker DMs)"
```

---

### Task 10: Docs + version bump

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `CHANGELOG.md`, `bot.py` (`BOT_VERSION`)

- [ ] **Step 1: Update `CLAUDE.md`**

- Add to the File Map table: `blizzard.py` (Blizzard Game Data AH client), `snipelist.py` (auction-snipe state + detection), and note `snipes.json` in the State Persistence section (separate file, like `watches.json`).
- Add an **Auction Sniper** subsection under Key Workflows describing: 30-min all-realm sweep, absolute per-item target, one record per item with subscribers, recipe→banker CC, open to all members.
- Add the four commands to the Commands table and `BLIZZ_CLIENT_ID`/`BLIZZ_CLIENT_SECRET`/`BLIZZ_REGION` to Environment Variables.

- [ ] **Step 2: Update `README.md`**

- Add an "Auction Sniper" feature section: what it does, that it uses Blizzard's API (register a client at develop.battle.net for `BLIZZ_CLIENT_ID`/`SECRET`), region is `BLIZZ_REGION` (default `us`), all realms swept automatically, recipes CC the banker.
- Add the four commands to the command table and the three env vars to the env table.

- [ ] **Step 3: Update `CHANGELOG.md` + bump version**

- Add a new top entry under a new version (minor bump from the current `BOT_VERSION`) with an `### Improvements` bullet describing the auction sniper.
- Bump `BOT_VERSION` in `bot.py` by one minor version.

- [ ] **Step 4: Verify + commit**

Run: `python -m pytest -q && python -m ruff check snipelist.py blizzard.py bot.py`
Expected: all PASS; ruff clean.

```bash
rtk git add CLAUDE.md README.md CHANGELOG.md bot.py
rtk git commit -m "docs: document the Blizzard auction sniper + version bump"
```

---

## Self-Review

**Spec coverage:** one-record-per-item + subscribers (Tasks 2–3), per-subscriber independent targets/anti-spam (Tasks 1–3), recipe→banker CC with dedup (Tasks 3, 5, 9), `!unsnipe` removes only caller / last-out deletes (Task 2), open commands (Task 8), 30-min conditional sweep + cross-realm min (Task 9), Blizzard OAuth/fetches/name caches (Tasks 5–6), config (Task 7), Pi-friendly per-realm processing (Task 9 loop), docs (Task 10). All spec sections map to a task.

**Placeholder scan:** none — every code step has complete code; commands and expected outputs are exact.

**Type consistency:** `best` is `(price, qty, realm_id)` from `cheapest` and consumed as such by `plan_alerts`/formatters/sweep. `AlertPlan(recipient_id, is_banker, target_copper)` is produced in Task 3 and consumed in Task 9. `best_price_for(auctions, kind, key_id)` signature matches its Task 9 call. `ItemInfo(name, is_recipe)` consistent across Tasks 5/6/8. `NOT_MODIFIED` sentinel produced in Task 6, checked in Task 9.
