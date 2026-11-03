"""Admin: coordinator and admin commands (cleanup, setup, help)."""

from discord.ext import commands


class Admin(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot


async def setup(bot) -> None:
    await bot.add_cog(Admin(bot))
