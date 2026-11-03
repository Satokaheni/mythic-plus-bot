"""Scheduling: key requests, signups, spot offers, fill/notify, hourly checks."""

from discord.ext import commands


class Scheduling(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot


async def setup(bot) -> None:
    await bot.add_cog(Scheduling(bot))
