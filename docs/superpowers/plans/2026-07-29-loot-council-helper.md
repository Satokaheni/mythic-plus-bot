# Loot Council Helper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone CLI (`python loot.py <itemId>`) that ranks who should receive a raid drop, blending WoWAudit droptimizer upgrade size with role-aware Warcraft Logs performance.

**Architecture:** A `lootcouncil/` package with one responsibility per module: two thin sync HTTP clients (WoWAudit, Warcraft Logs) whose response parsing lives in free `_parse_*` functions, and pure scoring/ranking logic that never touches the network. `loot.py` is a thin argparse shell over `LootRanker`. Expensive Warcraft Logs aggregation is cached to disk so a normal loot lookup makes zero API calls.

**Tech Stack:** Python 3.9+, `requests` (sync — this is a CLI, not the async bot), `pytest`, `pytest-mock`. Warcraft Logs v2 GraphQL + OAuth client-credentials; WoWAudit v1 REST.

**Source spec:** [`docs/superpowers/specs/2026-07-28-loot-council-helper-design.md`](../specs/2026-07-28-loot-council-helper-design.md)

## Global Constraints

- **Python floor is 3.9** (`pyproject.toml: requires-python = ">=3.9"`). Use `from typing import Dict, List, Optional, Tuple` — **never** builtin generics (`dict[str, int]`) or PEP 604 unions (`str | None`) in runtime annotations. This matches `blizzard.py`.
- **Line length 120** for both black and ruff. Ruff rules: `E`, `F`, `W`, `I` — `I` means **import order is enforced** (stdlib → third-party → first-party).
- **All file reads/writes pass `encoding="utf-8"` explicitly.** Windows defaults to cp1252 and corrupts em-dashes; this is an established repo convention.
- **Logger name is `"lootcouncil"`** (`logging.getLogger("lootcouncil")`). The bot modules use `"discord"`; this tool is standalone and must not hijack that logger.
- **Env var names are exact and case-sensitive**, copied verbatim from the spec: `wow_audit_api_key` (lowercase), `WARCRAFT_LOGS_API_CLIENT_ID`, `WARCRAFT_LOGS_API_CLIENT_SECRET`.
- **Client style follows `blizzard.py`:** response parsing lives in module-level `_parse_*(data) -> ...` free functions so it is unit-testable with captured JSON and no network. Classes hold only tokens, caches, and HTTP calls.
- **Value objects are `@dataclass(frozen=True)`** unless they are accumulators that must be mutated during aggregation.
- **Read-only tool.** No writes to WoWAudit, Warcraft Logs, or Discord. The only file this tool writes is its own cache.
- **Ranked within role only.** Performance scores are never compared across roles — cohort normalization and every output table are per-role.
- **Do not vendor, port, or translate WoWAnalyzer or Wipefest code.** Both are AGPL-3.0; copying code would force this tool to AGPL. Referencing ability IDs as data is fine. (v2 concern, but never violate it.)
- **Commit messages** use conventional-commit prefixes (`feat:`, `fix:`, `docs:`, `test:`) and end with:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  ```

## Deliberate deviations from the spec

Both are noted here so a reviewer does not flag them as drift:

1. **`lootcouncil/models.py` is added** (not in the spec's file list). The spec's key helper and shared dataclasses are needed by `wowaudit.py`, `performance.py`, and `ranker.py` alike; a shared leaf module is what keeps the import graph acyclic.
2. **Tests are split across four files** instead of the spec's single `tests/test_lootcouncil.py`. The repo convention is one test file per module (`test_raider.py`, `test_schedule.py`, `test_utils.py`), and one file for this whole package would be unwieldy.

## Known data risk — read before Task 2

The design spike confirmed the WoWAudit **wishlist path** (`characters[].instances[].difficulties[].wishlist.encounters[].items[]`) but **could not confirm the leaf item fields**, because wishlists were empty at design time (end of season — nobody re-simming). `_parse_wishlists` is therefore written deliberately tolerant of two shapes, and Task 2 ends with an explicit re-verification step to run once real droptimizers exist. Do not treat upgrade *values* as trusted until that step passes.

## File Structure

| File | Responsibility |
|------|----------------|
| `lootcouncil/__init__.py` | Public exports: `Config`, `LootRanker`, `PerformanceAnalyzer` |
| `lootcouncil/models.py` | Shared value objects + `character_key` / `server_slug` / `normalize_role` |
| `lootcouncil/config.py` | `Config` dataclass, JSON+env loading, default role weights |
| `lootcouncil/wowaudit.py` | `WowAuditClient` + `_parse_roster` / `_parse_wishlists` |
| `lootcouncil/warcraftlogs.py` | `WarcraftLogsClient` (OAuth + GraphQL) + `_parse_*` |
| `lootcouncil/performance.py` | Pure role-aware scoring + `PerformanceAnalyzer` aggregation/cache |
| `lootcouncil/ranker.py` | `LootRanker` + pure `normalize_upgrades` / `weighted_score` |
| `loot.py` | Thin argparse CLI + table rendering |
| `tests/test_lootcouncil_core.py` | models + config |
| `tests/test_lootcouncil_clients.py` | WoWAudit + WCL parsers |
| `tests/test_lootcouncil_performance.py` | scoring, death-order, cache freshness |
| `tests/test_lootcouncil_ranker.py` | normalization, blend, CLI formatting |

Dependency direction (no cycles): `models` ← `config` ← {`wowaudit`, `warcraftlogs`} ← `performance` ← `ranker` ← `loot.py`.

---

### Task 1: Package scaffold, shared models, and config

**Files:**
- Create: `lootcouncil/__init__.py`, `lootcouncil/models.py`, `lootcouncil/config.py`, `loot_config.example.json`
- Modify: `pyproject.toml`, `.gitignore`
- Test: `tests/test_lootcouncil_core.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `TANK`/`HEALER`/`DPS` constants; `server_slug(realm: str) -> str`; `character_key(name: str, realm: str) -> str`; `normalize_role(raw: Optional[str]) -> str`; `DIFFICULTY_NAMES: Dict[int, str]`; dataclasses `Character`, `UpgradeInfo`, `RawMetrics`, `PerformanceScore`; `Config` with `.load(path) -> Config`, `.validate() -> None`, and properties `wowaudit_key` / `wcl_client_id` / `wcl_client_secret`; `ConfigError`; constants `CONFIG_FILE`, `CACHE_FILE`, `DEFAULT_ROLE_WEIGHTS`.

- [ ] **Step 1: Branch (done by the controller before dispatch)**

The branch `feature/loot-council-helper` already exists — verify you are on it and do not create or switch branches.

```bash
git rev-parse --abbrev-ref HEAD   # expect: feature/loot-council-helper
```

> **Why not `main`:** `main` is stranded at January 2026 with only 6 files and no
> `pyproject.toml`, no `tests/`, and no `conftest.py`. `feature/availability-forecasting`
> is the de facto trunk, so this branch stacks on it.

- [ ] **Step 2: Write the failing tests for models and config**

Create `tests/test_lootcouncil_core.py`:

```python
"""Tests for lootcouncil.models and lootcouncil.config — pure helpers, no network."""

import json

import pytest

from lootcouncil.config import DEFAULT_ROLE_WEIGHTS, Config, ConfigError
from lootcouncil.models import (
    DPS,
    HEALER,
    TANK,
    Character,
    RawMetrics,
    character_key,
    normalize_role,
    server_slug,
)


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def test_server_slug_strips_apostrophes_and_spaces():
    assert server_slug("Mal'Ganis") == "malganis"
    assert server_slug("Aerie Peak") == "aeriepeak"
    assert server_slug("Illidan") == "illidan"


def test_character_key_is_lowercase_and_joins_name_and_realm():
    assert character_key("Thrall", "Mal'Ganis") == "thrall-malganis"


def test_character_key_property_matches_helper():
    char = Character(name="Thrall", realm="Mal'Ganis", role=TANK, class_name="Shaman")
    assert char.key == character_key("Thrall", "Mal'Ganis")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Tank", TANK),
        ("tank", TANK),
        ("Healer", HEALER),
        ("Heal", HEALER),
        ("DPS", DPS),
        ("Melee", DPS),
        ("Ranged", DPS),
        ("", DPS),
        (None, DPS),
        ("something-unknown", DPS),
    ],
)
def test_normalize_role(raw, expected):
    assert normalize_role(raw) == expected


def test_raw_metrics_averages_guard_against_zero_division():
    empty = RawMetrics()
    assert empty.parse_avg == 0.0
    assert empty.tmi_avg == 0.0

    filled = RawMetrics(parse_total=180.0, parse_count=2, tmi_total=10.0, tmi_count=4)
    assert filled.parse_avg == 90.0
    assert filled.tmi_avg == 2.5


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_config_defaults_match_spec():
    cfg = Config()
    assert cfg.difficulty == 5
    assert cfg.weight_upgrade == 0.6
    assert cfg.weight_performance == 0.4
    assert cfg.report_lookback_days == 60
    assert cfg.cache_ttl_hours == 24
    assert cfg.min_fights == 3
    assert cfg.guild_region == "us"


def test_default_role_weight_rows_sum_to_one():
    for role, weights in DEFAULT_ROLE_WEIGHTS.items():
        assert round(sum(weights.values()), 6) == 1.0, f"{role} weights must sum to 1.0"


def test_dps_leans_on_parse_and_tank_does_not():
    assert DEFAULT_ROLE_WEIGHTS[DPS]["parse"] == 0.60
    assert DEFAULT_ROLE_WEIGHTS[TANK]["parse"] == 0.15
    assert DEFAULT_ROLE_WEIGHTS[HEALER]["parse"] == 0.20


def test_load_returns_defaults_when_file_absent(tmp_path):
    cfg = Config.load(str(tmp_path / "nope.json"))
    assert cfg.difficulty == 5
    assert cfg.guild_name == ""


def test_load_overrides_from_json(tmp_path):
    path = tmp_path / "loot_config.json"
    path.write_text(
        json.dumps({"guild_name": "Wipefest", "season_zone_id": 42, "weight_upgrade": 0.8}),
        encoding="utf-8",
    )
    cfg = Config.load(str(path))
    assert cfg.guild_name == "Wipefest"
    assert cfg.season_zone_id == 42
    assert cfg.weight_upgrade == 0.8
    assert cfg.weight_performance == 0.4  # untouched key keeps its default


def test_load_merges_role_weights_partially(tmp_path):
    path = tmp_path / "loot_config.json"
    path.write_text(json.dumps({"role_weights": {"dps": {"parse": 0.5}}}), encoding="utf-8")
    cfg = Config.load(str(path))
    assert cfg.role_weights[DPS]["parse"] == 0.5
    assert cfg.role_weights[DPS]["deaths"] == 0.20  # sibling key preserved
    assert cfg.role_weights[TANK]["parse"] == 0.15  # other roles untouched


def test_load_ignores_unknown_keys(tmp_path):
    path = tmp_path / "loot_config.json"
    path.write_text(json.dumps({"not_a_setting": 1, "difficulty": 4}), encoding="utf-8")
    cfg = Config.load(str(path))
    assert cfg.difficulty == 4
    assert not hasattr(cfg, "not_a_setting")


def test_load_does_not_mutate_module_level_defaults(tmp_path):
    path = tmp_path / "loot_config.json"
    path.write_text(json.dumps({"role_weights": {"dps": {"parse": 0.99}}}), encoding="utf-8")
    Config.load(str(path))
    assert DEFAULT_ROLE_WEIGHTS[DPS]["parse"] == 0.60


def test_validate_lists_every_missing_required_setting():
    with pytest.raises(ConfigError) as exc:
        Config().validate()
    message = str(exc.value)
    assert "guild_name" in message
    assert "guild_server_slug" in message
    assert "season_zone_id" in message


def test_validate_passes_when_required_present():
    cfg = Config(guild_name="Wipefest", guild_server_slug="malganis", season_zone_id=42)
    cfg.validate()  # must not raise


def test_secret_properties_read_exact_env_var_names(monkeypatch):
    monkeypatch.setenv("wow_audit_api_key", "wa-key")
    monkeypatch.setenv("WARCRAFT_LOGS_API_CLIENT_ID", "cid")
    monkeypatch.setenv("WARCRAFT_LOGS_API_CLIENT_SECRET", "secret")
    cfg = Config()
    assert cfg.wowaudit_key == "wa-key"
    assert cfg.wcl_client_id == "cid"
    assert cfg.wcl_client_secret == "secret"


def test_secret_properties_default_to_empty(monkeypatch):
    monkeypatch.delenv("wow_audit_api_key", raising=False)
    assert Config().wowaudit_key == ""
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_lootcouncil_core.py -v`
Expected: FAIL — collection error, `ModuleNotFoundError: No module named 'lootcouncil'`

