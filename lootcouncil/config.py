"""Configuration for the loot council tool: dataclass, JSON file, and env secrets."""

import json
import os
from dataclasses import dataclass, field
from typing import Dict

from lootcouncil.models import DPS, HEALER, TANK

CONFIG_FILE = "loot_config.json"
CACHE_FILE = "lootcouncil_cache.json"

# Component keys: parse, deaths, damage, utility, survivability. Rows sum to 1.0.
DEFAULT_ROLE_WEIGHTS: Dict[str, Dict[str, float]] = {
    TANK: {"parse": 0.15, "deaths": 0.25, "damage": 0.30, "utility": 0.10, "survivability": 0.20},
    HEALER: {"parse": 0.20, "deaths": 0.30, "damage": 0.25, "utility": 0.25, "survivability": 0.00},
    DPS: {"parse": 0.60, "deaths": 0.20, "damage": 0.20, "utility": 0.00, "survivability": 0.00},
}


class ConfigError(RuntimeError):
    """Raised when a required setting is missing."""


def _default_role_weights() -> Dict[str, Dict[str, float]]:
    # Deep-ish copy so a loaded config can never mutate the module-level defaults.
    return {role: dict(weights) for role, weights in DEFAULT_ROLE_WEIGHTS.items()}


@dataclass
class Config:
    guild_name: str = ""
    guild_server_slug: str = ""
    guild_region: str = "us"
    season_zone_id: int = 0
    difficulty: int = 5
    weight_upgrade: float = 0.6
    weight_performance: float = 0.4
    report_lookback_days: int = 60
    cache_ttl_hours: int = 24
    min_fights: int = 3
    role_weights: Dict[str, Dict[str, float]] = field(default_factory=_default_role_weights)

    # Secrets live in .env, never in loot_config.json. Names are case-sensitive.
    @property
    def wowaudit_key(self) -> str:
        return os.getenv("wow_audit_api_key", "")

    @property
    def wcl_client_id(self) -> str:
        return os.getenv("WARCRAFT_LOGS_API_CLIENT_ID", "")

    @property
    def wcl_client_secret(self) -> str:
        return os.getenv("WARCRAFT_LOGS_API_CLIENT_SECRET", "")

    def validate(self) -> None:
        missing = []
        if not self.guild_name:
            missing.append("guild_name")
        if not self.guild_server_slug:
            missing.append("guild_server_slug")
        if not self.season_zone_id:
            missing.append("season_zone_id")
        if missing:
            raise ConfigError(f"{CONFIG_FILE} is missing required setting(s): {', '.join(missing)}")

    @classmethod
    def load(cls, path: str = CONFIG_FILE) -> "Config":
        cfg = cls()
        if not os.path.exists(path):
            return cfg
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for key, value in data.items():
            if key == "role_weights":
                for role, weights in (value or {}).items():
                    cfg.role_weights.setdefault(role, {}).update(weights)
            elif hasattr(cfg, key):
                setattr(cfg, key, value)
        return cfg
