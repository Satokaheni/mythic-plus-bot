"""Schedule class for managing WoW Mythic+ raid team composition."""

from datetime import datetime, timezone
from raider import Raider


class Schedule:
    """Represents a WoW Mythic+ raid schedule with team composition and signup management."""
    def __init__(self, raider_scheduled: Raider, dungeon: str, level: str,
                 date_scheduled: str, start_time: datetime, end_time: datetime):
        """Initialize a new schedule with the scheduling raider and details."""
        self.dungeon = dungeon
        self.level = level
        # Parse date_scheduled as UTC
        self.date_scheduled = datetime.strptime(date_scheduled, "%Y-%m-%d").replace(tzinfo=raider_scheduled.timezone)
        # Ensure start_time and end_time are UTC
        self.start_time = start_time.astimezone(raider_scheduled.timezone) if start_time.tzinfo else start_time.replace(tzinfo=raider_scheduled.timezone)
        self.end_time = end_time.astimezone(raider_scheduled.timezone) if end_time.tzinfo else end_time.replace(tzinfo=raider_scheduled.timezone)
        self.full = False
        self.team = {
            'tank': None,
            'healer': None,
            'dps': [],
            'fill': []
        }
        self.members = []
        self.missing = ['tank', 'healer', 'dps']
        self.signup = 0
        self.tier_reached = '🟢'
        self.primary = True
        self.asks = 0
        self.raider_signup(raider_scheduled)

    def _check_fill(self):
        """Check fill raiders and assign them to available slots."""
        filled = None
        for raider in list(self.team['fill']):
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
            self.team['fill'].remove(filled)

    def send_message(self) -> str:
        """Generate the formatted message for the schedule post."""
        message = f"""
        Scheduled Mythic+ Run:
        Date: <t:{int(self.date_scheduled.astimezone(timezone.utc).timestamp())}:D>
        Time: <t:{int(self.start_time.astimezone(timezone.utc).timestamp())}:t> to <t:{int(self.end_time.astimezone(timezone.utc).timestamp())}:t>

        Dungeon: {self.dungeon} (Level {self.level})

        Please React to this message to confirm your attendance.
        Please Remove your reaction if you can no longer attend.

        Team:
        Tank: {self.team['tank'].mention if self.team['tank'] else 'TBD'}
        Healer: {self.team['healer'].mention if self.team['healer'] else 'TBD'}
        DPS: {'TBD' if len(self.team['dps']) < 1 else self.team['dps'][0].mention}
        DPS: {'TBD' if len(self.team['dps']) < 2 else self.team['dps'][1].mention}
        DPS: {'TBD' if len(self.team['dps']) < 3 else self.team['dps'][2].mention}
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
        role = raider.roles[0] if raider.roles[0] in self.missing else (raider.roles[1] if len(raider.roles) > 1 and raider.roles[1] in self.missing else raider.roles[0])
        
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
        self.members.append(raider)
        if self.signup == 5:
            self.full = True

    def raider_remove(self, raider: Raider):
        """Remove a raider from the schedule and update team composition."""
        roles = raider.roles
        if raider in self.team['fill']:
            self.team['fill'].remove(raider)
        else:
            for role in roles:
                if role == 'dps' and raider in self.team['dps']:
                    self.team['dps'].remove(raider)
                    self.signup -= 1
                    self.missing.append('dps')
                    self._check_fill()
                elif role in self.team and self.team[role] == raider:
                    self.team[role] = None
                    self.signup -= 1
                    self.missing.append(role)
                    self._check_fill()
        if raider in self.members:
            self.members.remove(raider)
        if self.signup < 5:
            self.full = False

    def is_filled(self) -> bool:
        """Return True if the schedule is full (5 signups)."""
        return self.full

    def try_signup(self, raider: Raider) -> bool:
        """Attempt to sign up a raider. Returns True if they were added.
        Prevents duplicate signups.
        """
        if raider in self.members:
            return False
        before = self.signup
        self.raider_signup(raider)
        return self.signup > before