- [ ] **Step 4: Write `lootcouncil/models.py`**

```python
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
```

- [ ] **Step 5: Write `lootcouncil/config.py`**

```python
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
```

- [ ] **Step 6: Write `lootcouncil/__init__.py`**

Keep it import-light for now; `LootRanker` and `PerformanceAnalyzer` are added to `__all__` in the tasks that create them.

```python
"""Loot council helper — rank drop recipients by upgrade size and role-aware performance."""

from lootcouncil.config import Config, ConfigError

__all__ = ["Config", "ConfigError"]
```

- [ ] **Step 7: Write `loot_config.example.json`**

```json
{
  "guild_name": "Your Guild Name",
  "guild_server_slug": "malganis",
  "guild_region": "us",
  "season_zone_id": 0,
  "difficulty": 5,
  "weight_upgrade": 0.6,
  "weight_performance": 0.4,
  "report_lookback_days": 60,
  "cache_ttl_hours": 24,
  "min_fights": 3
}
```

- [ ] **Step 8: Register the package and ignore generated files**

In `pyproject.toml`, add `requests` to `dependencies` (currently `discord.py`, `python-dotenv`, `tzdata`):

```toml
dependencies = [
    "discord.py>=2.0.0",
    "python-dotenv>=1.0.0",
    # Bundled IANA timezone database — zoneinfo falls back to it when the OS tz data
    # lacks a key (e.g. the US/* aliases on minimal Raspberry Pi / container images).
    "tzdata>=2024.1",
    # Sync HTTP for the standalone loot council CLI (the bot itself uses aiohttp).
    "requests>=2.31.0",
]
```

In the same file, add `loot` to `py-modules` and declare the new package:

```toml
[tool.setuptools]
py-modules = ["bot", "raider", "schedule", "utils", "views", "undermine", "watchlist", "eventlog", "raiderio", "forecast", "loot"]
packages = ["lootcouncil"]
```

And teach ruff's isort that `lootcouncil` is first-party:

```toml
[tool.ruff.isort]
known-first-party = ["bot", "raider", "schedule", "views", "utils", "undermine", "watchlist", "lootcouncil"]
```

In `.gitignore`, under the `# State files` block, add the two generated files:

```
lootcouncil_cache.json
loot_config.json
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `python -m pytest tests/test_lootcouncil_core.py -v`
Expected: PASS — all tests green.

- [ ] **Step 10: Verify the existing suite still passes and linting is clean**

Run: `python -m pytest -q`
Expected: PASS — the pre-existing bot tests are unaffected.

Run: `python -m ruff check lootcouncil tests/test_lootcouncil_core.py`
Expected: no output (clean).

- [ ] **Step 11: Commit**

```bash
git add lootcouncil/ tests/test_lootcouncil_core.py loot_config.example.json pyproject.toml .gitignore
git commit -m "$(cat <<'EOF'
feat: scaffold lootcouncil package with shared models and config

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: WoWAudit client — roster and wishlist parsing

**Files:**
- Create: `lootcouncil/wowaudit.py`
- Test: `tests/test_lootcouncil_clients.py`

**Interfaces:**
- Consumes: `Character`, `UpgradeInfo`, `normalize_role`, `character_key`, `DIFFICULTY_NAMES` from `lootcouncil.models`; `Config` from `lootcouncil.config`.
- Produces: `WowAuditClient(cfg)` with `.roster() -> List[Character]`, `.wishlists() -> Dict[int, Dict[str, Dict[str, UpgradeInfo]]]`, `.upgrades_for(item_id: int, difficulty: int) -> Dict[str, UpgradeInfo]`, `.current_season() -> dict`; free functions `_parse_roster(data) -> List[Character]` and `_parse_wishlists(data) -> Dict[int, Dict[str, Dict[str, UpgradeInfo]]]`; `WowAuditError`.

The wishlist map is keyed `{item_id: {difficulty_name: {character_key: UpgradeInfo}}}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lootcouncil_clients.py`:

```python
"""Tests for the WoWAudit and Warcraft Logs response parsers — captured shapes, no network."""

import pytest

from lootcouncil.models import DPS, HEALER, TANK
from lootcouncil.wowaudit import _parse_roster, _parse_wishlists


# ---------------------------------------------------------------------------
# WoWAudit — roster
# ---------------------------------------------------------------------------

ROSTER_PAYLOAD = {
    "characters": [
        {"id": 1, "name": "Thrall", "realm": "Mal'Ganis", "class": "Shaman", "role": "Healer", "rank": 0, "blizzard_id": 111},
        {"id": 2, "name": "Grom", "realm": "Aerie Peak", "class": "Warrior", "role": "Tank", "rank": 1, "blizzard_id": 222},
        {"id": 3, "name": "Sylvanas", "realm": "Illidan", "class": "Hunter", "role": "Ranged", "rank": 2, "blizzard_id": 333},
    ]
}


def test_parse_roster_maps_fields_and_normalizes_roles():
    chars = _parse_roster(ROSTER_PAYLOAD)
    assert len(chars) == 3
    by_name = {c.name: c for c in chars}
    assert by_name["Thrall"].role == HEALER
    assert by_name["Grom"].role == TANK
    assert by_name["Sylvanas"].role == DPS  # "Ranged" collapses to dps
    assert by_name["Thrall"].key == "thrall-malganis"
    assert by_name["Grom"].key == "grom-aeriepeak"
    assert by_name["Thrall"].class_name == "Shaman"
    assert by_name["Grom"].blizzard_id == 222


def test_parse_roster_accepts_a_bare_list():
    chars = _parse_roster(ROSTER_PAYLOAD["characters"])
    assert len(chars) == 3


def test_parse_roster_skips_rows_missing_name_or_realm():
    chars = _parse_roster({"characters": [{"name": "NoRealm"}, {"realm": "NoName"}, {}]})
    assert chars == []


def test_parse_roster_handles_empty_payload():
    assert _parse_roster({}) == []
    assert _parse_roster({"characters": None}) == []


# ---------------------------------------------------------------------------
# WoWAudit — wishlists
# ---------------------------------------------------------------------------

# Path confirmed during the design spike:
# characters[].instances[].difficulties[].wishlist.encounters[].items[]
WISHLIST_PAYLOAD = {
    "characters": [
        {
            "name": "Thrall",
            "realm": "Mal'Ganis",
            "instances": [
                {
                    "name": "Sporefall",
                    "difficulties": [
                        {
                            "difficulty": "Mythic",
                            "wishlist": {
                                "encounters": [
                                    {
                                        "name": "Rotmire",
                                        "items": [
                                            {"id": 215147, "name": "Vibrant Shard", "percentage": 4.2, "absolute": 1800, "spec": "Restoration"},
                                            {"id": 215148, "name": "Dull Shard", "percentage": 0.3, "absolute": 90, "spec": "Restoration"},
                                        ],
                                    }
                                ]
                            },
                        },
                        {
                            "difficulty": "Heroic",
                            "wishlist": {
                                "encounters": [
                                    {"name": "Rotmire", "items": [{"id": 215147, "percentage": 1.1, "absolute": 400, "spec": "Restoration"}]}
                                ]
                            },
                        },
                    ],
                }
            ],
        },
        {
            "name": "Grom",
            "realm": "Aerie Peak",
            "instances": [
                {
                    "difficulties": [
                        {
                            "difficulty": "Mythic",
                            "wishlist": {
                                "encounters": [
                                    {
                                        "items": [
                                            # Alternate shape: per-spec list instead of flat fields.
                                            {"id": 215147, "specs": [
                                                {"spec": "Protection", "percentage": 2.0, "absolute": 700},
                                                {"spec": "Fury", "percentage": 6.5, "absolute": 2400},
                                            ]}
                                        ]
                                    }
                                ]
                            },
                        }
                    ]
                }
            ],
        },
    ]
}


def test_parse_wishlists_indexes_by_item_difficulty_and_character():
    wl = _parse_wishlists(WISHLIST_PAYLOAD)
    assert 215147 in wl
    mythic = wl[215147]["Mythic"]
    assert set(mythic) == {"thrall-malganis", "grom-aeriepeak"}
    assert mythic["thrall-malganis"].percentage == 4.2
    assert mythic["thrall-malganis"].absolute == 1800
    assert mythic["thrall-malganis"].spec == "Restoration"


def test_parse_wishlists_keeps_difficulties_separate():
    wl = _parse_wishlists(WISHLIST_PAYLOAD)
    assert wl[215147]["Heroic"]["thrall-malganis"].percentage == 1.1
    assert "grom-aeriepeak" not in wl[215147]["Heroic"]


def test_parse_wishlists_picks_best_spec_from_a_specs_list():
    wl = _parse_wishlists(WISHLIST_PAYLOAD)
    grom = wl[215147]["Mythic"]["grom-aeriepeak"]
    assert grom.percentage == 6.5  # the Fury line wins on percentage
    assert grom.spec == "Fury"


def test_parse_wishlists_returns_empty_for_the_end_of_season_case():
    # Wishlists were empty during the design spike; this must not raise.
    assert _parse_wishlists({"characters": []}) == {}
    assert _parse_wishlists({}) == {}
    assert _parse_wishlists({"characters": [{"name": "Thrall", "realm": "Illidan", "instances": []}]}) == {}


def test_parse_wishlists_skips_items_without_an_id():
    payload = {
        "characters": [
            {
                "name": "Thrall",
                "realm": "Illidan",
                "instances": [
                    {"difficulties": [{"difficulty": "Mythic", "wishlist": {"encounters": [{"items": [{"percentage": 5.0}]}]}}]}
                ],
            }
        ]
    }
    assert _parse_wishlists(payload) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_lootcouncil_clients.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lootcouncil.wowaudit'`

- [ ] **Step 3: Write `lootcouncil/wowaudit.py`**

