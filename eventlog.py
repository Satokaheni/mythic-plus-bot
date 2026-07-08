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


def log_event(
    event_type: str,
    *,
    ts_utc: datetime,
    user_id: Optional[int] = None,
    tz: Optional[ZoneInfo] = None,
    source: str = "discord",
    run_id: Optional[int] = None,
    path: str = EVENTS_PATH,
    **fields,
) -> None:
    """Append one event record to the JSONL log. Best-effort: never raises."""
    try:
        record = {
            "type": event_type,
            "ts_utc": ts_utc.astimezone(timezone.utc).isoformat(),
            "user_id": user_id,
            "source": source,
            "run_id": run_id,
        }
        if tz is not None:
            weekday, block = local_slot(ts_utc, tz)
            record["local_weekday"] = weekday
            record["local_block"] = block
            record["tz"] = str(tz)
        else:
            record["local_weekday"] = None
            record["local_block"] = None
            record["tz"] = None
        record.update(fields)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except (OSError, ValueError) as exc:
        logger.warning("eventlog: failed to log %s event: %s", event_type, exc)


def read_events(path: str = EVENTS_PATH) -> List[dict]:
    """Read all events from the JSONL log, skipping malformed lines. Missing file -> []."""
    events: List[dict] = []
    if not os.path.exists(path):
        return events
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("eventlog: skipping malformed line in %s", path)
    except OSError as exc:
        logger.warning("eventlog: failed to read %s: %s", path, exc)
    return events
