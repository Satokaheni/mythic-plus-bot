"""Raider class representing a World of Warcraft player."""

from typing import List, TYPE_CHECKING
from zoneinfo import ZoneInfo
from discord import Member

if TYPE_CHECKING:
    from schedule import Schedule

class Raider:
    """Represents a World of Warcraft raider with class and roles."""
    user_id: int
    mention: str
    class_play: str
    timezone: ZoneInfo
    roles: List[str]
    current_runs: List['Schedule'] = []
    denied_runs: List['Schedule'] = []

    def __init__(self, member: Member, class_play: str, roles: List[str], timezone: str) -> None:
        """Initialize a Raider from a Discord member."""
        self.user_id = member.id
        self.mention = member.mention
        self.name = member.display_name
        self.class_play = class_play
        self.roles = roles
        self.timezone = ZoneInfo(timezone)

    def add_run(self, schedule: 'Schedule') -> None:
        """Add a scheduled run time to the raider's current runs."""
        if schedule not in self.current_runs:
            self.current_runs.append(schedule)
        if schedule in self.denied_runs:
            self.denied_runs.remove(schedule)

    def remove_run(self, schedule: 'Schedule') -> None:
        """Remove a scheduled run time from the raider's current runs."""
        if schedule in self.current_runs:
            self.current_runs.remove(schedule)
        if not schedule in self.denied_runs:
            self.denied_runs.append(schedule)

    def check_availability(self, schedule: 'Schedule') -> bool:
        """Check if the raider is available for a given scheduled time."""
        return schedule not in self.current_runs
    
    def get_current_runs(self) -> str:
        """Return a string representation of the raider's current runs (only filled)."""
        return "Your current scheduled runs for the week that are filled are the following: \n" + '\n'.join([
            f"Day: {run.date_scheduled.strftime('%A')} Start Time: {run.start_time.strftime('%H:%M')} Level: {run.level}"
            for run in self.current_runs if run.is_filled()
        ])

    def __eq__(self, other) -> bool:
        """Check equality based on id, class, and roles."""
        if isinstance(other, Raider):
            return (self.user_id == other.user_id and
                    self.class_play == other.class_play and
                    set(self.roles) == set(other.roles))
        return NotImplemented
