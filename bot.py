"""Discord bot for managing WoW Mythic+ raid scheduling."""

import logging

import discord
from discord.ext import commands

import blizzard
import cogs
from config import (  # noqa: F401  (re-exported for the harness and tests)
    _CST,
    ADMINS,
    AVAIL_CHANNEL_ID,
    BANKER_BUDGET_COPPER,
    BANKER_BULK_QTY,
    BANKER_ID,
    BLIZZ_CLIENT_ID,
    BLIZZ_CLIENT_SECRET,
    BOT_VERSION,
    CLIENT_ID,
    COORDINATOR_ID,
    DPS_ID,
    ELEVATED_IDS,
    GEAR_AUDIT_CONCURRENCY,
    GUILD_ID,
    HEALER_ID,
    KEY_CHANNEL_ID,
    MYTHIC_PLUS_ID,
    SNIPE_SWEEP_MINUTES,
    TANK_ID,
    TOKEN_POLL_MINUTES,
)
from messages import (  # noqa: F401
    AVAILABILITY_MESSAGE,
    _dm_reminder,
    _dm_spot_available,
    _read_changelog,
    _read_last_version,
    _write_version,
)
from raider import Raider
from schedule import Schedule
from state import BotState
from utils import GREEN, RED, YELLOW  # noqa: F401  (re-exported for tests)
from views import (
    KeyRequestButtonView,
    ScheduleButtonView,
)

logger = logging.getLogger("discord")


# ---------------------------
# Bot Class
# ---------------------------
class MyClient(commands.Bot):
    """Discord bot for Mythic+ scheduling. Features live in cogs/; this class owns startup and shared state."""

    def __init__(self, *, intents: discord.Intents) -> None:
        super().__init__(command_prefix="!", help_command=None, case_insensitive=False, intents=intents)

    # ---------------------------
    # User Defined Functions
    # ---------------------------

    async def message_user(self, raider: Raider, emoji: str, schedule: Schedule):
        await self.get_cog("Scheduling").message_user(raider, emoji, schedule)

    async def notify_schedule(self, schedule: Schedule):
        await self.get_cog("Scheduling").notify_schedule(schedule)

    async def _do_key_request_flow(self, user: discord.User):
        await self.get_cog("Scheduling")._do_key_request_flow(user)

    async def _notify_promoted(self, promoted: Raider, schedule: Schedule):
        await self.get_cog("Scheduling")._notify_promoted(promoted, schedule)

    async def _notify_displaced(self, displaced: Raider, schedule: Schedule, role: str):
        await self.get_cog("Scheduling")._notify_displaced(displaced, schedule, role)

    async def _notify_schedule_changed(self, schedule: Schedule, message: str, organizer_id: int):
        await self.get_cog("Scheduling")._notify_schedule_changed(schedule, message, organizer_id)

    async def new_availability_signup_fill_schedule(self, raider: Raider, tier: str):
        await self.get_cog("Scheduling").new_availability_signup_fill_schedule(raider, tier)









    # ---------------------------
    # Weekly Availability Reset
    # ---------------------------


    # ---------------------------
    # Weekly Forecast Dry-Run Preview
    # ---------------------------


    # ---------------------------
    # Startup
    # ---------------------------
    async def setup_hook(self) -> None:
        """Calls before on ready to set up all environmental variables"""
        self.state = BotState.load()
        self.coordinator_id = COORDINATOR_ID
        self.elevated_ids = ELEVATED_IDS
        self.blizzard = blizzard.BlizzardClient()
        logger.info("Loaded state from file.")

        # Register persistent views so buttons work after bot restarts
        self.add_view(KeyRequestButtonView(self))

        # Re-register each existing schedule's button view so interactions
        # still work after a restart (discord.py routes by message_id)
        for schedule_id, schedule in self.state.schedules.items():
            self.add_view(ScheduleButtonView(schedule, self), message_id=schedule_id)
        logger.info("Re-registered %d schedule views", len(self.state.schedules))

        for extension in cogs.EXTENSIONS:
            await self.load_extension(extension)

    async def on_ready(self):
        """Called when the bot is ready."""
        logger.info("Logged in as %s", self.user)

    # ---------------------------
    # Error Handling
    # ---------------------------
    async def on_error(self, event, *args, **kwargs):
        """Log errors to the console."""
        logger.exception("Error in event %s: %s", event, args)

    async def on_command_error(self, ctx, error) -> None:
        """Unknown commands and failed gates stay silent, as before; anything else is logged."""
        if isinstance(error, (commands.CommandNotFound, commands.CheckFailure)):
            return
        logger.exception("Command %s failed", getattr(ctx.command, "qualified_name", None), exc_info=error)


# ---------------------------
# Bot Setup
# ---------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True
intents.dm_messages = True

client = MyClient(intents=intents)

if __name__ == "__main__":
    # log_handler=None: logging is configured by the logging setup, so discord.py must not add its own handler.
    client.run(CLIENT_ID, log_handler=None)
