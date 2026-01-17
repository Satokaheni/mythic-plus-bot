"""Schedule class for managing WoW Mythic+ raid team composition."""

from datetime import datetime
from typing import List, Dict, Union, ClassVar

from raider import Raider


class Schedule:
    """Represents a WoW Mythic+ raid schedule with team composition and signup management."""
    full: ClassVar[bool] = False
    team: ClassVar[Dict[str, Union[Raider, List[Raider], None]]] = {
        'tank': None,
        'healer': None,
        'dps': [],
        'flex': [],
        'fill': []
    }
    missing: ClassVar[List[str]] = ['tank', 'healer', 'dps']
    signup: ClassVar[int] = 0

    def __init__(self, raider_scheduled: Raider, dungeon: str, level: str,
                 date_scheduled: str, start_time: datetime, end_time: datetime):
        """Initialize a new schedule with the scheduling raider and details."""
        self.dungeon = dungeon
        self.level = level
        self.date_scheduled = datetime.strptime(date_scheduled, "%Y-%m-%d")
        self.start_time = start_time
        self.end_time = end_time
        self.raider_signup(raider_scheduled)

    def _check_flex(self):
        """Check flex raiders and assign them to missing roles if possible."""
        flex = []
        for raider in self.team['flex']:
            found = [True if role in self.missing else False for role in raider.roles]

            if sum(found) == 1:
                role = raider.roles[found.index(True)]
                if role == 'dps':
                    self.team['dps'].append(raider)
                    self.signup += 1
                else:
                    self.team[role] = raider
                    self.signup += 1

                self.missing.remove(role)
            else:
                flex.append(raider)

        self.team['flex'] = flex

    def _check_fill(self):
        """Check fill raiders and assign them to available slots."""
        filled = None
        for raider in self.team['fill']:
            if not filled:
                roles = raider.roles
                for role in roles:
                    if role == 'dps':
                        if len(self.team['dps']) < 3:
                            self.team['dps'].append(raider)
                            self.signup += 1
                            filled = raider
                        else:
                            if not self.team[role]:
                                self.team[role] = raider
                                self.signup += 1
                                filled = raider

        if filled:
            self.team['fill'].remove(raider)



    def send_message(self) -> str:
        """Generate the formatted message for the schedule post."""
        message = f"""
        Scheduled Mythic+ Run:
        Date: {self.date_scheduled.strftime('%Y-%m-%d')}
        Time: {self.start_time.strftime('%H:%M')} to {self.end_time.strftime('%H:%M')}

        Dungeon: {self.dungeon} (Level {self.level})

        Please React to this message to confirm your attendance.
        Please Remove your reaction if you can no longer attend.

        Team:
        Tank: {self.team['tank'].mention if self.team['tank'] else 'TBD'}
        Healer: {self.team['healer'].mention if self.team['healer'] else 'TBD'}
        DPS: {', '.join([raider.mention for raider in self.team['dps']]) if self.team['dps'] else 'TBD'}
        Flex: {', '.join([f'{raider.mention} {raider.roles}' for raider in self.team['flex']]) if self.team['flex'] else 'None'}
        Fill: {', '.join([f'{raider.mention} {raider.roles}' for raider in self.team['fill']]) if self.team['fill'] else 'None'}
        """

        return message

    def send_reminder(self) -> str:
        """Generate a reminder message for the team."""
        return f"""
            Reminder for {self.team['tank'].mention} {self.team['healer'].mention} {self.team['dps'][0].mention} {self.team['dps'][1].mention} {self.team['dps'][2].mention}
        """

    def raider_signup(self, raider: Raider):
        """Add a raider to the schedule, assigning them to appropriate roles."""
        # Only play one role easy to assign
        if len(raider.roles) == 1:
            role = raider.roles[0]

            if role == 'tank' and not self.team['tank']:
                self.team['tank'] = raider
                self.signup += 1
                self.missing.remove('tank')

            elif role == 'healer' and not self.team['healer']:
                self.team['healer'] = raider
                self.signup += 1
                self.missing.remove('healer')

            elif role == 'dps' and len(self.team['dps']) < 3:
                self.team['dps'].append(raider)
                self.signup += 1
                self.missing.remove('dps')

            else:
                self.team['fill'].append(raider)

        else:
            self.team['flex'].append(raider)

        self._check_flex()

        if self.signup == 5:
            self.full = True

    def raider_remove(self, raider: Raider):
        """Remove a raider from the schedule and update team composition."""
        roles = raider.roles

        if raider in self.team['fill']:
            self.team['fill'].remove(raider)

        elif raider in self.team['flex']:
            self.team['flex'].remove(raider)

        else:
            for role in roles:
                if role == 'dps':
                    self.team['dps'].remove(raider)
                    self.signup -= 1
                    self.missing.append('dps')
                    self._check_fill()
                elif self.team[role] == raider:
                    self.team[role] = None
                    self.signup -= 1
                    self.missing.append(role)
                    self._check_fill()
                else:
                    pass

        if self.signup < 5:
            self.full = False
