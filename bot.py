"""Discord bot for managing WoW Mythic+ raid scheduling."""

import asyncio
import logging
import os
from datetime import datetime, time, timedelta, timezone
from logging.handlers import RotatingFileHandler
from textwrap import dedent
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord.ext import tasks
from dotenv import load_dotenv

import blizzard
import eventlog
import forecast
import gearaudit
import raiderio
import snipelist as snipelist_mod
import tokenwatch
import undermine
import watchlist
from raider import Raider
from schedule import Schedule
from utils import GREEN, RED, YELLOW, load_state, save_state
from views import KeyRequestButtonView, KeyRequestView, PrePostAddRaiderView, RoleSelectView, WoWSelectionView
from watchlist import Watchlist

# ---------------------------
# Logging Setup
# ---------------------------
logger = logging.getLogger("discord")


def _configure_logging() -> None:
    """Log to a rotating file (for headless deploys) and the console.

    The file is capped so it can't fill a Raspberry Pi's SD card. Tunable via env:
    LOG_FILE (default bot.log), LOG_LEVEL (default INFO).
    """
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    log_file = os.getenv("LOG_FILE", "bot.log")
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    # ~5 MB per file, 3 rotated backups -> at most ~20 MB on disk.
    file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    console_handler = logging.StreamHandler()
    for handler in (file_handler, console_handler):
        handler.setFormatter(fmt)
        root.addHandler(handler)


# ---------------------------
# Global Variables
# ---------------------------
load_dotenv(".env")
_configure_logging()


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


CLIENT_ID = _require_env("CLIENT_KEY")
AVAIL_CHANNEL_ID = int(_require_env("AVAIL_CHANNEL_ID"))
KEY_CHANNEL_ID = int(_require_env("KEY_CHANNEL_ID"))
GUILD_ID = int(_require_env("GUILD_ID"))
TANK_ID = int(_require_env("TANK_ROLE_ID"))
HEALER_ID = int(_require_env("HEALER_ROLE_ID"))
DPS_ID = int(_require_env("DPS_ROLE_ID"))
COORDINATOR_ID = int(_require_env("COORDINATOR_ID"))
BANKER_ID = int(_require_env("BANKER_ID"))
# Gold budget used to size price-watch buy suggestions (default 100,000 gold).
BANKER_BUDGET_COPPER = int(os.getenv("BANKER_BUDGET_GOLD", "100000")) * 10000
# Default bulk order size the price-watch buy signal targets (per-item overridable via `!watch -x`).
BANKER_BULK_QTY = int(os.getenv("BANKER_BULK_QTY", "100"))
GEAR_AUDIT_CONCURRENCY = 5  # Parallel Blizzard profile fetches; the roster is ~30 characters.
# Blizzard Game Data API — server-specific auction sniper (see snipes.json).
BLIZZ_CLIENT_ID = _require_env("BLIZZ_CLIENT_ID")
BLIZZ_CLIENT_SECRET = _require_env("BLIZZ_CLIENT_SECRET")
os.environ.setdefault("BLIZZ_REGION", os.getenv("BLIZZ_REGION", "us"))
SNIPE_SWEEP_MINUTES = 30
# Blizzard refreshes the WoW Token price roughly every 20 minutes.
TOKEN_POLL_MINUTES = 20
MYTHIC_PLUS_ID = int(_require_env("MYTHIC_PLUS_ID"))
ADMINS = [int(id_str) for id_str in _require_env("ADMIN_ID").split(",") if id_str.strip().isdigit()]
ELEVATED_IDS = {COORDINATOR_ID} | set(ADMINS)
_CST = ZoneInfo("America/Chicago")
# ---------------------------
# Version & Changelog
# ---------------------------
BOT_VERSION = "1.9.0"

_VERSION_FILE = "version.txt"


