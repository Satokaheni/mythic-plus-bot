"""BotState: the bot's persisted scheduling state and its single save path."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from mythicbot import storage
from mythicbot.scheduling.raider import Raider
from mythicbot.storage import GREEN, RED, YELLOW


class BotState:
    def __init__(
        self,
        raiders=None,
        schedules=None,
        availability=None,
        availability_message_id=None,
        dm_map=None,
        dm_timestamps=None,
    ) -> None:
        self.raiders: Dict[int, Any] = raiders if raiders is not None else {}
        self.schedules: Dict[int, Any] = schedules if schedules is not None else {}
        self.availability: Dict[str, List[Any]] = (
            availability if availability is not None else {GREEN: [], YELLOW: [], RED: []}
        )
        self.availability_message_id: Optional[int] = availability_message_id
        self.dm_map: Dict[int, Dict[int, int]] = dm_map if dm_map is not None else {}
        self.dm_timestamps: Dict[int, Dict[int, datetime]] = dm_timestamps if dm_timestamps is not None else {}
        self.role_mentions: Dict[str, Any] = {}
        self.reminder_messages: Dict[int, List[int]] = {}

    def register_raider(self, user, class_play: str, roles: List[str], tz: str) -> Raider:
        """Store a new Raider for `user` and return it, or return the one already stored.

        The class/role DM form stays open for minutes, so the user may have registered through
        another form meanwhile; replacing that record would orphan the one their seats point to.
        """
        if user.id not in self.raiders:
            self.raiders[user.id] = Raider(user, class_play, roles, tz)
        return self.raiders[user.id]

    @classmethod
    def load(cls) -> "BotState":
        return cls(*storage.load_state())

    def save(self) -> None:
        storage.save_state(
            self.raiders,
            self.schedules,
            self.availability,
            self.availability_message_id,
            self.dm_map,
            self.dm_timestamps,
        )