```python
"""Sync client for the WoWAudit v1 API (roster + droptimizer wishlists)."""

import logging
from typing import Any, Dict, List, Optional

import requests

from lootcouncil.config import Config
from lootcouncil.models import DIFFICULTY_NAMES, Character, UpgradeInfo, character_key, normalize_role

logger = logging.getLogger("lootcouncil")

BASE_URL = "https://wowaudit.com/v1"
TIMEOUT = 30


class WowAuditError(RuntimeError):
    """Raised when the WoWAudit API returns a non-200 response."""


def _rows(data: Any, key: str) -> List[dict]:
    """WoWAudit sometimes wraps collections in an object and sometimes returns a bare list."""
    if isinstance(data, dict):
        rows = data.get(key)
    else:
        rows = data
    return rows or []


def _parse_roster(data: Any) -> List[Character]:
    out: List[Character] = []
    for row in _rows(data, "characters"):
        name = row.get("name")
        realm = row.get("realm")
        if not name or not realm:
            continue
        out.append(
            Character(
                name=name,
                realm=realm,
                role=normalize_role(row.get("role")),
                class_name=row.get("class", ""),
                rank=row.get("rank"),
                blizzard_id=row.get("blizzard_id"),
            )
        )
    return out


def _best_upgrade(item: dict) -> Optional[UpgradeInfo]:
    """Pull the upgrade figures off a wishlist item.

    Two shapes are tolerated because wishlists were empty during the design
    spike (end of season) — the *path* to items is confirmed, the leaf fields
    are not. Re-verify against a real payload once sims are uploaded.
    """
    specs = item.get("specs") or item.get("wishlist_specs")
    if isinstance(specs, list) and specs:
        best = max(specs, key=lambda s: float(s.get("percentage", 0) or 0))
        return UpgradeInfo(
            percentage=float(best.get("percentage", 0) or 0),
            absolute=float(best.get("absolute", 0) or 0),
            spec=best.get("spec", ""),
        )
    if "percentage" in item or "absolute" in item:
        return UpgradeInfo(
            percentage=float(item.get("percentage", 0) or 0),
            absolute=float(item.get("absolute", 0) or 0),
            spec=item.get("spec", ""),
        )
    return None


def _parse_wishlists(data: Any) -> Dict[int, Dict[str, Dict[str, UpgradeInfo]]]:
    """-> {item_id: {difficulty_name: {character_key: UpgradeInfo}}}"""
    out: Dict[int, Dict[str, Dict[str, UpgradeInfo]]] = {}
    for char in _rows(data, "characters"):
        name = char.get("name")
        realm = char.get("realm")
        if not name or not realm:
            continue
        key = character_key(name, realm)
        for instance in char.get("instances") or []:
            for diff in instance.get("difficulties") or []:
                diff_name = diff.get("difficulty") or diff.get("name") or ""
                wishlist = diff.get("wishlist") or {}
                for encounter in wishlist.get("encounters") or []:
                    for item in encounter.get("items") or []:
                        item_id = item.get("id") or item.get("item_id")
                        if not item_id:
                            continue
                        info = _best_upgrade(item)
                        if info is None:
                            continue
                        out.setdefault(int(item_id), {}).setdefault(diff_name, {})[key] = info
    return out


class WowAuditClient:
    """Thin sync wrapper over the WoWAudit v1 REST API."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._wishlists: Optional[Dict[int, Dict[str, Dict[str, UpgradeInfo]]]] = None

    def _get(self, path: str) -> Any:
        key = self._cfg.wowaudit_key
        if not key:
            raise WowAuditError("wow_audit_api_key is not set in the environment")
        resp = requests.get(f"{BASE_URL}{path}", headers={"Authorization": key}, timeout=TIMEOUT)
        if resp.status_code != 200:
            raise WowAuditError(f"WoWAudit GET {path} returned {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def roster(self) -> List[Character]:
        return _parse_roster(self._get("/characters"))

    def current_season(self) -> dict:
        return self._get("/period")

    def wishlists(self) -> Dict[int, Dict[str, Dict[str, UpgradeInfo]]]:
        if self._wishlists is None:
            self._wishlists = _parse_wishlists(self._get("/wishlists"))
        return self._wishlists

    def upgrades_for(self, item_id: int, difficulty: int) -> Dict[str, UpgradeInfo]:
        """-> {character_key: UpgradeInfo} for one item at one WCL difficulty id."""
        diff_name = DIFFICULTY_NAMES.get(difficulty, "")
        by_difficulty = self.wishlists().get(int(item_id), {})
        if diff_name in by_difficulty:
            return by_difficulty[diff_name]
        if not by_difficulty:
            logger.info("No wishlist entries for item %s (are droptimizers uploaded?)", item_id)
        return {}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_lootcouncil_clients.py -v`
Expected: PASS

- [ ] **Step 5: Lint**

Run: `python -m ruff check lootcouncil tests/test_lootcouncil_clients.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add lootcouncil/wowaudit.py tests/test_lootcouncil_clients.py
git commit -m "$(cat <<'EOF'
feat: add WoWAudit client with roster and wishlist parsing

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Record the deferred live-verification step**

Do not skip this — it is the one place this plan knowingly builds against an unconfirmed shape. Append to `docs/superpowers/specs/2026-07-28-loot-council-helper-design.md` under **Known data risk** (or open a tracking note) that once raiders upload droptimizers next season, someone must run:

```bash
python -c "from lootcouncil.config import Config; from lootcouncil.wowaudit import WowAuditClient; import json; print(json.dumps(WowAuditClient(Config.load()).wishlists(), default=str)[:2000])"
```

and confirm the real leaf fields match `_best_upgrade`'s two tolerated shapes. If they do not, `_best_upgrade` is the only function that needs changing.

---

### Task 3: Warcraft Logs client — OAuth, GraphQL, and parsers

**Files:**
- Create: `lootcouncil/warcraftlogs.py`
- Modify: `tests/test_lootcouncil_clients.py` (append a WCL section)

**Interfaces:**
- Consumes: `Config` from `lootcouncil.config`.
- Produces: `WarcraftLogsClient(cfg)` with `.ensure_token() -> str`, `.graphql(query: str, variables: Optional[dict]) -> dict`, `.character_parses(name, server_slug, region, zone_id) -> ParseData`, `.recent_report_codes(name, server_slug, region) -> List[ReportRef]`, `.report_fights(code, difficulty) -> List[Fight]`, `.report_table(code, fight_ids, data_type) -> List[dict]`; dataclasses `ParseData(average, per_encounter, metric)`, `ReportRef(code, zone_name, start_time)`, `Fight(id, name, encounter_id, difficulty, kill)`; free functions `_parse_zone_rankings`, `_parse_recent_reports`, `_parse_fights`, `_parse_table_entries`; `WarcraftLogsError`.

- [ ] **Step 1: Append the failing tests to `tests/test_lootcouncil_clients.py`**

First add this import to the **existing import block at the top of the file**, directly below the `lootcouncil.wowaudit` import (ruff's `E402` forbids mid-file imports):

```python
from lootcouncil.warcraftlogs import (
    _parse_fights,
    _parse_recent_reports,
    _parse_table_entries,
    _parse_zone_rankings,
)
```

Then append this section to the end of the file:

```python
# ---------------------------------------------------------------------------
# Warcraft Logs — parsers
# ---------------------------------------------------------------------------

ZONE_RANKINGS_PAYLOAD = {
    "data": {
        "characterData": {
            "character": {
                "zoneRankings": {
                    "bestPerformanceAverage": 88.5,
                    "medianPerformanceAverage": 74.25,
                    "rankings": [
                        {"encounter": {"id": 3159, "name": "Rotmire"}, "rankPercent": 91.0},
                        {"encounter": {"id": 3160, "name": "Second"}, "rankPercent": 61.0},
                        {"encounter": {"id": 3161, "name": "Third"}, "rankPercent": None},
                    ],
                    "metric": "dps",
                }
            }
        }
    }
}


def test_parse_zone_rankings_averages_only_real_percents():
    parsed = _parse_zone_rankings(ZONE_RANKINGS_PAYLOAD)
    assert parsed.metric == "dps"
    assert parsed.per_encounter == {3159: 91.0, 3160: 61.0}  # the None entry is dropped
    assert parsed.average == pytest.approx(76.0)


def test_parse_zone_rankings_handles_missing_character():
    parsed = _parse_zone_rankings({"data": {"characterData": {"character": None}}})
    assert parsed.per_encounter == {}
    assert parsed.average == 0.0
    assert parsed.metric == ""


def test_parse_recent_reports_extracts_codes():
    payload = {
        "data": {
            "characterData": {
                "character": {
                    "recentReports": {
                        "data": [
                            {"code": "abc123", "zone": {"name": "Sporefall"}, "startTime": 1700000000000},
                            {"code": "def456", "zone": None, "startTime": 1700100000000},
                        ]
                    }
                }
            }
        }
    }
    refs = _parse_recent_reports(payload)
    assert [r.code for r in refs] == ["abc123", "def456"]
    assert refs[0].zone_name == "Sporefall"
    assert refs[1].zone_name == ""
    assert refs[0].start_time == 1700000000000


def test_parse_fights_filters_to_the_requested_difficulty():
    payload = {
        "data": {
            "reportData": {
                "report": {
                    "fights": [
                        {"id": 1, "name": "Rotmire", "encounterID": 3159, "difficulty": 5, "kill": True},
                        {"id": 2, "name": "Rotmire", "encounterID": 3159, "difficulty": 4, "kill": False},
                        {"id": 3, "name": "Trash", "encounterID": 0, "difficulty": 5, "kill": False},
                    ]
                }
            }
        }
    }
    fights = _parse_fights(payload, difficulty=5)
    assert [f.id for f in fights] == [1]  # difficulty 4 and encounterID 0 both excluded


def test_parse_table_entries_reads_the_nested_entries_list():
    payload = {
        "data": {
            "reportData": {
                "report": {
                    "table": {
                        "data": {
                            "entries": [
                                {"name": "Thrall", "total": 100},
                                {"name": "Grom", "total": 200},
                            ]
                        }
                    }
                }
            }
        }
    }
    entries = _parse_table_entries(payload)
    assert [e["name"] for e in entries] == ["Thrall", "Grom"]


def test_parse_table_entries_accepts_a_bare_list_and_empty_shapes():
    bare = {"data": {"reportData": {"report": {"table": {"data": [{"name": "Thrall"}]}}}}}
    assert _parse_table_entries(bare) == [{"name": "Thrall"}]
    assert _parse_table_entries({}) == []
    assert _parse_table_entries({"data": {"reportData": {"report": {"table": {"data": {}}}}}}) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_lootcouncil_clients.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lootcouncil.warcraftlogs'`

- [ ] **Step 3: Write `lootcouncil/warcraftlogs.py`**

