"""Discord bot for managing WoW Mythic+ raid scheduling."""

import logging
import os

import discord
import asyncio
from discord.ext import tasks
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from typing import Dict, Tuple
from textwrap import dedent

from dropdown import WoWSelectionView, KeyRequestView
from raider import Raider
from schedule import Schedule
from utils import (
    GREEN,
    YELLOW,
    RED,
    load_state,
    save_state
)

# ---------------------------
# Logging Setup
# ---------------------------
logger = logging.getLogger('discord')

# ---------------------------
# Global Variables
# ---------------------------
load_dotenv('.env')
CLIENT_ID = os.getenv('CLIENT_KEY')
AVAIL_CHANNEL_ID = int(os.getenv('AVAIL_CHANNEL_ID'))
KEY_CHANNEL_ID = int(os.getenv('KEY_CHANNEL_ID'))
GUILD_ID = int(os.getenv('GUILD_ID'))
TANK_ID = int(os.getenv('TANK_ROLE_ID'))
HEALER_ID = int(os.getenv('HEALER_ROLE_ID'))
DPS_ID = int(os.getenv('DPS_ROLE_ID'))
COORDINATOR_ID = int(os.getenv('COORDINATOR_ID'))
MYTHIC_PLUS_ID = int(os.getenv('MYTHIC_PLUS_ID'))
AVAILABILITY_MESSAGE: str = lambda x, y, z: f"""
React to this message to set your availability for this week's mythic plus runs
<t:{int(x)}:F> to <t:{int(y)}:F>


:green_circle: - Available
:yellow_circle: - Maybe Available
:red_circle: - Not Available

{z}
"""

