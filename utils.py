"""Utility functions and constants for the WoW Mythic+ bot."""

import json
import logging
import os
import pickle
from datetime import datetime
from typing import Any, Dict, Tuple

logger = logging.getLogger("discord")

GREEN = "🟢"
YELLOW = "🟡"
RED = "🔴"


ARMOR_DICT: Dict[str, str] = {
    "Warrior": "Plate",
    "Paladin": "Plate",
    "Death Knight": "Plate",
    "Hunter": "Mail",
    "Evoker": "Mail",
    "Shaman": "Mail",
    "Rogue": "Leather",
    "Druid": "Leather",
    "Monk": "Leather",
    "Demon Hunter": "Leather",
    "Priest": "Cloth",
    "Mage": "Cloth",
    "Warlock": "Cloth",
}

ROLES_DICT: Dict[str, list[str]] = {
    "Warrior": ["tank", "dps"],
    "Paladin": ["tank", "healer", "dps"],
    "Death Knight": ["tank", "dps"],
    "Hunter": ["dps"],
    "Evoker": ["healer", "dps"],
    "Shaman": ["healer", "dps"],
    "Rogue": ["dps"],
    "Druid": ["tank", "healer", "dps"],
    "Monk": ["tank", "healer", "dps"],
    "Warlock": ["dps"],
    "Mage": ["dps"],
    "Demon Hunter": ["tank", "dps"],
    "Priest": ["healer", "dps"],
}


def save_state(raiders, schedules, availability, availability_message_id, dm_map, dm_timestamps) -> None:
    """Save the bot's state to a JSON file atomically."""
    # Build a reverse map: Python object id -> message_id, so Raiders can
    # serialize their current_runs / denied_runs as stable integer keys.
    schedule_to_id = {id(s): mid for mid, s in schedules.items()}

    data = {
        "version": 2,
        "raiders": {str(uid): r.to_dict(schedule_to_id) for uid, r in raiders.items()},
        "schedules": {str(mid): s.to_dict(mid) for mid, s in schedules.items()},
        "availability": {emoji: [r.user_id for r in raider_list] for emoji, raider_list in availability.items()},
        "availability_message_id": availability_message_id,
        "dm_map": {str(uid): {str(k): v for k, v in v_dict.items()} for uid, v_dict in dm_map.items()},
        "dm_timestamps": {
            str(uid): {str(k): v.isoformat() if isinstance(v, datetime) else v for k, v in v_dict.items()}
            for uid, v_dict in dm_timestamps.items()
        },
    }

    # Write to a temp file then rename for an atomic update.
    tmp_path = "state.json.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, "state.json")


def load_state() -> Tuple[
    Dict[Any, Any], Dict[Any, Any], Dict[str, Any], int, Dict[int, Dict[int, int]], Dict[int, Dict[int, datetime]]
]:
    """Load the bot's state from state.json, migrating from state.pkl if needed."""
    if not os.path.exists("state.json"):
        if os.path.exists("state.pkl"):
            return _migrate_from_pickle()
        return {}, {}, {}, 0, {}, {}

    try:
        with open("state.json", "r", encoding="utf-8") as f:
            data = json.load(f)

        return _deserialize(data)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Error loading state.json: %s. Using default state.", exc)
        return {}, {}, {}, 0, {}, {}


def _deserialize(data: dict):
    """Reconstruct all objects from a JSON state dict."""
    from raider import Raider
    from schedule import Schedule

    # Pass 1 — create all Raider objects (runs wired up in pass 3)
    raiders: Dict[int, Any] = {}
    for uid_str, r_data in data.get("raiders", {}).items():
        raider = Raider.from_dict(r_data)
        raiders[raider.user_id] = raider

    # Pass 2 — create all Schedule objects, resolving Raider references
    schedules: Dict[int, Any] = {}
    for mid_str, s_data in data.get("schedules", {}).items():
        mid = int(mid_str)
        schedules[mid] = Schedule.from_dict(s_data, raiders)

    # Pass 3 — wire up Raider.current_runs / denied_runs now that schedules exist
    for uid_str, r_data in data.get("raiders", {}).items():
        raider = raiders[int(uid_str)]
        raider.current_runs = {schedules[mid] for mid in r_data.get("current_runs", []) if mid in schedules}
        raider.denied_runs = {schedules[mid] for mid in r_data.get("denied_runs", []) if mid in schedules}

    # Pass 4 — reconcile current_runs with schedule membership.
    # If a raider appears in a schedule's members list but is missing from their
    # current_runs (e.g. due to a prior bug that reset current_runs to []), add
    # them back so both sides of the relationship are consistent.
    for schedule in schedules.values():
        for raider in schedule.members:
            raider.current_runs.add(schedule)

    # Availability — list of Raiders per emoji
    availability = {GREEN: [], YELLOW: [], RED: []}
    for emoji, uid_list in data.get("availability", {}).items():
        if emoji in availability:
            availability[emoji] = [raiders[uid] for uid in uid_list if uid in raiders]

    availability_message_id: int = data.get("availability_message_id", 0)

    dm_map: Dict[int, Dict[int, int]] = {
        int(uid_str): {int(k): v for k, v in v_dict.items()} for uid_str, v_dict in data.get("dm_map", {}).items()
    }

    dm_timestamps: Dict[int, Dict[int, datetime]] = {
        int(uid_str): {int(k): datetime.fromisoformat(v) if isinstance(v, str) else v for k, v in v_dict.items()}
        for uid_str, v_dict in data.get("dm_timestamps", {}).items()
    }

    return raiders, schedules, availability, availability_message_id, dm_map, dm_timestamps


def _migrate_from_pickle():
    """Load from legacy state.pkl and immediately re-save as state.json."""
    try:
        with open("state.pkl", "rb") as f:
            data = pickle.load(f)

        raiders = data["raiders"]
        schedules = data["schedules"]
        availability = data["availability"]
        availability_message_id = data["availability_message_id"]
        dm_map = data.get("dm_map", {})
        dm_timestamps = data.get("dm_timestamps", {})

        save_state(raiders, schedules, availability, availability_message_id, dm_map, dm_timestamps)
        logger.info("Migrated state from state.pkl to state.json.")
        return raiders, schedules, availability, availability_message_id, dm_map, dm_timestamps
    except Exception as exc:
        logger.warning("Error migrating from state.pkl: %s. Using default state.", exc)
        return {}, {}, {}, 0, {}, {}
