"""Raider class representing a World of Warcraft player."""

from typing import List, Set, TYPE_CHECKING
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
    current_runs: Set['Schedule']
    denied_runs: Set['Schedule']

    def __init__(self, member: Member, class_play: str, roles: List[str], timezone: str) -> None:
        """Initialize a Raider from a Discord member."""
        self.user_id = member.id
        self.mention = member.mention
        self.name = member.display_name
        self.class_play = class_play
        self.roles = roles
        self.timezone = ZoneInfo(timezone)
        self.current_runs = set()
        self.denied_runs = set()

    def add_run(self, schedule: 'Schedule') -> None:
        """Add a scheduled run time to the raider's current runs."""
        self.current_runs.add(schedule)
        self.denied_runs.discard(schedule)

    def remove_run(self, schedule: 'Schedule') -> None:
        """Remove a scheduled run time from the raider's current runs."""
        self.current_runs.discard(schedule)
        self.denied_runs.add(schedule)

    def check_availability(self, schedule: 'Schedule') -> bool:
        """Check if the raider is available for a given scheduled time.

        Rules:
        - Cannot join if already in this exact schedule
        - Cannot join if another single-key schedule is within 1 hour
        - Cannot join if a multiple-key schedule is within 2 hours (before or after)
        - Cannot join another schedule if already in a multiple-key run within 2 hours
        """
        # Check if already in this exact schedule
        if schedule in self.current_runs:
            return False

        for current_schedule in self.current_runs:
            # Calculate time difference between schedules (in seconds)
            time_diff = abs((schedule.start_time - current_schedule.start_time).total_seconds())

            # If either schedule is multiple keys, require 2 hour gap
            if schedule.run_type == "multiple" or current_schedule.run_type == "multiple":
                if time_diff < 7200:  # 2 hours = 7200 seconds
                    return False
            # For single-key runs, require 1 hour gap
            else:
                if time_diff < 3600:  # 1 hour = 3600 seconds
                    return False

        return True

    def get_schedule_conflict_reason(self, schedule: 'Schedule') -> str:
        """Get a human-readable reason why a raider cannot join a schedule.
        Returns empty string if no conflict.
        """
        # Check if already in this exact schedule
        if schedule in self.current_runs:
            return "You're already signed up for this run."

        for current_schedule in self.current_runs:
            # Calculate time difference between schedules
            time_diff_seconds = abs((schedule.start_time - current_schedule.start_time).total_seconds())
            time_diff_hours = time_diff_seconds / 3600

            # Format the conflicting schedule time
            conflict_time = f"<t:{int(current_schedule.start_time.timestamp())}:t>"

            # If either schedule is multiple keys, check 2 hour gap
            if schedule.run_type == "multiple" or current_schedule.run_type == "multiple":
                if time_diff_seconds < 7200:  # 2 hours
                    if current_schedule.run_type == "multiple":
                        return f"❌ Conflict: You're signed up for a **multiple-key** run at {conflict_time}, which requires a 2+ hour gap. Time difference: {time_diff_hours:.1f} hours."
                    else:
                        return f"❌ Conflict: This is a **multiple-key** run, but you have another run at {conflict_time} within 2 hours. Time difference: {time_diff_hours:.1f} hours."
            # For single-key runs, check 1 hour gap
            else:
                if time_diff_seconds < 3600:  # 1 hour
                    return f"❌ Conflict: You have another run at {conflict_time}, which is less than 1 hour away. Time difference: {time_diff_hours:.1f} hours."

        return ""  # No conflict
    
    def get_current_runs(self) -> str:
        """Return a string representation of the raider's current runs (only filled)."""
        
        filled_runs = [run for run in self.current_runs if run.is_filled()]
        non_filled = [run for run in self.current_runs if not run.is_filled()]
        
        runs = ""
        
        if len(filled_runs) > 0:
            runs += "Your current scheduled runs for the week that are filled are the following: \n" + '\n'.join([
                f"Day: {run.date_scheduled.strftime('%A')} Start Time: {run.start_time.strftime('%H:%M')} Level: {run.level}"
                for run in self.current_runs if run.is_filled()
            ])
        if len(non_filled):
            runs += "Your current scheduled runs for the week that are not filled yet are the following: \n" + '\n'.join([
                f"Day: {run.date_scheduled.strftime('%A')} Start Time: {run.start_time.strftime('%H:%M')} Level: {run.level}"
                for run in self.current_runs if not run.is_filled()
            ])
        
        return runs

    def __eq__(self, other) -> bool:
        """Check equality based on user_id only."""
        if isinstance(other, Raider):
            return self.user_id == other.user_id
        return NotImplemented
    
    def __hash__(self) -> int:
        """Return hash based on user_id for use in sets and dicts."""
        return hash(self.user_id)
    
    def __str__(self) -> str:
        return f"{self.name}"
