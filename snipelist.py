"""Server-specific auction-snipe state, cross-realm detection, and formatting."""

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("discord")

PET_ITEM_ID = 82800  # AH "caged battle pet" item id; pets are keyed by species id


def parse_gold(text: str) -> Optional[int]:
    """Parse a whole/decimal gold amount into copper. None if invalid or non-positive."""
    try:
        gold = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(gold) or gold <= 0:
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
