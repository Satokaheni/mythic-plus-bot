"""Sniper: per-realm auction sniping commands and check."""

from discord.ext import commands


class Sniper(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot


async def setup(bot) -> None:
    await bot.add_cog(Sniper(bot))
