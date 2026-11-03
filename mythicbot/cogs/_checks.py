"""Command gates mirroring the author/channel conditions bot.py used before commands.Bot."""

from discord.ext import commands

from mythicbot import config


def is_banker():
    return commands.check(lambda ctx: ctx.author.id == config.BANKER_ID)


def is_elevated():
    return commands.check(lambda ctx: ctx.author.id in config.ELEVATED_IDS)


def in_channels(*channel_ids: int):
    return commands.check(lambda ctx: ctx.guild is not None and ctx.channel.id in channel_ids)
