from datetime import datetime
from typing import ClassVar, List, Dict
from raider import Raider


class Schedule:
    date: ClassVar[datetime]
    day: ClassVar[str]
    time: ClassVar[str]
    team: ClassVar[Dict] = {
        'tank': None,
        'healer': None,
        'dps': [],
        'flex': [],
        'fill': []
    }
    missing: ClassVar[List[str]] = [
        'tank', 'healer', 'dps', 'dps', 'dps'
    ]
    bloodlust: ClassVar[bool]
    full: ClassVar[bool]
    signup: ClassVar[int]
    dates: ClassVar[List[str]] = [
        'Monday', 'Tuesday', 'Wendesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'
    ]
    lust_classes: ClassVar[List[str]] = [
        'evoker', 'shaman', 'mage', 'hunter'
    ]
    
    def __init__(self, date: datetime):
        self.date = date
        self.day = self.dates[date.weekday()]
        self.time = f"<t:{int(date.timestamp())}:t>"
        self.full = False
        self.signup = 0
        
    def _check_flex(self):
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
        message = f"{self.day} at {self.time} "
        if self.team['tank']:
            message += f"Tank: {self.team['tank'].mention} "
        if self.team['healer']:
            message += f"Healer: {self.team['healer'].mention} "
        if self.team['dps']:
            for raider in self.team['dps']:
                message += f"DPS: {raider.mention} "
        if self.team['flex']:
            for raider in self.team['flex']:
                message += f"Flex: {raider.mention} Roles: {str(raider.roles)} "
        if self.team['fill']:
            for raider in self.team['fill']:
                message += f"Fill: {raider.mention} "
                
        if len(self.missing) > 0:
            message += "Missing: "
            for role in self.missing:
                message += f"{role} "
                
        message += "\n----------------------------------------------------------------"

        return message
    
    def send_reminder(self) -> str:
        return f"""
            Reminder for {self.team['tank'].mention} {self.team['healer'].mention} {self.team['dps'][0].mention} {self.team['dps'][1].mention} {self.team['dps'][2].mention}
        """
    
    def raider_signup(self, raider: Raider):
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
                self.dps.append(raider)
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
