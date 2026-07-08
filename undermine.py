"""Async client for the Undermine Exchange commodities API."""

import asyncio
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
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
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
