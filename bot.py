"""Discord bot for managing WoW Mythic+ raid scheduling."""

import logging
import os

import discord
import asyncio
from discord.ext import tasks
from datetime import datetime, timezone
from dotenv import load_dotenv
from typing import Dict, Tuple

from dropdown import WoWSelectionView, KeyRequestView
from raider import Raider
from schedule import Schedule
from utils import (
    AVAILABILITY_MESSAGE,
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
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))

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
    availability_message_id: int = None

    # ---------------------------
    # User Defined Functions
    # ---------------------------

    def reset_availability(self):
        """Reset the availability lists for all colors."""
        self.availability = {
            GREEN: [],
            YELLOW: [],
            RED: []
        }

    async def message_user(self, raider: Raider, emoji: str, schedule: Schedule):
        """Send a direct message to a raider based on their availability reaction."""
        try:
            if emoji in ['✅', '❌']:
                dm_channel = await self.get_user(raider.user_id).create_dm()
                if emoji == '✅':
                    await dm_channel.send(
                        f"""
                        Hello {raider.member.display_name}, you have successfully signed up for (Level {schedule.level})
                        Run on <t:{int(schedule.date_scheduled.astimezone(timezone.utc).timestamp())}:F>.
                        See you there!
                        """
                    )
                elif emoji == '❌':
                    await dm_channel.send(
                        f"""
                        Hello {raider.member.display_name}, you have successfully declined the spot for (Level {schedule.level})
                        Run on <t:{int(schedule.date_scheduled.astimezone(timezone.utc).timestamp())}:F>.
                        """
                    )
            
        except discord.Forbidden:
            logger.warning(f"Could not DM {raider.member} for schedule {schedule}")

    async def get_message_history(self):
        """Retrieve message history from the designated channel."""
        await self.get_channel(CHANNEL_ID).fetch_message(self.availability_message_id)
        [await self.get_channel(cid).fetch_message(mid[1]) for cid, mid in self.dm_map.items()]

    @tasks.loop(hours=1)
    async def hourly_check(self):
        """Background task that runs every hour. Makes sure to fill schedules and perform reminders"""

        for schedule_id, schedule in self.schedules.items():
            # Check if schedule needs to be filled
            if not schedule.is_filled() and schedule.asks >= 5 and schedule.tier_reached != RED:
                schedule.asks = 0
                await self.fill_remaining_spots(schedule_id)
            elif not schedule.is_filled() and schedule.asks < 5:
                self.schedules[schedule_id].asks += 1

            # Check for reminders
            if schedule.is_filled():
                now = datetime.now(datetime.timezone.utc)
                difference = (schedule.start_time.astimezone(timezone.utc) - now).total_seconds()
                if difference <= 3600 * 2:
                    channel = self.get_channel(CHANNEL_ID)
                    await channel.send(f"{schedule.send_reminder()} in {difference // 3600} hours and {(difference % 3600) // 60} minutes.")
                    
        # Clean up schedules that are past along with direct messages for them as well
        schedules_delete = []
        for schedule_id, schedule in self.schedules.items():
            now = datetime.now(datetime.timezone.utc)
            if schedule.start_time.astimezone(timezone.utc) < now:
                schedules_delete.append(schedule_id)
                
        dm_delete = []
        for dm_channel_id, dm_id in list(self.dm_map.items()):
            if self.dm_map[dm_channel_id][dm_id] in schedules_delete:
                dm_delete.append((dm_channel_id, dm_id))
                
        # Delete schedules and associated DMs
        for schedule_id in schedules_delete:
            del self.schedules[schedule_id]
        
        for dm_channel_id, dm_id in dm_delete:
            del self.dm_map[dm_channel_id][dm_id]

        save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map)
        
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
            
            # Check if we should ask this raider based on schedule's tier_reached
            schedule_tier_priority = tier_priority[schedule.tier_reached]
            raider_tier_priority = tier_priority[raider_tier]
            
            # Ask if raider is at least as available as the schedule's current tier
            if raider_tier_priority <= schedule_tier_priority:
                
                # Check if raider's primary role can fill a missing spot
                primary_role = raider.roles[0]
            
                if primary_role and primary_role in schedule.missing and raider.check_availability(schedule):
                    # DM the raider
                    try:
                        dm_channel = await raider.member.create_dm()
                        dm = await dm_channel.send(
                            f"""
                            Hello {raider.member.display_name}, a spot has opened up for (Level {schedule.level})
                            Run on <t:{int(schedule.date_scheduled.astimezone(timezone.utc).timestamp())}:F>.
                            Your primary role ({primary_role}) is needed.
                                React with :green_check_mark: if you would like to go, or :x: to decline.
                            """
                        )
                        await dm.add_reaction('✅')
                        await dm.add_reaction('❌')
                        
                        if dm_channel.id not in self.dm_map:
                            self.dm_map[dm_channel.id] = {}
                        self.dm_map[dm_channel.id][dm.id] = schedule_id
                    except discord.Forbidden:
                        logger.warning(f"Could not DM {raider.member} for schedule {schedule_id}")
            elif not schedule.primary and len(raider.roles) > 1:
                # Check if raider's secondary role can fill a missing spot
                secondary_role = raider.roles[1]
                
                if secondary_role and secondary_role in schedule.missing and raider.check_availability(schedule):
                    # DM the raider
                    try:
                        dm_channel = await raider.member.create_dm()
                        dm = await dm_channel.send(
                            f"""
                            Hello {raider.member.display_name}, a spot has opened up for (Level {schedule.level})
                            Run on <t:{int(schedule.date_scheduled.astimezone(timezone.utc).timestamp())}:F>.
                            Your secondary role ({secondary_role}) is needed.
                                React with :green_check_mark: if you would like to go, or :x: to decline.
                            """
                        )
                        await dm.add_reaction('✅')
                        await dm.add_reaction('❌')
                        
                        if dm_channel.id not in self.dm_map:
                            self.dm_map[dm_channel.id] = {}
                        self.dm_map[dm_channel.id][dm.id] = schedule_id
                    except discord.Forbidden:
                        logger.warning(f"Could not DM {raider.member} for schedule {schedule_id}")

    async def fill_remaining_spots(self, schedule_id: int):
        """Background task to fill remaining schedule spots from available raiders, asking all eligible in a tier in parallel."""
        await asyncio.sleep(60)  # 1 minute for manual signups

        schedule = self.schedules.get(schedule_id)
        tier = schedule.tier_reached
        primary = schedule.primary

        if not schedule:
            return  # Schedule no longer exists

        channel = self.get_channel(CHANNEL_ID)
        if not channel:
            return  # Channel no longer accessible

        # For each user in the tier if their role fits an open spot, DM them to ask if they want to join
        for raider in self.availability[tier]:
            if schedule.is_filled():
                break  # Schedule is full

            if primary:
                if raider.roles[0] in schedule.missing and raider.check_availability(schedule):
                    try:
                        dm_channel = await raider.member.create_dm()
                        dm = await dm_channel.send(
                            f"""
                                Hello {raider.member.display_name}, a spot has opened up for (Level {schedule.level})
                                Run on <t:{int(schedule.date_scheduled.astimezone(timezone.utc).timestamp())}:F>.
                                React with :green_check_mark: if you would like to go, or :x: to decline.
                            """
                        )
                        await dm.add_reaction('✅')
                        await dm.add_reaction('❌')
                        
                        if not dm_channel.id in self.dm_map:
                            self.dm_map[dm_channel.id] = {}
                        self.dm_map[dm_channel.id][dm.id] = schedule_id
                    except discord.Forbidden:
                        logger.warning(f"Could not DM {raider.member} for schedule {schedule_id}")
            else:
                if len(raider.roles) > 1 and raider.roles[1] in schedule.missing and raider.check_availability(schedule):
                    try:
                        dm_channel = await raider.member.create_dm()
                        dm = await dm_channel.send(
                            f"""
                                Hello {raider.member.display_name}, a spot has opened up for (Level {schedule.level})
                                Run on <t:{int(schedule.date_scheduled.astimezone(timezone.utc).timestamp())}:F>.
                                React with :green_check_mark: if you would like to go, or :x: to decline.
                            """
                        )
                        await dm.add_reaction('✅')
                        await dm.add_reaction('❌')

                        if not dm_channel.id in self.dm_map:
                            self.dm_map[dm_channel.id] = {}
                        self.dm_map[dm_channel.id][dm.id] = schedule_id
                    except discord.Forbidden:
                        logger.warning(f"Could not DM {raider.member} for schedule {schedule_id}")

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
        save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map)

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
                    await dm_channel.send(
                        f"""
                        Your Mythic+ run (Level {schedule.level}) is now **filled**!
                        Date: {day_of_week}, {dt.date()} at {time_str}
                        See you there!
                        """
                    )
                else:
                    await dm_channel.send(
                        f"""
                        Your Mythic+ run (Level {schedule.level}) is no longer **filled**!
                        Date: {day_of_week}, {dt.date()} at {time_str}
                        """
                    )
            except discord.Forbidden as e:
                logger.warning(f"Could not DM {raider.member} for filled schedule: {e}")

    # ---------------------------
    # Startup
    # ---------------------------
    async def on_ready(self):
        """Called when the bot is ready. Loads state from file."""
        logger.info("Logged in as %s", self.user)

        self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map = load_state()
        logger.info("Loaded state from file.")

        # Start hourly background task
        self.hourly_check.start()
        if self.availability_message_id:
            await self.get_message_history()
        

    # ---------------------------
    # Message Listener
    # ---------------------------
    async def on_message(self, message: discord.Message):
        """Handle incoming messages, responding to commands like !avail and !key."""
        if message.author.id == self.user.id:
            return

        if message.channel.id == CHANNEL_ID:
            if message.content == '!avail':
                self.reset_availability()
                channel = self.get_channel(CHANNEL_ID)
                message = await channel.send(AVAILABILITY_MESSAGE)
                self.availability_message_id = message.id
                await message.add_reaction(GREEN)
                await message.add_reaction(YELLOW)
                await message.add_reaction(RED)
            elif message.content == '!key':
                if message.author.id in self.raiders:
                    try:
                        view = KeyRequestView(timeout=300)  # 5 minutes
                        await message.author.send(
                            "Request a key: Select dungeon, level, start time, and end time.",
                            view=view
                        )
                        await view.wait()
                        # After wait, selections are on view if submitted
                        if view.selected_dungeon:
                            logger.info(f"Key request from {message.author}: Day={view.selected_day}, {view.selected_level}, {view.selected_start_time.strftime('%H:%M')} to {view.selected_end_time.strftime('%H:%M')}")
                            schedule = Schedule(
                                raider_scheduled=self.raiders[message.author.id],
                                level=view.selected_level,
                                date_scheduled=view.selected_day,
                                start_time=view.selected_start_time,
                            )
                            message = await self.get_channel(CHANNEL_ID).send(schedule.send_message())
                            await message.add_reaction('✅')  # Confirm attendance
                            await message.add_reaction('❌')  # Remove attendance

                            self.schedules[message.id] = schedule
                            save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map)
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

        if reaction.message.channel.id == CHANNEL_ID and reaction.message.id == self.availability_message_id and reaction.emoji in [GREEN, YELLOW, RED]:
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

                        save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map)
                    else:
                        logger.info(f"No valid selection from {user} (timed out or incomplete)")
                except discord.Forbidden:
                    logger.warning(f"Could not DM {user}")

        elif reaction.message.channel.id == CHANNEL_ID and reaction.message.id in self.schedules and user.id in self.raiders:
            if reaction.emoji not in ['✅', '❌']:
                return
            
            if reaction.emoji == '✅':
                schedule = self.schedules[reaction.message.id]
                raider = self.raiders[user.id]
                schedule.raider_signup(raider)
                raider.add_run(schedule)
                await reaction.message.edit(content=schedule.send_message())
                await self.message_user(raider, reaction.emoji, schedule)
                if schedule.is_filled():
                    await self.notify_schedule_filled(schedule)
                save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map)
                logger.info("%s signed up for schedule %s", user, reaction.message.id)
            elif reaction.emoji == '❌':
                schedule = self.schedules[reaction.message.id]
                raider = self.raiders[user.id]
                fill_status = schedule.is_filled()
                schedule.raider_remove(raider)
                raider.remove_run(schedule)
                if schedule.is_filled() != fill_status:
                    await self.notify_schedule(schedule)
                await reaction.message.edit(content=schedule.send_message())
                await self.message_user(raider, reaction.emoji, schedule)
                save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map)
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
                    await self.notify_schedule_filled(schedule)
                await self.message_user(raider, reaction.emoji, schedule)
                message = await self.get_channel(CHANNEL_ID).fetch_message(schedule_id)
                await message.edit(content=schedule.send_message())
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
                message = await self.get_channel(CHANNEL_ID).fetch_message(schedule_id)
                await message.edit(content=schedule.send_message())
            
            await reaction.message.edit(content=schedule.send_message())
            save_state(self.raiders, self.schedules, self.availability, self.availability_message_id, self.dm_map)
            logger.info("%s signed up for schedule %s via DM", user, schedule)



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