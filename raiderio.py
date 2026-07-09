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
    result = data.get("result", {})
    raw = list(result.get("mythic_plus_recent_runs", [])) + list(result.get("mythic_plus_best_runs", []))
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
