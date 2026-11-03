"""Sniper: per-realm auction snipe commands and the periodic realm sweep."""

import logging
from datetime import datetime, timedelta, timezone

import aiohttp
from discord.ext import commands, tasks

import blizzard
import config
import snipelist as snipelist_mod

logger = logging.getLogger("discord")


class Sniper(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.snipelist = snipelist_mod.Snipelist.load()
        self._snipe_realm_ids: list = []  # cached connected-realm ids
        self._snipe_price_cache: dict = {}  # realm_id -> {(kind,key_id): (price, qty)}
        self._snipe_realm_modified: dict = {}  # realm_id -> Last-Modified str
        self._snipe_realm_ids_fetched = None

    async def cog_load(self) -> None:
        self.auction_snipe_check.start()

    async def cog_unload(self) -> None:
        self.auction_snipe_check.cancel()

    @tasks.loop(minutes=config.SNIPE_SWEEP_MINUTES)
    async def auction_snipe_check(self):
        """Sweep every region realm; DM subscribers when a snipe's cheapest price beats target."""
        if not self.bot.is_ready():
            logger.warning("auction_snipe_check: not ready, skipping")
            return
        keys = self.snipelist.watched_keys()
        if not keys:
            return

        async with aiohttp.ClientSession() as session:
            try:
                now = datetime.now(timezone.utc)
                stale = (
                    self._snipe_realm_ids_fetched is None
                    or (now - self._snipe_realm_ids_fetched) >= timedelta(hours=24)
                )
                if not self._snipe_realm_ids or stale:
                    self._snipe_realm_ids = await self.bot.blizzard.list_connected_realms(session)
                    self._snipe_realm_ids_fetched = now
            except Exception as exc:  # noqa: BLE001
                logger.warning("auction_snipe_check: realm list failed: %s", exc)
                return

            # Refresh each realm's watched-item prices (conditional; 304 -> reuse cache).
            for realm_id in self._snipe_realm_ids:
                try:
                    result = await self.bot.blizzard.get_realm_auctions(
                        session, realm_id, self._snipe_realm_modified.get(realm_id)
                    )
                    if result is blizzard.NOT_MODIFIED:
                        continue
                    auctions, last_modified = result
                    prices = {}
                    for kind, key_id in keys:
                        bp = snipelist_mod.best_price_for(auctions, kind, key_id)
                        if bp is not None:
                            prices[(kind, key_id)] = bp
                    self._snipe_price_cache[realm_id] = prices
                    self._snipe_realm_modified[realm_id] = last_modified
                except Exception as exc:  # noqa: BLE001 - one bad realm must not kill the sweep
                    logger.warning("auction_snipe_check: realm %s failed: %s", realm_id, exc)

            # Aggregate cheapest-anywhere per key and decide alerts.
            for snipe in self.snipelist.all():
                try:
                    key = (snipe.kind, snipe.key_id)
                    realm_prices = {
                        rid: prices[key] for rid, prices in self._snipe_price_cache.items() if key in prices
                    }
                    best = snipelist_mod.cheapest(realm_prices)
                    plans = snipelist_mod.plan_alerts(snipe, best, config.BANKER_ID)
                    if not plans or best is None:
                        continue
                    realm = await self.bot.blizzard.realm_name(session, best[2])
                    for plan in plans:
                        await self._send_snipe_dm(session, snipe, best, realm, plan)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("auction_snipe_check: snipe %s failed: %s", snipe.key_id, exc)

        self.snipelist.save()

    async def _send_snipe_dm(self, session, snipe, best, realm, plan):
        try:
            user = await self.bot.fetch_user(plan.recipient_id)
            if plan.is_banker:
                wanters = [(f"<@{uid}>", s.target_copper) for uid, s in snipe.subscribers.items()]
                await user.send(snipelist_mod.format_banker_alert(snipe, best, realm, wanters))
            else:
                await user.send(snipelist_mod.format_alert(snipe, best, realm, plan.target_copper))
        except Exception as exc:  # noqa: BLE001 - one bad DM must not abort the fan-out
            logger.warning("auction_snipe_check: could not DM %s: %s", plan.recipient_id, exc)

    @commands.command(name="snipe")
    async def cmd_snipe(self, ctx: commands.Context) -> None:
        message = ctx.message
        args = message.content.split()[1:]
        if len(args) < 2 or not args[0].isdigit():
            await message.channel.send("Usage: `!snipe <itemId> <maxGold> [label]`")
            return
        item_id = int(args[0])
        target = snipelist_mod.parse_gold(args[1])
        if target is None:
            await message.channel.send("Max price must be a positive number of gold, e.g. `!snipe 194123 5000`.")
            return
        async with aiohttp.ClientSession() as session:
            try:
                info = await self.bot.blizzard.item_info(session, item_id)
                label = " ".join(args[2:]) if len(args) > 2 else info.name or f"Item {item_id}"
                is_recipe = info.is_recipe
            except Exception as exc:  # noqa: BLE001
                logger.warning("snipe item_info failed for %s: %s", item_id, exc)
                existing = self.snipelist.get("item", item_id)
                label = " ".join(args[2:]) if len(args) > 2 else (existing.label if existing else f"Item {item_id}")
                is_recipe = existing.is_recipe if existing else False
        self.snipelist.subscribe(message.author.id, "item", item_id, target, label, is_recipe)
        self.snipelist.save()
        note = " (recipe — the banker is also alerted)" if is_recipe else ""
        await message.channel.send(
            f"🎯 Sniping **{label}** (item {item_id}) under {snipelist_mod.format_gold(target)}{note}."
        )
        return

    @commands.command(name="snipepet")
    async def cmd_snipepet(self, ctx: commands.Context) -> None:
        message = ctx.message
        args = message.content.split()[1:]
        if len(args) < 2 or not args[0].isdigit():
            await message.channel.send("Usage: `!snipepet <speciesId> <maxGold> [label]`")
            return
        species_id = int(args[0])
        target = snipelist_mod.parse_gold(args[1])
        if target is None:
            await message.channel.send("Max price must be a positive number of gold.")
            return
        async with aiohttp.ClientSession() as session:
            try:
                name = await self.bot.blizzard.pet_name(session, species_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("snipe pet_name failed for %s: %s", species_id, exc)
                name = f"Pet {species_id}"
        label = " ".join(args[2:]) if len(args) > 2 else name
        self.snipelist.subscribe(message.author.id, "pet", species_id, target, label, False)
        self.snipelist.save()
        await message.channel.send(
            f"🎯 Sniping pet **{label}** (species {species_id}) under {snipelist_mod.format_gold(target)}."
        )
        return

    @commands.command(name="unsnipe")
    async def cmd_unsnipe(self, ctx: commands.Context) -> None:
        message = ctx.message
        args = message.content.split()[1:]
        if not args or not all(a.isdigit() for a in args):
            await message.channel.send("Usage: `!unsnipe <id ...>`")
            return
        removed = []
        for a in args:
            iid = int(a)
            # a bare id may be an item or a pet the caller subscribes to; try both
            if self.snipelist.unsubscribe(message.author.id, "item", iid):
                removed.append(iid)
            elif self.snipelist.unsubscribe(message.author.id, "pet", iid):
                removed.append(iid)
        if removed:
            self.snipelist.save()
            await message.channel.send(f"🚫 Stopped sniping: {', '.join(str(i) for i in removed)}.")
        else:
            await message.channel.send("You weren't sniping any of those.")
        return

    @commands.command(name="snipes")
    async def cmd_snipes(self, ctx: commands.Context) -> None:
        message = ctx.message
        snipes = self.snipelist.for_owner(message.author.id)
        if not snipes:
            await message.channel.send("You aren't sniping anything. Add one with `!snipe <itemId> <maxGold>`.")
            return
        lines = [snipelist_mod.format_snipe_line(s, s.subscribers[message.author.id]) for s in snipes]
        await message.author.send("**Your snipes:**\n" + "\n".join(lines))
        return


async def setup(bot) -> None:
    await bot.add_cog(Sniper(bot))
