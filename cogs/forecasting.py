"""Forecasting: event-log harvest and the weekly forecast preview."""

import logging
from datetime import datetime, time, timezone

import aiohttp
import discord
from discord.ext import commands, tasks

import config
import eventlog
import forecast
import raiderio
from utils import GREEN

logger = logging.getLogger("discord")


class Forecasting(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.char_mappings = raiderio.load_character_mappings()

    async def cog_load(self) -> None:
        self.raiderio_harvest.start()
        self.forecast_preview.start()

    async def cog_unload(self) -> None:
        self.raiderio_harvest.cancel()
        self.forecast_preview.cancel()

    @tasks.loop(hours=24)
    async def raiderio_harvest(self):
        """Daily Raider.io backfill/harvest: append new raiderio_run events (idempotent)."""
        if not self.bot.is_ready():
            logger.warning("raiderio_harvest: Bot not ready yet, skipping this iteration")
            return
        if not self.char_mappings:
            return
        try:
            async with aiohttp.ClientSession() as session:
                added = await raiderio.harvest(self.bot.state.raiders, session, self.char_mappings)
            logger.info("raiderio_harvest: appended %d new run events", added)
        except Exception as exc:  # noqa: BLE001 - harvest must never kill the loop
            logger.warning("raiderio_harvest failed: %s", exc)

    @raiderio_harvest.before_loop
    async def before_raiderio_harvest(self):
        await self.bot.wait_until_ready()

    @tasks.loop(time=time(hour=12, minute=0, tzinfo=config._CST))
    async def forecast_preview(self):
        """Wednesday-noon-CST dry-run: predict the best run from the green pool, DM config.BANKER_ID."""
        if datetime.now(config._CST).weekday() != 2:  # 2 = Wednesday (day after the Tuesday availability post)
            return
        if not self.bot.is_ready():
            return

        green = list(self.bot.state.availability.get(GREEN, []))
        if not forecast.can_field_team(green):
            logger.info("forecast_preview: green pool cannot field a role-valid team; skipping")
            return

        try:
            now = datetime.now(timezone.utc)
            events = eventlog.read_events()
            obs = forecast.observations(events, self.bot.state.raiders, now)
            obs_by_user = {}
            for o in obs:
                obs_by_user.setdefault(o.user_id, []).append(o)
            ranked = forecast.rank_slots(green, obs_by_user, datetime.now(config._CST))
            text = forecast.format_preview(ranked)
            banker = await self.bot.fetch_user(config.BANKER_ID)
            await banker.send(text)
            logger.info("forecast_preview: sent dry-run preview to banker")
        except discord.HTTPException as exc:
            logger.warning("forecast_preview: could not DM banker: %s", exc)
        except Exception as exc:  # noqa: BLE001 - preview must never kill the loop
            logger.warning("forecast_preview failed: %s", exc)


async def setup(bot) -> None:
    await bot.add_cog(Forecasting(bot))
