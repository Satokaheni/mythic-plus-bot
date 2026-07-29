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
    """WoWAudit sometimes wraps collections in an object and sometimes returns a bare list.

    Filters out non-dict elements to defend against malformed payloads.
    """
    if isinstance(data, dict):
        rows = data.get(key)
    else:
        rows = data
    rows = rows or []
    return [x for x in rows if isinstance(x, dict)]


def _as_float(value: Any) -> float:
    """Safely coerce a value to float, returning 0.0 on any error.

    Handles None, falsy values, and non-numeric strings gracefully.
    """
    value = value or 0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


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
        # Filter out non-dict elements to handle malformed payloads
        specs = [x for x in specs if isinstance(x, dict)]
        if specs:
            best = max(specs, key=lambda s: _as_float(s.get("percentage", 0)))
            return UpgradeInfo(
                percentage=_as_float(best.get("percentage", 0)),
                absolute=_as_float(best.get("absolute", 0)),
                spec=best.get("spec", ""),
            )
    if "percentage" in item or "absolute" in item:
        return UpgradeInfo(
            percentage=_as_float(item.get("percentage", 0)),
            absolute=_as_float(item.get("absolute", 0)),
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
        instances = [x for x in (char.get("instances") or []) if isinstance(x, dict)]
        for instance in instances:
            difficulties = [x for x in (instance.get("difficulties") or []) if isinstance(x, dict)]
            for diff in difficulties:
                diff_name = diff.get("difficulty") or diff.get("name") or ""
                wishlist = diff.get("wishlist") or {}
                encounters = [x for x in (wishlist.get("encounters") or []) if isinstance(x, dict)]
                for encounter in encounters:
                    items = [x for x in (encounter.get("items") or []) if isinstance(x, dict)]
                    for item in items:
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
