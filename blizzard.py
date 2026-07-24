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
