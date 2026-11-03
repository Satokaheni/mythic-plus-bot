"""WowToken: WoW Token sell-alert commands and check."""

from discord.ext import commands


class WowToken(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot


async def setup(bot) -> None:
    await bot.add_cog(WowToken(bot))