```python
"""Sync client for the Warcraft Logs v2 GraphQL API (OAuth client-credentials)."""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from lootcouncil.config import Config

logger = logging.getLogger("lootcouncil")

OAUTH_URL = "https://www.warcraftlogs.com/oauth/token"
API_URL = "https://www.warcraftlogs.com/api/v2/client"
TIMEOUT = 60

ZONE_RANKINGS_QUERY = """
query($name: String!, $server: String!, $region: String!, $zone: Int!) {
  characterData {
    character(name: $name, serverSlug: $server, serverRegion: $region) {
      zoneRankings(zoneID: $zone)
    }
  }
}
"""

RECENT_REPORTS_QUERY = """
query($name: String!, $server: String!, $region: String!) {
  characterData {
    character(name: $name, serverSlug: $server, serverRegion: $region) {
      recentReports(limit: 25) {
        data { code startTime zone { name } }
      }
    }
  }
}
"""

FIGHTS_QUERY = """
query($code: String!) {
  reportData {
    report(code: $code) {
      fights(killType: Encounters) { id name encounterID difficulty kill }
    }
  }
}
"""

TABLE_QUERY = """
query($code: String!, $fightIDs: [Int]!, $dataType: TableDataType!) {
  reportData {
    report(code: $code) {
      table(fightIDs: $fightIDs, dataType: $dataType)
    }
  }
}
"""


class WarcraftLogsError(RuntimeError):
    """Raised on OAuth failure or a GraphQL error response."""


@dataclass(frozen=True)
class ParseData:
    average: float = 0.0
    per_encounter: Dict[int, float] = field(default_factory=dict)
    metric: str = ""


@dataclass(frozen=True)
class ReportRef:
    code: str
    zone_name: str
    start_time: int


@dataclass(frozen=True)
class Fight:
    id: int
    name: str
    encounter_id: int
    difficulty: int
    kill: bool


def _dig(data: Any, *keys: str) -> Any:
    """Walk nested dicts, returning None the moment anything is missing or not a dict."""
    node = data
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _parse_zone_rankings(payload: Any) -> ParseData:
    rankings = _dig(payload, "data", "characterData", "character", "zoneRankings")
    if not isinstance(rankings, dict):
        return ParseData()
    per_encounter: Dict[int, float] = {}
    for row in rankings.get("rankings") or []:
        pct = row.get("rankPercent")
        enc_id = _dig(row, "encounter", "id")
        if pct is None or enc_id is None:
            continue
        per_encounter[int(enc_id)] = float(pct)
    average = sum(per_encounter.values()) / len(per_encounter) if per_encounter else 0.0
    return ParseData(average=average, per_encounter=per_encounter, metric=rankings.get("metric") or "")


def _parse_recent_reports(payload: Any) -> List[ReportRef]:
    rows = _dig(payload, "data", "characterData", "character", "recentReports", "data") or []
    out: List[ReportRef] = []
    for row in rows:
        code = row.get("code")
        if not code:
            continue
        out.append(
            ReportRef(
                code=code,
                zone_name=(_dig(row, "zone", "name") or ""),
                start_time=int(row.get("startTime") or 0),
            )
        )
    return out


def _parse_fights(payload: Any, difficulty: int) -> List[Fight]:
    rows = _dig(payload, "data", "reportData", "report", "fights") or []
    out: List[Fight] = []
    for row in rows:
        encounter_id = int(row.get("encounterID") or 0)
        if not encounter_id:  # trash / non-encounter
            continue
        if int(row.get("difficulty") or 0) != int(difficulty):
            continue
        out.append(
            Fight(
                id=int(row["id"]),
                name=row.get("name", ""),
                encounter_id=encounter_id,
                difficulty=int(row.get("difficulty") or 0),
                kill=bool(row.get("kill")),
            )
        )
    return out


def _parse_table_entries(payload: Any) -> List[dict]:
    table = _dig(payload, "data", "reportData", "report", "table", "data")
    if isinstance(table, list):
        return table
    if isinstance(table, dict):
        return table.get("entries") or []
    return []


class WarcraftLogsClient:
    """Holds the OAuth token and issues GraphQL queries.

    The 3,600 points/hour budget is only ever touched during season aggregation,
    which is cached — normal loot lookups make zero calls.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0  # monotonic seconds

    def ensure_token(self) -> str:
        if self._token and time.monotonic() < self._token_expiry:
            return self._token
        cid, secret = self._cfg.wcl_client_id, self._cfg.wcl_client_secret
        if not cid or not secret:
            raise WarcraftLogsError(
                "WARCRAFT_LOGS_API_CLIENT_ID / WARCRAFT_LOGS_API_CLIENT_SECRET are not set"
            )
        resp = requests.post(
            OAUTH_URL, data={"grant_type": "client_credentials"}, auth=(cid, secret), timeout=TIMEOUT
        )
        if resp.status_code != 200:
            raise WarcraftLogsError(f"WCL OAuth failed ({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.monotonic() + max(0, int(data.get("expires_in", 0)) - 300)
        return self._token

    def graphql(self, query: str, variables: Optional[dict] = None) -> dict:
        # Retry once on 401: a cached token can be revoked before its assumed expiry.
        for attempt in range(2):
            token = self.ensure_token()
            resp = requests.post(
                API_URL,
                json={"query": query, "variables": variables or {}},
                headers={"Authorization": f"Bearer {token}"},
                timeout=TIMEOUT,
            )
            if resp.status_code == 401 and attempt == 0:
                self._token = None
                self._token_expiry = 0.0
                continue
            if resp.status_code != 200:
                raise WarcraftLogsError(f"WCL query failed ({resp.status_code}): {resp.text[:200]}")
            payload = resp.json()
            if payload.get("errors"):
                raise WarcraftLogsError(f"WCL GraphQL errors: {payload['errors']}")
            return payload
        raise WarcraftLogsError("WCL query failed after re-authenticating")

    def character_parses(self, name: str, server_slug: str, region: str, zone_id: int) -> ParseData:
        payload = self.graphql(
            ZONE_RANKINGS_QUERY,
            {"name": name, "server": server_slug, "region": region, "zone": int(zone_id)},
        )
        return _parse_zone_rankings(payload)

    def recent_report_codes(self, name: str, server_slug: str, region: str) -> List[ReportRef]:
        payload = self.graphql(
            RECENT_REPORTS_QUERY, {"name": name, "server": server_slug, "region": region}
        )
        return _parse_recent_reports(payload)

    def report_fights(self, code: str, difficulty: int) -> List[Fight]:
        return _parse_fights(self.graphql(FIGHTS_QUERY, {"code": code}), difficulty)

    def report_table(self, code: str, fight_ids: List[int], data_type: str) -> List[dict]:
        """data_type is a WCL TableDataType: Deaths, DamageTaken, Interrupts, Dispels."""
        payload = self.graphql(
            TABLE_QUERY, {"code": code, "fightIDs": list(fight_ids), "dataType": data_type}
        )
        return _parse_table_entries(payload)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_lootcouncil_clients.py -v`
Expected: PASS

- [ ] **Step 5: Lint**

Run: `python -m ruff check lootcouncil tests/test_lootcouncil_clients.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add lootcouncil/warcraftlogs.py tests/test_lootcouncil_clients.py
git commit -m "$(cat <<'EOF'
feat: add Warcraft Logs v2 GraphQL client with OAuth and table parsers

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Pure role-aware performance scoring

**Files:**
- Create: `lootcouncil/performance.py` (scoring half only — aggregation lands in Task 5)
- Test: `tests/test_lootcouncil_performance.py`

**Interfaces:**
- Consumes: `RawMetrics`, `PerformanceScore`, `TANK`/`HEALER`/`DPS` from `lootcouncil.models`; `Config` from `lootcouncil.config`.
- Produces: `early_death_names(entries, limit=2) -> List[str]`; `rank_normalize(value, cohort, invert) -> float`; `component_values(raw) -> Dict[str, float]`; `score_cohort(raws: Dict[str, RawMetrics], roles: Dict[str, str], cfg: Config) -> Dict[str, PerformanceScore]`.

**Scoring contract** (this is the whole design, stated once so later tasks can rely on it):

Per character, five raw component values are derived, then normalized **within their role cohort** to 0–1 where higher is always better:

| Component | Raw value | Normalization |
|---|---|---|
| `parse` | `raw.parse_avg` (0–100) | `/100`, clamped — no cohort needed, already spec-normalized by WCL |
| `deaths` | `(deaths + early_deaths) / fights` | cohort, **inverted** |
| `damage` | `damage_taken / active_time_ms` | cohort, **inverted** (tanks *should* take damage, so role-relative) |
| `utility` | `(interrupts + dispels) / fights` | cohort, direct |
| `survivability` | `raw.tmi_avg` | cohort, **inverted** |

`score = Σ role_weights[role][component] × component_value`. Components with weight `0.0` contribute nothing. A cohort where every member is equal normalizes to `1.0` for all (nobody is penalized for a tie). `low_confidence` is `fights < cfg.min_fights`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lootcouncil_performance.py`:

```python
"""Tests for lootcouncil.performance — pure scoring, death order, cache freshness."""

import pytest

from lootcouncil.config import Config
from lootcouncil.models import DPS, HEALER, TANK, RawMetrics
from lootcouncil.performance import (
    component_values,
    early_death_names,
    rank_normalize,
    score_cohort,
)


# ---------------------------------------------------------------------------
# death order
# ---------------------------------------------------------------------------


def test_early_death_names_returns_the_first_two_by_timestamp():
    entries = [
        {"name": "Grom", "timestamp": 5000},
        {"name": "Thrall", "timestamp": 1000},
        {"name": "Sylvanas", "timestamp": 3000},
    ]
    assert early_death_names(entries) == ["Thrall", "Sylvanas"]


def test_early_death_names_handles_fewer_than_the_limit():
    assert early_death_names([{"name": "Thrall", "timestamp": 1}]) == ["Thrall"]
    assert early_death_names([]) == []


def test_early_death_names_respects_a_custom_limit():
    entries = [{"name": f"P{i}", "timestamp": i} for i in range(5)]
    assert early_death_names(entries, limit=3) == ["P0", "P1", "P2"]


def test_early_death_names_skips_entries_without_a_name():
    entries = [{"timestamp": 1}, {"name": "Thrall", "timestamp": 2}]
    assert early_death_names(entries) == ["Thrall"]


def test_early_death_names_treats_missing_timestamp_as_zero():
    entries = [{"name": "NoTs"}, {"name": "Late", "timestamp": 900}]
    assert early_death_names(entries) == ["NoTs", "Late"]


# ---------------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------------


def test_rank_normalize_direct_maps_best_to_one():
    assert rank_normalize(10, [0, 5, 10], invert=False) == 1.0
    assert rank_normalize(0, [0, 5, 10], invert=False) == 0.0
    assert rank_normalize(5, [0, 5, 10], invert=False) == 0.5


def test_rank_normalize_inverted_maps_lowest_to_one():
    assert rank_normalize(0, [0, 5, 10], invert=True) == 1.0
    assert rank_normalize(10, [0, 5, 10], invert=True) == 0.0


def test_rank_normalize_returns_one_when_the_cohort_is_flat():
    # Everyone equal -> nobody penalized, in both directions.
    assert rank_normalize(4, [4, 4, 4], invert=True) == 1.0
    assert rank_normalize(4, [4, 4, 4], invert=False) == 1.0


def test_rank_normalize_handles_an_empty_cohort():
    assert rank_normalize(4, [], invert=True) == 1.0


# ---------------------------------------------------------------------------
# component extraction
# ---------------------------------------------------------------------------


def test_component_values_are_per_fight_rates():
    raw = RawMetrics(
        fights=4,
        parse_total=320.0,
        parse_count=4,
        deaths=2,
        early_deaths=1,
        interrupts=6,
        dispels=2,
        damage_taken=8000,
        active_time_ms=4000,
        tmi_total=40.0,
        tmi_count=4,
    )
    vals = component_values(raw)
    assert vals["parse"] == 80.0
    assert vals["deaths"] == pytest.approx(0.75)  # (2 + 1) / 4
    assert vals["utility"] == pytest.approx(2.0)  # (6 + 2) / 4
    assert vals["damage"] == pytest.approx(2.0)  # 8000 / 4000
    assert vals["survivability"] == 10.0


def test_component_values_guard_against_zero_fights_and_time():
    vals = component_values(RawMetrics())
    assert vals["deaths"] == 0.0
    assert vals["utility"] == 0.0
    assert vals["damage"] == 0.0


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def _dps_pair():
    """Two DPS identical except that 'good' parses higher and dies less."""
    good = RawMetrics(fights=10, parse_total=900.0, parse_count=10, deaths=1, early_deaths=0,
                      damage_taken=1000, active_time_ms=1000, tmi_total=10.0, tmi_count=10)
    bad = RawMetrics(fights=10, parse_total=300.0, parse_count=10, deaths=8, early_deaths=4,
                     damage_taken=3000, active_time_ms=1000, tmi_total=30.0, tmi_count=10)
    return good, bad


def test_score_cohort_ranks_the_better_dps_higher():
    good, bad = _dps_pair()
    scores = score_cohort({"good": good, "bad": bad}, {"good": DPS, "bad": DPS}, Config())
    assert scores["good"].score > scores["bad"].score


def test_score_is_bounded_zero_to_one():
    good, bad = _dps_pair()
    scores = score_cohort({"good": good, "bad": bad}, {"good": DPS, "bad": DPS}, Config())
    for s in scores.values():
        assert 0.0 <= s.score <= 1.0


def test_more_deaths_lowers_the_score_all_else_equal():
    base = dict(fights=10, parse_total=700.0, parse_count=10, damage_taken=1000,
                active_time_ms=1000, tmi_total=10.0, tmi_count=10)
    steady = RawMetrics(deaths=0, early_deaths=0, **base)
    dying = RawMetrics(deaths=9, early_deaths=5, **base)
    scores = score_cohort({"steady": steady, "dying": dying}, {"steady": DPS, "dying": DPS}, Config())
    assert scores["steady"].score > scores["dying"].score


def test_roles_are_normalized_independently():
    # The tank takes far more damage than the healer but must not be punished for it:
    # each is the only member of its own cohort, so both normalize to 1.0 on damage.
    tank = RawMetrics(fights=10, parse_total=500.0, parse_count=10, damage_taken=50000,
                      active_time_ms=1000, tmi_total=50.0, tmi_count=10)
    healer = RawMetrics(fights=10, parse_total=500.0, parse_count=10, damage_taken=100,
                        active_time_ms=1000, tmi_total=5.0, tmi_count=10)
    scores = score_cohort({"t": tank, "h": healer}, {"t": TANK, "h": HEALER}, Config())
    assert scores["t"].components["damage"] == 1.0
    assert scores["h"].components["damage"] == 1.0


def test_utility_is_ignored_for_dps_and_counted_for_healers():
    cfg = Config()
    base = dict(fights=10, parse_total=700.0, parse_count=10, damage_taken=1000,
                active_time_ms=1000, tmi_total=10.0, tmi_count=10)
    quiet = RawMetrics(interrupts=0, dispels=0, **base)
    busy = RawMetrics(interrupts=20, dispels=20, **base)

    dps_scores = score_cohort({"q": quiet, "b": busy}, {"q": DPS, "b": DPS}, cfg)
    assert dps_scores["q"].score == pytest.approx(dps_scores["b"].score)

    heal_scores = score_cohort({"q": quiet, "b": busy}, {"q": HEALER, "b": HEALER}, cfg)
    assert heal_scores["b"].score > heal_scores["q"].score


def test_low_confidence_flag_tracks_min_fights():
    cfg = Config(min_fights=3)
    thin = RawMetrics(fights=2, parse_total=180.0, parse_count=2)
    thick = RawMetrics(fights=3, parse_total=270.0, parse_count=3)
    scores = score_cohort({"thin": thin, "thick": thick}, {"thin": DPS, "thick": DPS}, cfg)
    assert scores["thin"].low_confidence is True
    assert scores["thick"].low_confidence is False
    assert scores["thin"].fights == 2


def test_components_are_reported_for_transparency():
    good, bad = _dps_pair()
    scores = score_cohort({"good": good, "bad": bad}, {"good": DPS, "bad": DPS}, Config())
    assert set(scores["good"].components) == {"parse", "deaths", "damage", "utility", "survivability"}


def test_unknown_role_falls_back_to_dps_weights():
    good, _ = _dps_pair()
    scores = score_cohort({"g": good}, {"g": "sponge"}, Config())
    assert 0.0 <= scores["g"].score <= 1.0


def test_score_cohort_on_empty_input():
    assert score_cohort({}, {}, Config()) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_lootcouncil_performance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lootcouncil.performance'`