def _read_last_version() -> str:
    try:
        with open(_VERSION_FILE, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def _write_version(version: str) -> None:
    with open(_VERSION_FILE, "w") as f:
        f.write(version)


def _read_changelog(version: str) -> list[str]:
    """Read entries for a given version from CHANGELOG.md, preserving section headers."""
    try:
        with open("CHANGELOG.md", "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    in_section = False
    entries = []
    for line in lines:
        if line.startswith(f"## [{version}]"):
            in_section = True
            continue
        if in_section:
            if line.startswith("## ["):
                break
            if line.startswith("### "):
                entries.append(f"**{line[4:]}**")
            elif line.startswith("- "):
                entries.append(line[2:])
    return entries


def AVAILABILITY_MESSAGE(x, y, z) -> str:
    return f"""
React to this message to set your availability for this week's mythic plus runs
<t:{int(x)}:F> to <t:{int(y)}:F>

**If this is your first time signing up please check your DMs after reacting**


:green_circle: - Available
:yellow_circle: - Maybe Available
:red_circle: - Not Available

{z}
"""


# ---------------------------
# DM Message Builders
# ---------------------------


def _dm_spot_available(name: str, level: str, ts: int, roster: str, role: str, role_type: str = "primary") -> str:
    return dedent(f"""
        Hello {name}, a spot has opened up for (Level {level})
        Run on <t:{ts}:F>.
        Your {role_type} role ({role}) is needed.

        {roster}

        React with :white_check_mark: if you would like to go, or :x: to decline.
        """)


def _dm_reminder(name: str, level: str, ts: int, roster: str, role: str) -> str:
    return dedent(f"""
        🔔 **Reminder** 🔔
        Hello {name}, a spot is still available for (Level {level})
        Run on <t:{ts}:F>.
        Your role ({role}) is needed.

        {roster}

        React with :white_check_mark: if you would like to go, or :x: to decline.
        """)


# ---------------------------
# Bot Class
# ---------------------------
class MyClient(discord.Client):
    """Discord bot client for managing WoW Mythic+ raid scheduling and availability."""

    raiders: Dict[discord.Member, Raider] = {}
    schedules: Dict[int, Schedule] = {}
    availability: Dict[str, Raider] = {GREEN: [], YELLOW: [], RED: []}
    dm_map: Dict[int, Dict[int, int]] = {}
    dm_timestamps: Dict[int, Dict[int, datetime]] = {}
    reminder_messages: Dict[int, List[int]] = {}  # schedule_id -> [channel_message_id, ...]
    availability_message_id: int = None
    role_mentions = {}

    # ---------------------------
    # User Defined Functions
    # ---------------------------

    async def message_user(self, raider: Raider, emoji: str, schedule: Schedule):
        """Send a direct message to a raider based on their availability reaction."""
        try:
            if emoji in ["✅", "❌"]:
                dm_channel = await self.get_user(raider.user_id).create_dm()
                if emoji == "✅":
                    await dm_channel.send(
                        dedent(
                            f"""
                        Hello {raider.name}, you have successfully signed up for (Level {schedule.level})
                        Run on <t:{int(schedule.start_time.astimezone(timezone.utc).timestamp())}:F>.
                        See you there!
                        """
                        )
                    )
                elif emoji == "❌":
                    await dm_channel.send(
                        dedent(
                            f"""
                        Hello {raider.name}, you have successfully declined the spot for (Level {schedule.level})
                        Run on <t:{int(schedule.start_time.astimezone(timezone.utc).timestamp())}:F>.
                        """
                        )
                    )

        except discord.Forbidden:
            logger.warning(f"Could not DM {raider.name} for schedule {schedule}")

    async def repost_schedule(self, schedule: Schedule, schedule_post: int) -> Tuple[int, Schedule]:
        """
        Delete and repost message if it's been up for over 24 hours.
        """
        try:
            message = await self.get_channel(KEY_CHANNEL_ID).fetch_message(schedule_post)
            await message.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

        embed, view, content = schedule.send_message(self.role_mentions, self)
        schedule.posted = datetime.now(timezone.utc)

        # Post with role mentions if there are missing roles
        new_message = await self.get_channel(KEY_CHANNEL_ID).send(
            content=content if content else None, embed=embed, view=view
        )

        return new_message.id, schedule

    async def get_message_history(self):
        """Warm the message cache so reactions on old messages fire after a restart.

        Each fetch is best-effort: a message may have been deleted since last run
        (e.g. the availability message), which fetch_message reports as NotFound.
        Skip missing/inaccessible messages so one gone message can't abort on_ready
        or skip caching the rest.
        """
        avail_channel = self.get_channel(AVAIL_CHANNEL_ID)
        if avail_channel and self.availability_message_id:
            try:
                await avail_channel.fetch_message(self.availability_message_id)
            except (discord.NotFound, discord.Forbidden):
                pass

        for cid, mid in self.dm_map.items():
            channel = self.get_channel(cid)
            if channel:
                try:
                    await channel.fetch_message(mid[1])
                except (discord.NotFound, discord.Forbidden):
                    pass

    # Add new method to handle DM retries
    async def retry_unanswered_dms(self):
        """Retry sending DMs for schedule requests that haven't been answered."""
        now = datetime.now(timezone.utc)
        retry_threshold = 3600 * 2  # 2 hour before retry

        dms_to_retry = []
        dms_to_delete = []

        # Check all active DMs
        for dm_channel_id, dm_messages in list(self.dm_map.items()):
            for dm_message_id, schedule_id in list(dm_messages.items()):
                # Check if this DM has a timestamp
                if dm_channel_id in self.dm_timestamps and dm_message_id in self.dm_timestamps[dm_channel_id]:
                    sent_time = self.dm_timestamps[dm_channel_id][dm_message_id]
                    time_elapsed = (now - sent_time).total_seconds()

                    # If DM is older than threshold and schedule still exists, isn't filled, and hasn't started
                    if time_elapsed >= retry_threshold:
                        schedule = self.schedules.get(schedule_id)
                        if schedule and not schedule.is_filled() and not schedule.is_past():
                            dms_to_retry.append((dm_channel_id, dm_message_id, schedule_id, schedule))
                            dms_to_delete.append((dm_channel_id, dm_message_id))

        # Process retries
        for dm_channel_id, old_dm_id, schedule_id, schedule in dms_to_retry:
            try:
                # Get the channel and try to delete old message
                dm_channel = self.get_channel(dm_channel_id)
                if dm_channel:
                    try:
                        old_message = await dm_channel.fetch_message(old_dm_id)
                        await old_message.delete()
                    except (discord.NotFound, discord.Forbidden):
                        pass  # Message already deleted or can't access

                # Find the raider who received this DM
                raider = None
                for uid, r in self.raiders.items():
                    user = self.get_user(uid)
                    if user:
                        r_dm = await user.create_dm()
                        if r_dm.id == dm_channel_id:
                            raider = r
                            break

                if (
                    raider
                    and schedule not in raider.denied_runs
                    and schedule not in raider.current_runs
                    and not schedule.has_raider(raider)
                ):
                    # Determine which role is needed
                    role_needed = None
                    if raider.roles[0] in schedule.missing:
                        role_needed = raider.roles[0]
                    elif len(raider.roles) > 1 and raider.roles[1] in schedule.missing:
                        role_needed = raider.roles[1]

                    if role_needed:
                        # Send new DM
                        dm_channel = await self.get_user(raider.user_id).create_dm()
                        ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
                        new_dm = await dm_channel.send(
                            _dm_reminder(raider.name, schedule.level, ts, schedule.format_dm_roster(), role_needed)
                        )
                        await new_dm.add_reaction("✅")
                        await new_dm.add_reaction("❌")

                        # Update dm_map with new message ID
                        if dm_channel.id not in self.dm_map:
                            self.dm_map[dm_channel.id] = {}
                        self.dm_map[dm_channel.id][new_dm.id] = schedule_id

                        # Update timestamp
                        if dm_channel.id not in self.dm_timestamps:
                            self.dm_timestamps[dm_channel.id] = {}
                        self.dm_timestamps[dm_channel.id][new_dm.id] = now

                        logger.info(f"Retried DM for {raider.name} for schedule {schedule_id}")

            except discord.Forbidden:
                logger.warning(f"Could not retry DM for schedule {schedule_id}")
            except Exception as e:
                logger.error(f"Error retrying DM: {e}")

        # Clean up old DM references
        for dm_channel_id, dm_message_id in dms_to_delete:
            if dm_channel_id in self.dm_map and dm_message_id in self.dm_map[dm_channel_id]:
                del self.dm_map[dm_channel_id][dm_message_id]
            if dm_channel_id in self.dm_timestamps and dm_message_id in self.dm_timestamps[dm_channel_id]:
                del self.dm_timestamps[dm_channel_id][dm_message_id]

        if dms_to_retry:
            save_state(
                self.raiders,
                self.schedules,
                self.availability,
                self.availability_message_id,
                self.dm_map,
                self.dm_timestamps,
            )

    async def check_schedule_conflicts(self):
        """Check for multiple unfilled schedules at the same time and notify coordinator."""
        now = datetime.now(timezone.utc)
        time_slots = {}

        # Group unfilled schedules by their start time
        for schedule_id, schedule in self.schedules.items():
            if not schedule.is_filled():
                time_key = (schedule.date_scheduled.date(), schedule.start_time.hour)
                if time_key not in time_slots:
                    time_slots[time_key] = []
                time_slots[time_key].append((schedule_id, schedule))

        # Only flag conflicts where ALL three conditions hold:
        # 1. Multiple unfilled schedules share the same slot
        # 2. Their combined rosters cover every required role (>= 1 tank, >= 1 healer, >= 3 DPS)
        # 3. The run starts within 5 hours
        conflicts = {}
        for time_key, schedules in time_slots.items():
            if len(schedules) <= 1:
                continue

            _, first = schedules[0]
            seconds_until = (first.start_time.astimezone(timezone.utc) - now).total_seconds()
            if seconds_until < 0 or seconds_until > 3600 * 5:
                continue

            combined_tanks = sum(1 for _, s in schedules if s.team["tank"])
            combined_healers = sum(1 for _, s in schedules if s.team["healer"])
            combined_dps = sum(len(s.team["dps"]) for _, s in schedules)
            if combined_tanks >= 1 and combined_healers >= 1 and combined_dps >= 3:
                conflicts[time_key] = schedules

        if conflicts:
            try:
                coordinator = await self.fetch_user(COORDINATOR_ID)
                dm_channel = await coordinator.create_dm()

                conflict_message = "⚠️ **Schedule Conflicts Detected** ⚠️\n\n"
                conflict_message += "Multiple unfilled runs are scheduled at the same time:\n\n"

                for time_key, schedules in conflicts.items():
                    date, hour = time_key
                    conflict_message += f"**{date.strftime('%A, %B %d')} at {hour:02d}:00**\n"

                    for schedule_id, schedule in schedules:
                        missing_roles = ", ".join(schedule.missing)
                        filled = f"{schedule.signup}/5"
                        conflict_message += f"  • Level {schedule.level} - {filled} filled - Missing: {missing_roles}\n"
                        conflict_message += f"    [Jump to message](https://discord.com/channels/{self.get_channel(KEY_CHANNEL_ID).guild.id}/{KEY_CHANNEL_ID}/{schedule_id})\n"

                    conflict_message += "\n"

                conflict_message += "Please manually resolve these conflicts by consolidating or rescheduling runs."

                await dm_channel.send(dedent(conflict_message))
                logger.info(f"Notified coordinator about {len(conflicts)} scheduling conflict(s)")

            except discord.Forbidden:
                logger.warning(f"Could not DM coordinator (ID: {COORDINATOR_ID})")
            except discord.NotFound:
                logger.error(f"Coordinator user not found (ID: {COORDINATOR_ID})")

    async def new_availability_signup_fill_schedule(self, raider: Raider, tier: str):
        """DM the raider to ask if they can fill unfilled schedules based on tier and primary role."""
        # Determine the raider's availability tier (highest priority)
        raider_tier = tier
        tier_priority = {GREEN: 1, YELLOW: 2, RED: 3}

        if raider_tier is None or raider_tier == RED:
            return  # Raider not in any availability list

        # For each unfilled schedule
        for schedule_id, schedule in self.schedules.items():
            if schedule.is_filled():
                continue

            if schedule.is_past():
                continue

            # If hasn't been listed for an hour yet don't ask
            if (datetime.now(timezone.utc) - schedule.posted).total_seconds() < 3600:
                continue

            # Check if we should ask this raider based on schedule's tier_reached
            schedule_tier_priority = tier_priority[schedule.tier_reached]
            raider_tier_priority = tier_priority[raider_tier]

            # Ask if raider is at least as available as the schedule's current tier
            if (
                raider_tier_priority <= schedule_tier_priority
                and raider.check_availability(schedule)
                and schedule not in raider.denied_runs
                and schedule not in raider.current_runs
                and not schedule.has_raider(raider)
            ):
                # Check if raider's primary role can fill a missing spot
                primary_role = raider.roles[0]

                if primary_role and primary_role in schedule.missing:
                    # DM the raider
                    try:
                        dm_channel = await self.get_user(raider.user_id).create_dm()
                        ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
                        dm = await dm_channel.send(
                            _dm_spot_available(
                                raider.name, schedule.level, ts, schedule.format_dm_roster(), primary_role
                            )
                        )
                        await dm.add_reaction("✅")
                        await dm.add_reaction("❌")

                        # Track timestamp
                        if dm_channel.id not in self.dm_timestamps:
                            self.dm_timestamps[dm_channel.id] = {}
                        self.dm_timestamps[dm_channel.id][dm.id] = datetime.now(timezone.utc)

                        if dm_channel.id not in self.dm_map:
                            self.dm_map[dm_channel.id] = {}
                        self.dm_map[dm_channel.id][dm.id] = schedule_id
                    except discord.Forbidden:
                        logger.warning(f"Could not DM {raider.name} for schedule {schedule_id}")
            elif not schedule.primary and len(raider.roles) > 1:
                # Check if raider's secondary role can fill a missing spot
                secondary_role = raider.roles[1]

                if (
                    secondary_role
                    and secondary_role in schedule.missing
                    and raider.check_availability(schedule)
                    and not schedule.has_raider(raider)
                ):
                    # DM the raider
                    try:
                        dm_channel = await self.get_user(raider.user_id).create_dm()
                        ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
                        dm = await dm_channel.send(
                            _dm_spot_available(
                                raider.name,
                                schedule.level,
                                ts,
                                schedule.format_dm_roster(),
                                secondary_role,
                                "secondary",
                            )
                        )
                        await dm.add_reaction("✅")
                        await dm.add_reaction("❌")

                        # Track timestamp
                        if dm_channel.id not in self.dm_timestamps:
                            self.dm_timestamps[dm_channel.id] = {}
                        self.dm_timestamps[dm_channel.id][dm.id] = datetime.now(timezone.utc)

                        if dm_channel.id not in self.dm_map:
                            self.dm_map[dm_channel.id] = {}
                        self.dm_map[dm_channel.id][dm.id] = schedule_id
                    except discord.Forbidden:
                        logger.warning(f"Could not DM {raider.name} for schedule {schedule_id}")

    async def fill_remaining_spots(self, schedule_id: int):
        """Background task to fill remaining schedule spots from available raiders, asking all eligible in a tier in parallel."""
        await asyncio.sleep(3600)  # Wait 1 hour before DMing to allow manual signups first

        schedule = self.schedules.get(schedule_id)

        if not schedule:
            return  # Schedule no longer exists

        if schedule.is_past():
            return  # Schedule has already started

        tier = schedule.tier_reached
        primary = schedule.primary

        if schedule.is_filled():
            return  # Schedule already filled

        channel = self.get_channel(KEY_CHANNEL_ID)
        if not channel:
            return  # Channel no longer accessible

        # For each user in the tier if their role fits an open spot, DM them to ask if they want to join
        for raider in self.availability[tier]:
            if not raider.check_availability(schedule):
                continue  # Raider not available for this schedule

            if schedule in raider.denied_runs or schedule in raider.current_runs or schedule.has_raider(raider):
                continue  # Raider has previously denied this schedule or is already in it

            if primary:
                if raider.roles[0] in schedule.missing and raider.check_availability(schedule):
                    try:
                        dm_channel = await self.get_user(raider.user_id).create_dm()
                        ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
                        dm = await dm_channel.send(
                            _dm_spot_available(
                                raider.name, schedule.level, ts, schedule.format_dm_roster(), raider.roles[0]
                            )
                        )
                        await dm.add_reaction("✅")
                        await dm.add_reaction("❌")

                        # Track timestamp
                        if dm_channel.id not in self.dm_timestamps:
                            self.dm_timestamps[dm_channel.id] = {}
                        self.dm_timestamps[dm_channel.id][dm.id] = datetime.now(timezone.utc)

                        if dm_channel.id not in self.dm_map:
                            self.dm_map[dm_channel.id] = {}
                        self.dm_map[dm_channel.id][dm.id] = schedule_id
                    except discord.Forbidden:
                        logger.warning(f"Could not DM {raider.name} for schedule {schedule_id}")
            else:
                if len(raider.roles) > 1 and raider.roles[1] in schedule.missing and not schedule.has_raider(raider):
                    try:
                        dm_channel = await self.get_user(raider.user_id).create_dm()
                        ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
                        dm = await dm_channel.send(
                            _dm_spot_available(
                                raider.name,
                                schedule.level,
                                ts,
                                schedule.format_dm_roster(),
                                raider.roles[1],
                                "secondary",
                            )
                        )
                        await dm.add_reaction("✅")
                        await dm.add_reaction("❌")

                        # Track timestamp
                        if dm_channel.id not in self.dm_timestamps:
                            self.dm_timestamps[dm_channel.id] = {}
                        self.dm_timestamps[dm_channel.id][dm.id] = datetime.now(timezone.utc)

                        if dm_channel.id not in self.dm_map:
                            self.dm_map[dm_channel.id] = {}
                        self.dm_map[dm_channel.id][dm.id] = schedule_id
                    except discord.Forbidden:
                        logger.warning(f"Could not DM {raider.name} for schedule {schedule_id}")

        # Update schedule parameters
        if tier == GREEN and primary:
            schedule.primary = False
        elif tier == GREEN and not primary:
            schedule.primary = True
            schedule.tier_reached = YELLOW
        elif tier == YELLOW and primary:
            schedule.primary = False
        else:
            schedule.tier_reached = RED

        self.schedules[schedule_id] = schedule
        save_state(
            self.raiders,
            self.schedules,
            self.availability,
            self.availability_message_id,
            self.dm_map,
            self.dm_timestamps,
        )

    async def _notify_schedule_changed(self, schedule: Schedule, message: str, organizer_id: int):
        """DM all schedule members except the organizer about a change or cancellation."""
        for raider in list(schedule.members):
            if raider.user_id == organizer_id:
                continue
            try:
                user = self.get_user(raider.user_id)
                if user:
                    await user.send(message)
            except discord.Forbidden:
                logger.warning("Could not notify %s of schedule change", raider.name)

    async def _notify_displaced(self, displaced: Raider, schedule: Schedule, role: str):
        """DM a raider who was bumped from a slot by a main-role signup."""
        try:
            ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
            user = self.get_user(displaced.user_id)
            if user:
                await user.send(
                    f"⚠️ **You've been removed from a run**\n\n"
                    f"A player signed up for **{role.title()}** as their main role, "
                    f"replacing you (who was filling that slot as a secondary role).\n"
                    f"**Run:** Level {schedule.level} on <t:{ts}:F>\n\n"
                    f"You may still sign up for another available role if one is open."
                )
        except discord.Forbidden:
            logger.warning(f"Could not notify displaced raider {displaced.name}")

    async def notify_schedule(self, schedule: Schedule):
        """DM all members of a filled schedule (tank, healer, dps) with the day of week and time in their timezone."""
        members = []
        if schedule.team["tank"]:
            members.append(schedule.team["tank"])
        if schedule.team["healer"]:
            members.append(schedule.team["healer"])
        for dps in schedule.team["dps"]:
            if dps:
                members.append(dps)

        for raider in members:
            try:
                # Format time in raider's timezone
                dt = int(schedule.start_time.astimezone(timezone.utc).timestamp())
                user = self.get_user(raider.user_id)
                if not user:
                    continue
                dm_channel = await user.create_dm()
                if schedule.is_filled():
                    await dm_channel.send(
                        dedent(
                            f"""
                        Your Mythic+ run (Level {schedule.level}) is now **filled**!
                        Date: <t:{dt}:F>
                        {schedule.format_dm_roster()}
                        See you there!
                        """
                        )
                    )
                else:
                    await dm_channel.send(
                        dedent(
                            f"""
                        Your Mythic+ run (Level {schedule.level}) is no longer **filled**!
                        Date: <t:{dt}:F>
                        """
                        )
                    )
            except discord.HTTPException as e:
                logger.warning(f"Could not DM {raider.name} for filled schedule: {e}")

    async def _post_avail_message(self) -> None:
        """Post a fresh availability message and add emoji reactions. Sets availability_message_id."""
        channel = self.get_channel(AVAIL_CHANNEL_ID)
        if not channel:
            logger.warning("_post_avail_message: AVAIL_CHANNEL not found")
            return
        date_start = datetime.now(timezone.utc)
        date_end = (date_start + timedelta(days=7)).timestamp()
        msg = await channel.send(
            dedent(
                AVAILABILITY_MESSAGE(
                    date_start.timestamp(), date_end, self.get_guild(GUILD_ID).get_role(MYTHIC_PLUS_ID).mention
                )
            )
        )
        self.availability_message_id = msg.id
        await msg.add_reaction(GREEN)
        await msg.add_reaction(YELLOW)
        await msg.add_reaction(RED)

    # ---------------------------
    # Hourly Task
    # ---------------------------

    @tasks.loop(hours=1)
    async def hourly_check(self):
        """Background task that runs every hour. Makes sure to fill schedules and perform reminders"""

        # Ensure bot is fully logged in before running
        if not self.is_ready():
            logger.warning("hourly_check: Bot not ready yet, skipping this iteration")
            return

        # Clean up past schedules and their associated DMs
        now = datetime.now(timezone.utc)
        past_schedule_ids = {sid for sid, s in self.schedules.items() if s.start_time.astimezone(timezone.utc) < now}

        # Record completed runs to the event log before their schedules are removed
        for _sid in past_schedule_ids:
            _sched = self.schedules.get(_sid)
            if _sched is None:
                continue
            _roster = []
            if _sched.team["tank"]:
                _roster.append(_sched.team["tank"].user_id)
            if _sched.team["healer"]:
                _roster.append(_sched.team["healer"].user_id)
            _roster.extend(r.user_id for r in _sched.team["dps"])
            eventlog.log_event(
                "run_completed",
                ts_utc=_sched.start_time,
                run_id=_sid,
                level=_sched.level,
                run_type=_sched.run_type,
                roster=_roster,
            )

        # Delete past schedule messages and their reminder messages from the key channel
        if past_schedule_ids:
            channel = self.get_channel(KEY_CHANNEL_ID)
            if channel:
                for sid in past_schedule_ids:
                    try:
                        msg = await channel.fetch_message(sid)
                        await msg.delete()
                    except (discord.NotFound, discord.Forbidden):
                        pass
                for sid in past_schedule_ids:
                    for msg_id in self.reminder_messages.pop(sid, []):
                        try:
                            msg = await channel.fetch_message(msg_id)
                            await msg.delete()
                        except (discord.NotFound, discord.Forbidden):
                            pass

        # Remove past schedules
        self.schedules = {sid: s for sid, s in self.schedules.items() if sid not in past_schedule_ids}

        # Delete DM messages for past schedules
        for dm_channel_id, dm_messages in list(self.dm_map.items()):
            dm_channel = self.get_channel(dm_channel_id)
            if not dm_channel:
                try:
                    dm_channel = await self.fetch_channel(dm_channel_id)
                except (discord.NotFound, discord.Forbidden):
                    dm_channel = None
            if dm_channel:
                for dm_message_id, schedule_id in list(dm_messages.items()):
                    if schedule_id in past_schedule_ids:
                        try:
                            msg = await dm_channel.fetch_message(dm_message_id)
                            await msg.delete()
                        except (discord.NotFound, discord.Forbidden):
                            pass

        # Clean up DM references
        for dm_channel_id in list(self.dm_map.keys()):
            # Remove DMs for past schedules
            self.dm_map[dm_channel_id] = {
                mid: sid for mid, sid in self.dm_map[dm_channel_id].items() if sid not in past_schedule_ids
            }
            if dm_channel_id in self.dm_timestamps:
                self.dm_timestamps[dm_channel_id] = {
                    mid: ts
                    for mid, ts in self.dm_timestamps[dm_channel_id].items()
                    if mid in self.dm_map[dm_channel_id]
                }

            # Remove empty channels
            if not self.dm_map[dm_channel_id]:
                del self.dm_map[dm_channel_id]
            if dm_channel_id in self.dm_timestamps and not self.dm_timestamps[dm_channel_id]:
                del self.dm_timestamps[dm_channel_id]

        await self.retry_unanswered_dms()
        await self.check_schedule_conflicts()

        save_state(
            self.raiders,
            self.schedules,
            self.availability,
            self.availability_message_id,
            self.dm_map,
            self.dm_timestamps,
        )

        for schedule_id, schedule in list(self.schedules.items()):
            # Check if schedule is filled
            if not schedule.is_filled():
                # Check if schedule needs to be reposted after 24 hours of not being filled
                time_passed = (datetime.now(timezone.utc) - schedule.posted).total_seconds()
                if time_passed >= 86400:  # 24 hours
                    new_schedule_id, new_schedule = await self.repost_schedule(schedule, schedule_id)
                    del self.schedules[schedule_id]
                    self.schedules[new_schedule_id] = new_schedule
                    schedule_id = new_schedule_id
                    schedule = new_schedule
                # Check if schedule needs to be filled
                if schedule_id in self.schedules:
                    if schedule.asks >= 5 and schedule.tier_reached != RED:
                        schedule.asks = 0
                        await self.fill_remaining_spots(schedule_id)
                    elif schedule.asks < 5:
                        self.schedules[schedule_id].asks += 1

            # Check for reminders
            if schedule.is_filled():
                now = datetime.now(timezone.utc)
                difference = (schedule.start_time.astimezone(timezone.utc) - now).total_seconds()
                if difference <= 3600 * 2:
                    channel = self.get_channel(KEY_CHANNEL_ID)
                    reminder_msg = await channel.send(
                        f"{schedule.send_reminder()} in {difference // 3600} hours and {(difference % 3600) // 60} minutes."
                    )
                    self.reminder_messages.setdefault(schedule_id, []).append(reminder_msg.id)

    @tasks.loop(hours=1)
    async def price_watch_check(self):
        """Hourly sweep of watched commodities; DMs the banker on a buy signal."""
        if not self.is_ready():
            logger.warning("price_watch_check: Bot not ready yet, skipping this iteration")
            return

        watches = self.watchlist.all()
        if not watches:
            return

        now = datetime.now(timezone.utc)
        # Only alert during waking hours (10 AM–11:59 PM CST). Outside the window we skip the
        # alert/state transition so a still-good dip re-fires on the next in-window check.
        alert_ok = watchlist.in_alert_window(datetime.now(_CST))
        banker = None
        async with aiohttp.ClientSession() as session:
            for watch in watches:
                try:
                    now_result = await undermine.fetch_now(session, watch.item_id)
                    if now_result is None:
                        continue
                    daily = await undermine.fetch_daily(session, watch.item_id)
                    target = watch.target_qty or BANKER_BULK_QTY
                    signal = watchlist.evaluate(
                        now_result.price, now_result.quantity, daily, watch, now_result.auctions, target
                    )
                    if alert_ok and watchlist.process_signal(watch, signal, now):
                        if banker is None:
                            banker = await self.fetch_user(BANKER_ID)
                        try:
                            await banker.send(watchlist.format_alert(watch, signal, BANKER_BUDGET_COPPER))
                        except discord.HTTPException:
                            logger.warning("price_watch_check: could not DM banker for item %s", watch.item_id)
                    watchlist.auto_tune(watch, now)
                except Exception as exc:  # noqa: BLE001 - one bad item must not kill the sweep
                    logger.warning("price_watch_check failed for item %s: %s", watch.item_id, exc)

        self.watchlist.save()

    @tasks.loop(minutes=TOKEN_POLL_MINUTES)
    async def token_watch_check(self):
        """Poll the WoW Token price; DM the banker when it rises past their sell threshold."""
        if not self.is_ready():
            logger.warning("token_watch_check: Bot not ready yet, skipping this iteration")
            return
        if self.token_watch.threshold is None:
            return

        try:
            async with aiohttp.ClientSession() as session:
                result = await self.blizzard.token_price(session)
            if result is None:
                logger.warning("token_watch_check: token payload carried no price, skipping")
                return
            price, _updated = result

            # Price fell back under the threshold: reset the ratchet so the next
            # crossing alerts again. Guarded so a quiet sub-threshold price does
            # not rewrite the state file every 20 minutes.
            if tokenwatch.should_rearm(self.token_watch, price):
                self.token_watch.last_alert = None
                self.token_watch.save()
                return

            report = tokenwatch.evaluate(self.token_watch, price)
            if report is None:
                return

            banker = await self.fetch_user(BANKER_ID)
            try:
                await banker.send(
                    tokenwatch.format_alert(report, self.token_watch.threshold, self.token_watch.last_alert)
                )
            except discord.HTTPException:
                logger.warning("token_watch_check: could not DM banker; not advancing the ratchet")
                return

            # Commit the ratchet only after the DM actually landed, so a Discord
            # failure cannot silently swallow an alert the owner never saw.
            self.token_watch.last_alert = report
            self.token_watch.save()
        except Exception as exc:  # noqa: BLE001 - a bad poll must not kill the loop
            logger.warning("token_watch_check failed: %s", exc)

    @tasks.loop(minutes=SNIPE_SWEEP_MINUTES)
    async def auction_snipe_check(self):
        """Sweep every region realm; DM subscribers when a snipe's cheapest price beats target."""
        if not self.is_ready():
            logger.warning("auction_snipe_check: not ready, skipping")
            return
        keys = self.snipelist.watched_keys()
        if not keys:
            return

        async with aiohttp.ClientSession() as session:
            try:
                now = datetime.now(timezone.utc)
                stale = (
                    self._snipe_realm_ids_fetched is None
                    or (now - self._snipe_realm_ids_fetched) >= timedelta(hours=24)
                )
                if not self._snipe_realm_ids or stale:
                    self._snipe_realm_ids = await self.blizzard.list_connected_realms(session)
                    self._snipe_realm_ids_fetched = now
            except Exception as exc:  # noqa: BLE001
                logger.warning("auction_snipe_check: realm list failed: %s", exc)
                return

            # Refresh each realm's watched-item prices (conditional; 304 -> reuse cache).
            for realm_id in self._snipe_realm_ids:
                try:
                    result = await self.blizzard.get_realm_auctions(
                        session, realm_id, self._snipe_realm_modified.get(realm_id)
                    )
                    if result is blizzard.NOT_MODIFIED:
                        continue
                    auctions, last_modified = result
                    prices = {}
                    for kind, key_id in keys:
                        bp = snipelist_mod.best_price_for(auctions, kind, key_id)
                        if bp is not None:
                            prices[(kind, key_id)] = bp
                    self._snipe_price_cache[realm_id] = prices
                    self._snipe_realm_modified[realm_id] = last_modified
                except Exception as exc:  # noqa: BLE001 - one bad realm must not kill the sweep
                    logger.warning("auction_snipe_check: realm %s failed: %s", realm_id, exc)

            # Aggregate cheapest-anywhere per key and decide alerts.
            for snipe in self.snipelist.all():
                try:
                    key = (snipe.kind, snipe.key_id)
                    realm_prices = {
                        rid: prices[key] for rid, prices in self._snipe_price_cache.items() if key in prices
                    }
                    best = snipelist_mod.cheapest(realm_prices)
                    plans = snipelist_mod.plan_alerts(snipe, best, BANKER_ID)
                    if not plans or best is None:
                        continue
                    realm = await self.blizzard.realm_name(session, best[2])
                    for plan in plans:
                        await self._send_snipe_dm(session, snipe, best, realm, plan)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("auction_snipe_check: snipe %s failed: %s", snipe.key_id, exc)

        self.snipelist.save()

    async def _send_snipe_dm(self, session, snipe, best, realm, plan):
        try:
            user = await self.fetch_user(plan.recipient_id)
            if plan.is_banker:
                wanters = [(f"<@{uid}>", s.target_copper) for uid, s in snipe.subscribers.items()]
                await user.send(snipelist_mod.format_banker_alert(snipe, best, realm, wanters))
            else:
                await user.send(snipelist_mod.format_alert(snipe, best, realm, plan.target_copper))
        except Exception as exc:  # noqa: BLE001 - one bad DM must not abort the fan-out
            logger.warning("auction_snipe_check: could not DM %s: %s", plan.recipient_id, exc)

    @tasks.loop(hours=24)
    async def raiderio_harvest(self):
        """Daily Raider.io backfill/harvest: append new raiderio_run events (idempotent)."""
        if not self.is_ready():
            logger.warning("raiderio_harvest: Bot not ready yet, skipping this iteration")
            return
        if not self._char_mappings:
            return
        try:
            async with aiohttp.ClientSession() as session:
                added = await raiderio.harvest(self.raiders, session, self._char_mappings)
            logger.info("raiderio_harvest: appended %d new run events", added)
        except Exception as exc:  # noqa: BLE001 - harvest must never kill the loop
            logger.warning("raiderio_harvest failed: %s", exc)

    @raiderio_harvest.before_loop
    async def before_raiderio_harvest(self):
        await self.wait_until_ready()

    # ---------------------------
    # Weekly Availability Reset
    # ---------------------------

    @tasks.loop(time=time(hour=12, minute=0, tzinfo=_CST))
    async def weekly_avail_reset(self):
        """Reset the availability message every Tuesday at noon CST."""
        if datetime.now(_CST).weekday() != 1:  # 1 = Tuesday
            return

        avail_channel = self.get_channel(AVAIL_CHANNEL_ID)

        # Delete the old availability message
        if avail_channel and self.availability_message_id:
            try:
                old_msg = await avail_channel.fetch_message(self.availability_message_id)
                await old_msg.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

        # Reset availability state and post fresh message
        self.availability = {GREEN: [], YELLOW: [], RED: []}
        self.availability_message_id = None
        await self._post_avail_message()
        logger.info("weekly_avail_reset: availability reset and new message posted")

        save_state(
            self.raiders,
            self.schedules,
            self.availability,
            self.availability_message_id,
            self.dm_map,
            self.dm_timestamps,
        )

    # ---------------------------
    # Weekly Forecast Dry-Run Preview
    # ---------------------------

    @tasks.loop(time=time(hour=12, minute=0, tzinfo=_CST))
    async def forecast_preview(self):
        """Wednesday-noon-CST dry-run: predict the best run from the green pool, DM BANKER_ID."""
        if datetime.now(_CST).weekday() != 2:  # 2 = Wednesday (day after the Tuesday availability post)
            return
        if not self.is_ready():
            return

        green = list(self.availability.get(GREEN, []))
        if not forecast.can_field_team(green):
            logger.info("forecast_preview: green pool cannot field a role-valid team; skipping")
            return

        try:
            now = datetime.now(timezone.utc)
            events = eventlog.read_events()
            obs = forecast.observations(events, self.raiders, now)
            obs_by_user = {}
            for o in obs:
                obs_by_user.setdefault(o.user_id, []).append(o)
            ranked = forecast.rank_slots(green, obs_by_user, datetime.now(_CST))
            text = forecast.format_preview(ranked)
            banker = await self.fetch_user(BANKER_ID)
            await banker.send(text)
            logger.info("forecast_preview: sent dry-run preview to banker")
        except discord.HTTPException as exc:
            logger.warning("forecast_preview: could not DM banker: %s", exc)
        except Exception as exc:  # noqa: BLE001 - preview must never kill the loop
            logger.warning("forecast_preview failed: %s", exc)

    # ---------------------------
    # Key Request Flow
    # ---------------------------
    async def _ask_organizer_role(self, user: discord.User, raider) -> Optional[str]:
        """Ask a multi-role raider which role they want to fill as run organizer.

        Returns the selected role string, or None if they only have one role or timed out.
        """
        if len(raider.roles) <= 1:
            return None
        role_view = RoleSelectView(raider.roles, timeout=60)
        await user.send("Which role would you like to fill for this run?", view=role_view)
        await role_view.wait()
        if not role_view.selected_role:
            await user.send("⚠️ No role selected. Key request cancelled.")
        return role_view.selected_role

    async def _do_key_request_flow(self, user: discord.User):
        """Run the full key request flow via DM for the given user."""
        if user.id not in self.raiders:
            try:
                selection_view = WoWSelectionView(timeout=300)
                await user.send(
                    "Before scheduling a key, please choose your **World of Warcraft class** and **roles** if you have only one role please ignore the secondary selection:",
                    view=selection_view,
                )
                await selection_view.wait()

                roles = []
                if selection_view.selected_primary:
                    roles.append(selection_view.selected_primary)
                if (
                    selection_view.selected_secondary
                    and selection_view.selected_secondary != selection_view.selected_primary
                ):
                    roles.append(selection_view.selected_secondary)

                if selection_view.selected_class and roles:
                    self.raiders[user.id] = Raider(
                        user, selection_view.selected_class, roles, selection_view.selected_timezone
                    )
                    logger.info(
                        f"New raider from key request: {user}: class={selection_view.selected_class} roles={roles} timezone={selection_view.selected_timezone}"
                    )
                    save_state(
                        self.raiders,
                        self.schedules,
                        self.availability,
                        self.availability_message_id,
                        self.dm_map,
                        self.dm_timestamps,
                    )
                else:
                    await user.send("Registration cancelled or incomplete. Please try again.")
                    logger.info(f"No valid selection from {user} (timed out or incomplete)")
                    return
            except discord.Forbidden:
                logger.warning(f"Could not DM {user} for class/role selection")
                return

        try:
            raider = self.raiders[user.id]
            view = KeyRequestView(timezone_str=str(raider.timezone), timeout=300)
            await user.send(
                "Request a key: Select Level, Date, Time and How many Keys you are wanting to do.", view=view
            )
            await view.wait()

            if view.selected_level and view.selected_day and view.selected_start_time and view.run_type:
                logger.info(
                    f"Key request from {user}: Day={view.selected_day}, {view.selected_level}, {view.selected_start_time.strftime('%H:%M')}, {view.run_type}"
                )

                raider = self.raiders[user.id]
                temp_schedule = Schedule(
                    raider_scheduled=raider,
                    level=view.selected_level,
                    date_scheduled=view.selected_day,
                    start_time=view.selected_start_time,
                    run_type=view.run_type,
                )
                temp_schedule.raider_remove(raider)

                if not raider.check_availability(temp_schedule):
                    conflict_reason = raider.get_schedule_conflict_reason(temp_schedule)
                    await user.send(f"⚠️ **Cannot create schedule**\n\n{conflict_reason}")
                    logger.info(f"Key request denied for {user} due to schedule conflict")
                    return

                existing_schedule, existing_schedule_id = None, None
                for sched_id, sched in self.schedules.items():
                    if (
                        sched.date_scheduled == view.selected_day
                        and sched.start_time.time() == view.selected_start_time.time()
                    ):
                        existing_schedule = sched
                        existing_schedule_id = sched_id
                        break

                if existing_schedule and sum(role in existing_schedule.missing for role in self.raiders[user.id].roles):
                    dm = await user.send(
                        f"A run already exists at this time (Level {existing_schedule.level}). Would you like to join that run instead?\nReact with ✅ to join, or ❌ to list your key request."
                    )
                    await dm.add_reaction("✅")
                    await dm.add_reaction("❌")

                    def check(reaction, u):
                        return u == user and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == dm.id

                    try:
                        reaction, _ = await self.wait_for("reaction_add", timeout=120.0, check=check)
                        if str(reaction.emoji) == "✅":
                            if not self.raiders[user.id].check_availability(existing_schedule):
                                conflict_reason = self.raiders[user.id].get_schedule_conflict_reason(existing_schedule)
                                await user.send(f"⚠️ **Cannot join this run**\n\n{conflict_reason}")
                                return

                            existing_schedule.raider_signup(self.raiders[user.id])
                            self.raiders[user.id].add_run(existing_schedule)
                            await user.send(
                                f"✅ You have been added to the existing run on {existing_schedule.date_scheduled} at {existing_schedule.start_time.strftime('%I:%M %p')}."
                            )
                            if existing_schedule.is_filled():
                                await self.notify_schedule(existing_schedule)
                            msg = await self.get_channel(KEY_CHANNEL_ID).fetch_message(existing_schedule_id)
                            embed, view_buttons, content = existing_schedule.send_message(self.role_mentions, self)
                            await msg.edit(content=content if content else None, embed=embed, view=view_buttons)
                            save_state(
                                self.raiders,
                                self.schedules,
                                self.availability,
                                self.availability_message_id,
                                self.dm_map,
                                self.dm_timestamps,
                            )
                        else:
                            organizer_role = await self._ask_organizer_role(user, self.raiders[user.id])
                            if len(self.raiders[user.id].roles) > 1 and not organizer_role:
                                return
                            schedule = Schedule(
                                raider_scheduled=self.raiders[user.id],
                                level=view.selected_level,
                                date_scheduled=view.selected_day,
                                start_time=view.selected_start_time,
                                run_type=view.run_type,
                                organizer_role=organizer_role,
                            )
                            schedule.note = view.note
                            pre_post_view = PrePostAddRaiderView(
                                schedule, self, self.get_channel(KEY_CHANNEL_ID), timeout=300
                            )
                            await user.send(
                                "➕ Would you like to add any registered raiders before posting? "
                                "Click **Add Raider** to search by display name (up to 4), then click **Post Schedule** to publish.",
                                view=pre_post_view,
                            )
                            await pre_post_view.wait()
                            if pre_post_view.message_id:
                                self.raiders[user.id].add_run(schedule)
                                save_state(
                                    self.raiders,
                                    self.schedules,
                                    self.availability,
                                    self.availability_message_id,
                                    self.dm_map,
                                    self.dm_timestamps,
                                )
                                self.loop.create_task(self.fill_remaining_spots(pre_post_view.message_id))
                            else:
                                for raider in list(schedule.members):
                                    raider.current_runs.discard(schedule)
                                await user.send(
                                    "⚠️ Schedule was not posted (timed out). Please create a new key request."
                                )
                    except asyncio.TimeoutError:
                        await user.send("No response received. Key request cancelled.")
                    return

                # No existing schedule, create new
                organizer_role = await self._ask_organizer_role(user, self.raiders[user.id])
                if len(self.raiders[user.id].roles) > 1 and not organizer_role:
                    return
                schedule = Schedule(
                    raider_scheduled=self.raiders[user.id],
                    level=view.selected_level,
                    date_scheduled=view.selected_day,
                    start_time=view.selected_start_time,
                    run_type=view.run_type,
                    organizer_role=organizer_role,
                )
                schedule.note = view.note
                pre_post_view = PrePostAddRaiderView(schedule, self, self.get_channel(KEY_CHANNEL_ID), timeout=300)
                await user.send(
                    "➕ Would you like to add any registered raiders before posting? "
                    "Click **Add Raider** to search by display name (up to 4), then click **Post Schedule** to publish.",
                    view=pre_post_view,
                )
                await pre_post_view.wait()
                if pre_post_view.message_id:
                    self.raiders[user.id].add_run(schedule)
                    save_state(
                        self.raiders,
                        self.schedules,
                        self.availability,
                        self.availability_message_id,
                        self.dm_map,
                        self.dm_timestamps,
                    )
                    self.loop.create_task(self.fill_remaining_spots(pre_post_view.message_id))
                else:
                    for raider in list(schedule.members):
                        raider.current_runs.discard(schedule)
                    await user.send("⚠️ Schedule was not posted (timed out). Please create a new key request.")
            else:
                logger.info(f"Key request timed out or cancelled for {user}")
        except discord.Forbidden:
            logger.warning(f"Could not DM {user} for key request")

    # ---------------------------
    # Startup
    # ---------------------------
    async def setup_hook(self) -> None:
        """Calls before on ready to set up all environmental variables"""
        from views import ScheduleButtonView

        (
            self.raiders,
            self.schedules,
            self.availability,
            self.availability_message_id,
            self.dm_map,
            self.dm_timestamps,
        ) = load_state()
        self.coordinator_id = COORDINATOR_ID
        self.elevated_ids = ELEVATED_IDS
        self.watchlist = Watchlist.load()
        self.blizzard = blizzard.BlizzardClient()
        self.snipelist = snipelist_mod.Snipelist.load()
        self.token_watch = tokenwatch.TokenWatch.load()
        self._snipe_realm_ids: list = []  # cached connected-realm ids
        self._snipe_price_cache: dict = {}  # realm_id -> {(kind,key_id): (price, qty)}
        self._snipe_realm_modified: dict = {}  # realm_id -> Last-Modified str
        self._snipe_realm_ids_fetched = None
        self._char_mappings = raiderio.load_character_mappings()
        logger.info("Loaded state from file.")

        # Register persistent views so buttons work after bot restarts
        self.add_view(KeyRequestButtonView(self))

        # Re-register each existing schedule's button view so interactions
        # still work after a restart (discord.py routes by message_id)
        for schedule_id, schedule in self.schedules.items():
            self.add_view(ScheduleButtonView(schedule, self), message_id=schedule_id)
        logger.info("Re-registered %d schedule views", len(self.schedules))

        # Start hourly background task
        if not self.hourly_check.is_running():
            self.hourly_check.start()

        # Start weekly availability reset (Tuesdays at noon CST)
        if not self.weekly_avail_reset.is_running():
            self.weekly_avail_reset.start()

        # Start hourly Undermine price-watch sweep
        if not self.price_watch_check.is_running():
            self.price_watch_check.start()

        # Start daily Raider.io harvest (initial backfill runs on first iteration)
        if not self.raiderio_harvest.is_running():
            self.raiderio_harvest.start()

        # Start weekly forecast dry-run preview (Wednesdays at noon CST)
        if not self.forecast_preview.is_running():
            self.forecast_preview.start()

        # Start 30-minute auction snipe sweep
        if not self.auction_snipe_check.is_running():
            self.auction_snipe_check.start()

        # Start 20-minute WoW Token sell-signal poll
        if not self.token_watch_check.is_running():
            self.token_watch_check.start()

    async def on_ready(self):
        """Called when the bot is ready. Loads state from file."""
        logger.info("Logged in as %s", self.user)

        # Get roles for later use
        if not self.role_mentions:
            self.role_mentions["healer"] = self.get_guild(GUILD_ID).get_role(HEALER_ID)
            self.role_mentions["tank"] = self.get_guild(GUILD_ID).get_role(TANK_ID)
            self.role_mentions["dps"] = self.get_guild(GUILD_ID).get_role(DPS_ID)

        # Fetch message history now that channels are cached
        if self.availability_message_id:
            await self.get_message_history()

        # Post changelog if the bot version has changed since last run
        last_version = _read_last_version()
        if last_version != BOT_VERSION:
            changelog_channel = self.get_channel(AVAIL_CHANNEL_ID)
            entries = _read_changelog(BOT_VERSION)
            if changelog_channel and entries:
                changes = "\n".join(f"• {line}" for line in entries)
                changelog_msg = await changelog_channel.send(
                    f"🤖 **Bot updated to v{BOT_VERSION}**\n\n**What's new:**\n{changes}"
                )
                await changelog_msg.pin()
                logger.info("Posted and pinned changelog for v%s", BOT_VERSION)
            _write_version(BOT_VERSION)

        # Ensure the persistent key request button is pinned in KEY_CHANNEL
        key_channel = self.get_channel(KEY_CHANNEL_ID)
        if key_channel:
            pins = await key_channel.pins()
            button_exists = any(p.author.id == self.user.id and p.components for p in pins)
            if not button_exists:
                button_msg = await key_channel.send(
                    "⚔️ **Schedule a Mythic+ Run**\nClick the button below to create a key request.",
                    view=KeyRequestButtonView(self),
                )
                await button_msg.pin()
                logger.info("on_ready: posted and pinned missing key request button in KEY_CHANNEL")

    async def _run_gear_audit(
        self, mappings: List[dict]
    ) -> Tuple[List[gearaudit.CharacterFindings], List[Tuple[str, str]]]:
        """Fetch and audit every mapped character. Returns (findings, failures).

        One character's failure never ends the sweep — it is collected and reported, since a
        404 usually means a rename or transfer the mapping file hasn't caught up with.
        """
        semaphore = asyncio.Semaphore(GEAR_AUDIT_CONCURRENCY)
        findings: List[gearaudit.CharacterFindings] = []
        failures: List[Tuple[str, str]] = []

        async with aiohttp.ClientSession() as session:

            async def audit_one(entry: dict) -> None:
                character = entry.get("character") or ""
                realm_slug = entry.get("realm_slug") or ""
                realm_name = entry.get("realm_name") or realm_slug
                if not character or not realm_slug:
                    # Report it rather than dropping it silently — a half-filled mapping entry is
                    # exactly the kind of drift this command is meant to surface.
                    failures.append((character or "(unnamed entry)", realm_name or "?"))
                    return
                # The semaphore wraps the whole body, not just the equipment fetch: the per-gem
                # item_info lookups are Blizzard calls too, and bounding only the first request
                # would let ~30 characters' worth of gem lookups fan out at once.
                async with semaphore:
                    try:
                        items = await self.blizzard.character_equipment(session, realm_slug, character)
                    except Exception as exc:  # noqa: BLE001 - one bad character must not end the sweep
                        logger.warning("gearaudit: %s/%s failed: %s", realm_slug, character, exc)
                        failures.append((character, realm_name))
                        return
                    if items is None:
                        failures.append((character, realm_name))
                        return
                    gem_quality: Dict[int, str] = {}
                    for gem_id in gearaudit.gem_ids(items):
                        try:
                            gem_quality[gem_id] = (await self.blizzard.item_info(session, gem_id)).quality
                        except Exception as exc:  # noqa: BLE001 - an unknown gem is simply not graded
                            logger.warning("gearaudit: gem %s lookup failed: %s", gem_id, exc)
                    findings.append(gearaudit.audit_character(character, realm_name, items, gem_quality))

            await asyncio.gather(*(audit_one(entry) for entry in mappings))

        return findings, failures

    # ---------------------------
    # Message Listener
    # ---------------------------
    async def on_message(self, message: discord.Message):
        """Handle incoming messages, responding to commands like !avail and !key."""
        if message.author.id == self.user.id:
            return

        if message.content.startswith("!watch ") and message.author.id == BANKER_ID:
            args = message.content.split()[1:]
            if message.guild is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
            parsed = watchlist.parse_watch_command(args)
            if parsed["kind"] == "error":
                reason = parsed.get("reason")
                if reason == "bad_flag":
                    hint = (
                        "❌ A `-x` quantity must be **attached with no space** (e.g. `-x100`, not `-x 100`). "
                        "A spaced `-x` in a multi-item command swallows the next item id."
                    )
                elif reason == "multi_label":
                    hint = (
                        "❌ Labels aren't supported when watching several items at once — "
                        "drop the text, or add that item on its own to give it a label."
                    )
                else:
                    hint = "❌ Give at least one numeric item id."
                await message.author.send(
                    f"{hint}\n"
                    "Usage: `!watch <itemId> [-x<qty>] [label]`  •  several at once: "
                    "`!watch <id1> -x<qty> <id2> -x<qty> ...`\n"
                    "Example: `!watch 241326 -x100 241322 -x100 241324 -x100`"
                )
                return
            # Two or more ids -> watch several at once (auto-labelled), each with an optional -x target.
            if parsed["kind"] == "multi":
                added, already = [], []
                for iid, iqty in parsed["items"]:
                    target = iqty or BANKER_BULK_QTY
                    if self.watchlist.get(iid) is None:
                        self.watchlist.add(iid, f"Item {iid}", iqty)
                        added.append(f"{iid} (bulk {target:,})")
                    else:
                        already.append(str(iid))
                self.watchlist.save()
                reply = []
                if added:
                    reply.append(f"👁️ Now watching {len(added)} item(s): {', '.join(added)}.")
                if already:
                    reply.append(f"Already watching: {', '.join(already)}.")
                await message.author.send("\n".join(reply))
                return
            # Single item, with an optional multi-word label and optional -x bulk target.
            item_id = parsed["item_id"]
            label = parsed["label"] or f"Item {item_id}"
            target_qty = parsed["target_qty"]
            self.watchlist.add(item_id, label, target_qty)
            self.watchlist.save()
            qty_note = f" — bulk target **{target_qty:,}**" if target_qty else ""
            await message.author.send(f"👁️ Now watching **{label}** (item {item_id}){qty_note}.")
            return

        if message.content.startswith("!unwatch ") and message.author.id == BANKER_ID:
            args = message.content.split()[1:]
            if message.guild is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
            if not args or not all(a.isdigit() for a in args):
                await message.author.send("Usage: `!unwatch <itemId> [itemId ...]`")
                return
            removed, missing = [], []
            for a in args:
                iid = int(a)
                (removed if self.watchlist.remove(iid) else missing).append(iid)
            if removed:
                self.watchlist.save()
            reply = []
            if removed:
                reply.append(f"🚫 Stopped watching {len(removed)} item(s): {', '.join(str(i) for i in removed)}.")
            if missing:
                reply.append(f"Not being watched: {', '.join(str(i) for i in missing)}.")
            await message.author.send("\n".join(reply))
            return

        if message.content == "!watches" and message.author.id == BANKER_ID:
            if message.guild is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
            watches = self.watchlist.all()
            if not watches:
                await message.author.send("No items are being watched. Add one with `!watch <itemId> [label]`.")
                return
            lines = []
            async with aiohttp.ClientSession() as session:
                for watch in watches:
                    try:
                        now_result = await undermine.fetch_now(session, watch.item_id)
                        daily = await undermine.fetch_daily(session, watch.item_id)
                        target = watch.target_qty or BANKER_BULK_QTY
                        sig = (
                            watchlist.evaluate(
                                now_result.price, now_result.quantity, daily, watch,
                                now_result.auctions, target,
                            )
                            if now_result
                            else None
                        )
                    except Exception:  # noqa: BLE001 - display best-effort
                        sig = None
                    lines.append(watchlist.format_watch_line(watch, sig))
            await message.author.send("**Watched items:**\n" + "\n".join(lines))
            return

        if message.content.startswith("!gearaudit") and message.author.id in ELEVATED_IDS:
            if message.guild is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
            wanted = message.content[len("!gearaudit"):].strip()
            mappings = raiderio.load_character_mappings()
            if wanted:
                mappings = [m for m in mappings if (m.get("character") or "").lower() == wanted.lower()]
                if not mappings:
                    await message.author.send(f"No mapping for `{wanted}`.")
                    return
            if not mappings:
                await message.author.send("No characters are mapped, so there is nothing to audit.")
                return
            try:
                findings, failures = await self._run_gear_audit(mappings)
            except Exception as exc:  # noqa: BLE001 - a failed audit must not kill on_message
                logger.warning("gearaudit failed: %s", exc)
                await message.author.send("The gear audit failed. Check the logs.")
                return
            for chunk in gearaudit.format_report(findings, failures):
                await message.author.send(chunk)
            return

        if message.content.startswith("!tokenalert") and message.author.id == BANKER_ID:
            if message.guild is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
            parts = message.content.split(maxsplit=1)
            if len(parts) < 2:
                await message.author.send(
                    "Usage: `!tokenalert <gold>` (e.g. `!tokenalert 300000`) or `!tokenalert off`."
                )
                return
            try:
                threshold = tokenwatch.parse_threshold(parts[1])
            except ValueError:
                await message.author.send(
                    f"`{parts[1].strip()}` isn't a valid gold amount. "
                    "Use `!tokenalert 300000`, `!tokenalert 300,000`, or `!tokenalert off`."
                )
                return
            # Setting or clearing a threshold always re-arms, so a new threshold never
            # inherits a stale ratchet position from the previous one.
            self.token_watch.threshold = threshold
            self.token_watch.last_alert = None
            self.token_watch.save()
            if threshold is None:
                await message.author.send("🔕 WoW Token alerts disabled.")
            else:
                await message.author.send(
                    f"💰 WoW Token alert set: I'll DM you when the price rises above "
                    f"**{watchlist.format_gold(threshold)}**."
                )
            return

        if message.content == "!token" and message.author.id == BANKER_ID:
            if message.guild is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
            price = None
            try:
                async with aiohttp.ClientSession() as session:
                    result = await self.blizzard.token_price(session)
                if result is not None:
                    price = result[0]
            except Exception as exc:  # noqa: BLE001 - report state even if the API is down
                logger.warning("!token: could not fetch token price: %s", exc)
            await message.author.send(tokenwatch.format_status(self.token_watch, price))
            return

        if message.content.startswith("!snipe ") and not message.content.startswith("!snipepet "):
            args = message.content.split()[1:]
            if len(args) < 2 or not args[0].isdigit():
                await message.channel.send("Usage: `!snipe <itemId> <maxGold> [label]`")
                return
            item_id = int(args[0])
            target = snipelist_mod.parse_gold(args[1])
            if target is None:
                await message.channel.send("Max price must be a positive number of gold, e.g. `!snipe 194123 5000`.")
                return
            async with aiohttp.ClientSession() as session:
                try:
                    info = await self.blizzard.item_info(session, item_id)
                    label = " ".join(args[2:]) if len(args) > 2 else info.name or f"Item {item_id}"
                    is_recipe = info.is_recipe
                except Exception as exc:  # noqa: BLE001
                    logger.warning("snipe item_info failed for %s: %s", item_id, exc)
                    existing = self.snipelist.get("item", item_id)
                    label = " ".join(args[2:]) if len(args) > 2 else (existing.label if existing else f"Item {item_id}")
                    is_recipe = existing.is_recipe if existing else False
            self.snipelist.subscribe(message.author.id, "item", item_id, target, label, is_recipe)
            self.snipelist.save()
            note = " (recipe — the banker is also alerted)" if is_recipe else ""
            await message.channel.send(
                f"🎯 Sniping **{label}** (item {item_id}) under {snipelist_mod.format_gold(target)}{note}."
            )
            return

        if message.content.startswith("!snipepet "):
            args = message.content.split()[1:]
            if len(args) < 2 or not args[0].isdigit():
                await message.channel.send("Usage: `!snipepet <speciesId> <maxGold> [label]`")
                return
            species_id = int(args[0])
            target = snipelist_mod.parse_gold(args[1])
            if target is None:
                await message.channel.send("Max price must be a positive number of gold.")
                return
            async with aiohttp.ClientSession() as session:
                try:
                    name = await self.blizzard.pet_name(session, species_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("snipe pet_name failed for %s: %s", species_id, exc)
                    name = f"Pet {species_id}"
            label = " ".join(args[2:]) if len(args) > 2 else name
            self.snipelist.subscribe(message.author.id, "pet", species_id, target, label, False)
            self.snipelist.save()
            await message.channel.send(
                f"🎯 Sniping pet **{label}** (species {species_id}) under {snipelist_mod.format_gold(target)}."
            )
            return

        if message.content.startswith("!unsnipe "):
            args = message.content.split()[1:]
            if not args or not all(a.isdigit() for a in args):
                await message.channel.send("Usage: `!unsnipe <id ...>`")
                return
            removed = []
            for a in args:
                iid = int(a)
                # a bare id may be an item or a pet the caller subscribes to; try both
                if self.snipelist.unsubscribe(message.author.id, "item", iid):
                    removed.append(iid)
                elif self.snipelist.unsubscribe(message.author.id, "pet", iid):
                    removed.append(iid)
            if removed:
                self.snipelist.save()
                await message.channel.send(f"🚫 Stopped sniping: {', '.join(str(i) for i in removed)}.")
            else:
                await message.channel.send("You weren't sniping any of those.")
            return

        if message.content == "!snipes":
            snipes = self.snipelist.for_owner(message.author.id)
            if not snipes:
                await message.channel.send("You aren't sniping anything. Add one with `!snipe <itemId> <maxGold>`.")
                return
            lines = [snipelist_mod.format_snipe_line(s, s.subscribers[message.author.id]) for s in snipes]
            await message.author.send("**Your snipes:**\n" + "\n".join(lines))
            return

        if message.content in ("!help", "!tools"):
            embed = discord.Embed(
                title="🤖 Mythic+ Bot — Commands",
                description="Here's everything I can do. Most commands work in a DM or the relevant channel.",
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="📅 Mythic+ Scheduling (anyone)",
                value=(
                    "`!key` — start a key request (in the key channel)\n"
                    "`!keys` — show your scheduled runs\n"
                    "`!modify` — update your class, roles, or timezone"
                ),
                inline=False,
            )
            embed.add_field(
                name="🗓️ Availability & Setup (coordinator/admin)",
                value=(
                    "`!avail` — post the weekly availability message\n"
                    "`!setup` — re-post the key-request button\n"
                    "`!cleanup` — purge the channel and reset state"
                ),
                inline=False,
            )
            embed.add_field(
                name="🔍 Gear Audit (coordinator/admin)",
                value=(
                    "`!gearaudit` — check every mapped character for missing enchants, "
                    "empty sockets, and low-quality enchants/gems\n"
                    "`!gearaudit <character>` — check one character"
                ),
                inline=False,
            )
            embed.add_field(
                name="💰 Price Watch — region commodities (banker)",
                value=(
                    "`!watch <itemId> [-x<qty>] [label]` — watch a commodity; `-x` sets a bulk target\n"
                    "`!watch <id1> <id2> …` — watch several at once\n"
                    "`!unwatch <itemId …>` — stop watching\n"
                    "`!watches` — list your watches"
                ),
                inline=False,
            )
            embed.add_field(
                name="🪙 WoW Token — sell signal (banker)",
                value=(
                    "`!token` — current token price and your alert state\n"
                    "`!tokenalert <gold>` — DM me when the price rises above this\n"
                    "`!tokenalert off` — disable token alerts"
                ),
                inline=False,
            )
            embed.add_field(
                name="🎯 Auction Sniper — per-realm items (anyone)",
                value=(
                    "`!snipe <itemId> <maxGold> [label]` — alert when an item is under your price on any realm\n"
                    "`!snipepet <speciesId> <maxGold> [label]` — same, for a battle pet\n"
                    "`!unsnipe <id …>` — stop sniping\n"
                    "`!snipes` — list your snipes"
                ),
                inline=False,
            )
            embed.add_field(
                name="ℹ️ Help",
                value="`!help` or `!tools` — show this message",
                inline=False,
            )
            await message.channel.send(embed=embed)
            return

        if message.content == "!keys":
            if message.author.id in self.raiders:
                try:
                    await message.author.send(self.raiders[message.author.id].get_current_runs())
                except discord.Forbidden:
                    logger.warning(f"Could not DM {message.author} for current runs")

                try:
                    await message.delete()
                except discord.Forbidden:
                    logger.warning(f"Could not delete !keys commands from {message.author}")

        if message.content == "!modify":
            is_dm = message.guild is None
            is_allowed_channel = not is_dm and message.channel.id in (KEY_CHANNEL_ID, AVAIL_CHANNEL_ID)

            if not (is_dm or is_allowed_channel):
                return

            # Delete the command from the channel (not possible in DMs)
            if not is_dm:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    logger.warning(f"Could not delete !modify message from {message.author}")

            if message.author.id not in self.raiders:
                try:
                    await message.author.send(
                        "❌ You are not registered. Please sign up first via the availability reaction or `!key`."
                    )
                except discord.Forbidden:
                    pass
                return

            try:
                raider = self.raiders[message.author.id]
                selection_view = WoWSelectionView(timeout=180)
                await message.author.send(
                    "✏️ **Modify your registration**\n"
                    "Select your new class, roles, and timezone.\n"
                    "⚠️ You will be removed from all currently scheduled runs.",
                    view=selection_view,
                )

                await selection_view.wait()

                roles = []
                if selection_view.selected_primary:
                    roles.append(selection_view.selected_primary)
                if (
                    selection_view.selected_secondary
                    and selection_view.selected_secondary != selection_view.selected_primary
                ):
                    roles.append(selection_view.selected_secondary)

                if not selection_view.selected_class or not roles or not selection_view.selected_timezone:
                    await message.author.send(
                        "❌ Modification incomplete. Please use `!modify` again and fill out all fields."
                    )
                    return

                # Remove raider from all their current schedules
                schedules_to_delete = []
                key_channel = self.get_channel(KEY_CHANNEL_ID)
                for schedule_id, schedule in list(self.schedules.items()):
                    if schedule in raider.current_runs:
                        fill_status = schedule.is_filled()
                        schedule.raider_remove(raider)

                        if schedule.signup == 0:
                            try:
                                key_msg = await key_channel.fetch_message(schedule_id)
                                await key_msg.delete()
                            except (discord.NotFound, discord.Forbidden):
                                pass
                            schedules_to_delete.append(schedule_id)
                        else:
                            try:
                                key_msg = await key_channel.fetch_message(schedule_id)
                                embed, view_buttons, content = schedule.send_message(self.role_mentions, self)
                                await key_msg.edit(content=content if content else None, embed=embed, view=view_buttons)
                            except (discord.NotFound, discord.Forbidden):
                                pass

                            if schedule.is_filled() != fill_status:
                                await self.notify_schedule(schedule)

                for schedule_id in schedules_to_delete:
                    del self.schedules[schedule_id]

                # Reset run tracking and update profile
                raider.current_runs = set()
                raider.denied_runs = set()
                raider.class_play = selection_view.selected_class
                raider.roles = roles
                raider.timezone = ZoneInfo(selection_view.selected_timezone)

                save_state(
                    self.raiders,
                    self.schedules,
                    self.availability,
                    self.availability_message_id,
                    self.dm_map,
                    self.dm_timestamps,
                )
                logger.info(
                    f"Raider {raider.name} modified: class={raider.class_play} roles={raider.roles} timezone={raider.timezone}"
                )

                roles_display = ", ".join(r.upper() for r in roles)
                await message.author.send(
                    f"✅ Your registration has been updated!\n"
                    f"Class: **{raider.class_play.title()}**\n"
                    f"Roles: **{roles_display}**\n"
                    f"Timezone: **{selection_view.selected_timezone}**"
                )

            except discord.Forbidden:
                logger.warning(f"Could not DM {message.author} for !modify")

        if message.content == "!cleanup" and message.author.id in ELEVATED_IDS:
            if message.channel.id == AVAIL_CHANNEL_ID or message.channel.id == KEY_CHANNEL_ID:
                try:
                    # Purge both channels, skipping pinned messages
                    avail_channel = self.get_channel(AVAIL_CHANNEL_ID)
                    key_channel = self.get_channel(KEY_CHANNEL_ID)
                    avail_pins = {m.id for m in await avail_channel.pins()}
                    key_pins = {m.id for m in await key_channel.pins()}
                    avail_deleted = await avail_channel.purge(check=lambda m: m.id not in avail_pins)
                    key_deleted = await key_channel.purge(check=lambda m: m.id not in key_pins)
                    logger.info(
                        "Cleanup: purged %d messages from AVAIL and %d from KEY channel.",
                        len(avail_deleted),
                        len(key_deleted),
                    )

                    # Reset all state except raiders
                    self.schedules = {}
                    self.availability = {GREEN: [], YELLOW: [], RED: []}
                    self.availability_message_id = None
                    self.dm_map = {}
                    self.dm_timestamps = {}
                    for raider in self.raiders:
                        self.raiders[raider].current_runs = set()
                        self.raiders[raider].denied_runs = set()

                    save_state(
                        self.raiders,
                        self.schedules,
                        self.availability,
                        self.availability_message_id,
                        self.dm_map,
                        self.dm_timestamps,
                    )
                    logger.info("Cleanup: state reset complete. Raiders preserved: %d", len(self.raiders))

                    # Confirm in whichever channel the command was issued
                    await message.author.send(
                        "✅ Cleanup complete. Both channels have been purged and all state (except raiders) has been reset."
                    )
                except discord.Forbidden:
                    logger.warning("Cleanup: missing permissions to purge one or both channels.")
                    await message.author.send("❌ Missing permissions to purge one or both channels.")
                except Exception as exc:
                    logger.exception("Cleanup: unexpected error: %s", exc)
                    await message.author.send("❌ An unexpected error occurred during cleanup.")
            else:
                await message.author.send("❌ `!cleanup` must be used in the availability or key channel.")
            return

        if message.channel.id == AVAIL_CHANNEL_ID or message.channel.id == KEY_CHANNEL_ID:
            if (
                message.content == "!avail"
                and message.author.id in ELEVATED_IDS
                and message.channel.id == AVAIL_CHANNEL_ID
            ):
                await self._post_avail_message()

                try:
                    await message.delete()
                except discord.Forbidden:
                    logger.warning(f"Could not delete !avail commands from {message.author}")

            elif (
                message.content == "!setup"
                and message.author.id in ELEVATED_IDS
                and message.channel.id == KEY_CHANNEL_ID
            ):
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
                channel = self.get_channel(KEY_CHANNEL_ID)
                # Unpin and delete any previous bot button messages to keep channel clean
                for pinned in await channel.pins():
                    if pinned.author.id == self.user.id and pinned.components:
                        await pinned.unpin()
                        await pinned.delete()
                button_msg = await channel.send(
                    "⚔️ **Schedule a Mythic+ Run**\nClick the button below to create a key request.",
                    view=KeyRequestButtonView(self),
                )
                await button_msg.pin()
                logger.info("Setup: posted and pinned key request button in KEY_CHANNEL")

            elif message.content == "!key" and message.channel.id == KEY_CHANNEL_ID:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    logger.warning(f"Could not delete !key message from {message.author}")
                await self._do_key_request_flow(message.author)

            # Delete !key and !avail commands from KEY_CHANNEL
            elif message.content in ["!key", "!avail"] and message.channel.id == KEY_CHANNEL_ID:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    logger.warning(f"Could not delete command from {message.author} (message not found or forbidden)")

    def _log_avail_reaction(self, raider, emoji) -> None:
        """Log an availability-reaction event (best-effort)."""
        now = datetime.now(timezone.utc)
        eventlog.log_event(
            "avail_reaction",
            ts_utc=now,
            user_id=raider.user_id,
            tz=raider.timezone,
            emoji=str(emoji),
            week_of=eventlog.week_of(now),
        )

    # ---------------------------
    # Reaction Listener
    # ---------------------------
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.Member):
        """Handle reaction additions for availability signup or schedule signup."""

        if user.id == self.user.id:
            return

        logger.info(
            f"Reaction added: {user.display_name}, {reaction.emoji} Raider: {user.id in self.raiders} ID in schedules: {reaction.message.id in self.schedules}"
        )

        if (
            reaction.message.channel.id == AVAIL_CHANNEL_ID
            and reaction.message.id == self.availability_message_id
            and reaction.emoji in [GREEN, YELLOW, RED]
        ):
            if user is None:
                return

            if user.id in self.raiders:
                # Check if they're already in any availability list and remove them
                if self.raiders[user.id] in self.availability[GREEN]:
                    self.availability[GREEN].remove(self.raiders[user.id])
                if self.raiders[user.id] in self.availability[YELLOW]:
                    self.availability[YELLOW].remove(self.raiders[user.id])
                if self.raiders[user.id] in self.availability[RED]:
                    self.availability[RED].remove(self.raiders[user.id])

                if self.raiders[user.id] not in self.availability[reaction.emoji]:
                    self.availability[reaction.emoji].append(self.raiders[user.id])
                    await self.new_availability_signup_fill_schedule(self.raiders[user.id], reaction.emoji)

                    self._log_avail_reaction(self.raiders[user.id], reaction.emoji)
            else:
                try:
                    view = WoWSelectionView(timeout=180)  # 3 minutes timeout
                    await user.send(
                        "Choose your **World of Warcraft class** and **roles** if you have only one role please ignore the secondary selection:",
                        view=view,
                    )

                    # wait for the user to click Submit (or timeout)
                    await view.wait()

                    # build roles list from selections
                    roles = []
                    if view.selected_primary:
                        roles.append(view.selected_primary)
                    if view.selected_secondary and view.selected_secondary != view.selected_primary:
                        roles.append(view.selected_secondary)

                    # If user selected a class and at least one role, create a Raider and handle signup
                    if view.selected_class and roles:
                        self.raiders[user.id] = Raider(user, view.selected_class, roles, view.selected_timezone)
                        self.availability[reaction.emoji].append(self.raiders[user.id])
                        logger.info(
                            f"Signup from {user}: class={view.selected_class} roles={roles} timezone={view.selected_timezone}"
                        )

                        await self.new_availability_signup_fill_schedule(self.raiders[user.id], reaction.emoji)

                        self._log_avail_reaction(self.raiders[user.id], reaction.emoji)

                        save_state(
                            self.raiders,
                            self.schedules,
                            self.availability,
                            self.availability_message_id,
                            self.dm_map,
                            self.dm_timestamps,
                        )
                    else:
                        logger.info(f"No valid selection from {user} (timed out or incomplete)")
                except discord.Forbidden:
                    logger.warning(f"Could not DM {user}")

        # DM reactions for schedule signup/removal
        # Note: Main channel uses buttons (handled in on_interaction), not reactions
        elif reaction.message.channel.id in self.dm_map and user.id in self.raiders:
            if reaction.emoji not in ["✅", "❌"]:
                return

            if reaction.emoji == "✅":
                schedule_id = self.dm_map.get(reaction.message.channel.id, {}).get(reaction.message.id)
                if schedule_id is None:
                    return
                raider = self.raiders[user.id]
                schedule = self.schedules.get(schedule_id)
                if schedule is None:
                    return
                eventlog.log_event(
                    "offer_accepted",
                    ts_utc=schedule.start_time,
                    user_id=user.id,
                    tz=raider.timezone,
                    run_id=schedule_id,
                )
                if raider.check_availability(schedule) and schedule not in raider.current_runs:
                    displaced = schedule.try_displace_off_roler(raider, raider.roles[0])
                    if displaced:
                        await self._notify_displaced(displaced, schedule, raider.roles[0])

                    if schedule.is_filled():
                        # Schedule still full after displacement attempt — ask if they want fill spot
                        dm_channel = reaction.message.channel
                        ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
                        dps_names = ", ".join(r.name for r in schedule.team["dps"]) or "None"
                        roster_text = (
                            f"**Current Roster:**\n"
                            f"🛡️ Tank: {schedule.team['tank'].name if schedule.team['tank'] else 'None'}\n"
                            f"💚 Healer: {schedule.team['healer'].name if schedule.team['healer'] else 'None'}\n"
                            f"⚔️ DPS: {dps_names}"
                        )
                        fill_msg = await dm_channel.send(
                            f"⚠️ The Level {schedule.level} run on <t:{ts}:F> is already **full**.\n"
                            f"{roster_text}\n\n"
                            f"Would you like to be added as a **fill** in case someone drops?\n"
                            f"React with ✅ to join as fill or ❌ to decline."
                        )
                        await fill_msg.add_reaction("✅")
                        await fill_msg.add_reaction("❌")

                        def fill_check(r, u):
                            return u == user and str(r.emoji) in ["✅", "❌"] and r.message.id == fill_msg.id

                        try:
                            fill_reaction, _ = await self.wait_for("reaction_add", timeout=120.0, check=fill_check)
                            if str(fill_reaction.emoji) == "✅":
                                schedule.raider_signup(raider)
                                raider.add_run(schedule)
                                await dm_channel.send(
                                    f"✅ You've been added as a fill for the Level {schedule.level} run on "
                                    f"<t:{ts}:F>. You'll be notified if a spot opens up!"
                                )
                                key_msg = await self.get_channel(KEY_CHANNEL_ID).fetch_message(schedule_id)
                                embed, view, content = schedule.send_message(self.role_mentions, self)
                                await key_msg.edit(content=content if content else None, embed=embed, view=view)
                                logger.info("%s added as fill for schedule %s via DM", user, schedule)
                            else:
                                await dm_channel.send("❌ Understood, you won't be added to this run.")
                        except asyncio.TimeoutError:
                            pass
                    else:
                        schedule.raider_signup(raider)
                        raider.add_run(schedule)
                        if schedule.is_filled():
                            await self.notify_schedule(schedule)
                        await self.message_user(raider, reaction.emoji, schedule)
                        message = await self.get_channel(KEY_CHANNEL_ID).fetch_message(schedule_id)
                        embed, view, content = schedule.send_message(self.role_mentions, self)
                        await message.edit(content=content if content else None, embed=embed, view=view)
                        logger.info("%s signed up for schedule %s via DM", user, schedule)
            elif reaction.emoji == "❌":
                schedule_id = self.dm_map.get(reaction.message.channel.id, {}).get(reaction.message.id)
                if schedule_id is None:
                    return
                raider = self.raiders[user.id]
                schedule = self.schedules.get(schedule_id)
                if schedule is None:
                    return
                eventlog.log_event(
                    "offer_declined",
                    ts_utc=schedule.start_time,
                    user_id=user.id,
                    tz=raider.timezone,
                    run_id=schedule_id,
                )
                if schedule in raider.current_runs:
                    fill_status = schedule.is_filled()
                    schedule.raider_remove(raider)
                    raider.remove_run(schedule)
                    if schedule.is_filled() != fill_status:
                        await self.notify_schedule(schedule)

                    # If schedule is now empty, delete it
                    if schedule.signup == 0:
                        message = await self.get_channel(KEY_CHANNEL_ID).fetch_message(schedule_id)
                        await message.delete()
                        del self.schedules[schedule_id]
                        logger.info("Deleted empty schedule %s", schedule_id)
                    else:
                        # Update the message with new roster
                        message = await self.get_channel(KEY_CHANNEL_ID).fetch_message(schedule_id)
                        embed, view, content = schedule.send_message(self.role_mentions, self)
                        await message.edit(content=content if content else None, embed=embed, view=view)

                await self.message_user(raider, reaction.emoji, schedule)
                logger.info("%s denied schedule %s via DM", user, schedule)
                del self.dm_map[reaction.message.channel.id][reaction.message.id]
                self.dm_timestamps.get(reaction.message.channel.id, {}).pop(reaction.message.id, None)

            save_state(
                self.raiders,
                self.schedules,
                self.availability,
                self.availability_message_id,
                self.dm_map,
                self.dm_timestamps,
            )

    # ---------------------------
    # Error Handling
    # ---------------------------
    async def on_error(self, event, *args, **kwargs):
        """Log errors to the console."""
        logger.exception("Error in event %s: %s", event, args)


# ---------------------------
# Bot Setup

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True
intents.dm_messages = True

client = MyClient(intents=intents)

if __name__ == "__main__":
    # log_handler=None: we configured logging ourselves (file + console) in _configure_logging.
    client.run(CLIENT_ID, log_handler=None)
