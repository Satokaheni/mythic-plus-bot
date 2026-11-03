"""Availability: weekly availability message, reactions, and reset."""

from discord.ext import commands


class Availability(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot


async def setup(bot) -> None:
    await bot.add_cog(Availability(bot))