- [ ] **Step 3: Write the scoring half of `lootcouncil/performance.py`**

```python
"""Role-aware performance scoring, plus season aggregation over Warcraft Logs.

The scoring functions in the top half are pure: they take already-collected
RawMetrics and never touch the network, so they are fully unit-testable.
"""

import logging
from typing import Dict, Iterable, List, Sequence

from lootcouncil.config import DEFAULT_ROLE_WEIGHTS, Config
from lootcouncil.models import DPS, PerformanceScore, RawMetrics

logger = logging.getLogger("lootcouncil")

# Components where a lower raw value means a better player.
INVERTED_COMPONENTS = ("deaths", "damage", "survivability")
COMPONENTS = ("parse", "deaths", "damage", "utility", "survivability")


def early_death_names(entries: Iterable[dict], limit: int = 2) -> List[str]:
    """Names of the first `limit` players to die in one fight, earliest first.

    This is the "one of the first two to die in a pull" signal: sort the WCL
    Deaths table by timestamp and take the head.
    """
    named = [e for e in entries if e.get("name")]
    named.sort(key=lambda e: e.get("timestamp") or 0)
    return [e["name"] for e in named[:limit]]


def rank_normalize(value: float, cohort: Sequence[float], invert: bool) -> float:
    """Min-max a value against its cohort onto 0..1 where 1.0 is always best.

    A flat or empty cohort returns 1.0 — if everyone is equal, nobody is penalized.
    """
    if not cohort:
        return 1.0
    lo, hi = min(cohort), max(cohort)
    if hi == lo:
        return 1.0
    scaled = (value - lo) / (hi - lo)
    return 1.0 - scaled if invert else scaled


def component_values(raw: RawMetrics) -> Dict[str, float]:
    """Raw per-fight component values, before cohort normalization."""
    fights = raw.fights or 0
    return {
        "parse": raw.parse_avg,
        "deaths": (raw.deaths + raw.early_deaths) / fights if fights else 0.0,
        "damage": raw.damage_taken / raw.active_time_ms if raw.active_time_ms else 0.0,
        "utility": (raw.interrupts + raw.dispels) / fights if fights else 0.0,
        "survivability": raw.tmi_avg,
    }


def score_cohort(
    raws: Dict[str, RawMetrics], roles: Dict[str, str], cfg: Config
) -> Dict[str, PerformanceScore]:
    """Score every character, normalizing each component within its own role cohort."""
    if not raws:
        return {}

    values = {key: component_values(raw) for key, raw in raws.items()}
    keys_by_role: Dict[str, List[str]] = {}
    for key in raws:
        keys_by_role.setdefault(roles.get(key, DPS), []).append(key)

    scores: Dict[str, PerformanceScore] = {}
    for role, keys in keys_by_role.items():
        weights = cfg.role_weights.get(role) or DEFAULT_ROLE_WEIGHTS[DPS]
        cohorts = {c: [values[k][c] for k in keys] for c in COMPONENTS}
        for key in keys:
            components: Dict[str, float] = {}
            for component in COMPONENTS:
                if component == "parse":
                    # Already a 0-100 spec-normalized percentile; no cohort needed.
                    components[component] = max(0.0, min(1.0, values[key][component] / 100.0))
                else:
                    components[component] = rank_normalize(
                        values[key][component],
                        cohorts[component],
                        invert=component in INVERTED_COMPONENTS,
                    )
            total = sum(weights.get(c, 0.0) * components[c] for c in COMPONENTS)
            scores[key] = PerformanceScore(
                score=max(0.0, min(1.0, total)),
                components=components,
                fights=raws[key].fights,
                low_confidence=raws[key].fights < cfg.min_fights,
            )
    return scores
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_lootcouncil_performance.py -v`
Expected: PASS

- [ ] **Step 5: Lint**

Run: `python -m ruff check lootcouncil tests/test_lootcouncil_performance.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add lootcouncil/performance.py tests/test_lootcouncil_performance.py
git commit -m "$(cat <<'EOF'
feat: add pure role-aware performance scoring

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Season aggregation and disk cache

**Files:**
- Modify: `lootcouncil/performance.py` (append the `PerformanceAnalyzer` class + cache helpers)
- Modify: `tests/test_lootcouncil_performance.py` (append a cache section)
- Modify: `lootcouncil/__init__.py`

**Interfaces:**
- Consumes: everything from Task 4; `WarcraftLogsClient`, `Fight`, `ReportRef` from `lootcouncil.warcraftlogs`; `Character`, `character_key`, `server_slug` from `lootcouncil.models`.
- Produces: `cache_is_fresh(cache: Optional[dict], cfg: Config, now_ts: float) -> bool`; `metrics_to_dict(raws) -> Dict[str, dict]`; `metrics_from_dict(data) -> Dict[str, RawMetrics]`; `PerformanceAnalyzer(cfg, wcl)` with `.aggregate_season(roster: List[Character], refresh: bool = False, cache_path: str = CACHE_FILE) -> Dict[str, RawMetrics]`, `.load_cache(path=CACHE_FILE) -> Optional[dict]`, `.save_cache(path, raws) -> None`. `wcl` may be `None` when only the cache/scoring helpers are exercised.

Cache file shape (`lootcouncil_cache.json`):

```json
{"fetched_at": 1753800000.0, "zone_id": 42, "difficulty": 5, "metrics": {"thrall-malganis": {"fights": 12, "...": 0}}}
```

A cache whose `zone_id` or `difficulty` differs from the current config is treated as stale regardless of age.

- [ ] **Step 1: Append the failing cache tests**

First extend the **existing import block at the top of the file** — add `import json` to the stdlib group, and add the four new names to the existing `lootcouncil.performance` import so it reads:

```python
from lootcouncil.performance import (
    PerformanceAnalyzer,
    cache_is_fresh,
    component_values,
    early_death_names,
    metrics_from_dict,
    metrics_to_dict,
    rank_normalize,
    score_cohort,
)
```

Then append this section to the end of `tests/test_lootcouncil_performance.py`:

```python
# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------

HOUR = 3600.0


def test_cache_is_fresh_within_the_ttl():
    cache = {"fetched_at": 1000.0, "zone_id": 42, "difficulty": 5}
    cfg = Config(season_zone_id=42, difficulty=5, cache_ttl_hours=24)
    assert cache_is_fresh(cache, cfg, now_ts=1000.0 + 23 * HOUR) is True


def test_cache_is_stale_past_the_ttl():
    cache = {"fetched_at": 1000.0, "zone_id": 42, "difficulty": 5}
    cfg = Config(season_zone_id=42, difficulty=5, cache_ttl_hours=24)
    assert cache_is_fresh(cache, cfg, now_ts=1000.0 + 25 * HOUR) is False


def test_cache_is_stale_when_the_tier_or_difficulty_changed():
    cfg = Config(season_zone_id=42, difficulty=5, cache_ttl_hours=24)
    assert cache_is_fresh({"fetched_at": 1000.0, "zone_id": 99, "difficulty": 5}, cfg, 1000.0) is False
    assert cache_is_fresh({"fetched_at": 1000.0, "zone_id": 42, "difficulty": 4}, cfg, 1000.0) is False


def test_cache_is_stale_when_empty_or_malformed():
    cfg = Config(season_zone_id=42, difficulty=5)
    assert cache_is_fresh(None, cfg, 1000.0) is False
    assert cache_is_fresh({}, cfg, 1000.0) is False


def test_metrics_roundtrip_through_dicts():
    raws = {"thrall-malganis": RawMetrics(fights=7, deaths=2, parse_total=500.0, parse_count=7)}
    restored = metrics_from_dict(metrics_to_dict(raws))
    assert restored["thrall-malganis"].fights == 7
    assert restored["thrall-malganis"].deaths == 2
    assert restored["thrall-malganis"].parse_avg == pytest.approx(500.0 / 7)


def test_metrics_from_dict_ignores_unknown_fields():
    restored = metrics_from_dict({"a": {"fights": 3, "bogus_field": 1}})
    assert restored["a"].fights == 3


def test_save_and_load_cache_roundtrip(tmp_path):
    cfg = Config(season_zone_id=42, difficulty=5)
    analyzer = PerformanceAnalyzer(cfg, wcl=None)
    path = str(tmp_path / "cache.json")
    analyzer.save_cache(path, {"thrall-malganis": RawMetrics(fights=4)})

    loaded = analyzer.load_cache(path)
    assert loaded["zone_id"] == 42
    assert loaded["difficulty"] == 5
    assert loaded["metrics"]["thrall-malganis"]["fights"] == 4

    on_disk = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    assert "fetched_at" in on_disk


def test_load_cache_returns_none_when_absent_or_corrupt(tmp_path):
    analyzer = PerformanceAnalyzer(Config(), wcl=None)
    assert analyzer.load_cache(str(tmp_path / "nope.json")) is None

    bad = tmp_path / "bad.json"
    bad.write_text("{ not json }", encoding="utf-8")
    assert analyzer.load_cache(str(bad)) is None


