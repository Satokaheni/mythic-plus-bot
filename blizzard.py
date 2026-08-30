"""Async client for Blizzard's WoW Game Data and Profile APIs (auctions, token price, character equipment)."""

import base64
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

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
class Enchant:
    """One enchantment on an equipped item. `slot_type` is PERMANENT, TEMPORARY, or ON_USE_SPELL."""

    slot_type: str
    display_string: str
    enchantment_id: int


@dataclass(frozen=True)
class EquippedItem:
    """One equipped item. `sockets` holds a gem item id per socket, None where the socket is empty."""

    slot: str
    name: str
    item_id: int
    item_class_id: int
    enchants: Tuple[Enchant, ...]
    sockets: Tuple[Optional[int], ...]


@dataclass(frozen=True)
class ItemInfo:
    name: str
    is_recipe: bool
    quality: str = ""


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
    quality = (data.get("quality") or {}).get("type", "")
    return ItemInfo(name=name, is_recipe=is_recipe, quality=quality)


def _parse_equipment(data: dict) -> List[EquippedItem]:
    """Parse a character equipment payload. Skips malformed entries rather than raising."""
    items: List[EquippedItem] = []
    for raw in data.get("equipped_items", []):
        if not isinstance(raw, dict):
            continue
        slot = (raw.get("slot") or {}).get("type")
        if not slot:
            continue
        enchants = tuple(
            Enchant(
                slot_type=(e.get("enchantment_slot") or {}).get("type", ""),
                display_string=e.get("display_string", ""),
                enchantment_id=int(e.get("enchantment_id", 0)),
            )
            for e in raw.get("enchantments", [])
            if isinstance(e, dict)
        )
        sockets = tuple(
            (s.get("item") or {}).get("id") for s in raw.get("sockets", []) if isinstance(s, dict)
        )
        items.append(
            EquippedItem(
                slot=slot,
                name=raw.get("name", ""),
                item_id=int((raw.get("item") or {}).get("id", 0)),
                item_class_id=int((raw.get("item_class") or {}).get("id", 0)),
                enchants=enchants,
                sockets=sockets,
            )
        )
    return items


def _parse_realm_name(data: dict) -> str:
    realms = data.get("realms", [])
    return realms[0].get("name", "") if realms else ""


def _parse_token_price(data: dict) -> Optional[Tuple[int, int]]:
    """Parse a WoW Token index payload into (price_copper, last_updated_ms).

    Returns None when the payload carries no price, so a malformed or partial
    response is skipped rather than raising inside the poll loop.
    """
    if "price" not in data:
        return None
    return int(data["price"]), int(data.get("last_updated_timestamp", 0))


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

    async def _get(self, session: aiohttp.ClientSession, path: str, namespace: str, headers: Optional[Dict[str, str]] = None) -> aiohttp.ClientResponse:
        # Retry once on 401: the cached token may have been revoked before its
        # assumed expiry, so clear it and re-mint before giving up.
        resp = None
        for attempt in range(2):
            token = await self.ensure_token(session)
            h = {"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"}
            if headers:
                h.update(headers)
            url = f"{_api_host()}{path}?namespace={namespace}-{_region()}&locale=en_US"
            resp = await session.get(url, headers=h)
            if resp.status == 401 and attempt == 0:
                resp.close()
                self._token = None
                self._token_expiry = 0.0
                continue
            return resp
        return resp

    async def list_connected_realms(self, session: aiohttp.ClientSession) -> List[int]:
        async with await self._get(session, "/data/wow/connected-realm/index", "dynamic") as resp:
            resp.raise_for_status()
            return _parse_realm_index(await resp.json())

    async def get_realm_auctions(self, session: aiohttp.ClientSession, realm_id: int, if_modified_since: Optional[str] = None) -> object:
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

    async def character_equipment(
        self, session: aiohttp.ClientSession, realm_slug: str, character: str
    ) -> Optional[List[EquippedItem]]:
        """Equipped items for one character, or None when Blizzard has no such character.

        A 404 means the character was renamed, transferred, or deleted — a stale mapping,
        which the caller reports rather than treating as an error.
        """
        path = f"/profile/wow/character/{realm_slug}/{quote(character.lower())}/equipment"
        async with await self._get(session, path, "profile") as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            return _parse_equipment(await resp.json())

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

    async def token_price(self, session: aiohttp.ClientSession) -> Optional[Tuple[int, int]]:
        """Current WoW Token price in copper, plus Blizzard's update timestamp (ms epoch).

        The token is not an auction-house commodity — it has its own endpoint, and it
        is region-wide, so there is no realm parameter.
        """
        async with await self._get(session, "/data/wow/token/index", "dynamic") as resp:
            resp.raise_for_status()
            return _parse_token_price(await resp.json())
