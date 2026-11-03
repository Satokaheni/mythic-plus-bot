"""PriceWatch: Undermine price-watch commands and hourly check."""

import logging
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands, tasks

import config
import undermine
import watchlist
from cogs._checks import is_banker
from watchlist import Watchlist

logger = logging.getLogger("discord")


class PriceWatch(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.watchlist = Watchlist.load()

    async def cog_load(self) -> None:
        self.price_watch_check.start()

    async def cog_unload(self) -> None:
        self.price_watch_check.cancel()

    @tasks.loop(hours=1)
    async def price_watch_check(self):
        """Hourly sweep of watched commodities; DMs the banker on a buy signal."""
        if not self.bot.is_ready():
            logger.warning("price_watch_check: Bot not ready yet, skipping this iteration")
            return

        watches = self.watchlist.all()
        if not watches:
            return

        now = datetime.now(timezone.utc)
        # Only alert during waking hours (10 AM–11:59 PM CST). Outside the window we skip the
        # alert/state transition so a still-good dip re-fires on the next in-window check.
        alert_ok = watchlist.in_alert_window(datetime.now(config._CST))
        banker = None
        async with aiohttp.ClientSession() as session:
            for watch in watches:
                try:
                    now_result = await undermine.fetch_now(session, watch.item_id)
                    if now_result is None:
                        continue
                    daily = await undermine.fetch_daily(session, watch.item_id)
                    target = watch.target_qty or config.BANKER_BULK_QTY
                    signal = watchlist.evaluate(
                        now_result.price, now_result.quantity, daily, watch, now_result.auctions, target
                    )
                    if alert_ok and watchlist.process_signal(watch, signal, now):
                        if banker is None:
                            banker = await self.bot.fetch_user(config.BANKER_ID)
                        try:
                            await banker.send(watchlist.format_alert(watch, signal, config.BANKER_BUDGET_COPPER))
                        except discord.HTTPException:
                            logger.warning("price_watch_check: could not DM banker for item %s", watch.item_id)
                    watchlist.auto_tune(watch, now)
                except Exception as exc:  # noqa: BLE001 - one bad item must not kill the sweep
                    logger.warning("price_watch_check failed for item %s: %s", watch.item_id, exc)

        self.watchlist.save()

    @commands.command(name="watch")
    @is_banker()
    async def cmd_watch(self, ctx: commands.Context) -> None:
        message = ctx.message
        args = message.content.split()[1:]
        if message.guild is not None:
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
        parsed = watchlist.parse_watch_command(args)
        if parsed["kind"] == "error":
            reason = parsed.get("reason")
            if reason == "bad_flag":
                hint = (
                    "❌ A `-x` quantity must be **attached with no space** (e.g. `-x100`, not `-x 100`). "
                    "A spaced `-x` in a multi-item command swallows the next item id."
                )
            elif reason == "multi_label":
                hint = (
                    "❌ Labels aren't supported when watching several items at once — "
                    "drop the text, or add that item on its own to give it a label."
                )
            else:
                hint = "❌ Give at least one numeric item id."
            await message.author.send(
                f"{hint}\n"
                "Usage: `!watch <itemId> [-x<qty>] [label]`  •  several at once: "
                "`!watch <id1> -x<qty> <id2> -x<qty> ...`\n"
                "Example: `!watch 241326 -x100 241322 -x100 241324 -x100`"
            )
            return
        # Two or more ids -> watch several at once (auto-labelled), each with an optional -x target.
        if parsed["kind"] == "multi":
            added, already = [], []
            for iid, iqty in parsed["items"]:
                target = iqty or config.BANKER_BULK_QTY
                if self.watchlist.get(iid) is None:
                    self.watchlist.add(iid, f"Item {iid}", iqty)
                    added.append(f"{iid} (bulk {target:,})")
                else:
                    already.append(str(iid))
            self.watchlist.save()
            reply = []
            if added:
                reply.append(f"👁️ Now watching {len(added)} item(s): {', '.join(added)}.")
            if already:
                reply.append(f"Already watching: {', '.join(already)}.")
            await message.author.send("\n".join(reply))
            return
        # Single item, with an optional multi-word label and optional -x bulk target.
        item_id = parsed["item_id"]
        label = parsed["label"] or f"Item {item_id}"
        target_qty = parsed["target_qty"]
        self.watchlist.add(item_id, label, target_qty)
        self.watchlist.save()
        qty_note = f" — bulk target **{target_qty:,}**" if target_qty else ""
        await message.author.send(f"👁️ Now watching **{label}** (item {item_id}){qty_note}.")
        return

    @commands.command(name="unwatch")
    @is_banker()
    async def cmd_unwatch(self, ctx: commands.Context) -> None:
        message = ctx.message
        args = message.content.split()[1:]
        if message.guild is not None:
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
        if not args or not all(a.isdigit() for a in args):
            await message.author.send("Usage: `!unwatch <itemId> [itemId ...]`")
            return
        removed, missing = [], []
        for a in args:
            iid = int(a)
            (removed if self.watchlist.remove(iid) else missing).append(iid)
        if removed:
            self.watchlist.save()
        reply = []
        if removed:
            reply.append(f"🚫 Stopped watching {len(removed)} item(s): {', '.join(str(i) for i in removed)}.")
        if missing:
            reply.append(f"Not being watched: {', '.join(str(i) for i in missing)}.")
        await message.author.send("\n".join(reply))
        return

    @commands.command(name="watches")
    @is_banker()
    async def cmd_watches(self, ctx: commands.Context) -> None:
        message = ctx.message
        if message.guild is not None:
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
        watches = self.watchlist.all()
        if not watches:
            await message.author.send("No items are being watched. Add one with `!watch <itemId> [label]`.")
            return
        lines = []
        async with aiohttp.ClientSession() as session:
            for watch in watches:
                try:
                    now_result = await undermine.fetch_now(session, watch.item_id)
                    daily = await undermine.fetch_daily(session, watch.item_id)
                    target = watch.target_qty or config.BANKER_BULK_QTY
                    sig = (
                        watchlist.evaluate(
                            now_result.price, now_result.quantity, daily, watch,
                            now_result.auctions, target,
                        )
                        if now_result
                        else None
                    )
                except Exception:  # noqa: BLE001 - display best-effort
                    sig = None
                lines.append(watchlist.format_watch_line(watch, sig))
        await message.author.send("**Watched items:**\n" + "\n".join(lines))
        return


async def setup(bot) -> None:
    await bot.add_cog(PriceWatch(bot))
