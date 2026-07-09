"""Async client + harvester for Raider.io Mythic+ run history (forecasting backfill)."""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List
from urllib.parse import urlencode

import aiohttp

import eventlog

logger = logging.getLogger("discord")

BASE_URL = "https://raider.io/api/v1/characters/profile"
_MAPPINGS_PATH = "character_mappings.json"


def _api_key() -> str:
    return os.getenv("RAIDERIO_API_KEY", "")


def _region() -> str:
    return os.getenv("RAIDERIO_REGION", "us")


@dataclass(frozen=True)
class Run:
    """One Mythic+ run: keystone run id, completion time (UTC), and level."""

    run_id: int
    completed_at: datetime
    level: int


def _parse_completed_at(s: str) -> datetime:
    """Parse Raider.io's '2026-04-22T06:07:47.000Z' to a UTC-aware datetime (py3.9-safe)."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _parse_runs(data: dict) -> List[Run]:
    """Combine recent + best runs into a list of Run, deduped by run_id; skip malformed entries."""
    result = data.get("result") or {}
    raw = list(result.get("mythic_plus_recent_runs") or []) + list(result.get("mythic_plus_best_runs") or [])
    runs = {}
    for entry in raw:
        try:
            run_id = int(entry["keystone_run_id"])
            completed = _parse_completed_at(entry["completed_at"])
            level = int(entry["mythic_level"])
        except (KeyError, ValueError, TypeError):
            continue
        runs[run_id] = Run(run_id=run_id, completed_at=completed, level=level)
    return list(runs.values())


async def fetch_character_runs(session: aiohttp.ClientSession, realm_slug: str, character: str) -> List[Run]:
    """Fetch a character's recent + best M+ runs. Best-effort: returns [] on any error."""
    params = {
        "region": _region(),
        "realm": realm_slug,
        "name": character,
        "fields": "mythic_plus_recent_runs,mythic_plus_best_runs",
        "access_key": _api_key(),
    }
    url = f"{BASE_URL}?{urlencode(params)}"
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.warning("Raider.io %s/%s returned status %s", realm_slug, character, resp.status)
                return []
            data = await resp.json()
        return _parse_runs(data)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("Raider.io request failed for %s/%s: %s", realm_slug, character, exc)
        return []


def load_character_mappings(path: str = _MAPPINGS_PATH) -> List[dict]:
    """Load the character->discord mapping array. Missing/corrupt -> []. `alt_of` is ignored by callers."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            logger.warning("%s is not a JSON array; ignoring", path)
            return []
        return data
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return []


async def harvest(raiders: dict, session, mappings: List[dict], events_path: str = eventlog.EVENTS_PATH) -> int:
    """Append new raiderio_run events for registered mapped raiders. Idempotent (dedup by (user_id, run_id))."""
    existing = eventlog.read_events(events_path)
    seen = {(e.get("user_id"), e.get("run_id")) for e in existing if e.get("source") == "raiderio"}
    added = 0
    for entry in mappings:
        try:
            discord_id = int(entry["discord_id"])
        except (KeyError, ValueError, TypeError):
            continue
        raider = raiders.get(discord_id)
        if raider is None:
            continue  # unregistered -> no timezone -> skip
        realm_slug = entry.get("realm_slug")
        character = entry.get("character")
        if not realm_slug or not character:
            continue
        runs = await fetch_character_runs(session, realm_slug, character)
        for run in runs:
            key = (discord_id, run.run_id)
            if key in seen:
                continue
            eventlog.log_event(
                "raiderio_run",
                ts_utc=run.completed_at,
                user_id=discord_id,
                tz=raider.timezone,
                source="raiderio",
                run_id=run.run_id,
                level=run.level,
                path=events_path,
            )
            seen.add(key)
            added += 1
    return added
