from typing import ClassVar, List
from discord import Member

class Raider:
    id: ClassVar[int]
    mention: ClassVar[str]
    class_play: ClassVar[str]
    roles: ClassVar[List[str]]
    
    def __init__(self, member: Member, class_play: str, roles: List[str]):
        self.id = member.id
        self.mention = member.mention
        self.class_play = class_play
        self.roles = roles
        
    def __eq__(self, other) -> bool:
        if isinstance(other, Raider):
            return self.id == other.id and self.class_play == other.class_play and set(self.roles) == set(other.roles)
        return NotImplemented