"""WowToken: WoW Token sell-threshold alert, status command, and poll loop."""

import logging

import aiohttp
import discord
from discord.ext import commands, tasks

import config
import tokenwatch
import watchlist
from cogs._checks import is_banker

logger = logging.getLogger("discord")


class WowToken(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.token_watch = tokenwatch.TokenWatch.load()

    async def cog_load(self) -> None:
        self.token_watch_check.start()

    async def cog_unload(self) -> None:
        self.token_watch_check.cancel()

    @tasks.loop(minutes=config.TOKEN_POLL_MINUTES)
    async def token_watch_check(self):
        """Poll the WoW Token price; DM the banker when it rises past their sell threshold."""
        if not self.bot.is_ready():
            logger.warning("token_watch_check: Bot not ready yet, skipping this iteration")
            return
        if self.token_watch.threshold is None:
            return

        try:
            async with aiohttp.ClientSession() as session:
                result = await self.bot.blizzard.token_price(session)
            if result is None:
                logger.warning("token_watch_check: token payload carried no price, skipping")
                return
            price, _updated = result

            # Price fell back under the threshold: reset the ratchet so the next
            # crossing alerts again. Guarded so a quiet sub-threshold price does
            # not rewrite the state file every 20 minutes.
            if tokenwatch.should_rearm(self.token_watch, price):
                self.token_watch.last_alert = None
                self.token_watch.save()
                return

            report = tokenwatch.evaluate(self.token_watch, price)
            if report is None:
                return

            banker = await self.bot.fetch_user(config.BANKER_ID)
            try:
                await banker.send(
                    tokenwatch.format_alert(report, self.token_watch.threshold, self.token_watch.last_alert)
                )
            except discord.HTTPException:
                logger.warning("token_watch_check: could not DM banker; not advancing the ratchet")
                return

            # Commit the ratchet only after the DM actually landed, so a Discord
            # failure cannot silently swallow an alert the owner never saw.
            self.token_watch.last_alert = report
            self.token_watch.save()
        except Exception as exc:  # noqa: BLE001 - a bad poll must not kill the loop
            logger.warning("token_watch_check failed: %s", exc)

    @commands.command(name="tokenalert")
    @is_banker()
    async def cmd_tokenalert(self, ctx: commands.Context) -> None:
        message = ctx.message
        if message.guild is not None:
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
        parts = message.content.split(maxsplit=1)
        if len(parts) < 2:
            await message.author.send("Usage: `!tokenalert <gold>` (e.g. `!tokenalert 300000`) or `!tokenalert off`.")
            return
        try:
            threshold = tokenwatch.parse_threshold(parts[1])
        except ValueError:
            await message.author.send(
                f"`{parts[1].strip()}` isn't a valid gold amount. "
                "Use `!tokenalert 300000`, `!tokenalert 300,000`, or `!tokenalert off`."
            )
            return
        # Setting or clearing a threshold always re-arms, so a new threshold never
        # inherits a stale ratchet position from the previous one.
        self.token_watch.threshold = threshold
        self.token_watch.last_alert = None
        self.token_watch.save()
        if threshold is None:
            await message.author.send("🔕 WoW Token alerts disabled.")
        else:
            await message.author.send(
                f"💰 WoW Token alert set: I'll DM you when the price rises above "
                f"**{watchlist.format_gold(threshold)}**."
            )

    @commands.command(name="token")
    @is_banker()
    async def cmd_token(self, ctx: commands.Context) -> None:
        message = ctx.message
        if message.guild is not None:
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
        price = None
        try:
            async with aiohttp.ClientSession() as session:
                result = await self.bot.blizzard.token_price(session)
            if result is not None:
                price = result[0]
        except Exception as exc:  # noqa: BLE001 - report state even if the API is down
            logger.warning("!token: could not fetch token price: %s", exc)
        await message.author.send(tokenwatch.format_status(self.token_watch, price))


async def setup(bot) -> None:
    await bot.add_cog(WowToken(bot))
