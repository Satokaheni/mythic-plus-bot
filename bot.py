"""Discord bot for managing WoW Mythic+ raid scheduling."""

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from textwrap import dedent
from typing import Dict, List, Tuple

import aiohttp
import discord
from discord.ext import commands, tasks

import blizzard
import cogs
import eventlog
import forecast
import gearaudit
import raiderio
import snipelist as snipelist_mod
import tokenwatch
import undermine
import watchlist
from config import (  # noqa: F401  (re-exported for the harness and tests)
    _CST,
    ADMINS,
    AVAIL_CHANNEL_ID,
    BANKER_BUDGET_COPPER,
    BANKER_BULK_QTY,
    BANKER_ID,
    BLIZZ_CLIENT_ID,
    BLIZZ_CLIENT_SECRET,
    BOT_VERSION,
    CLIENT_ID,
    COORDINATOR_ID,
    DPS_ID,
    ELEVATED_IDS,
    GEAR_AUDIT_CONCURRENCY,
    GUILD_ID,
    HEALER_ID,
    KEY_CHANNEL_ID,
    MYTHIC_PLUS_ID,
    SNIPE_SWEEP_MINUTES,
    TANK_ID,
    TOKEN_POLL_MINUTES,
)
from messages import (  # noqa: F401
    AVAILABILITY_MESSAGE,
    _dm_reminder,
    _dm_spot_available,
    _read_changelog,
    _read_last_version,
    _write_version,
)
from raider import Raider
from schedule import Schedule
from state import BotState
from utils import GREEN, RED, YELLOW
from views import (
    KeyRequestButtonView,
    ScheduleButtonView,
    WoWSelectionView,
)
from watchlist import Watchlist

# ---------------------------
# Logging
# ---------------------------
logger = logging.getLogger("discord")


