"""PriceWatch: Undermine price-watch commands and hourly check."""

from discord.ext import commands


class PriceWatch(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot


async def setup(bot) -> None:
    await bot.add_cog(PriceWatch(bot))