def test_aggregate_season_uses_a_fresh_cache_without_calling_wcl(tmp_path):
    cfg = Config(season_zone_id=42, difficulty=5, cache_ttl_hours=24)

    class ExplodingClient:
        def __getattr__(self, name):
            raise AssertionError(f"WCL must not be called on a cache hit (tried {name})")

    path = str(tmp_path / "cache.json")
    PerformanceAnalyzer(cfg, wcl=None).save_cache(path, {"thrall-malganis": RawMetrics(fights=9)})

    analyzer = PerformanceAnalyzer(cfg, wcl=ExplodingClient())
    raws = analyzer.aggregate_season(roster=[], cache_path=path)
    assert raws["thrall-malganis"].fights == 9
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_lootcouncil_performance.py -v`
Expected: FAIL — `ImportError: cannot import name 'PerformanceAnalyzer' from 'lootcouncil.performance'`

- [ ] **Step 3: Append the aggregation and cache code to `lootcouncil/performance.py`**

**Replace** the import block written in Task 4 with this fuller one (ruff enforces stdlib → third-party → first-party order):

```python
import json
import logging
import os
import time
from dataclasses import fields as dataclass_fields
from typing import Dict, Iterable, List, Optional, Sequence

from lootcouncil.config import CACHE_FILE, DEFAULT_ROLE_WEIGHTS, Config
from lootcouncil.models import DPS, Character, PerformanceScore, RawMetrics
```

Then append to the end of the file:

```python
def cache_is_fresh(cache: Optional[dict], cfg: Config, now_ts: float) -> bool:
    """A cache is fresh only if it is young AND describes the configured tier."""
    if not cache:
        return False
    if int(cache.get("zone_id", -1)) != int(cfg.season_zone_id):
        return False
    if int(cache.get("difficulty", -1)) != int(cfg.difficulty):
        return False
    age_hours = (now_ts - float(cache.get("fetched_at", 0.0))) / 3600.0
    return age_hours <= cfg.cache_ttl_hours


def metrics_to_dict(raws: Dict[str, RawMetrics]) -> Dict[str, dict]:
    names = [f.name for f in dataclass_fields(RawMetrics)]
    return {key: {n: getattr(raw, n) for n in names} for key, raw in raws.items()}


def metrics_from_dict(data: Dict[str, dict]) -> Dict[str, RawMetrics]:
    names = {f.name for f in dataclass_fields(RawMetrics)}
    out: Dict[str, RawMetrics] = {}
    for key, row in (data or {}).items():
        out[key] = RawMetrics(**{k: v for k, v in row.items() if k in names})
    return out


class PerformanceAnalyzer:
    """Walks the tier's reports once, accumulates per-character metrics, caches the result.

    `wcl` may be None when only the cache/scoring helpers are used.
    """

    def __init__(self, cfg: Config, wcl) -> None:
        self._cfg = cfg
        self._wcl = wcl

    def load_cache(self, path: str = CACHE_FILE) -> Optional[dict]:
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Ignoring unreadable cache %s: %s", path, exc)
            return None

    def save_cache(self, path: str, raws: Dict[str, RawMetrics]) -> None:
        payload = {
            "fetched_at": time.time(),
            "zone_id": self._cfg.season_zone_id,
            "difficulty": self._cfg.difficulty,
            "metrics": metrics_to_dict(raws),
        }
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, path)  # atomic, matching utils.save_state

    def aggregate_season(
        self, roster: List[Character], refresh: bool = False, cache_path: str = CACHE_FILE
    ) -> Dict[str, RawMetrics]:
        if not refresh:
            cache = self.load_cache(cache_path)
            if cache_is_fresh(cache, self._cfg, time.time()):
                logger.info("Using cached season metrics from %s", cache_path)
                return metrics_from_dict(cache.get("metrics", {}))

        raws = self._collect(roster)
        self.save_cache(cache_path, raws)
        return raws

    def _collect(self, roster: List[Character]) -> Dict[str, RawMetrics]:
        cfg = self._cfg
        raws: Dict[str, RawMetrics] = {c.key: RawMetrics() for c in roster}
        by_name = {c.name: c.key for c in roster}

        # 1. Parses come straight from zoneRankings, one query per character.
        for char in roster:
            try:
                parses = self._wcl.character_parses(
                    char.name, char.realm_slug, cfg.guild_region, cfg.season_zone_id
                )
            except Exception as exc:  # a single missing character must not kill the sweep
                logger.warning("No parse data for %s-%s: %s", char.name, char.realm, exc)
                continue
            if parses.per_encounter:
                raw = raws[char.key]
                raw.parse_total += parses.average
                raw.parse_count += 1

        # 2. Fight-level metrics come from the guild's reports.
        cutoff_ms = (time.time() - cfg.report_lookback_days * 86400) * 1000
        for code in self._report_codes(roster, cutoff_ms):
            try:
                fights = self._wcl.report_fights(code, cfg.difficulty)
            except Exception as exc:
                logger.warning("Skipping report %s: %s", code, exc)
                continue
            if not fights:
                continue
            self._accumulate_report(code, fights, raws, by_name)
        return raws

    def _report_codes(self, roster: List[Character], cutoff_ms: float) -> List[str]:
        """Union of the roster's recent reports — the guild's raid logs."""
        seen: Dict[str, None] = {}
        for char in roster:
            try:
                refs = self._wcl.recent_report_codes(
                    char.name, char.realm_slug, self._cfg.guild_region
                )
            except Exception as exc:
                logger.warning("No recent reports for %s: %s", char.name, exc)
                continue
            for ref in refs:
                if ref.start_time >= cutoff_ms:
                    seen.setdefault(ref.code, None)
        return list(seen)

    def _accumulate_report(self, code, fights, raws, by_name) -> None:
        fight_ids = [f.id for f in fights]

        # Deaths are queried per fight because death *order* is only meaningful
        # within a single pull.
        for fight in fights:
            deaths = self._table(code, [fight.id], "Deaths")
            early = set(early_death_names(deaths))
            for entry in deaths:
                key = by_name.get(entry.get("name"))
                if key is None:
                    continue
                raws[key].deaths += 1
                if entry["name"] in early:
                    raws[key].early_deaths += 1

        # Fight participation comes from DamageTaken, which lists every player
        # present in the fight.
        for entry in self._table(code, fight_ids, "DamageTaken"):
            key = by_name.get(entry.get("name"))
            if key is None:
                continue
            raw = raws[key]
            raw.fights += 1
            raw.damage_taken += int(entry.get("total") or 0)
            raw.active_time_ms += int(entry.get("activeTime") or 0)
            tmi = entry.get("tmi")
            if tmi is not None:
                raw.tmi_total += float(tmi)
                raw.tmi_count += 1

        for data_type, field_name in (("Interrupts", "interrupts"), ("Dispels", "dispels")):
            for entry in self._table(code, fight_ids, data_type):
                key = by_name.get(entry.get("name"))
                if key is None:
                    continue
                setattr(raws[key], field_name, getattr(raws[key], field_name) + int(entry.get("total") or 0))

    def _table(self, code: str, fight_ids: List[int], data_type: str) -> List[dict]:
        try:
            return self._wcl.report_table(code, fight_ids, data_type)
        except Exception as exc:
            logger.warning("%s table failed for %s: %s", data_type, code, exc)
            return []
```

- [ ] **Step 4: Export the analyzer from the package**

Update `lootcouncil/__init__.py`:

```python
"""Loot council helper — rank drop recipients by upgrade size and role-aware performance."""

from lootcouncil.config import Config, ConfigError
from lootcouncil.performance import PerformanceAnalyzer

