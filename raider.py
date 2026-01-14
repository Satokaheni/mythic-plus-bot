from typing import ClassVar, List

class Raider:
    name: ClassVar[str]
    class_play: ClassVar[str]
    roles: ClassVar[List[str]]
    
    def __init__(self, name, class_play, roles):
        self.name = name
        self.class_play = class_play
        self.roles = roles
        
    def __eq__(self, other) -> bool:
        if isinstance(other, Raider):
            return self.name == other.name and self.class_play == other.class_play and set(self.roles) == set(other.roles)
        return NotImplemented