# ---------------------------
# Bot Class
# ---------------------------
class MyClient(commands.Bot):
    """Discord bot for Mythic+ scheduling. Features live in cogs/; this class owns startup and shared state."""

    def __init__(self, *, intents: discord.Intents) -> None:
        super().__init__(command_prefix="!", help_command=None, case_insensitive=False, intents=intents)

    # ---------------------------
    # User Defined Functions
    # ---------------------------

    async def message_user(self, raider: Raider, emoji: str, schedule: Schedule):
        await self.get_cog("Scheduling").message_user(raider, emoji, schedule)

    async def notify_schedule(self, schedule: Schedule):
        await self.get_cog("Scheduling").notify_schedule(schedule)

    async def _do_key_request_flow(self, user: discord.User):
        await self.get_cog("Scheduling")._do_key_request_flow(user)

    async def _notify_promoted(self, promoted: Raider, schedule: Schedule):
        await self.get_cog("Scheduling")._notify_promoted(promoted, schedule)

    async def _notify_displaced(self, displaced: Raider, schedule: Schedule, role: str):
        await self.get_cog("Scheduling")._notify_displaced(displaced, schedule, role)

    async def _notify_schedule_changed(self, schedule: Schedule, message: str, organizer_id: int):
        await self.get_cog("Scheduling")._notify_schedule_changed(schedule, message, organizer_id)

    async def new_availability_signup_fill_schedule(self, raider: Raider, tier: str):
        await self.get_cog("Scheduling").new_availability_signup_fill_schedule(raider, tier)

    async def get_message_history(self):
        """Warm the message cache so reactions on old messages fire after a restart.

        Each fetch is best-effort: a message may have been deleted since last run
        (e.g. the availability message), which fetch_message reports as NotFound.
        Skip missing/inaccessible messages so one gone message can't abort on_ready
        or skip caching the rest.
        """
        avail_channel = self.get_channel(AVAIL_CHANNEL_ID)
        if avail_channel and self.state.availability_message_id:
            try:
                await avail_channel.fetch_message(self.state.availability_message_id)
            except (discord.NotFound, discord.Forbidden):
                pass

        for cid, mid in self.state.dm_map.items():
            channel = self.get_channel(cid)
            if channel:
                try:
                    await channel.fetch_message(mid[1])
                except (discord.NotFound, discord.Forbidden):
                    pass

    async def _post_avail_message(self) -> None:
        """Post a fresh availability message and add emoji reactions. Sets availability_message_id."""
        channel = self.get_channel(AVAIL_CHANNEL_ID)
        if not channel:
            logger.warning("_post_avail_message: AVAIL_CHANNEL not found")
            return
        date_start = datetime.now(timezone.utc)
        date_end = (date_start + timedelta(days=7)).timestamp()
        msg = await channel.send(
            dedent(
                AVAILABILITY_MESSAGE(
                    date_start.timestamp(), date_end, self.get_guild(GUILD_ID).get_role(MYTHIC_PLUS_ID).mention
                )
            )
        )
        self.state.availability_message_id = msg.id
        await msg.add_reaction(GREEN)
        await msg.add_reaction(YELLOW)
        await msg.add_reaction(RED)

    @tasks.loop(hours=1)
    async def price_watch_check(self):
        """Hourly sweep of watched commodities; DMs the banker on a buy signal."""
        if not self.is_ready():
            logger.warning("price_watch_check: Bot not ready yet, skipping this iteration")
            return

        watches = self.watchlist.all()
        if not watches:
            return

        now = datetime.now(timezone.utc)
        # Only alert during waking hours (10 AM–11:59 PM CST). Outside the window we skip the
        # alert/state transition so a still-good dip re-fires on the next in-window check.
        alert_ok = watchlist.in_alert_window(datetime.now(_CST))
        banker = None
        async with aiohttp.ClientSession() as session:
            for watch in watches:
                try:
                    now_result = await undermine.fetch_now(session, watch.item_id)
                    if now_result is None:
                        continue
                    daily = await undermine.fetch_daily(session, watch.item_id)
                    target = watch.target_qty or BANKER_BULK_QTY
                    signal = watchlist.evaluate(
                        now_result.price, now_result.quantity, daily, watch, now_result.auctions, target
                    )
                    if alert_ok and watchlist.process_signal(watch, signal, now):
                        if banker is None:
                            banker = await self.fetch_user(BANKER_ID)
                        try:
                            await banker.send(watchlist.format_alert(watch, signal, BANKER_BUDGET_COPPER))
                        except discord.HTTPException:
                            logger.warning("price_watch_check: could not DM banker for item %s", watch.item_id)
                    watchlist.auto_tune(watch, now)
                except Exception as exc:  # noqa: BLE001 - one bad item must not kill the sweep
                    logger.warning("price_watch_check failed for item %s: %s", watch.item_id, exc)

        self.watchlist.save()

    @tasks.loop(minutes=TOKEN_POLL_MINUTES)
    async def token_watch_check(self):
        """Poll the WoW Token price; DM the banker when it rises past their sell threshold."""
        if not self.is_ready():
            logger.warning("token_watch_check: Bot not ready yet, skipping this iteration")
            return
        if self.token_watch.threshold is None:
            return

        try:
            async with aiohttp.ClientSession() as session:
                result = await self.blizzard.token_price(session)
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

            banker = await self.fetch_user(BANKER_ID)
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

    @tasks.loop(minutes=SNIPE_SWEEP_MINUTES)
    async def auction_snipe_check(self):
        """Sweep every region realm; DM subscribers when a snipe's cheapest price beats target."""
        if not self.is_ready():
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
                    self._snipe_realm_ids = await self.blizzard.list_connected_realms(session)
                    self._snipe_realm_ids_fetched = now
            except Exception as exc:  # noqa: BLE001
                logger.warning("auction_snipe_check: realm list failed: %s", exc)
                return

            # Refresh each realm's watched-item prices (conditional; 304 -> reuse cache).
            for realm_id in self._snipe_realm_ids:
                try:
                    result = await self.blizzard.get_realm_auctions(
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
                    plans = snipelist_mod.plan_alerts(snipe, best, BANKER_ID)
                    if not plans or best is None:
                        continue
                    realm = await self.blizzard.realm_name(session, best[2])
                    for plan in plans:
                        await self._send_snipe_dm(session, snipe, best, realm, plan)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("auction_snipe_check: snipe %s failed: %s", snipe.key_id, exc)

        self.snipelist.save()

    async def _send_snipe_dm(self, session, snipe, best, realm, plan):
        try:
            user = await self.fetch_user(plan.recipient_id)
            if plan.is_banker:
                wanters = [(f"<@{uid}>", s.target_copper) for uid, s in snipe.subscribers.items()]
                await user.send(snipelist_mod.format_banker_alert(snipe, best, realm, wanters))
            else:
                await user.send(snipelist_mod.format_alert(snipe, best, realm, plan.target_copper))
        except Exception as exc:  # noqa: BLE001 - one bad DM must not abort the fan-out
            logger.warning("auction_snipe_check: could not DM %s: %s", plan.recipient_id, exc)

    @tasks.loop(hours=24)
    async def raiderio_harvest(self):
        """Daily Raider.io backfill/harvest: append new raiderio_run events (idempotent)."""
        if not self.is_ready():
            logger.warning("raiderio_harvest: Bot not ready yet, skipping this iteration")
            return
        if not self._char_mappings:
            return
        try:
            async with aiohttp.ClientSession() as session:
                added = await raiderio.harvest(self.state.raiders, session, self._char_mappings)
            logger.info("raiderio_harvest: appended %d new run events", added)
        except Exception as exc:  # noqa: BLE001 - harvest must never kill the loop
            logger.warning("raiderio_harvest failed: %s", exc)

    @raiderio_harvest.before_loop
    async def before_raiderio_harvest(self):
        await self.wait_until_ready()

    # ---------------------------
    # Weekly Availability Reset
    # ---------------------------

    @tasks.loop(time=time(hour=12, minute=0, tzinfo=_CST))
    async def weekly_avail_reset(self):
        """Reset the availability message every Tuesday at noon CST."""
        if datetime.now(_CST).weekday() != 1:  # 1 = Tuesday
            return

        avail_channel = self.get_channel(AVAIL_CHANNEL_ID)

        # Delete the old availability message
        if avail_channel and self.state.availability_message_id:
            try:
                old_msg = await avail_channel.fetch_message(self.state.availability_message_id)
                await old_msg.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

        # Reset availability state and post fresh message
        self.state.availability = {GREEN: [], YELLOW: [], RED: []}
        self.state.availability_message_id = None
        await self._post_avail_message()
        logger.info("weekly_avail_reset: availability reset and new message posted")

        self.state.save()

    # ---------------------------
    # Weekly Forecast Dry-Run Preview
    # ---------------------------

    @tasks.loop(time=time(hour=12, minute=0, tzinfo=_CST))
    async def forecast_preview(self):
        """Wednesday-noon-CST dry-run: predict the best run from the green pool, DM BANKER_ID."""
        if datetime.now(_CST).weekday() != 2:  # 2 = Wednesday (day after the Tuesday availability post)
            return
        if not self.is_ready():
            return

        green = list(self.state.availability.get(GREEN, []))
        if not forecast.can_field_team(green):
            logger.info("forecast_preview: green pool cannot field a role-valid team; skipping")
            return

        try:
            now = datetime.now(timezone.utc)
            events = eventlog.read_events()
            obs = forecast.observations(events, self.state.raiders, now)
            obs_by_user = {}
            for o in obs:
                obs_by_user.setdefault(o.user_id, []).append(o)
            ranked = forecast.rank_slots(green, obs_by_user, datetime.now(_CST))
            text = forecast.format_preview(ranked)
            banker = await self.fetch_user(BANKER_ID)
            await banker.send(text)
            logger.info("forecast_preview: sent dry-run preview to banker")
        except discord.HTTPException as exc:
            logger.warning("forecast_preview: could not DM banker: %s", exc)
        except Exception as exc:  # noqa: BLE001 - preview must never kill the loop
            logger.warning("forecast_preview failed: %s", exc)

    # ---------------------------
    # Startup
    # ---------------------------
    async def setup_hook(self) -> None:
        """Calls before on ready to set up all environmental variables"""
        self.state = BotState.load()
        self.coordinator_id = COORDINATOR_ID
        self.elevated_ids = ELEVATED_IDS
        self.watchlist = Watchlist.load()
        self.blizzard = blizzard.BlizzardClient()
        self.snipelist = snipelist_mod.Snipelist.load()
        self.token_watch = tokenwatch.TokenWatch.load()
        self._snipe_realm_ids: list = []  # cached connected-realm ids
        self._snipe_price_cache: dict = {}  # realm_id -> {(kind,key_id): (price, qty)}
        self._snipe_realm_modified: dict = {}  # realm_id -> Last-Modified str
        self._snipe_realm_ids_fetched = None
        self._char_mappings = raiderio.load_character_mappings()
        logger.info("Loaded state from file.")

        # Register persistent views so buttons work after bot restarts
        self.add_view(KeyRequestButtonView(self))

        # Re-register each existing schedule's button view so interactions
        # still work after a restart (discord.py routes by message_id)
        for schedule_id, schedule in self.state.schedules.items():
            self.add_view(ScheduleButtonView(schedule, self), message_id=schedule_id)
        logger.info("Re-registered %d schedule views", len(self.state.schedules))

        for extension in cogs.EXTENSIONS:
            await self.load_extension(extension)

            self.weekly_avail_reset.start()

        if not self.price_watch_check.is_running():
            self.price_watch_check.start()

        if not self.raiderio_harvest.is_running():
            self.raiderio_harvest.start()

        if not self.forecast_preview.is_running():
            self.forecast_preview.start()

        if not self.auction_snipe_check.is_running():
            self.auction_snipe_check.start()

        if not self.token_watch_check.is_running():
            self.token_watch_check.start()

    async def on_ready(self):
        """Called when the bot is ready."""
        logger.info("Logged in as %s", self.user)

        if not self.state.role_mentions:
            self.state.role_mentions["healer"] = self.get_guild(GUILD_ID).get_role(HEALER_ID)
            self.state.role_mentions["tank"] = self.get_guild(GUILD_ID).get_role(TANK_ID)
            self.state.role_mentions["dps"] = self.get_guild(GUILD_ID).get_role(DPS_ID)

        # Fetch message history now that channels are cached
        if self.state.availability_message_id:
            await self.get_message_history()

        # Post changelog if the bot version has changed since last run
        last_version = _read_last_version()
        if last_version != BOT_VERSION:
            changelog_channel = self.get_channel(AVAIL_CHANNEL_ID)
            entries = _read_changelog(BOT_VERSION)
            if changelog_channel and entries:
                changes = "\n".join(f"• {line}" for line in entries)
                changelog_msg = await changelog_channel.send(
                    f"🤖 **Bot updated to v{BOT_VERSION}**\n\n**What's new:**\n{changes}"
                )
                await changelog_msg.pin()
                logger.info("Posted and pinned changelog for v%s", BOT_VERSION)
            _write_version(BOT_VERSION)

        # Ensure the persistent key request button is pinned in KEY_CHANNEL
        key_channel = self.get_channel(KEY_CHANNEL_ID)
        if key_channel:
            pins = await key_channel.pins()
            button_exists = any(p.author.id == self.user.id and p.components for p in pins)
            if not button_exists:
                button_msg = await key_channel.send(
                    "⚔️ **Schedule a Mythic+ Run**\nClick the button below to create a key request.",
                    view=KeyRequestButtonView(self),
                )
                await button_msg.pin()
                logger.info("on_ready: posted and pinned missing key request button in KEY_CHANNEL")

    async def _run_gear_audit(
        self, mappings: List[dict]
    ) -> Tuple[List[gearaudit.CharacterFindings], List[Tuple[str, str, str]]]:
        """Fetch and audit every mapped character. Returns (findings, failures).

        One character's failure never ends the sweep — it is collected and reported, since a
        404 usually means a rename or transfer the mapping file hasn't caught up with.
        """
        semaphore = asyncio.Semaphore(GEAR_AUDIT_CONCURRENCY)
        findings: List[gearaudit.CharacterFindings] = []
        failures: List[Tuple[str, str, str]] = []

        async with aiohttp.ClientSession() as session:

            async def audit_one(entry: dict) -> None:
                character = entry.get("character") or ""
                realm_slug = entry.get("realm_slug") or ""
                realm_name = entry.get("realm_name") or realm_slug
                if not character or not realm_slug:
                    # Report it rather than dropping it silently — a half-filled mapping entry is
                    # exactly the kind of drift this command is meant to surface.
                    failures.append(
                        (character or "(unnamed entry)", realm_name or "?", "incomplete mapping entry")
                    )
                    return
                # The semaphore wraps the whole body, not just the equipment fetch: the per-gem
                # item_info lookups are Blizzard calls too, and bounding only the first request
                # would let ~30 characters' worth of gem lookups fan out at once.
                async with semaphore:
                    try:
                        items = await self.blizzard.character_equipment(session, realm_slug, character)
                    except Exception as exc:  # noqa: BLE001 - one bad character must not end the sweep
                        logger.warning("gearaudit: %s/%s failed: %s", realm_slug, character, exc)
                        failures.append((character, realm_name, "fetch failed"))
                        return
                    if items is None:
                        failures.append(
                            (character, realm_name, "not found — renamed, transferred, or a stale mapping")
                        )
                        return
                    gem_quality: Dict[int, str] = {}
                    for gem_id in gearaudit.gem_ids(items):
                        try:
                            gem_quality[gem_id] = (await self.blizzard.item_info(session, gem_id)).quality
                        except Exception as exc:  # noqa: BLE001 - an unknown gem is simply not graded
                            logger.warning("gearaudit: gem %s lookup failed: %s", gem_id, exc)
                    findings.append(gearaudit.audit_character(character, realm_name, items, gem_quality))

            await asyncio.gather(*(audit_one(entry) for entry in mappings), return_exceptions=True)

        return findings, failures

    # ---------------------------
    # Message Listener
    # ---------------------------
    async def on_message(self, message: discord.Message):
        """Handle incoming messages, responding to commands like !avail and !key."""
        if message.author.id == self.user.id:
            return

        await self.process_commands(message)

        if message.content.startswith("!watch ") and message.author.id == BANKER_ID:
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
                    target = iqty or BANKER_BULK_QTY
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

        if message.content.startswith("!unwatch ") and message.author.id == BANKER_ID:
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

        if message.content == "!watches" and message.author.id == BANKER_ID:
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
                        target = watch.target_qty or BANKER_BULK_QTY
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

        if message.content.startswith("!gearaudit") and message.author.id in ELEVATED_IDS:
            if message.guild is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
            wanted = message.content[len("!gearaudit"):].strip()
            mappings = raiderio.load_character_mappings()
            if wanted:
                mappings = [m for m in mappings if (m.get("character") or "").lower() == wanted.lower()]
                if not mappings:
                    await message.author.send(f"No mapping for `{wanted}`.")
                    return
            if not mappings:
                await message.author.send("No characters are mapped, so there is nothing to audit.")
                return
            try:
                findings, failures = await self._run_gear_audit(mappings)
            except Exception as exc:  # noqa: BLE001 - a failed audit must not kill on_message
                logger.warning("gearaudit failed: %s", exc)
                await message.author.send("The gear audit failed. Check the logs.")
                return
            for chunk in gearaudit.format_report(findings, failures):
                await message.author.send(chunk)
            return

        if message.content.startswith("!tokenalert") and message.author.id == BANKER_ID:
            if message.guild is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
            parts = message.content.split(maxsplit=1)
            if len(parts) < 2:
                await message.author.send(
                    "Usage: `!tokenalert <gold>` (e.g. `!tokenalert 300000`) or `!tokenalert off`."
                )
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
            return

        if message.content == "!token" and message.author.id == BANKER_ID:
            if message.guild is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
            price = None
            try:
                async with aiohttp.ClientSession() as session:
                    result = await self.blizzard.token_price(session)
                if result is not None:
                    price = result[0]
            except Exception as exc:  # noqa: BLE001 - report state even if the API is down
                logger.warning("!token: could not fetch token price: %s", exc)
            await message.author.send(tokenwatch.format_status(self.token_watch, price))
            return

        if message.content.startswith("!snipe ") and not message.content.startswith("!snipepet "):
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
                    info = await self.blizzard.item_info(session, item_id)
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

        if message.content.startswith("!snipepet "):
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
                    name = await self.blizzard.pet_name(session, species_id)
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

        if message.content.startswith("!unsnipe "):
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

        if message.content == "!snipes":
            snipes = self.snipelist.for_owner(message.author.id)
            if not snipes:
                await message.channel.send("You aren't sniping anything. Add one with `!snipe <itemId> <maxGold>`.")
                return
            lines = [snipelist_mod.format_snipe_line(s, s.subscribers[message.author.id]) for s in snipes]
            await message.author.send("**Your snipes:**\n" + "\n".join(lines))
            return

        if message.content in ("!help", "!tools"):
            embed = discord.Embed(
                title="🤖 Mythic+ Bot — Commands",
                description="Here's everything I can do. Most commands work in a DM or the relevant channel.",
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="📅 Mythic+ Scheduling (anyone)",
                value=(
                    "`!key` — start a key request (in the key channel)\n"
                    "`!keys` — show your scheduled runs\n"
                    "`!modify` — update your class, roles, or timezone"
                ),
                inline=False,
            )
            embed.add_field(
                name="🗓️ Availability & Setup (coordinator/admin)",
                value=(
                    "`!avail` — post the weekly availability message\n"
                    "`!setup` — re-post the key-request button\n"
                    "`!cleanup` — purge the channel and reset state"
                ),
                inline=False,
            )
            embed.add_field(
                name="🔍 Gear Audit (coordinator/admin)",
                value=(
                    "`!gearaudit` — check every mapped character for missing enchants, "
                    "empty sockets, and low-quality enchants/gems\n"
                    "`!gearaudit <character>` — check one character"
                ),
                inline=False,
            )
            embed.add_field(
                name="💰 Price Watch — region commodities (banker)",
                value=(
                    "`!watch <itemId> [-x<qty>] [label]` — watch a commodity; `-x` sets a bulk target\n"
                    "`!watch <id1> <id2> …` — watch several at once\n"
                    "`!unwatch <itemId …>` — stop watching\n"
                    "`!watches` — list your watches"
                ),
                inline=False,
            )
            embed.add_field(
                name="🪙 WoW Token — sell signal (banker)",
                value=(
                    "`!token` — current token price and your alert state\n"
                    "`!tokenalert <gold>` — DM me when the price rises above this\n"
                    "`!tokenalert off` — disable token alerts"
                ),
                inline=False,
            )
            embed.add_field(
                name="🎯 Auction Sniper — per-realm items (anyone)",
                value=(
                    "`!snipe <itemId> <maxGold> [label]` — alert when an item is under your price on any realm\n"
                    "`!snipepet <speciesId> <maxGold> [label]` — same, for a battle pet\n"
                    "`!unsnipe <id …>` — stop sniping\n"
                    "`!snipes` — list your snipes"
                ),
                inline=False,
            )
            embed.add_field(
                name="ℹ️ Help",
                value="`!help` or `!tools` — show this message",
                inline=False,
            )
            await message.channel.send(embed=embed)
            return

        if message.content == "!cleanup" and message.author.id in ELEVATED_IDS:
            if message.channel.id == AVAIL_CHANNEL_ID or message.channel.id == KEY_CHANNEL_ID:
                try:
                    # Purge both channels, skipping pinned messages
                    avail_channel = self.get_channel(AVAIL_CHANNEL_ID)
                    key_channel = self.get_channel(KEY_CHANNEL_ID)
                    avail_pins = {m.id for m in await avail_channel.pins()}
                    key_pins = {m.id for m in await key_channel.pins()}
                    avail_deleted = await avail_channel.purge(check=lambda m: m.id not in avail_pins)
                    key_deleted = await key_channel.purge(check=lambda m: m.id not in key_pins)
                    logger.info(
                        "Cleanup: purged %d messages from AVAIL and %d from KEY channel.",
                        len(avail_deleted),
                        len(key_deleted),
                    )

                    # Reset all state except raiders
                    self.state.schedules = {}
                    self.state.availability = {GREEN: [], YELLOW: [], RED: []}
                    self.state.availability_message_id = None
                    self.state.dm_map = {}
                    self.state.dm_timestamps = {}
                    for raider in self.state.raiders:
                        self.state.raiders[raider].current_runs = set()
                        self.state.raiders[raider].denied_runs = set()

                    self.state.save()
                    logger.info("Cleanup: state reset complete. Raiders preserved: %d", len(self.state.raiders))

                    await message.author.send(
                        "✅ Cleanup complete. Both channels have been purged and all state (except raiders) has been reset."
                    )
                except discord.Forbidden:
                    logger.warning("Cleanup: missing permissions to purge one or both channels.")
                    await message.author.send("❌ Missing permissions to purge one or both channels.")
                except Exception as exc:
                    logger.exception("Cleanup: unexpected error: %s", exc)
                    await message.author.send("❌ An unexpected error occurred during cleanup.")
            else:
                await message.author.send("❌ `!cleanup` must be used in the availability or key channel.")
            return

        if message.channel.id == AVAIL_CHANNEL_ID or message.channel.id == KEY_CHANNEL_ID:
            if (
                message.content == "!avail"
                and message.author.id in ELEVATED_IDS
                and message.channel.id == AVAIL_CHANNEL_ID
            ):
                await self._post_avail_message()

                try:
                    await message.delete()
                except discord.Forbidden:
                    logger.warning(f"Could not delete !avail commands from {message.author}")

            elif (
                message.content == "!setup"
                and message.author.id in ELEVATED_IDS
                and message.channel.id == KEY_CHANNEL_ID
            ):
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
                channel = self.get_channel(KEY_CHANNEL_ID)
                # Unpin and delete any previous bot button messages to keep channel clean
                for pinned in await channel.pins():
                    if pinned.author.id == self.user.id and pinned.components:
                        await pinned.unpin()
                        await pinned.delete()
                button_msg = await channel.send(
                    "⚔️ **Schedule a Mythic+ Run**\nClick the button below to create a key request.",
                    view=KeyRequestButtonView(self),
                )
                await button_msg.pin()
                logger.info("Setup: posted and pinned key request button in KEY_CHANNEL")

            elif message.content in ["!key", "!avail"] and message.channel.id == KEY_CHANNEL_ID:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    logger.warning(f"Could not delete command from {message.author} (message not found or forbidden)")

    def _log_avail_reaction(self, raider, emoji) -> None:
        """Log an availability-reaction event (best-effort)."""
        now = datetime.now(timezone.utc)
        eventlog.log_event(
            "avail_reaction",
            ts_utc=now,
            user_id=raider.user_id,
            tz=raider.timezone,
            emoji=str(emoji),
            week_of=eventlog.week_of(now),
        )

    # ---------------------------
    # Reaction Listener
    # ---------------------------
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.Member):
        """Handle reaction additions for availability signup or schedule signup."""

        if user.id == self.user.id:
            return

        logger.info(
            f"Reaction added: {user.display_name}, {reaction.emoji} Raider: {user.id in self.state.raiders} ID in schedules: {reaction.message.id in self.state.schedules}"
        )

        if (
            reaction.message.channel.id == AVAIL_CHANNEL_ID
            and reaction.message.id == self.state.availability_message_id
            and reaction.emoji in [GREEN, YELLOW, RED]
        ):
            if user.id in self.state.raiders:
                # Check if they're already in any availability list and remove them
                if self.state.raiders[user.id] in self.state.availability[GREEN]:
                    self.state.availability[GREEN].remove(self.state.raiders[user.id])
                if self.state.raiders[user.id] in self.state.availability[YELLOW]:
                    self.state.availability[YELLOW].remove(self.state.raiders[user.id])
                if self.state.raiders[user.id] in self.state.availability[RED]:
                    self.state.availability[RED].remove(self.state.raiders[user.id])

                if self.state.raiders[user.id] not in self.state.availability[reaction.emoji]:
                    self.state.availability[reaction.emoji].append(self.state.raiders[user.id])
                    await self.new_availability_signup_fill_schedule(self.state.raiders[user.id], reaction.emoji)

                    self._log_avail_reaction(self.state.raiders[user.id], reaction.emoji)
            else:
                try:
                    view = WoWSelectionView(timeout=180)
                    await user.send(
                        "Choose your **World of Warcraft class** and **roles** if you have only one role please ignore the secondary selection:",
                        view=view,
                    )

                    await view.wait()

                    roles = []
                    if view.selected_primary:
                        roles.append(view.selected_primary)
                    if view.selected_secondary and view.selected_secondary != view.selected_primary:
                        roles.append(view.selected_secondary)

                    if view.selected_class and roles:
                        self.state.raiders[user.id] = Raider(user, view.selected_class, roles, view.selected_timezone)
                        self.state.availability[reaction.emoji].append(self.state.raiders[user.id])
                        logger.info(
                            f"Signup from {user}: class={view.selected_class} roles={roles} timezone={view.selected_timezone}"
                        )

                        await self.new_availability_signup_fill_schedule(self.state.raiders[user.id], reaction.emoji)

                        self._log_avail_reaction(self.state.raiders[user.id], reaction.emoji)

                        self.state.save()
                    else:
                        logger.info(f"No valid selection from {user} (timed out or incomplete)")
                except discord.Forbidden:
                    logger.warning(f"Could not DM {user}")

    # ---------------------------
    # Error Handling
    # ---------------------------
    async def on_error(self, event, *args, **kwargs):
        """Log errors to the console."""
        logger.exception("Error in event %s: %s", event, args)

    async def on_command_error(self, ctx, error) -> None:
        """Unknown commands and failed gates stay silent, as before; anything else is logged."""
        if isinstance(error, (commands.CommandNotFound, commands.CheckFailure)):
            return
        logger.exception("Command %s failed", getattr(ctx.command, "qualified_name", None), exc_info=error)


# ---------------------------
# Bot Setup
# ---------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True
intents.dm_messages = True

client = MyClient(intents=intents)

if __name__ == "__main__":
    # log_handler=None: we configured logging ourselves (file + console) in _configure_logging.
    client.run(CLIENT_ID, log_handler=None)
