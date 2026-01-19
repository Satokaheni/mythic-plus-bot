"""Utility functions and constants for the WoW Mythic+ bot."""

import pickle
from typing import Dict, Any, Tuple
from schedule import Schedule
import logging

logger = logging.getLogger('discord')

GREEN = '🟢'
YELLOW = '🟡'
RED = '🔴'

AVAILABILITY_MESSAGE: str = """
React to this message to set your availability for this week's myhic plus runs

:green_circle: - Available
:yellow_circle: - Maybe Available
:red_circle: - Not Available
"""

ARMOR_DICT: Dict[str, str] = {
    'Warrior': 'Plate',
    'Paladin': 'Plate',
    'Death Knight': 'Plate',
    'Hunter': 'Mail',
    'Evoker': 'Mail',
    'Shaman': 'Mail',
    'Rogue': 'Leather',
    'Druid': 'Leather',
    'Monk': 'Leather',
    'Demon Hunter': 'Leather',
    'Priest': 'Cloth',
    'Mage': 'Cloth',
    'Warlock': 'Cloth'
}

ROLES_DICT: Dict[str, list[str]] = {
    'Warrior': ['tank', 'dps'],
    'Paladin': ['tank', 'healer', 'dps'],
    'Death Knight': ['tank', 'dps'],
    'Hunter': ['dps'],
    'Evoker': ['healer', 'dps'],
    'Shaman': ['healer', 'dps'],
    'Rogue': ['dps'],
    'Druid': ['tank', 'healer', 'dps'],
    'Monk': ['tank', 'healer', 'dps'],
    'Warlock': ['dps'],
    'Mage': ['dps'],
    'Demon Hunter': ['tank', 'dps'],
    'Priest': ['healer', 'dps'],
}

def save_state(raiders: Dict[Any, Any], schedules: Dict[Any, Any], availability: Dict[str, Any], availability_message_id: int, dm_map: Dict[int, Tuple[Schedule, int]]) -> None:
    """Save the bot's state to a pickle file."""
    with open('state.pkl', 'wb') as state_file:
        pickle.dump({
            'raiders': raiders,
            'schedules': schedules,
            'availability': availability,
            'availability_message_id': availability_message_id,
            'dm_map': dm_map
        }, state_file)

def load_state() -> tuple[Dict[Any, Any], Dict[Any, Any], Dict[str, Any], int, Dict[int, Tuple[Schedule, int]]]:
    """Load the bot's state from a pickle file, with error handling."""
    try:
        with open('state.pkl', 'rb') as state_file:
            data = pickle.load(state_file)
        return data['raiders'], data['schedules'], data['availability'], data['availability_message_id'], data['dm_map']
    except (FileNotFoundError, pickle.UnpicklingError, EOFError, KeyError) as exc:
        logger.warning("Error loading state.pkl: %s. Using default state.", exc)
        return {}, {}, {}, 0, {}