__all__ = ["Config", "ConfigError", "PerformanceAnalyzer"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_lootcouncil_performance.py -v`
Expected: PASS

- [ ] **Step 6: Lint**

Run: `python -m ruff check lootcouncil tests/test_lootcouncil_performance.py`
Expected: no output. Fix any unused-import (`F401`) findings from the appended import block.

- [ ] **Step 7: Commit**

```bash
git add lootcouncil/performance.py lootcouncil/models.py lootcouncil/__init__.py tests/test_lootcouncil_performance.py
git commit -m "$(cat <<'EOF'
feat: aggregate season metrics from Warcraft Logs with a disk cache

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: LootRanker — the weighted blend

**Files:**
- Create: `lootcouncil/ranker.py`
- Modify: `lootcouncil/__init__.py`
- Test: `tests/test_lootcouncil_ranker.py`

**Interfaces:**
- Consumes: `WowAuditClient` / `UpgradeInfo`; `PerformanceAnalyzer` / `score_cohort`; `Config`; `Character`.
- Produces: `normalize_upgrades(pcts: Dict[str, float]) -> Dict[str, float]`; `weighted_score(upgrade_norm: float, performance: float, weight_upgrade: float, weight_performance: float) -> float`; dataclasses `RankedCandidate(key, name, role, spec, upgrade_pct, upgrade_norm, performance, components, loot_score, low_confidence)` and `LootResult(item_id, difficulty, weight_upgrade, weight_performance, by_role, performance_by_role)`; `LootRanker(cfg, wowaudit, analyzer)` with `.rank(item_id, difficulty=None, refresh=False) -> LootResult`.

`by_role` and `performance_by_role` are both `Dict[str, List[RankedCandidate]]`, each list sorted descending — by `loot_score` and by `performance` respectively.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lootcouncil_ranker.py`:

```python
"""Tests for lootcouncil.ranker — pure normalization and the weighted blend."""

import pytest

from lootcouncil.ranker import normalize_upgrades, weighted_score


# ---------------------------------------------------------------------------
# normalize_upgrades
# ---------------------------------------------------------------------------


def test_normalize_upgrades_divides_by_the_set_maximum():
    out = normalize_upgrades({"a": 2.0, "b": 8.0, "c": 4.0})
    assert out["b"] == 1.0
    assert out["a"] == 0.25
    assert out["c"] == 0.5


def test_normalize_upgrades_single_candidate_is_one():
    assert normalize_upgrades({"solo": 3.3}) == {"solo": 1.0}


def test_normalize_upgrades_ties_all_map_to_one():
    out = normalize_upgrades({"a": 5.0, "b": 5.0, "c": 5.0})
    assert out == {"a": 1.0, "b": 1.0, "c": 1.0}


def test_normalize_upgrades_handles_empty_and_nonpositive():
    assert normalize_upgrades({}) == {}
    assert normalize_upgrades({"a": 0.0, "b": 0.0}) == {"a": 0.0, "b": 0.0}


# ---------------------------------------------------------------------------
# weighted_score
# ---------------------------------------------------------------------------


def test_weighted_score_blends_with_the_configured_weights():
    assert weighted_score(1.0, 0.5, 0.6, 0.4) == pytest.approx(0.8)
    assert weighted_score(0.0, 1.0, 0.6, 0.4) == pytest.approx(0.4)


def test_weighted_score_is_monotonic_in_both_inputs():
    assert weighted_score(0.9, 0.5, 0.6, 0.4) > weighted_score(0.5, 0.5, 0.6, 0.4)
    assert weighted_score(0.5, 0.9, 0.6, 0.4) > weighted_score(0.5, 0.5, 0.6, 0.4)


def test_performance_decides_when_upgrades_are_equally_large():
    """The primary use case from the spec: several big upgrades -> performance wins."""
    upgrades = normalize_upgrades({"ace": 9.8, "average": 9.9, "poor": 9.7})
    perf = {"ace": 0.95, "average": 0.55, "poor": 0.20}
    ranked = sorted(
        upgrades,
        key=lambda k: weighted_score(upgrades[k], perf[k], 0.6, 0.4),
        reverse=True,
    )
    assert ranked == ["ace", "average", "poor"]


def test_a_much_bigger_upgrade_can_still_outrank_better_performance():
    upgrades = normalize_upgrades({"huge": 20.0, "tiny": 1.0})
    perf = {"huge": 0.30, "tiny": 0.90}
    assert weighted_score(upgrades["huge"], perf["huge"], 0.6, 0.4) > weighted_score(
        upgrades["tiny"], perf["tiny"], 0.6, 0.4
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_lootcouncil_ranker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lootcouncil.ranker'`

- [ ] **Step 3: Write `lootcouncil/ranker.py`**

```python
"""Blend upgrade size with role-aware performance into a single loot ranking."""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from lootcouncil.config import Config
from lootcouncil.models import Character, PerformanceScore
from lootcouncil.performance import PerformanceAnalyzer, score_cohort

logger = logging.getLogger("lootcouncil")


def normalize_upgrades(pcts: Dict[str, float]) -> Dict[str, float]:
    """Scale upgrade percentages onto 0..1 *within the candidate set*.

    This is what makes performance the decider when several candidates all have
    a big upgrade: their normalized values all compress toward 1.0.
    """
    if not pcts:
        return {}
    best = max(pcts.values())
    if best <= 0:
        return {key: 0.0 for key in pcts}
    return {key: value / best for key, value in pcts.items()}


def weighted_score(
    upgrade_norm: float, performance: float, weight_upgrade: float, weight_performance: float
) -> float:
    return weight_upgrade * upgrade_norm + weight_performance * performance


@dataclass(frozen=True)
class RankedCandidate:
    key: str
    name: str
    role: str
    spec: str
    upgrade_pct: float
    upgrade_norm: float
    performance: float
    components: Dict[str, float]
    loot_score: float
    low_confidence: bool


@dataclass(frozen=True)
class LootResult:
    item_id: int
    difficulty: int
    weight_upgrade: float
    weight_performance: float
    by_role: Dict[str, List[RankedCandidate]] = field(default_factory=dict)
    performance_by_role: Dict[str, List[RankedCandidate]] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not any(self.by_role.values())


class LootRanker:
    """Ties the two data sources together and produces both rankings."""

    def __init__(self, cfg: Config, wowaudit, analyzer: PerformanceAnalyzer) -> None:
        self._cfg = cfg
        self._wowaudit = wowaudit
        self._analyzer = analyzer

    def rank(self, item_id: int, difficulty: Optional[int] = None, refresh: bool = False) -> LootResult:
        cfg = self._cfg
        difficulty = cfg.difficulty if difficulty is None else difficulty

        roster: List[Character] = self._wowaudit.roster()
        by_key = {c.key: c for c in roster}
        upgrades = self._wowaudit.upgrades_for(item_id, difficulty)

        candidates = {k: v for k, v in upgrades.items() if k in by_key}
        if not candidates:
            return LootResult(item_id, difficulty, cfg.weight_upgrade, cfg.weight_performance)

        raws = self._analyzer.aggregate_season(roster, refresh=refresh)
        roles = {c.key: c.role for c in roster}
        scores: Dict[str, PerformanceScore] = score_cohort(raws, roles, cfg)

        # Upgrades normalize within role, because the rankings are per-role.
        keys_by_role: Dict[str, List[str]] = {}
        for key in candidates:
            keys_by_role.setdefault(by_key[key].role, []).append(key)

        result_by_role: Dict[str, List[RankedCandidate]] = {}
        for role, keys in keys_by_role.items():
            norms = normalize_upgrades({k: candidates[k].percentage for k in keys})
            rows: List[RankedCandidate] = []
            for key in keys:
                score = scores.get(key)
                perf = score.score if score else 0.0
                rows.append(
                    RankedCandidate(
                        key=key,
                        name=by_key[key].name,
                        role=role,
                        spec=candidates[key].spec,
                        upgrade_pct=candidates[key].percentage,
                        upgrade_norm=norms[key],
                        performance=perf,
                        components=score.components if score else {},
                        loot_score=weighted_score(
                            norms[key], perf, cfg.weight_upgrade, cfg.weight_performance
                        ),
                        low_confidence=score.low_confidence if score else True,
                    )
                )
            result_by_role[role] = sorted(rows, key=lambda r: r.loot_score, reverse=True)

        performance_by_role = {
            role: sorted(rows, key=lambda r: r.performance, reverse=True)
            for role, rows in result_by_role.items()
        }

        return LootResult(
            item_id=item_id,
            difficulty=difficulty,
            weight_upgrade=cfg.weight_upgrade,
            weight_performance=cfg.weight_performance,
            by_role=result_by_role,
            performance_by_role=performance_by_role,
        )
```

- [ ] **Step 4: Export the ranker**

Update `lootcouncil/__init__.py`:

```python
"""Loot council helper — rank drop recipients by upgrade size and role-aware performance."""

from lootcouncil.config import Config, ConfigError
from lootcouncil.performance import PerformanceAnalyzer
from lootcouncil.ranker import LootRanker, LootResult, RankedCandidate

__all__ = ["Config", "ConfigError", "LootRanker", "LootResult", "PerformanceAnalyzer", "RankedCandidate"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_lootcouncil_ranker.py -v`
Expected: PASS

- [ ] **Step 6: Verify the spec's import contract works**

Run: `python -c "from lootcouncil import LootRanker; print(LootRanker)"`
Expected: `<class 'lootcouncil.ranker.LootRanker'>`

- [ ] **Step 7: Lint and commit**

Run: `python -m ruff check lootcouncil tests/test_lootcouncil_ranker.py`
Expected: no output.

```bash
git add lootcouncil/ranker.py lootcouncil/__init__.py tests/test_lootcouncil_ranker.py
git commit -m "$(cat <<'EOF'
feat: add LootRanker blending upgrade size with performance

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: The CLI

**Files:**
- Create: `loot.py`
- Modify: `tests/test_lootcouncil_ranker.py` (append a formatting section)

**Interfaces:**
- Consumes: `Config`, `LootRanker`, `LootResult`, `RankedCandidate`, `PerformanceAnalyzer`, `WowAuditClient`, `WarcraftLogsClient`.
- Produces: `format_loot_table(result) -> str`; `format_performance_table(result) -> str`; `parse_weights(text) -> Tuple[float, float]`; `main(argv=None) -> int`.

- [ ] **Step 1: Append the failing formatting tests**

First add these to the **existing import block at the top of the file** (`pytest` is already imported there — do not import it twice):

```python
from lootcouncil.models import DPS, TANK
from lootcouncil.ranker import LootResult, RankedCandidate
from loot import format_loot_table, format_performance_table, parse_weights
```

Then append this section to the end of `tests/test_lootcouncil_ranker.py`:

```python
# ---------------------------------------------------------------------------
# CLI formatting
# ---------------------------------------------------------------------------


def _candidate(name, role=DPS, upgrade=5.0, norm=1.0, perf=0.8, low=False):
    return RankedCandidate(
        key=f"{name.lower()}-illidan",
        name=name,
        role=role,
        spec="Fury",
        upgrade_pct=upgrade,
        upgrade_norm=norm,
        performance=perf,
        components={"parse": 0.9, "deaths": 0.8, "damage": 0.7, "utility": 0.0, "survivability": 0.0},
        loot_score=0.6 * norm + 0.4 * perf,
        low_confidence=low,
    )


def _result(rows):
    by_role = {}
    for row in rows:
        by_role.setdefault(row.role, []).append(row)
    return LootResult(
        item_id=215147,
        difficulty=5,
        weight_upgrade=0.6,
        weight_performance=0.4,
        by_role=by_role,
        performance_by_role=by_role,
    )


def test_parse_weights_accepts_a_comma_pair():
    assert parse_weights("0.7,0.3") == (0.7, 0.3)


def test_parse_weights_rejects_malformed_input():
    for bad in ("0.7", "a,b", "", "0.1,0.2,0.3"):
        with pytest.raises(ValueError):
            parse_weights(bad)


def test_loot_table_lists_candidates_best_first_with_the_weights_in_the_header():
    result = _result([_candidate("Ace", perf=0.9, norm=1.0), _candidate("Rookie", perf=0.2, norm=0.9)])
    out = format_loot_table(result)
    assert "215147" in out
    assert "0.6" in out and "0.4" in out  # weights surfaced per the spec
    assert out.index("Ace") < out.index("Rookie")


def test_loot_table_marks_low_confidence_rows():
    out = format_loot_table(_result([_candidate("Thin", low=True)]))
    assert "low confidence" in out.lower()


def test_loot_table_reports_the_empty_case_clearly():
    empty = LootResult(item_id=999, difficulty=5, weight_upgrade=0.6, weight_performance=0.4)
    out = format_loot_table(empty)
    assert "no candidates" in out.lower()


def test_tables_group_by_role():
    out = format_loot_table(_result([_candidate("Tanky", role=TANK), _candidate("Stabby", role=DPS)]))
    assert "TANK" in out.upper()
    assert "DPS" in out.upper()


def test_performance_table_shows_the_component_breakdown():
    out = format_performance_table(_result([_candidate("Ace")]))
    assert "parse" in out.lower()
    assert "deaths" in out.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_lootcouncil_ranker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loot'`

- [ ] **Step 3: Write `loot.py`**

```python
"""CLI: rank who should receive a dropped item.

Usage: python loot.py <itemId> [--difficulty 5] [--refresh] [--weights 0.6,0.4]
"""

import argparse
import logging
import sys
from typing import List, Tuple

from dotenv import load_dotenv

from lootcouncil.config import Config
from lootcouncil.performance import PerformanceAnalyzer
from lootcouncil.ranker import LootRanker, LootResult
from lootcouncil.warcraftlogs import WarcraftLogsClient
from lootcouncil.wowaudit import WowAuditClient

logger = logging.getLogger("lootcouncil")

COMPONENT_ORDER = ("parse", "deaths", "damage", "utility", "survivability")


def parse_weights(text: str) -> Tuple[float, float]:
    parts = text.split(",")
    if len(parts) != 2:
        raise ValueError("--weights must look like 0.6,0.4")
    try:
        upgrade, performance = float(parts[0]), float(parts[1])
    except ValueError:
        raise ValueError("--weights must be two numbers, e.g. 0.6,0.4")
    return upgrade, performance


def _rows_to_lines(rows, columns) -> List[str]:
    widths = [len(c) for c in columns]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    header = "  ".join(c.ljust(widths[i]) for i, c in enumerate(columns))
    sep = "  ".join("-" * widths[i] for i in range(len(columns)))
    body = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows]
    return [header, sep] + body


def format_loot_table(result: LootResult) -> str:
    lines = [
        f"Loot ranking for item {result.item_id} (difficulty {result.difficulty})",
        f"Weights: upgrade {result.weight_upgrade} / performance {result.weight_performance}",
        "",
    ]
    if result.is_empty:
        lines.append("No candidates — nobody has this item on a wishlist for that difficulty.")
        lines.append("(If the season just started, droptimizers may not be uploaded yet.)")
        return "\n".join(lines)

    for role, rows in result.by_role.items():
        lines.append(f"== {role.upper()} ==")
        table = [
            [
                str(i + 1),
                row.name + (" *" if row.low_confidence else ""),
                row.spec,
                f"{row.upgrade_pct:.2f}%",
                f"{row.performance:.3f}",
                f"{row.loot_score:.3f}",
            ]
            for i, row in enumerate(rows)
        ]
        lines.extend(_rows_to_lines(table, ["#", "Character", "Spec", "Upgrade", "Perf", "Score"]))
        lines.append("")

    if any(r.low_confidence for rows in result.by_role.values() for r in rows):
        lines.append("* low confidence — too few logged fights to score reliably")
    return "\n".join(lines)


def format_performance_table(result: LootResult) -> str:
    lines = ["Performance ranking (within role)", ""]
    if result.is_empty:
        lines.append("No candidates to score.")
        return "\n".join(lines)

    for role, rows in result.performance_by_role.items():
        lines.append(f"== {role.upper()} ==")
        table = [
            [str(i + 1), row.name, f"{row.performance:.3f}"]
            + [f"{row.components.get(c, 0.0):.2f}" for c in COMPONENT_ORDER]
            for i, row in enumerate(rows)
        ]
        lines.extend(
            _rows_to_lines(table, ["#", "Character", "Perf"] + [c.title() for c in COMPONENT_ORDER])
        )
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Rank who should receive a dropped item.")
    parser.add_argument("item_id", type=int, help="the dropped item's ID")
    parser.add_argument("--difficulty", type=int, default=None, help="WCL difficulty (5=Mythic, 4=Heroic)")
    parser.add_argument("--refresh", action="store_true", help="re-pull Warcraft Logs instead of using the cache")
    parser.add_argument("--weights", type=str, default=None, help="upgrade,performance e.g. 0.6,0.4")
    args = parser.parse_args(argv)

    cfg = Config.load()
    if args.weights:
        try:
            cfg.weight_upgrade, cfg.weight_performance = parse_weights(args.weights)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    try:
        cfg.validate()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    wcl = WarcraftLogsClient(cfg)
    ranker = LootRanker(cfg, WowAuditClient(cfg), PerformanceAnalyzer(cfg, wcl))
    try:
        result = ranker.rank(args.item_id, args.difficulty, refresh=args.refresh)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(format_loot_table(result))
    print()
    print(format_performance_table(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_lootcouncil_ranker.py -v`
Expected: PASS

- [ ] **Step 5: Verify the CLI's argument handling end to end**

Run: `python loot.py --help`
Expected: usage text listing `item_id`, `--difficulty`, `--refresh`, `--weights`.

Run: `python loot.py 215147`
Expected (with no `loot_config.json` present): exit code 2 and `error: loot_config.json is missing required setting(s): guild_name, guild_server_slug, season_zone_id`

- [ ] **Step 6: Run the whole suite and lint**

Run: `python -m pytest -q`
Expected: PASS — bot tests and loot council tests together.

Run: `python -m ruff check .`
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add loot.py tests/test_lootcouncil_ranker.py
git commit -m "$(cat <<'EOF'
feat: add loot.py CLI with loot and performance tables

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Documentation

**Files:**
- Create: `lootcouncil/README.md`
- Modify: `README.md`, `CLAUDE.md`

Per the spec, this tool is **not** part of the bot's `CHANGELOG.md` or `version.txt` — it is a separate tool, not a bot feature. Do not bump the version.

- [ ] **Step 1: Write `lootcouncil/README.md`**

```markdown
# Loot Council Helper

A standalone CLI that helps a raid loot council decide who should receive a
dropped item. It finds everyone the item is an upgrade for (WoWAudit
droptimizer wishlists), scores each candidate by blending upgrade size with
role-aware Warcraft Logs performance, and prints two tables.

Read-only: it never assigns loot, never writes to WoWAudit or Warcraft Logs.

## Setup

1. Install dependencies (from the repo root):

   ```bash
   pip install -e .
   ```

2. Add these to `.env` (names are case-sensitive):

   | Var | Purpose |
   |-----|---------|
   | `wow_audit_api_key` | WoWAudit API key, sent as the `Authorization` header |
   | `WARCRAFT_LOGS_API_CLIENT_ID` | Warcraft Logs OAuth client id |
   | `WARCRAFT_LOGS_API_CLIENT_SECRET` | Warcraft Logs OAuth client secret |

   Warcraft Logs credentials come from creating a v2 client at
   <https://www.warcraftlogs.com/api/clients/>.

3. Copy the example config and fill in your guild:

   ```bash
   cp loot_config.example.json loot_config.json
   ```

   | Setting | Meaning | Default |
   |---------|---------|---------|
   | `guild_name` | Guild name as Warcraft Logs knows it | *(required)* |
   | `guild_server_slug` | Realm slug, e.g. `malganis` for Mal'Ganis | *(required)* |
   | `guild_region` | `us`, `eu`, … | `us` |
   | `season_zone_id` | Warcraft Logs zone ID for the tier being analyzed | *(required)* |
   | `difficulty` | 5 = Mythic, 4 = Heroic | `5` |
   | `weight_upgrade` / `weight_performance` | The blend | `0.6` / `0.4` |
   | `report_lookback_days` | How far back to aggregate | `60` |
   | `cache_ttl_hours` | Cache lifetime | `24` |
   | `min_fights` | Below this, a candidate is flagged low-confidence | `3` |
   | `role_weights` | Per-role component weights | see below |

## Usage

```bash
python loot.py 215147                    # rank candidates for item 215147
python loot.py 215147 --difficulty 4     # Heroic instead of Mythic
python loot.py 215147 --refresh          # ignore the cache, re-pull Warcraft Logs
python loot.py 215147 --weights 0.4,0.6  # weight performance more heavily
```

## How the score works

```
upgrade_norm = candidate upgrade % / the largest upgrade % among candidates
loot_score   = weight_upgrade × upgrade_norm + weight_performance × performance
```

Normalizing the upgrade **within the candidate set** is the point: when several
people all have a big upgrade, their `upgrade_norm` values all compress toward
1.0 and performance becomes the decider.

`performance` is 0–1 and **role-aware**, because throughput parse understates
healers and tanks:

| Component | DPS | Healer | Tank |
|---|---|---|---|
| Parse percentile | **0.60** | 0.20 | 0.15 |
| Death avoidance (deaths + first-two-to-die) | 0.20 | 0.30 | 0.25 |
| Damage taken per active second | 0.20 | 0.25 | 0.30 |
| Utility (interrupts + dispels) | — | 0.25 | 0.10 |
| Survivability (TMI) | — | — | 0.20 |

Every component except parse is min-max normalized **within the character's own
role**, so a tank is never punished for taking tank damage. Candidates are
ranked within role — loot contention is naturally same-role.

## Reading the output

- **Loot ranking** — the recommendation, sorted by `loot_score`. A `*` marks a
  candidate with fewer than `min_fights` logged fights; their score is real but
  thin, so treat it as advisory.
- **Performance ranking** — the same people sorted by performance alone, with
  each component shown, so the council can see *why* someone scored as they did.

## Caching

Aggregating a season from Warcraft Logs is the only expensive step; results are
cached to `lootcouncil_cache.json` (gitignored). A normal lookup reads the cache
and makes **zero** Warcraft Logs calls. The cache is invalidated by age
(`cache_ttl_hours`), by `--refresh`, or by changing `season_zone_id` /
`difficulty`. Warcraft Logs allows 3,600 points/hour; this stays well inside it.

## Troubleshooting

- **"No candidates"** — nobody has that item wishlisted at that difficulty.
  Wishlists are empty until raiders upload Raidbots droptimizers, which
  typically doesn't happen until a season is underway.
- **"missing required setting(s)"** — fill in `loot_config.json` (see Setup).
- **Warcraft Logs OAuth errors** — check the two `WARCRAFT_LOGS_API_*` vars.
```

- [ ] **Step 2: Note the tool in the repo `README.md`**

Add a short section (place it after the bot's feature sections, before any development/setup appendix):

```markdown
## Loot Council Helper (standalone tool)

`python loot.py <itemId>` ranks who should receive a raid drop, blending
WoWAudit droptimizer upgrade size with role-aware Warcraft Logs performance.
It is a separate read-only CLI, not a bot command. See
[`lootcouncil/README.md`](lootcouncil/README.md) for setup and usage.
```

- [ ] **Step 3: Add the tool to `CLAUDE.md`**

`CLAUDE.md` documents the current state of the codebase, so add these rows to the **File Map** table:

```markdown
| `loot.py` | Standalone loot-council CLI entry point (not a bot command) |
| `lootcouncil/` | Loot council package — WoWAudit + Warcraft Logs clients, role-aware scoring, ranking |
| `loot_config.json` | Gitignored config for the loot council tool (see `loot_config.example.json`) |
```

And add a short section after the **Auction Sniper (Blizzard)** section:

```markdown
### Loot Council Helper (standalone CLI)
Not a bot feature — a separate read-only tool (`python loot.py <itemId>`) sharing the repo.
Finds everyone a dropped item upgrades (WoWAudit droptimizer wishlists), scores them with
`0.6 · upgrade_norm + 0.4 · performance`, and prints a loot ranking plus a performance
ranking, both **within role**. Upgrade % is normalized within the candidate set, so when
several candidates all have a big upgrade, performance decides. Performance is role-aware
(DPS lean on parse; healers/tanks on death avoidance, damage taken, utility, TMI) and every
component is min-max normalized within the character's own role cohort. Warcraft Logs
aggregation is cached to `lootcouncil_cache.json`, so a normal lookup makes zero API calls.
Env: `wow_audit_api_key`, `WARCRAFT_LOGS_API_CLIENT_ID`, `WARCRAFT_LOGS_API_CLIENT_SECRET`.
Deliberately excluded from the bot's `CHANGELOG.md` / `version.txt`.
```

Also add the three env vars to the **Environment Variables** block in `CLAUDE.md`:

```
wow_audit_api_key              WoWAudit API key (loot council tool)
WARCRAFT_LOGS_API_CLIENT_ID    Warcraft Logs OAuth client ID (loot council tool)
WARCRAFT_LOGS_API_CLIENT_SECRET  Warcraft Logs OAuth client secret (loot council tool)
```

- [ ] **Step 4: Verify the docs match reality**

Run: `python loot.py --help`
Expected: the flags shown match exactly the four documented in `lootcouncil/README.md` (`--difficulty`, `--refresh`, `--weights`, plus `item_id`).

Confirm by reading `lootcouncil/config.py` that every setting in the README's config table exists as a `Config` field with the documented default. Fix the README if any drifted.

- [ ] **Step 5: Full verification before declaring done**

Run: `python -m pytest -q`
Expected: PASS, no failures.

Run: `python -m ruff check .`
Expected: no output.

Run: `python -m black --check lootcouncil loot.py tests/test_lootcouncil_core.py tests/test_lootcouncil_clients.py tests/test_lootcouncil_performance.py tests/test_lootcouncil_ranker.py`
Expected: `All done!` — if it reports files needing reformatting, run without `--check` and re-run the tests.

- [ ] **Step 6: Commit**

```bash
git add lootcouncil/README.md README.md CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: document the loot council helper CLI

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Spec coverage check

| Spec requirement | Task |
|---|---|
| `lootcouncil/` package layout, `loot.py` CLI | 1, 7 |
| `WowAuditClient.roster` / `wishlists` / `upgrades_for` / `current_season` | 2 |
| WoWAudit auth header, non-200 raises a clear error | 2 |
| `WarcraftLogsClient` OAuth + `graphql` helper, 401 re-mint | 3 |
| `character_parses` from `zoneRankings` | 3 |
| `guild_reports` via roster `recentReports` fallback | 5 (`_report_codes`) |
| `report_fights`, `report_table` (Deaths/DamageTaken/Interrupts/Dispels) | 3, 5 |
| `aggregate_season` with cache + TTL + `--refresh` | 5 |
| Role-aware `score`, components reported | 4 |
| Death-order / first-two-to-die | 4 |
| Avoidable-damage v1 proxy, role-relative | 4 |
| `min_fights` low-confidence flag | 4 |
| `normalize_upgrades`, `weighted_score`, "performance decides" property | 6 |
| Both output tables with breakdowns | 7 |
| Config dataclass + all documented settings | 1 |
| `lootcouncil/README.md` + repo README note, no CHANGELOG entry | 8 |
| Zero WCL calls on a cache hit | 5 (test asserts it) |

**Not covered, by design:** Tier-2 per-boss mechanics (v2 — see the spec's *Prior art & the analysis tiers*). The v1 avoidable-damage proxy occupies the component slot v2 will later fill, so no rework is required.
