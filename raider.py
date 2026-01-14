from typing import ClassVar, List
from discord import Member

class Raider:
    member: ClassVar[Member]
    class_play: ClassVar[str]
    roles: ClassVar[List[str]]
    
    def __init__(self, member, class_play, roles):
        self.member = member
        self.class_play = class_play
        self.roles = roles
        
    def __eq__(self, other) -> bool:
        if isinstance(other, Raider):
            return self.member.id == other.member.id and self.class_play == other.class_play and set(self.roles) == set(other.roles)
        return NotImplemented