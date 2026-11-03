"""Gear: officer gear audit command."""

from discord.ext import commands


class Gear(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot


async def setup(bot) -> None:
    await bot.add_cog(Gear(bot))