# ---------------------------
# Bot Class
# ---------------------------
class MyClient(discord.Client):
    """Discord bot client for managing WoW Mythic+ raid scheduling and availability."""
    raiders: Dict[discord.Member, Raider] = {}
    schedules: Dict[int, Schedule] = {}
    availability: Dict[str, Raider] = {
        GREEN: [],
        YELLOW: [],
        RED: []
    }
    dm_map: Dict[int, Dict[int, int]] = {}
    dm_timestamps: Dict[int, Dict[int, datetime]] = {}
    availability_message_id: int = None
    role_mentions = {}

    # ---------------------------
    # User Defined Functions
    # ---------------------------

    def reset_week(self):
        """
            Reset the availability lists for all colors.
            Resets each raider's current and denied runs for the new week.
        """
        
        self.availability = {
            GREEN: [],
            YELLOW: [],
            RED: []
        }

        for raider in self.raiders.values():
            raider.current_runs = []
            raider.denied_runs = []

    async def message_user(self, raider: Raider, emoji: str, schedule: Schedule):
        """Send a direct message to a raider based on their availability reaction."""
        try:
            if emoji in ['✅', '❌']:
                dm_channel = await self.get_user(raider.user_id).create_dm()
                if emoji == '✅':
                    await dm_channel.send(dedent(
                        f"""
                        Hello {raider.name}, you have successfully signed up for (Level {schedule.level})
                        Run on <t:{int(schedule.date_scheduled.astimezone(timezone.utc).timestamp())}:F>.
                        See you there!
                        """
                    ))
                elif emoji == '❌':
                    await dm_channel.send(dedent(
                        f"""
                        Hello {raider.name}, you have successfully declined the spot for (Level {schedule.level})
                        Run on <t:{int(schedule.date_scheduled.astimezone(timezone.utc).timestamp())}:F>.
                        """
                    ))
            
        except discord.Forbidden:
            logger.warning(f"Could not DM {raider.name} for schedule {schedule}")

    async def repost_schedule(self, schedule: Schedule, schedule_post: int) -> Tuple[int, Schedule]:
        """
        Delete and repost message if it's been up for over 24 hours.
        """
        message = await self.get_channel(KEY_CHANNEL_ID).fetch_message(schedule_post)
        await message.delete()

        new_message = schedule.send_message()
        for role in schedule.missing:
            new_message += f"\n{self.role_mentions[role].mention} needed!"

        new_message = await self.get_channel(KEY_CHANNEL_ID).send(dedent(new_message))

        return new_message.id, schedule

    async def get_message_history(self):
        """Retrieve message history from the designated channel."""
        await self.get_channel(AVAIL_CHANNEL_ID).fetch_message(self.availability_message_id)
        [await self.get_channel(cid).fetch_message(mid[1]) for cid, mid in self.dm_map.items()]

    # Add new method to handle DM retries
    async def retry_unanswered_dms(self):
        """Retry sending DMs for schedule requests that haven't been answered."""
        now = datetime.now(timezone.utc)
        retry_threshold = 3600 * 2 # 2 hour before retry
        
        dms_to_retry = []
        dms_to_delete = []
        
        # Check all active DMs
        for dm_channel_id, dm_messages in list(self.dm_map.items()):
            for dm_message_id, schedule_id in list(dm_messages.items()):
                # Check if this DM has a timestamp
                if dm_channel_id in self.dm_timestamps and dm_message_id in self.dm_timestamps[dm_channel_id]:
                    sent_time = self.dm_timestamps[dm_channel_id][dm_message_id]
                    time_elapsed = (now - sent_time).total_seconds()
                    
                    # If DM is older than threshold and schedule still exists and isn't filled
                    if time_elapsed >= retry_threshold:
                        schedule = self.schedules.get(schedule_id)
                        if schedule and not schedule.is_filled():
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
                for r in self.raiders.values():
                    if r.member:
                        r_dm = await r.member.create_dm()
                        if r_dm.id == dm_channel_id:
                            raider = r
                            break
                
                if raider and schedule not in raider.denied_runs and schedule not in raider.current_runs and not schedule.has_raider(raider):
                    # Determine which role is needed
                    role_needed = None
                    if raider.roles[0] in schedule.missing:
                        role_needed = raider.roles[0]
                    elif len(raider.roles) > 1 and raider.roles[1] in schedule.missing:
                        role_needed = raider.roles[1]
                    
                    if role_needed:
                        # Send new DM
                        dm_channel = await raider.member.create_dm()
                        new_dm = await dm_channel.send(dedent(
                            f"""
                            🔔 **Reminder** 🔔
                            Hello {raider.name}, a spot is still available for (Level {schedule.level})
                            Run on <t:{int(schedule.date_scheduled.astimezone(timezone.utc).timestamp())}:F>.
                            Your role ({role_needed}) is needed.
                            
                            React with :white_check_mark: if you would like to go, or :x: to decline.
                            """
                        ))
                        await new_dm.add_reaction('✅')
                        await new_dm.add_reaction('❌')
                        
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
            save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map, self.dm_timestamps)

    async def check_schedule_conflicts(self):
        """Check for multiple unfilled schedules at the same time and notify coordinator."""
        time_slots = {}
        
        # Group unfilled schedules by their start time
        for schedule_id, schedule in self.schedules.items():
            if not schedule.is_filled():
                # Create a time key (date + hour)
                time_key = (schedule.date_scheduled.date(), schedule.start_time.hour)
                
                if time_key not in time_slots:
                    time_slots[time_key] = []
                time_slots[time_key].append((schedule_id, schedule))
        
        # Find conflicts (multiple unfilled schedules at same time)
        conflicts = {k: v for k, v in time_slots.items() if len(v) > 1}
        
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
                        missing_roles = ', '.join(schedule.missing)
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
            
            # If hasn't been listed for an hour yet don't ask
            if (datetime.now(timezone.utc) - schedule.posted).total_seconds() < 3600:
                break

            # Check if we should ask this raider based on schedule's tier_reached
            schedule_tier_priority = tier_priority[schedule.tier_reached]
            raider_tier_priority = tier_priority[raider_tier]
            
            # Ask if raider is at least as available as the schedule's current tier
            if raider_tier_priority <= schedule_tier_priority and raider.check_availability(schedule) and schedule not in raider.denied_runs and schedule not in raider.current_runs and not schedule.has_raider(raider):
                
                # Check if raider's primary role can fill a missing spot
                primary_role = raider.roles[0]
            
                if primary_role and primary_role in schedule.missing:
                    # DM the raider
                    try:
                        dm_channel = await self.get_user(raider.user_id).create_dm()
                        dm = await dm_channel.send(dedent(
                            f"""
                            Hello {raider.name}, a spot has opened up for (Level {schedule.level})
                            Run on <t:{int(schedule.date_scheduled.astimezone(timezone.utc).timestamp())}:F>.
                            Your primary role ({primary_role}) is needed.
                                React with :white_check_mark: if you would like to go, or :x: to decline.
                            """
                        ))
                        await dm.add_reaction('✅')
                        await dm.add_reaction('❌')
                        
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
                
                if secondary_role and secondary_role in schedule.missing and raider.check_availability(schedule) and not schedule.has_raider(raider):
                    # DM the raider
                    try:
                        dm_channel = await self.get_user(raider.user_id).create_dm()
                        dm = await dm_channel.send(dedent(
                            f"""
                            Hello {raider.name}, a spot has opened up for (Level {schedule.level})
                            Run on <t:{int(schedule.date_scheduled.astimezone(timezone.utc).timestamp())}:F>.
                            Your secondary role ({secondary_role}) is needed.
                                React with :white_check_mark: if you would like to go, or :x: to decline.
                            """
                        ))
                        await dm.add_reaction('✅')
                        await dm.add_reaction('❌')

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
        await asyncio.sleep(60)  # 1 minute for manual signups

        schedule = self.schedules.get(schedule_id)
        tier = schedule.tier_reached
        primary = schedule.primary

        if not schedule:
            return  # Schedule no longer exists
        
        if schedule.is_filled():
            return  # Schedule already filled

        channel = self.get_channel(KEY_CHANNEL_ID)
        if not channel:
            return  # Channel no longer accessible

        # For each user in the tier if their role fits an open spot, DM them to ask if they want to join
        for raider in self.availability[tier]:

            if not raider.check_availability(schedule):
                continue # Raider not available for this schedule
        
            if schedule in raider.denied_runs or schedule in raider.current_runs or schedule.has_raider(raider):
                continue # Raider has previously denied this schedule or is already in it

            if primary:
                if raider.roles[0] in schedule.missing and raider.check_availability(schedule):
                    try:
                        dm_channel = await self.get_user(raider.user_id).create_dm()
                        dm = await dm_channel.send(dedent(
                            f"""
                            Hello {raider.name}, a spot has opened up for (Level {schedule.level})
                            Run on <t:{int(schedule.date_scheduled.astimezone(timezone.utc).timestamp())}:F>.
                            Your primary role ({raider.roles[0]}) is needed.
                                React with :white_check_mark: if you would like to go, or :x: to decline.
                            """
                        ))
                        await dm.add_reaction('✅')
                        await dm.add_reaction('❌')

                        # Track timestamp
                        if dm_channel.id not in self.dm_timestamps:
                            self.dm_timestamps[dm_channel.id] = {}
                        self.dm_timestamps[dm_channel.id][dm.id] = datetime.now(timezone.utc)
                        
                        if not dm_channel.id in self.dm_map:
                            self.dm_map[dm_channel.id] = {}
                        self.dm_map[dm_channel.id][dm.id] = schedule_id
                    except discord.Forbidden:
                        logger.warning(f"Could not DM {raider.name} for schedule {schedule_id}")
            else:
                if len(raider.roles) > 1 and raider.roles[1] in schedule.missing and not schedule.has_raider(raider):
                    try:
                        dm_channel = await self.get_user(raider.user_id).create_dm()
                        dm = await dm_channel.send(dedent(
                            f"""
                            Hello {raider.name}, a spot has opened up for (Level {schedule.level})
                            Run on <t:{int(schedule.date_scheduled.astimezone(timezone.utc).timestamp())}:F>.
                            Your secondary role ({raider.roles[1]}) is needed.
                                React with :white_check_mark: if you would like to go, or :x: to decline.
                            """
                        ))
                        await dm.add_reaction('✅')
                        await dm.add_reaction('❌')

                        # Track timestamp
                        if dm_channel.id not in self.dm_timestamps:
                            self.dm_timestamps[dm_channel.id] = {}
                        self.dm_timestamps[dm_channel.id][dm.id] = datetime.now(timezone.utc)
                        
                        if not dm_channel.id in self.dm_map:
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
        save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map, self.dm_timestamps)

    async def notify_schedule(self, schedule: Schedule):
        """DM all members of a filled schedule (tank, healer, dps) with the day of week and time in their timezone."""
        members = []
        if schedule.team['tank']:
            members.append(schedule.team['tank'])
        if schedule.team['healer']:
            members.append(schedule.team['healer'])
        for dps in schedule.team['dps']:
            if dps:
                members.append(dps)

        for raider in members:
            try:
                # Format time in raider's timezone
                dt = schedule.start_time.astimezone(raider.timezone)
                day_of_week = dt.strftime('%A')
                time_str = dt.strftime('%I:%M %p %Z')
                dm_channel = await raider.member.create_dm()
                if schedule.is_filled():
                    await dm_channel.send(dedent(
                        f"""
                        Your Mythic+ run (Level {schedule.level}) is now **filled**!
                        Date: {day_of_week}, {dt.date()} at {time_str}
                        See you there!
                        """
                    ))
                else:
                    await dm_channel.send(dedent(
                        f"""
                        Your Mythic+ run (Level {schedule.level}) is no longer **filled**!
                        Date: {day_of_week}, {dt.date()} at {time_str}
                        """
                    ))
            except discord.Forbidden as e:
                logger.warning(f"Could not DM {raider.member} for filled schedule: {e}")

    # ---------------------------
    # Hourly Task
    # ---------------------------

    @tasks.loop(hours=1)
    async def hourly_check(self):
        """Background task that runs every hour. Makes sure to fill schedules and perform reminders"""

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
                    await channel.send(f"{schedule.send_reminder()} in {difference // 3600} hours and {(difference % 3600) // 60} minutes.")
                    
        # Clean up past schedules and their associated DMs - ULTRA ELEGANT VERSION
        now = datetime.now(timezone.utc)
        past_schedule_ids = {
            sid for sid, s in self.schedules.items()
            if s.start_time.astimezone(timezone.utc) < now
        }

        # Remove past schedules
        self.schedules = {
            sid: s for sid, s in self.schedules.items()
            if sid not in past_schedule_ids
        }

        # Clean up DM references
        for dm_channel_id in list(self.dm_map.keys()):
            # Remove DMs for past schedules
            self.dm_map[dm_channel_id] = {
                mid: sid for mid, sid in self.dm_map[dm_channel_id].items()
                if sid not in past_schedule_ids
            }
            if dm_channel_id in self.dm_timestamps:
                self.dm_timestamps[dm_channel_id] = {
                    mid: ts for mid, ts in self.dm_timestamps[dm_channel_id].items()
                    if mid in self.dm_map[dm_channel_id]
                }
            
            # Remove empty channels
            if not self.dm_map[dm_channel_id]:
                del self.dm_map[dm_channel_id]
            if dm_channel_id in self.dm_timestamps and not self.dm_timestamps[dm_channel_id]:
                del self.dm_timestamps[dm_channel_id]

        await self.check_schedule_conflicts()

        save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map, self.dm_timestamps)

    # ---------------------------
    # Startup
    # ---------------------------
    async def setup_hook(self) -> None:
        """Calls before on ready to set up all environmental variables"""
        self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map, self.dm_timestamps = load_state()
        logger.info("Loaded state from file.")

        # Start hourly background task
        if not self.hourly_check.is_running():
            self.hourly_check.start()
        
        if self.availability_message_id:
            await self.get_message_history()
    
    async def on_ready(self):
        """Called when the bot is ready. Loads state from file."""
        logger.info("Logged in as %s", self.user)
        
        # Get roles for later use
        if not self.role_mentions:
            self.role_mentions['healer'] = self.get_guild(GUILD_ID).get_role(HEALER_ID)
            self.role_mentions['tank'] = self.get_guild(GUILD_ID).get_role(TANK_ID)
            self.role_mentions['dps'] = self.get_guild(GUILD_ID).get_role(DPS_ID)
        

    # ---------------------------
    # Message Listener
    # ---------------------------
    async def on_message(self, message: discord.Message):
        """Handle incoming messages, responding to commands like !avail and !key."""
        if message.author.id == self.user.id:
            return
        
        if message.content == '!keys':
            if message.author.id in self.raiders:
                try:
                    await message.author.send(
                        self.raiders[message.author.id].get_current_runs()
                    )
                except discord.Forbidden:
                    logger.warning(f"Could not DM {message.author} for current runs")

        if message.content == '!cleanup' and message.author.id == COORDINATOR_ID:
            if message.channel.id == AVAIL_CHANNEL_ID or message.channel.id == KEY_CHANNEL_ID:
                try:
                    # Purge both channels
                    avail_channel = self.get_channel(AVAIL_CHANNEL_ID)
                    key_channel = self.get_channel(KEY_CHANNEL_ID)
                    avail_deleted = await avail_channel.purge()
                    key_deleted = await key_channel.purge()
                    logger.info("Cleanup: purged %d messages from AVAIL and %d from KEY channel.", len(avail_deleted), len(key_deleted))

                    # Reset all state except raiders
                    self.schedules = {}
                    self.availability = {GREEN: [], YELLOW: [], RED: []}
                    self.availability_message_id = None
                    self.dm_map = {}
                    self.dm_timestamps = {}
                    for raider in self.raiders.values():
                        raider.current_runs = []
                        raider.denied_runs = []

                    save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map, self.dm_timestamps)
                    logger.info("Cleanup: state reset complete. Raiders preserved: %d", len(self.raiders))

                    # Confirm in whichever channel the command was issued
                    await message.channel.send("✅ Cleanup complete. Both channels have been purged and all state (except raiders) has been reset.")
                except discord.Forbidden:
                    logger.warning("Cleanup: missing permissions to purge one or both channels.")
                    await message.channel.send("❌ Missing permissions to purge one or both channels.")
                except Exception as exc:
                    logger.exception("Cleanup: unexpected error: %s", exc)
                    await message.channel.send("❌ An unexpected error occurred during cleanup.")
            else:
                await message.channel.send("❌ `!cleanup` must be used in the availability or key channel.")
            return

        if message.channel.id == AVAIL_CHANNEL_ID or message.channel.id == KEY_CHANNEL_ID:
            if message.content == '!avail' and message.author.id == COORDINATOR_ID and message.channel.id == AVAIL_CHANNEL_ID:
                self.reset_week()
                date_start = datetime.now(timezone.utc)
                date_end = (date_start + timedelta(days=7)).timestamp()
                channel = self.get_channel(AVAIL_CHANNEL_ID)
                message = await channel.send(dedent(AVAILABILITY_MESSAGE(date_start.timestamp(), date_end, self.get_guild(GUILD_ID).get_role(MYTHIC_PLUS_ID).mention)))
                self.availability_message_id = message.id
                await message.add_reaction(GREEN)
                await message.add_reaction(YELLOW)
                await message.add_reaction(RED)
            elif message.content == '!key' and message.channel.id == KEY_CHANNEL_ID:
                if message.author.id in self.raiders:
                    try:
                        view = KeyRequestView(timeout=300)  # 5 minutes
                        await message.author.send(
                            "Request a key: Select Level, Date, Time and How many Keys you are wanting to do.",
                            view=view
                        )
                        await view.wait()
                        # After wait, selections are on view if submitted
                        if view.selected_level and view.selected_day and view.selected_start_time and view.run_type:
                            logger.info(f"Key request from {message.author}: Day={view.selected_day}, {view.selected_level}, {view.selected_start_time.strftime('%H:%M')}, {view.run_type}")
                            # Check for existing schedule at same date and time
                            existing_schedule, existing_schedule_id = None, None
                            for sched_id, sched in self.schedules.items():
                                if sched.date_scheduled == view.selected_day and sched.start_time.time() == view.selected_start_time.time():
                                    existing_schedule = sched
                                    existing_schedule_id = sched_id
                                    break
                            if existing_schedule and sum(role in existing_schedule.missing for role in self.raiders[message.author.id].roles):
                                # Ask user if they want to join existing schedule
                                dm = await message.author.send(
                                    f"A run already exists at this time (Level {existing_schedule.level}). Would you like to join that run instead?\nReact with ✅ to join, or ❌ to list your key request.")
                                await dm.add_reaction('✅')
                                await dm.add_reaction('❌')
                                
                                def check(reaction, user):
                                    return user == message.author and str(reaction.emoji) in ['✅', '❌'] and reaction.message.id == dm.id
                                try:
                                    reaction, _ = await self.wait_for('reaction_add', timeout=120.0, check=check)
                                    if str(reaction.emoji) == '✅':
                                        existing_schedule.raider_signup(self.raiders[message.author.id])
                                        self.raiders[message.author.id].add_run(existing_schedule)
                                        await message.author.send(f"You have been added to the existing run on {existing_schedule.date_scheduled} at {existing_schedule.start_time.strftime('%I:%M %p')}.")
                                        # Optionally notify if filled
                                        if existing_schedule.is_filled():
                                            await self.notify_schedule(existing_schedule)
                                        # Update the schedule message in channel
                                        msg = await self.get_channel(KEY_CHANNEL_ID).fetch_message(existing_schedule_id)
                                        await msg.edit(content=existing_schedule.send_message())
                                        save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map, self.dm_timestamps)
                                    else:
                                        schedule = Schedule(
                                            raider_scheduled=self.raiders[message.author.id],
                                            level=view.selected_level,
                                            date_scheduled=view.selected_day,
                                            start_time=view.selected_start_time,
                                        )
                                        message = await self.get_channel(KEY_CHANNEL_ID).send(dedent(schedule.send_message()))
                                        await message.add_reaction('✅')  # Confirm attendance
                                        await message.add_reaction('❌')  # Remove attendance

                                        self.schedules[message.id] = schedule
                                        save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map, self.dm_timestamps)
                                        # Start background task to fill remaining spots from availability
                                        self.loop.create_task(self.fill_remaining_spots(message.id))
                                except asyncio.TimeoutError:
                                    await message.author.send("No response received. Key request cancelled.")
                                return
                            # No existing schedule, create new
                            schedule = Schedule(
                                raider_scheduled=self.raiders[message.author.id],
                                level=view.selected_level,
                                date_scheduled=view.selected_day,
                                start_time=view.selected_start_time,
                            )
                            message = await self.get_channel(KEY_CHANNEL_ID).send(dedent(schedule.send_message()))
                            await message.add_reaction('✅')  # Confirm attendance
                            await message.add_reaction('❌')  # Remove attendance

                            self.schedules[message.id] = schedule
                            save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map, self.dm_timestamps)
                            # Start background task to fill remaining spots from availability
                            self.loop.create_task(self.fill_remaining_spots(message.id))
                        else:
                            logger.info(f"Key request timed out or cancelled for {message.author}")
                    except discord.Forbidden:
                        logger.warning(f"Could not DM {message.author} for key request")
                else:
                    await message.channel.send(f"{message.author.mention}, you need to sign up first by reacting to the availability message.")

    # ---------------------------
    # Reaction Listener
    # ---------------------------
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.Member):
        """Handle reaction additions for availability signup or schedule signup."""
        if user.id == self.user.id:
            return

        if reaction.message.channel.id == AVAIL_CHANNEL_ID and reaction.message.id == self.availability_message_id and reaction.emoji in [GREEN, YELLOW, RED]:
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
            else:
                try:
                    view = WoWSelectionView(timeout=180)  # 3 minutes timeout
                    await user.send(
                        "Choose your **World of Warcraft class** and **roles**:",
                        view=view
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
                        logger.info(f"Signup from {user}: class={view.selected_class} roles={roles} timezone={view.selected_timezone}")

                        await self.new_availability_signup_fill_schedule(self.raiders[user.id], reaction.emoji)

                        save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map, self.dm_timestamps)
                    else:
                        logger.info(f"No valid selection from {user} (timed out or incomplete)")
                except discord.Forbidden:
                    logger.warning(f"Could not DM {user}")

        elif reaction.message.channel.id == KEY_CHANNEL_ID and reaction.message.id in self.schedules and user.id in self.raiders:
            if reaction.emoji not in ['✅', '❌']:
                return
            
            if reaction.emoji == '✅':
                schedule = self.schedules[reaction.message.id]
                raider = self.raiders[user.id]
                schedule.raider_signup(raider)
                raider.add_run(schedule)
                await reaction.message.edit(content=dedent(schedule.send_message()))
                await self.message_user(raider, reaction.emoji, schedule)
                if schedule.is_filled():
                    await self.notify_schedule(schedule)
                save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map, self.dm_timestamps)
                logger.info("%s signed up for schedule %s", user, reaction.message.id)
            elif reaction.emoji == '❌':
                schedule = self.schedules[reaction.message.id]
                raider = self.raiders[user.id]
                fill_status = schedule.is_filled()
                schedule.raider_remove(raider)
                raider.remove_run(schedule)
                if schedule.is_filled() != fill_status:
                    await self.notify_schedule(schedule)
                await reaction.message.edit(content=dedent(schedule.send_message()))
                await self.message_user(raider, reaction.emoji, schedule)
                save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map, self.dm_timestamps)
                logger.info("%s removed from schedule %s", user, reaction.message.id)

        elif reaction.message.channel.id in self.dm_map and user.id in self.raiders:
            if reaction.emoji not in ['✅', '❌']:
                return
            
            if reaction.emoji == '✅':
                schedule_id = self.dm_map[reaction.message.channel.id][reaction.message.id]
                raider = self.raiders[user.id]
                schedule = self.schedules[schedule_id]
                schedule.raider_signup(raider)
                raider.add_run(schedule)
                if schedule.is_filled():
                    await self.notify_schedule(schedule)
                await self.message_user(raider, reaction.emoji, schedule)
                message = await self.get_channel(KEY_CHANNEL_ID).fetch_message(schedule_id)
                await message.edit(content=dedent(schedule.send_message()))
                logger.info("%s signed up for schedule %s via DM", user, schedule)
            elif reaction.emoji == '❌':
                schedule_id = self.dm_map[reaction.message.channel.id][reaction.message.id]
                raider = self.raiders[user.id]
                schedule = self.schedules[schedule_id]
                fill_status = schedule.is_filled()
                schedule.raider_remove(raider)
                raider.remove_run(schedule)
                if schedule.is_filled() != fill_status:
                    await self.notify_schedule(schedule)
                await self.message_user(raider, reaction.emoji, schedule)
                message = await self.get_channel(KEY_CHANNEL_ID).fetch_message(schedule_id)
                await message.edit(content=dedent(schedule.send_message()))
                logger.info("%s denied schedule %s via DM", user, schedule)
            
            save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map, self.dm_timestamps)



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
client.run(CLIENT_ID)