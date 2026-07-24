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
