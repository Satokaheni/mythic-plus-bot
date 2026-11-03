"""Forecasting: event-log harvest and the weekly forecast preview."""

from discord.ext import commands


class Forecasting(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot


async def setup(bot) -> None:
    await bot.add_cog(Forecasting(bot))
