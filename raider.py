"""Raider class representing a World of Warcraft player."""

from typing import List
from zoneinfo import ZoneInfo
from discord import Member

class Raider:
    """Represents a World of Warcraft raider with class and roles."""
    user_id: int
    mention: str
    class_play: str
    timezone: ZoneInfo
    roles: List[str]

    def __init__(self, member: Member, class_play: str, roles: List[str], timezone: str) -> None:
        """Initialize a Raider from a Discord member."""
        self.user_id = member.id
        self.mention = member.mention
        self.class_play = class_play
        self.roles = roles
        self.timezone = ZoneInfo(timezone)

    def __eq__(self, other) -> bool:
        """Check equality based on id, class, and roles."""
        if isinstance(other, Raider):
            return (self.user_id == other.user_id and
                    self.class_play == other.class_play and
                    set(self.roles) == set(other.roles))
        return NotImplemented
