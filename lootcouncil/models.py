"""Shared value objects and key helpers for the loot council tool."""

from dataclasses import dataclass
from typing import Dict, Optional

TANK = "tank"
HEALER = "healer"
DPS = "dps"
ROLES = (TANK, HEALER, DPS)

# Warcraft Logs difficulty id -> the difficulty name WoWAudit uses in wishlists.
DIFFICULTY_NAMES: Dict[int, str] = {5: "Mythic", 4: "Heroic", 3: "Normal"}

_ROLE_ALIASES = {
    "tank": TANK,
    "healer": HEALER,
    "heal": HEALER,
    "heals": HEALER,
    "dps": DPS,
    "melee": DPS,
    "ranged": DPS,
}


def server_slug(realm: str) -> str:
    """Blizzard realm name -> Warcraft Logs server slug ("Mal'Ganis" -> "malganis")."""
    return realm.lower().replace("'", "").replace(" ", "")


def character_key(name: str, realm: str) -> str:
    """Stable join key between WoWAudit roster rows and Warcraft Logs data."""
    return f"{name.lower()}-{server_slug(realm)}"


def normalize_role(raw: Optional[str]) -> str:
    """Map a WoWAudit role string onto tank/healer/dps; anything unknown is dps."""
    return _ROLE_ALIASES.get((raw or "").strip().lower(), DPS)


@dataclass(frozen=True)
class Character:
    name: str
    realm: str
    role: str
    class_name: str
    rank: Optional[int] = None
    blizzard_id: Optional[int] = None

    @property
    def key(self) -> str:
        return character_key(self.name, self.realm)

    @property
    def realm_slug(self) -> str:
        """The realm in the form Warcraft Logs wants (its serverSlug)."""
        return server_slug(self.realm)


@dataclass(frozen=True)
class UpgradeInfo:
    percentage: float
    absolute: float
    spec: str


@dataclass
class RawMetrics:
    """Mutable accumulator filled during season aggregation (one per character)."""

    fights: int = 0
    parse_total: float = 0.0
    parse_count: int = 0
    deaths: int = 0
    early_deaths: int = 0
    interrupts: int = 0
    dispels: int = 0
    damage_taken: int = 0
    active_time_ms: int = 0
    tmi_total: float = 0.0
    tmi_count: int = 0

    @property
    def parse_avg(self) -> float:
        return self.parse_total / self.parse_count if self.parse_count else 0.0

    @property
    def tmi_avg(self) -> float:
        return self.tmi_total / self.tmi_count if self.tmi_count else 0.0


@dataclass(frozen=True)
class PerformanceScore:
    score: float
    components: Dict[str, float]
    fights: int
    low_confidence: bool
