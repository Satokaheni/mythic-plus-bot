"""Discord bot for managing WoW Mythic+ raid scheduling."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

import aiohttp
import discord
from discord.ext import commands, tasks

import blizzard
import cogs
import gearaudit
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
from views import (
    KeyRequestButtonView,
    ScheduleButtonView,
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



    # ---------------------------
    # Weekly Availability Reset
    # ---------------------------


    # ---------------------------
    # Weekly Forecast Dry-Run Preview
    # ---------------------------


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

        if not self.price_watch_check.is_running():
            self.price_watch_check.start()

        if not self.auction_snipe_check.is_running():
            self.auction_snipe_check.start()

        if not self.token_watch_check.is_running():
            self.token_watch_check.start()

    async def on_ready(self):
        """Called when the bot is ready."""
        logger.info("Logged in as %s", self.user)

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


    # ---------------------------
    # Reaction Listener
    # ---------------------------

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
