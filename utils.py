from datetime import datetime, timedelta, time
from schedule import Schedule
from raider import Raider
from typing import List
from discord import Message

message: str = """
Please sign up for the following slots by reacting to the time slot you want to be in.
If you have not declared a role with your class and roles you can play (in order of prevelance using the !role command) you will not be considered for a spot
All runs will be based on completion for vault. The goal is not to push especially early in the tier. 
If you react to line up that is already full you will be added as an extra if one of them cannot make it. 
If you cannot make it to the run please remove your reaction from the schedule. 
If you are a flex you will be priority for your role you ranked the highest in the !role command but will be moved to make sure the group forms if needed
These are for your raid toons so please do not fill out a role you cannot play.


Reminders will be sent out 1 and 2 hours before start. 
"""

def create_schedules() -> List[Schedule]:
    def raid_day():
        return [time(17, 0), time(18, 0), time(23, 0)]
    
    def weekday():
        return [time(x, 0) for x in range(17, 24)]

    def weekend():
        return [time(x, 0) for x in range(9, 24)]
    
    schedules: List[Schedule] = []
    
    # Assume the day is Tuesday
    current = datetime.now().date()
    mythic_times = {
        0: raid_day(),
        1: raid_day(),
        2: weekday(),
        3: raid_day(),
        4: weekday(),
        5: weekend(),
        6: weekend()
    }
    
    for _ in range(7):
        day = current.weekday()
        schedules.extend(
            [Schedule(date=datetime.combine(current, time(t, 0))) for t in mythic_times[day]]
        )
        current = current + timedelta(days=1)
        
    return schedules

def validate_role_message(message: Message) -> Raider:
    wow_classes = [
        'mage', 'hunter', 'evoker', 'paladin', 'shaman', 'dk', 'rogue', 'priest',
        'warrior', 'warlock', 'druid', 'dh', 'monk'
    ]
    
    roles = [
        'tank', 'dps', 'healer'
    ]
    
    content = message.content.lower().split(' ')
    if not content[1] in wow_classes:
        return None
    
    if not set(content[2:]).issubset(roles):
        return None
    
    return Raider(message.author, content[1], content[2:])