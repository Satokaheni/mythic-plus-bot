"""Availability: weekly availability message, reactions, and reset."""

import logging
from datetime import datetime, time, timedelta, timezone
from textwrap import dedent

import discord
from discord.ext import commands, tasks

import config
import eventlog
from messages import AVAILABILITY_MESSAGE
from raider import Raider
from utils import GREEN, RED, YELLOW
from views import WoWSelectionView

logger = logging.getLogger("discord")


class Availability(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.weekly_avail_reset.start()

    async def cog_unload(self) -> None:
        self.weekly_avail_reset.cancel()

    async def _post_avail_message(self) -> None:
        """Post a fresh availability message and add emoji reactions. Sets availability_message_id."""
        channel = self.bot.get_channel(config.AVAIL_CHANNEL_ID)
        if not channel:
            logger.warning("_post_avail_message: AVAIL_CHANNEL not found")
            return
        date_start = datetime.now(timezone.utc)
        date_end = (date_start + timedelta(days=7)).timestamp()
        msg = await channel.send(
            dedent(
                AVAILABILITY_MESSAGE(
                    date_start.timestamp(),
                    date_end,
                    self.bot.get_guild(config.GUILD_ID).get_role(config.MYTHIC_PLUS_ID).mention,
                )
            )
        )
        self.bot.state.availability_message_id = msg.id
        await msg.add_reaction(GREEN)
        await msg.add_reaction(YELLOW)
        await msg.add_reaction(RED)

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

    @tasks.loop(time=time(hour=12, minute=0, tzinfo=config._CST))
    async def weekly_avail_reset(self):
        """Reset the availability message every Tuesday at noon CST."""
        if datetime.now(config._CST).weekday() != 1:  # 1 = Tuesday
            return

        avail_channel = self.bot.get_channel(config.AVAIL_CHANNEL_ID)

        # Delete the old availability message
        if avail_channel and self.bot.state.availability_message_id:
            try:
                old_msg = await avail_channel.fetch_message(self.bot.state.availability_message_id)
                await old_msg.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

        # Reset availability state and post fresh message
        self.bot.state.availability = {GREEN: [], YELLOW: [], RED: []}
        self.bot.state.availability_message_id = None
        await self._post_avail_message()
        logger.info("weekly_avail_reset: availability reset and new message posted")

        self.bot.state.save()

    @commands.command(name="avail")
    async def cmd_avail(self, ctx: commands.Context) -> None:
        message = ctx.message
        if message.guild is None:
            return
        if message.channel.id == config.KEY_CHANNEL_ID:
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                logger.warning(f"Could not delete command from {message.author} (message not found or forbidden)")
            return
        if message.channel.id == config.AVAIL_CHANNEL_ID and message.author.id in config.ELEVATED_IDS:
            await self._post_avail_message()
            try:
                await message.delete()
            except discord.Forbidden:
                logger.warning(f"Could not delete !avail commands from {message.author}")

    @commands.Cog.listener("on_reaction_add")
    async def on_availability_reaction(self, reaction: discord.Reaction, user: discord.Member) -> None:
        if user.id == self.bot.user.id:
            return
        if not (
            reaction.message.channel.id == config.AVAIL_CHANNEL_ID
            and reaction.message.id == self.bot.state.availability_message_id
            and reaction.emoji in [GREEN, YELLOW, RED]
        ):
            return

        if user.id in self.bot.state.raiders:
            # Check if they're already in any availability list and remove them
            if self.bot.state.raiders[user.id] in self.bot.state.availability[GREEN]:
                self.bot.state.availability[GREEN].remove(self.bot.state.raiders[user.id])
            if self.bot.state.raiders[user.id] in self.bot.state.availability[YELLOW]:
                self.bot.state.availability[YELLOW].remove(self.bot.state.raiders[user.id])
            if self.bot.state.raiders[user.id] in self.bot.state.availability[RED]:
                self.bot.state.availability[RED].remove(self.bot.state.raiders[user.id])

            if self.bot.state.raiders[user.id] not in self.bot.state.availability[reaction.emoji]:
                self.bot.state.availability[reaction.emoji].append(self.bot.state.raiders[user.id])
                await self.bot.new_availability_signup_fill_schedule(self.bot.state.raiders[user.id], reaction.emoji)

                self._log_avail_reaction(self.bot.state.raiders[user.id], reaction.emoji)

            self.bot.state.save()
        else:
            try:
                view = WoWSelectionView(timeout=180)
                await user.send(
                    "Choose your **World of Warcraft class** and **roles** if you have only one role please ignore the secondary selection:",
                    view=view,
                )

                await view.wait()

                roles = []
                if view.selected_primary:
                    roles.append(view.selected_primary)
                if view.selected_secondary and view.selected_secondary != view.selected_primary:
                    roles.append(view.selected_secondary)

                if view.selected_class and roles:
                    self.bot.state.raiders[user.id] = Raider(user, view.selected_class, roles, view.selected_timezone)
                    self.bot.state.availability[reaction.emoji].append(self.bot.state.raiders[user.id])
                    logger.info(
                        f"Signup from {user}: class={view.selected_class} roles={roles} timezone={view.selected_timezone}"
                    )

                    await self.bot.new_availability_signup_fill_schedule(
                        self.bot.state.raiders[user.id], reaction.emoji
                    )

                    self._log_avail_reaction(self.bot.state.raiders[user.id], reaction.emoji)

                    self.bot.state.save()
                else:
                    logger.info(f"No valid selection from {user} (timed out or incomplete)")
            except discord.Forbidden:
                logger.warning(f"Could not DM {user}")


async def setup(bot) -> None:
    await bot.add_cog(Availability(bot))
