"""Append-only event log for availability/attendance signals (forecasting data)."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("discord")

EVENTS_PATH = "events.jsonl"

_CST = ZoneInfo("America/Chicago")


def local_slot(ts_utc: datetime, tz: ZoneInfo) -> tuple:
    """Return (weekday 0=Mon..6=Sun, block 0..11) for ts_utc in local tz; block = local_hour // 2."""
    local = ts_utc.astimezone(tz)
    return (local.weekday(), local.hour // 2)


def week_of(ts_utc: datetime) -> str:
    """ISO date of the Tuesday-noon-CST anchor of ts_utc's availability week.

    Availability resets Tuesday at noon CST. Returns the date of the most recent
    Tuesday-noon-CST at or before ts_utc.
    """
    local = ts_utc.astimezone(_CST)
    days_since_tue = (local.weekday() - 1) % 7  # Tuesday.weekday() == 1
    anchor = local.replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=days_since_tue)
    if anchor > local:
        anchor -= timedelta(days=7)
    return anchor.date().isoformat()
