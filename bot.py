import os
import discord
import pickle
from discord.ext import commands
from pathlib import Path
from discord import Member, Message
from typing import Dict, List
from datetime import datetime
from discord.ext import tasks
from dotenv import load_dotenv
from logging import getLogger, basicConfig, INFO
from raider import Raider
from schedule import Schedule
from utils import schedule_boiler, create_schedules, validate_role_message

# Set up Logging
logger = getLogger()
basicConfig(
    level=INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Global Variables
load_dotenv('.env')
CLIENT_ID = os.getenv('CLIENT_KEY')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))


intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True
intents.dm_messages = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ---------------------------
# Dropdown Menus
# ---------------------------

class WoWClassSelect(discord.ui.Select):
    def __init__(self):
        classes = [
            "Warrior", "Paladin", "Hunter", "Rogue", "Priest",
            "Death Knight", "Shaman", "Mage", "Warlock",
            "Monk", "Druid", "Demon Hunter", "Evoker"
        ]

        options = [discord.SelectOption(label=c, value=c.lower()) for c in classes]

        super().__init__(
            placeholder="Choose your World of Warcraft class",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="wow_class"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"You selected **{self.values[0].title()}** as your class.",
            ephemeral=True
        )


class PrimaryRoleSelect(discord.ui.Select):
    def __init__(self):
        roles = ["Tank", "Healer", "DPS"]
        options = [discord.SelectOption(label=r, value=r.lower()) for r in roles]

        super().__init__(
            placeholder="Choose your primary role",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="primary_role"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Primary role set to **{self.values[0].upper()}**.",
            ephemeral=True
        )


class SecondaryRoleSelect(discord.ui.Select):
    def __init__(self):
        roles = ["Tank", "Healer", "DPS"]
        options = [discord.SelectOption(label=r, value=r.lower()) for r in roles]

        super().__init__(
            placeholder="Choose your secondary role (optional)",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="secondary_role"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Secondary role set to **{self.values[0].upper()}**.",
            ephemeral=True
        )


class WoWSelectionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(WoWClassSelect())
        self.add_item(PrimaryRoleSelect())
        self.add_item(SecondaryRoleSelect())


# ---------------------------
# Reaction Listener
# ---------------------------

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return

    if payload.channel_id == TARGET_CHANNEL_ID and payload.message_id == TARGET_MESSAGE_ID:
        user = bot.get_user(payload.user_id)
        if user is None:
            return

        try:
            await user.send(
                "Choose your **World of Warcraft class** and **roles**:",
                view=WoWSelectionView()
            )
        except discord.Forbidden:
            print(f"Could not DM {user}")


# ---------------------------
# Startup
# ---------------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


bot.run(TOKEN)
