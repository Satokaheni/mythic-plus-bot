"""Discord bot for managing WoW Mythic+ raid scheduling."""

import logging
import os

import discord
from discord import Member
from dotenv import load_dotenv
from typing import Dict

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
    raiders: Dict[Member, Raider] = {}
    schedules: Dict[int, Schedule] = {}
    availability: Dict[str, Raider] = {
        GREEN: [],
        YELLOW: [],
        RED: []
    }
    availability_message_id: int = None

    def reset_availability(self):
        """Reset the availability lists for all colors."""
        self.availability = {
            GREEN: [],
            YELLOW: [],
            RED: []
        }

    # ---------------------------
    # Startup
    # ---------------------------
    async def on_ready(self):
        """Called when the bot is ready. Loads state from file."""
        logger.info("Logged in as %s", self.user)

        self.raiders, self.schedules, self.availability, self.availability_message_id = load_state()
        logger.info("Loaded state from file.")

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
                            logger.info(f"Key request from {message.author}: Day={view.selected_day}, {view.selected_dungeon}, {view.selected_level}, {view.selected_start_time.strftime('%H:%M')} to {view.selected_end_time.strftime('%H:%M')}")
                            schedule = Schedule(
                                raider_scheduled=self.raiders[message.author.id],
                                dungeon=view.selected_dungeon,
                                level=view.selected_level,
                                date_scheduled=view.selected_day,
                                start_time=view.selected_start_time,
                                end_time=view.selected_end_time
                            )
                            message = await self.get_channel(CHANNEL_ID).send(schedule.send_message())
                            self.schedules[message.id] = schedule
                            save_state(self.raiders, self.schedules, self.availability, self.availability_message_id)
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
                self.availability[reaction.emoji].append(self.raiders[user.id])

            if not user.id in self.raiders:
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
                        self.raiders[user.id] = Raider(user, view.selected_class, roles)
                        self.availability[reaction.emoji].append(self.raiders[user.id])
                        logger.info(f"Signup from {user}: class={view.selected_class} roles={roles}")

                        save_state(self.raiders, self.schedules, self.availability, self.availability_message_id)
                    else:
                        logger.info(f"No valid selection from {user} (timed out or incomplete)")
                except discord.Forbidden:
                    logger.warning(f"Could not DM {user}")

        elif reaction.message.channel.id == CHANNEL_ID and reaction.message.id in self.schedules and user.id in self.raiders:
            schedule = self.schedules[reaction.message.id]
            raider = self.raiders[user.id]
            schedule.raider_signup(raider)
            await reaction.message.edit(content=schedule.send_message())
            save_state(self.raiders, self.schedules, self.availability, self.availability_message_id)
            logger.info("%s signed up for schedule %s", user, reaction.message.id)

    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.Member):
        """Handle reaction removals for availability signup or schedule signup."""
        channel = reaction.message.channel
        if channel.id == CHANNEL_ID and reaction.message.id == self.availability_message_id and reaction.emoji in [GREEN, YELLOW, RED] and user.id in self.raiders:
            if self.raiders[user.id] in self.availability[reaction.emoji]:
                self.availability[reaction.emoji].remove(self.raiders[user.id])
            else:
                logger.warning(f"Tried to remove {user} from availability list but they were not found meaning they removed someone else's reaction adding it back")
                await reaction.message.add_reaction(reaction.emoji)
            save_state(self.raiders, self.schedules, self.availability, self.availability_message_id)
            logger.info("Reaction removed by %s for emoji %s", user, reaction.emoji)

        elif channel.id == CHANNEL_ID and reaction.message.id in self.schedules and user.id in self.raiders:
            schedule = self.schedules[reaction.message.id]
            raider = self.raiders[user.id]
            schedule.raider_remove(raider)
            await reaction.message.edit(content=schedule.send_message())
            save_state(self.raiders, self.schedules, self.availability, self.availability_message_id)
            logger.info("%s removed signup for schedule %s", user, reaction.message.id)


# ---------------------------
# Bot Setup

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True
intents.dm_messages = True

client = MyClient(intents=intents)
client.run(CLIENT_ID)
