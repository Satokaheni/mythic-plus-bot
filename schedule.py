from datetime import datetime
from typing import ClassVar, List, Self, Dict
from raider import Raider


class Schedule:
    day: ClassVar[str]
    time: ClassVar[str]
    team: ClassVar[Dict] = {
        'tank': None,
        'healer': None,
        'dps': [],
        'flex': [],
        'fill': []
    }
    bloodlust: ClassVar[bool]
    full: ClassVar[bool]
    signup: ClassVar[int]
    dates: ClassVar[List[str]] = [
        'Monday', 'Tuesday', 'Wendesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'
    ]
    lust_classes: ClassVar[List[str]] = [
        'evoker', 'shaman', 'mage', 'hunter'
    ]
    
    def __init__(self, date: datetime) -> Self:
        self.day = self.dates[date.weekday()]
        self.time = f"<t:{date.timestamp}:t>"
        self.full = False
        self.signup = 0
        
    def _check_flex(self) -> Self:
        flex = []
        for raider in self.team['flex']:
            found = []
            for role in raider.roles:
                if role != 'dps':
                    found.append(False if self.team[role] else True)
                else:
                    found.append(False if len(self.team[role]) > 2 else True)
                    
            if sum(found) == 1:
                role = raider.roles[found.index(True)]
                if role == 'dps':
                    self.team['dps'].append(raider)
                    self.signup += 1
                else:
                    self.team[role] = raider
                    self.signup += 1
            else:
                flex.append(raider)
                
        self.team['flex'] = flex

    def _check_fill(self) -> Self:
        filled = False
        for raider in self.team['fill']:
            roles = raider.roles
            for role in roles:
                if role == 'dps':
                    if len(self.team['dps']) < 3:
                        self.team['dps'].append(raider)
                        self.signup += 1
                        self.filled = True
                    else:
                        if not self.team[role]:
                            self.team[role] = raider
                            self.signup += 1
                            self.filled = True
        if filled:
            self.team['fill'].pop(0)
        
        
    def send_message(self) -> str:
        message = f"{self.day} at {self.time} "
        if self.team['tank']:
            message += f"Tank: @{self.tank.name} "
        if self.team['healer']:
            message += f"Healer: @{self.healer.name} "
        if self.team['dps']:
            for raider in self.team['dps']:
                message += f"DPS: @{raider.name} "
        if self.team['flex']:
            for raider in self.team['flex']:
                message += f"Flex: @{raider.name} Roles: {str(raider.roles)}"
        if self.team['fill']:
            for raider in self.team['fill']:
                message += f"Fill: @{raider.name} "

        return message
    
    def raider_signup(self, raider: Raider) -> Self:
        # Only play one role easy to assign    
        if len(raider.roles) == 1:
            role = raider.roles[0]
            
            if role == 'tank' and not self.team['tank']:
                self.team['tank'] = raider
                self.signup += 1

            elif role == 'healer' and not self.team['healer']:
                self.team['healer'] = raider
                self.signup += 1
                
            elif role == 'dps' and len(self.team['dps']) < 3:
                self.dps.append(raider)
                self.signup += 1
                
            else:
                self.team['fill'].append(raider)
                
        else:
            self.team['flex'].append(raider)
            
        self._check_flex()
        
        if self.signup == 5:
            self.full = True
            
    def raider_remove(self, raider: Raider) -> Self:
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
                    self._check_fill()
                elif self.team[role] == raider:
                    self.team[role] = None
                    self.signup -= 1
                    self.check_fill()
                else:
                    pass
                
        if self.signup < 5:
            self.full = False
