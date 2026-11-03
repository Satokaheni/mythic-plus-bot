"""Admin: coordinator and admin commands (cleanup, setup, help) and startup tasks."""

import logging

import discord
from discord.ext import commands

import config
from cogs._checks import in_channels, is_elevated
from messages import _read_changelog, _read_last_version, _write_version
from utils import GREEN, RED, YELLOW
from views import KeyRequestButtonView

logger = logging.getLogger("discord")


class Admin(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def get_message_history(self):
        """Warm the message cache so reactions on old messages fire after a restart.

        Each fetch is best-effort: a message may have been deleted since last run
        (e.g. the availability message), which fetch_message reports as NotFound.
        Skip missing/inaccessible messages so one gone message can't abort on_ready
        or skip caching the rest.
        """
        avail_channel = self.bot.get_channel(config.AVAIL_CHANNEL_ID)
        if avail_channel and self.bot.state.availability_message_id:
            try:
                await avail_channel.fetch_message(self.bot.state.availability_message_id)
            except (discord.NotFound, discord.Forbidden):
                pass

        for cid, mid in self.bot.state.dm_map.items():
            channel = self.bot.get_channel(cid)
            if channel:
                try:
                    await channel.fetch_message(mid[1])
                except (discord.NotFound, discord.Forbidden):
                    pass

    @commands.Cog.listener("on_ready")
    async def on_ready_setup(self) -> None:
        """Called when the bot is ready."""
        if not self.bot.state.role_mentions:
            self.bot.state.role_mentions["healer"] = self.bot.get_guild(config.GUILD_ID).get_role(config.HEALER_ID)
            self.bot.state.role_mentions["tank"] = self.bot.get_guild(config.GUILD_ID).get_role(config.TANK_ID)
            self.bot.state.role_mentions["dps"] = self.bot.get_guild(config.GUILD_ID).get_role(config.DPS_ID)

        # Fetch message history now that channels are cached
        if self.bot.state.availability_message_id:
            await self.get_message_history()

        # Post changelog if the bot version has changed since last run
        last_version = _read_last_version()
        if last_version != config.BOT_VERSION:
            changelog_channel = self.bot.get_channel(config.AVAIL_CHANNEL_ID)
            entries = _read_changelog(config.BOT_VERSION)
            if changelog_channel and entries:
                changes = "\n".join(f"• {line}" for line in entries)
                changelog_msg = await changelog_channel.send(
                    f"🤖 **Bot updated to v{config.BOT_VERSION}**\n\n**What's new:**\n{changes}"
                )
                await changelog_msg.pin()
                logger.info("Posted and pinned changelog for v%s", config.BOT_VERSION)
            _write_version(config.BOT_VERSION)

        # Ensure the persistent key request button is pinned in KEY_CHANNEL
        key_channel = self.bot.get_channel(config.KEY_CHANNEL_ID)
        if key_channel:
            pins = await key_channel.pins()
            button_exists = any(p.author.id == self.bot.user.id and p.components for p in pins)
            if not button_exists:
                button_msg = await key_channel.send(
                    "⚔️ **Schedule a Mythic+ Run**\nClick the button below to create a key request.",
                    view=KeyRequestButtonView(self.bot),
                )
                await button_msg.pin()
                logger.info("on_ready: posted and pinned missing key request button in KEY_CHANNEL")

    @commands.command(name="cleanup")
    @is_elevated()
    async def cmd_cleanup(self, ctx: commands.Context) -> None:
        message = ctx.message
        if message.channel.id == config.AVAIL_CHANNEL_ID or message.channel.id == config.KEY_CHANNEL_ID:
            try:
                # Purge both channels, skipping pinned messages
                avail_channel = self.bot.get_channel(config.AVAIL_CHANNEL_ID)
                key_channel = self.bot.get_channel(config.KEY_CHANNEL_ID)
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
                self.bot.state.schedules = {}
                self.bot.state.availability = {GREEN: [], YELLOW: [], RED: []}
                self.bot.state.availability_message_id = None
                self.bot.state.dm_map = {}
                self.bot.state.dm_timestamps = {}
                for raider in self.bot.state.raiders:
                    self.bot.state.raiders[raider].current_runs = set()
                    self.bot.state.raiders[raider].denied_runs = set()

                self.bot.state.save()
                logger.info("Cleanup: state reset complete. Raiders preserved: %d", len(self.bot.state.raiders))

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

    @commands.command(name="setup")
    @is_elevated()
    @in_channels(config.KEY_CHANNEL_ID)
    async def cmd_setup(self, ctx: commands.Context) -> None:
        message = ctx.message
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass
        channel = self.bot.get_channel(config.KEY_CHANNEL_ID)
        # Unpin and delete any previous bot button messages to keep channel clean
        for pinned in await channel.pins():
            if pinned.author.id == self.bot.user.id and pinned.components:
                await pinned.unpin()
                await pinned.delete()
        button_msg = await channel.send(
            "⚔️ **Schedule a Mythic+ Run**\nClick the button below to create a key request.",
            view=KeyRequestButtonView(self.bot),
        )
        await button_msg.pin()
        logger.info("Setup: posted and pinned key request button in KEY_CHANNEL")

    @commands.command(name="help", aliases=["tools"])
    async def cmd_help(self, ctx: commands.Context) -> None:
        message = ctx.message
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
            name="🤖 Assistant (owner, experimental)",
            value="DM me, or mention me, in plain language — I'll answer or offer to run a command (writes ask for confirmation).",
            inline=False,
        )
        embed.add_field(
            name="ℹ️ Help",
            value="`!help` or `!tools` — show this message",
            inline=False,
        )
        await message.channel.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Admin(bot))
