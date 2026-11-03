"""Scheduling: key requests, signups, spot offers, fill/notify, hourly checks."""

import asyncio
import logging
from datetime import datetime, timezone
from textwrap import dedent
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

import config
import eventlog
from cogs._checks import in_channels
from messages import _dm_reminder, _dm_spot_available
from raider import Raider
from schedule import Schedule
from utils import GREEN, RED, YELLOW
from views import KeyRequestView, PrePostAddRaiderView, RoleSelectView, WoWSelectionView

logger = logging.getLogger("discord")


class Scheduling(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.hourly_check.start()

    async def cog_unload(self) -> None:
        self.hourly_check.cancel()

    async def message_user(self, raider: Raider, emoji: str, schedule: Schedule):
        """Send a direct message to a raider based on their availability reaction."""
        try:
            if emoji in ["✅", "❌"]:
                dm_channel = await self.bot.get_user(raider.user_id).create_dm()
                if emoji == "✅":
                    await dm_channel.send(
                        dedent(
                            f"""
                        Hello {raider.name}, you have successfully signed up for (Level {schedule.level})
                        Run on <t:{int(schedule.start_time.astimezone(timezone.utc).timestamp())}:F>.
                        See you there!
                        """
                        )
                    )
                elif emoji == "❌":
                    await dm_channel.send(
                        dedent(
                            f"""
                        Hello {raider.name}, you have successfully declined the spot for (Level {schedule.level})
                        Run on <t:{int(schedule.start_time.astimezone(timezone.utc).timestamp())}:F>.
                        """
                        )
                    )

        except discord.Forbidden:
            logger.warning(f"Could not DM {raider.name} for schedule {schedule}")

    async def repost_schedule(self, schedule: Schedule, schedule_post: int) -> Tuple[int, Schedule]:
        """
        Delete and repost message if it's been up for over 24 hours.
        """
        try:
            message = await self.bot.get_channel(config.KEY_CHANNEL_ID).fetch_message(schedule_post)
            await message.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

        embed, view, content = schedule.send_message(self.bot.state.role_mentions, self.bot)
        schedule.posted = datetime.now(timezone.utc)

        new_message = await self.bot.get_channel(config.KEY_CHANNEL_ID).send(
            content=content if content else None, embed=embed, view=view
        )

        return new_message.id, schedule

    async def retry_unanswered_dms(self):
        """Retry sending DMs for schedule requests that haven't been answered."""
        now = datetime.now(timezone.utc)
        retry_threshold = 3600 * 2  # 2 hour before retry

        dms_to_retry = []
        dms_to_delete = []

        for dm_channel_id, dm_messages in list(self.bot.state.dm_map.items()):
            for dm_message_id, schedule_id in list(dm_messages.items()):
                if (
                    dm_channel_id in self.bot.state.dm_timestamps
                    and dm_message_id in self.bot.state.dm_timestamps[dm_channel_id]
                ):
                    sent_time = self.bot.state.dm_timestamps[dm_channel_id][dm_message_id]
                    time_elapsed = (now - sent_time).total_seconds()

                    if time_elapsed >= retry_threshold:
                        schedule = self.bot.state.schedules.get(schedule_id)
                        if schedule and not schedule.is_filled() and not schedule.is_past():
                            dms_to_retry.append((dm_channel_id, dm_message_id, schedule_id, schedule))
                            dms_to_delete.append((dm_channel_id, dm_message_id))

        for dm_channel_id, old_dm_id, schedule_id, schedule in dms_to_retry:
            try:
                dm_channel = self.bot.get_channel(dm_channel_id)
                if dm_channel:
                    try:
                        old_message = await dm_channel.fetch_message(old_dm_id)
                        await old_message.delete()
                    except (discord.NotFound, discord.Forbidden):
                        pass

                # Find the raider who received this DM
                raider = None
                for uid, r in self.bot.state.raiders.items():
                    user = self.bot.get_user(uid)
                    if user:
                        r_dm = await user.create_dm()
                        if r_dm.id == dm_channel_id:
                            raider = r
                            break

                if (
                    raider
                    and schedule not in raider.denied_runs
                    and schedule not in raider.current_runs
                    and not schedule.has_raider(raider)
                ):
                    role_needed = None
                    if raider.roles[0] in schedule.missing:
                        role_needed = raider.roles[0]
                    elif len(raider.roles) > 1 and raider.roles[1] in schedule.missing:
                        role_needed = raider.roles[1]

                    if role_needed:
                        dm_channel = await self.bot.get_user(raider.user_id).create_dm()
                        ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
                        new_dm = await dm_channel.send(
                            _dm_reminder(raider.name, schedule.level, ts, schedule.format_dm_roster(), role_needed)
                        )
                        await new_dm.add_reaction("✅")
                        await new_dm.add_reaction("❌")

                        if dm_channel.id not in self.bot.state.dm_map:
                            self.bot.state.dm_map[dm_channel.id] = {}
                        self.bot.state.dm_map[dm_channel.id][new_dm.id] = schedule_id

                        if dm_channel.id not in self.bot.state.dm_timestamps:
                            self.bot.state.dm_timestamps[dm_channel.id] = {}
                        self.bot.state.dm_timestamps[dm_channel.id][new_dm.id] = now

                        logger.info(f"Retried DM for {raider.name} for schedule {schedule_id}")

            except discord.Forbidden:
                logger.warning(f"Could not retry DM for schedule {schedule_id}")
            except Exception as e:
                logger.error(f"Error retrying DM: {e}")

        for dm_channel_id, dm_message_id in dms_to_delete:
            if dm_channel_id in self.bot.state.dm_map and dm_message_id in self.bot.state.dm_map[dm_channel_id]:
                del self.bot.state.dm_map[dm_channel_id][dm_message_id]
            if (
                dm_channel_id in self.bot.state.dm_timestamps
                and dm_message_id in self.bot.state.dm_timestamps[dm_channel_id]
            ):
                del self.bot.state.dm_timestamps[dm_channel_id][dm_message_id]

        if dms_to_retry:
            self.bot.state.save()

    async def check_schedule_conflicts(self):
        """Check for multiple unfilled schedules at the same time and notify coordinator."""
        now = datetime.now(timezone.utc)
        time_slots = {}

        # Group unfilled schedules by their start time
        for schedule_id, schedule in self.bot.state.schedules.items():
            if not schedule.is_filled():
                time_key = (schedule.date_scheduled.date(), schedule.start_time.hour)
                if time_key not in time_slots:
                    time_slots[time_key] = []
                time_slots[time_key].append((schedule_id, schedule))

        # Only flag conflicts where ALL three conditions hold:
        # 1. Multiple unfilled schedules share the same slot
        # 2. Their combined rosters cover every required role (>= 1 tank, >= 1 healer, >= 3 DPS)
        # 3. The run starts within 5 hours
        conflicts = {}
        for time_key, schedules in time_slots.items():
            if len(schedules) <= 1:
                continue

            _, first = schedules[0]
            seconds_until = (first.start_time.astimezone(timezone.utc) - now).total_seconds()
            if seconds_until < 0 or seconds_until > 3600 * 5:
                continue

            combined_tanks = sum(1 for _, s in schedules if s.team["tank"])
            combined_healers = sum(1 for _, s in schedules if s.team["healer"])
            combined_dps = sum(len(s.team["dps"]) for _, s in schedules)
            if combined_tanks >= 1 and combined_healers >= 1 and combined_dps >= 3:
                conflicts[time_key] = schedules

        if conflicts:
            try:
                coordinator = await self.bot.fetch_user(config.COORDINATOR_ID)
                dm_channel = await coordinator.create_dm()

                conflict_message = "⚠️ **Schedule Conflicts Detected** ⚠️\n\n"
                conflict_message += "Multiple unfilled runs are scheduled at the same time:\n\n"

                for time_key, schedules in conflicts.items():
                    date, hour = time_key
                    conflict_message += f"**{date.strftime('%A, %B %d')} at {hour:02d}:00**\n"

                    for schedule_id, schedule in schedules:
                        missing_roles = ", ".join(schedule.missing)
                        filled = f"{schedule.signup}/5"
                        conflict_message += f"  • Level {schedule.level} - {filled} filled - Missing: {missing_roles}\n"
                        conflict_message += f"    [Jump to message](https://discord.com/channels/{self.bot.get_channel(config.KEY_CHANNEL_ID).guild.id}/{config.KEY_CHANNEL_ID}/{schedule_id})\n"

                    conflict_message += "\n"

                conflict_message += "Please manually resolve these conflicts by consolidating or rescheduling runs."

                await dm_channel.send(dedent(conflict_message))
                logger.info(f"Notified coordinator about {len(conflicts)} scheduling conflict(s)")

            except discord.Forbidden:
                logger.warning(f"Could not DM coordinator (ID: {config.COORDINATOR_ID})")
            except discord.NotFound:
                logger.error(f"Coordinator user not found (ID: {config.COORDINATOR_ID})")

    async def new_availability_signup_fill_schedule(self, raider: Raider, tier: str):
        """DM the raider to ask if they can fill unfilled schedules based on tier and primary role."""
        tier_priority = {GREEN: 1, YELLOW: 2, RED: 3}

        if tier is None or tier == RED:
            return

        for schedule_id, schedule in self.bot.state.schedules.items():
            if schedule.is_filled():
                continue

            if schedule.is_past():
                continue

            # If hasn't been listed for an hour yet don't ask
            if (datetime.now(timezone.utc) - schedule.posted).total_seconds() < 3600:
                continue

            # Ask if raider is at least as available as the schedule's current tier
            if (
                tier_priority[tier] <= tier_priority[schedule.tier_reached]
                and raider.check_availability(schedule)
                and schedule not in raider.denied_runs
                and schedule not in raider.current_runs
                and not schedule.has_raider(raider)
            ):
                primary_role = raider.roles[0]

                if primary_role and primary_role in schedule.missing:
                    try:
                        dm_channel = await self.bot.get_user(raider.user_id).create_dm()
                        ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
                        dm = await dm_channel.send(
                            _dm_spot_available(
                                raider.name, schedule.level, ts, schedule.format_dm_roster(), primary_role
                            )
                        )
                        await dm.add_reaction("✅")
                        await dm.add_reaction("❌")

                        if dm_channel.id not in self.bot.state.dm_timestamps:
                            self.bot.state.dm_timestamps[dm_channel.id] = {}
                        self.bot.state.dm_timestamps[dm_channel.id][dm.id] = datetime.now(timezone.utc)

                        if dm_channel.id not in self.bot.state.dm_map:
                            self.bot.state.dm_map[dm_channel.id] = {}
                        self.bot.state.dm_map[dm_channel.id][dm.id] = schedule_id
                    except discord.Forbidden:
                        logger.warning(f"Could not DM {raider.name} for schedule {schedule_id}")
            elif not schedule.primary and len(raider.roles) > 1:
                secondary_role = raider.roles[1]

                if (
                    secondary_role
                    and secondary_role in schedule.missing
                    and raider.check_availability(schedule)
                    and not schedule.has_raider(raider)
                ):
                    try:
                        dm_channel = await self.bot.get_user(raider.user_id).create_dm()
                        ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
                        dm = await dm_channel.send(
                            _dm_spot_available(
                                raider.name,
                                schedule.level,
                                ts,
                                schedule.format_dm_roster(),
                                secondary_role,
                                "secondary",
                            )
                        )
                        await dm.add_reaction("✅")
                        await dm.add_reaction("❌")

                        if dm_channel.id not in self.bot.state.dm_timestamps:
                            self.bot.state.dm_timestamps[dm_channel.id] = {}
                        self.bot.state.dm_timestamps[dm_channel.id][dm.id] = datetime.now(timezone.utc)

                        if dm_channel.id not in self.bot.state.dm_map:
                            self.bot.state.dm_map[dm_channel.id] = {}
                        self.bot.state.dm_map[dm_channel.id][dm.id] = schedule_id
                    except discord.Forbidden:
                        logger.warning(f"Could not DM {raider.name} for schedule {schedule_id}")

    async def fill_remaining_spots(self, schedule_id: int):
        """Background task to fill remaining schedule spots from available raiders, asking all eligible in a tier in parallel."""
        await asyncio.sleep(3600)  # Wait 1 hour before DMing to allow manual signups first

        schedule = self.bot.state.schedules.get(schedule_id)

        if not schedule:
            return

        if schedule.is_past():
            return

        tier = schedule.tier_reached
        primary = schedule.primary

        if schedule.is_filled():
            return

        channel = self.bot.get_channel(config.KEY_CHANNEL_ID)
        if not channel:
            return

        # For each user in the tier if their role fits an open spot, DM them to ask if they want to join
        for raider in self.bot.state.availability[tier]:
            if not raider.check_availability(schedule):
                continue

            if schedule in raider.denied_runs or schedule in raider.current_runs or schedule.has_raider(raider):
                continue

            if primary:
                if raider.roles[0] in schedule.missing:
                    try:
                        dm_channel = await self.bot.get_user(raider.user_id).create_dm()
                        ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
                        dm = await dm_channel.send(
                            _dm_spot_available(
                                raider.name, schedule.level, ts, schedule.format_dm_roster(), raider.roles[0]
                            )
                        )
                        await dm.add_reaction("✅")
                        await dm.add_reaction("❌")

                        if dm_channel.id not in self.bot.state.dm_timestamps:
                            self.bot.state.dm_timestamps[dm_channel.id] = {}
                        self.bot.state.dm_timestamps[dm_channel.id][dm.id] = datetime.now(timezone.utc)

                        if dm_channel.id not in self.bot.state.dm_map:
                            self.bot.state.dm_map[dm_channel.id] = {}
                        self.bot.state.dm_map[dm_channel.id][dm.id] = schedule_id
                    except discord.Forbidden:
                        logger.warning(f"Could not DM {raider.name} for schedule {schedule_id}")
            else:
                if len(raider.roles) > 1 and raider.roles[1] in schedule.missing:
                    try:
                        dm_channel = await self.bot.get_user(raider.user_id).create_dm()
                        ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
                        dm = await dm_channel.send(
                            _dm_spot_available(
                                raider.name,
                                schedule.level,
                                ts,
                                schedule.format_dm_roster(),
                                raider.roles[1],
                                "secondary",
                            )
                        )
                        await dm.add_reaction("✅")
                        await dm.add_reaction("❌")

                        if dm_channel.id not in self.bot.state.dm_timestamps:
                            self.bot.state.dm_timestamps[dm_channel.id] = {}
                        self.bot.state.dm_timestamps[dm_channel.id][dm.id] = datetime.now(timezone.utc)

                        if dm_channel.id not in self.bot.state.dm_map:
                            self.bot.state.dm_map[dm_channel.id] = {}
                        self.bot.state.dm_map[dm_channel.id][dm.id] = schedule_id
                    except discord.Forbidden:
                        logger.warning(f"Could not DM {raider.name} for schedule {schedule_id}")

        # Update schedule parameters
        if tier == GREEN and primary:
            schedule.primary = False
        elif tier == GREEN and not primary:
            schedule.primary = True
            schedule.tier_reached = YELLOW
        elif tier == YELLOW and primary:
            schedule.primary = False
        else:
            schedule.tier_reached = RED

        self.bot.state.save()

    async def _notify_schedule_changed(self, schedule: Schedule, message: str, organizer_id: int):
        """DM all schedule members except the organizer about a change or cancellation."""
        for raider in list(schedule.members):
            if raider.user_id == organizer_id:
                continue
            try:
                user = self.bot.get_user(raider.user_id)
                if user:
                    await user.send(message)
            except discord.Forbidden:
                logger.warning("Could not notify %s of schedule change", raider.name)

    async def _notify_displaced(self, displaced: Raider, schedule: Schedule, role: str):
        """DM a raider who was bumped from a slot by a main-role signup."""
        try:
            ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
            user = self.bot.get_user(displaced.user_id)
            if user:
                await user.send(
                    f"⚠️ **You've been removed from a run**\n\n"
                    f"A player signed up for **{role.title()}** as their main role, "
                    f"replacing you (who was filling that slot as a secondary role).\n"
                    f"**Run:** Level {schedule.level} on <t:{ts}:F>\n\n"
                    f"You may still sign up for another available role if one is open."
                )
        except discord.Forbidden:
            logger.warning(f"Could not notify displaced raider {displaced.name}")

    async def _notify_promoted(self, promoted: Raider, schedule: Schedule):
        """DM a fill raider who was moved into an open slot."""
        if schedule.team["tank"] == promoted:
            role = "Tank"
        elif schedule.team["healer"] == promoted:
            role = "Healer"
        else:
            role = "DPS"
        try:
            ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
            user = self.bot.get_user(promoted.user_id)
            if user:
                await user.send(
                    f"✅ **You've been moved off the fill queue**\n\n"
                    f"A spot opened up and you're now in the **{role}** slot.\n"
                    f"**Run:** Level {schedule.level} on <t:{ts}:F>\n\n"
                    f"{schedule.format_dm_roster()}"
                )
        except discord.HTTPException:
            logger.warning(f"Could not notify promoted raider {promoted.name}")

    async def notify_schedule(self, schedule: Schedule):
        """DM all members of a filled schedule (tank, healer, dps) with the day of week and time in their timezone."""
        members = []
        if schedule.team["tank"]:
            members.append(schedule.team["tank"])
        if schedule.team["healer"]:
            members.append(schedule.team["healer"])
        for dps in schedule.team["dps"]:
            if dps:
                members.append(dps)

        for raider in members:
            try:
                dt = int(schedule.start_time.astimezone(timezone.utc).timestamp())
                user = self.bot.get_user(raider.user_id)
                if not user:
                    continue
                dm_channel = await user.create_dm()
                if schedule.is_filled():
                    await dm_channel.send(
                        dedent(
                            f"""
                        Your Mythic+ run (Level {schedule.level}) is now **filled**!
                        Date: <t:{dt}:F>
                        {schedule.format_dm_roster()}
                        See you there!
                        """
                        )
                    )
                else:
                    await dm_channel.send(
                        dedent(
                            f"""
                        Your Mythic+ run (Level {schedule.level}) is no longer **filled**!
                        Date: <t:{dt}:F>
                        """
                        )
                    )
            except discord.HTTPException as e:
                logger.warning(f"Could not DM {raider.name} for filled schedule: {e}")

    async def _ask_organizer_role(self, user: discord.User, raider) -> Optional[str]:
        """Ask a multi-role raider which role they want to fill as run organizer.

        Returns the selected role string, or None if they only have one role or timed out.
        """
        if len(raider.roles) <= 1:
            return None
        role_view = RoleSelectView(raider.roles, timeout=60)
        await user.send("Which role would you like to fill for this run?", view=role_view)
        await role_view.wait()
        if not role_view.selected_role:
            await user.send("⚠️ No role selected. Key request cancelled.")
        return role_view.selected_role

    async def _do_key_request_flow(self, user: discord.User):
        """Run the full key request flow via DM for the given user."""
        if user.id not in self.bot.state.raiders:
            try:
                selection_view = WoWSelectionView(timeout=300)
                await user.send(
                    "Before scheduling a key, please choose your **World of Warcraft class** and **roles** if you have only one role please ignore the secondary selection:",
                    view=selection_view,
                )
                await selection_view.wait()

                roles = []
                if selection_view.selected_primary:
                    roles.append(selection_view.selected_primary)
                if (
                    selection_view.selected_secondary
                    and selection_view.selected_secondary != selection_view.selected_primary
                ):
                    roles.append(selection_view.selected_secondary)

                if selection_view.selected_class and roles:
                    self.bot.state.raiders[user.id] = Raider(
                        user, selection_view.selected_class, roles, selection_view.selected_timezone
                    )
                    logger.info(
                        f"New raider from key request: {user}: class={selection_view.selected_class} roles={roles} timezone={selection_view.selected_timezone}"
                    )
                    self.bot.state.save()
                else:
                    await user.send("Registration cancelled or incomplete. Please try again.")
                    logger.info(f"No valid selection from {user} (timed out or incomplete)")
                    return
            except discord.Forbidden:
                logger.warning(f"Could not DM {user} for class/role selection")
                return

        try:
            raider = self.bot.state.raiders[user.id]
            view = KeyRequestView(timezone_str=str(raider.timezone), timeout=300)
            await user.send(
                "Request a key: Select Level, Date, Time and How many Keys you are wanting to do.", view=view
            )
            await view.wait()

            if view.selected_level and view.selected_day and view.selected_start_time and view.run_type:
                logger.info(
                    f"Key request from {user}: Day={view.selected_day}, {view.selected_level}, {view.selected_start_time.strftime('%H:%M')}, {view.run_type}"
                )

                temp_schedule = Schedule(
                    raider_scheduled=raider,
                    level=view.selected_level,
                    date_scheduled=view.selected_day,
                    start_time=view.selected_start_time,
                    run_type=view.run_type,
                )
                temp_schedule.raider_remove(raider)

                if not raider.check_availability(temp_schedule):
                    conflict_reason = raider.get_schedule_conflict_reason(temp_schedule)
                    await user.send(f"⚠️ **Cannot create schedule**\n\n{conflict_reason}")
                    logger.info(f"Key request denied for {user} due to schedule conflict")
                    return

                existing_schedule, existing_schedule_id = None, None
                for sched_id, sched in self.bot.state.schedules.items():
                    if (
                        sched.date_scheduled == view.selected_day
                        and sched.start_time.time() == view.selected_start_time.time()
                    ):
                        existing_schedule = sched
                        existing_schedule_id = sched_id
                        break

                if existing_schedule and sum(
                    role in existing_schedule.missing for role in self.bot.state.raiders[user.id].roles
                ):
                    dm = await user.send(
                        f"A run already exists at this time (Level {existing_schedule.level}). Would you like to join that run instead?\nReact with ✅ to join, or ❌ to list your key request."
                    )
                    await dm.add_reaction("✅")
                    await dm.add_reaction("❌")

                    def check(reaction, u):
                        return u == user and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == dm.id

                    try:
                        reaction, _ = await self.bot.wait_for("reaction_add", timeout=120.0, check=check)
                        if str(reaction.emoji) == "✅":
                            if not self.bot.state.raiders[user.id].check_availability(existing_schedule):
                                conflict_reason = self.bot.state.raiders[user.id].get_schedule_conflict_reason(
                                    existing_schedule
                                )
                                await user.send(f"⚠️ **Cannot join this run**\n\n{conflict_reason}")
                                return

                            existing_schedule.raider_signup(self.bot.state.raiders[user.id])
                            self.bot.state.raiders[user.id].add_run(existing_schedule)
                            await user.send(
                                f"✅ You have been added to the existing run on {existing_schedule.date_scheduled} at {existing_schedule.start_time.strftime('%I:%M %p')}."
                            )
                            if existing_schedule.is_filled():
                                await self.notify_schedule(existing_schedule)
                            msg = await self.bot.get_channel(config.KEY_CHANNEL_ID).fetch_message(existing_schedule_id)
                            embed, view_buttons, content = existing_schedule.send_message(
                                self.bot.state.role_mentions, self.bot
                            )
                            await msg.edit(content=content if content else None, embed=embed, view=view_buttons)
                            self.bot.state.save()
                        else:
                            organizer_role = await self._ask_organizer_role(user, self.bot.state.raiders[user.id])
                            if len(self.bot.state.raiders[user.id].roles) > 1 and not organizer_role:
                                return
                            schedule = Schedule(
                                raider_scheduled=self.bot.state.raiders[user.id],
                                level=view.selected_level,
                                date_scheduled=view.selected_day,
                                start_time=view.selected_start_time,
                                run_type=view.run_type,
                                organizer_role=organizer_role,
                            )
                            schedule.note = view.note
                            pre_post_view = PrePostAddRaiderView(
                                schedule, self.bot, self.bot.get_channel(config.KEY_CHANNEL_ID), timeout=300
                            )
                            await user.send(
                                "➕ Would you like to add any registered raiders before posting? "
                                "Click **Add Raider** to search by display name (up to 4), then click **Post Schedule** to publish.",
                                view=pre_post_view,
                            )
                            await pre_post_view.wait()
                            if pre_post_view.message_id:
                                self.bot.state.raiders[user.id].add_run(schedule)
                                self.bot.state.save()
                                self.bot.loop.create_task(self.fill_remaining_spots(pre_post_view.message_id))
                            else:
                                for raider in list(schedule.members):
                                    raider.current_runs.discard(schedule)
                                await user.send(
                                    "⚠️ Schedule was not posted (timed out). Please create a new key request."
                                )
                    except asyncio.TimeoutError:
                        await user.send("No response received. Key request cancelled.")
                    return

                # No existing schedule, create new
                organizer_role = await self._ask_organizer_role(user, self.bot.state.raiders[user.id])
                if len(self.bot.state.raiders[user.id].roles) > 1 and not organizer_role:
                    return
                schedule = Schedule(
                    raider_scheduled=self.bot.state.raiders[user.id],
                    level=view.selected_level,
                    date_scheduled=view.selected_day,
                    start_time=view.selected_start_time,
                    run_type=view.run_type,
                    organizer_role=organizer_role,
                )
                schedule.note = view.note
                pre_post_view = PrePostAddRaiderView(
                    schedule, self.bot, self.bot.get_channel(config.KEY_CHANNEL_ID), timeout=300
                )
                await user.send(
                    "➕ Would you like to add any registered raiders before posting? "
                    "Click **Add Raider** to search by display name (up to 4), then click **Post Schedule** to publish.",
                    view=pre_post_view,
                )
                await pre_post_view.wait()
                if pre_post_view.message_id:
                    self.bot.state.raiders[user.id].add_run(schedule)
                    self.bot.state.save()
                    self.bot.loop.create_task(self.fill_remaining_spots(pre_post_view.message_id))
                else:
                    for raider in list(schedule.members):
                        raider.current_runs.discard(schedule)
                    await user.send("⚠️ Schedule was not posted (timed out). Please create a new key request.")
            else:
                logger.info(f"Key request timed out or cancelled for {user}")
        except discord.Forbidden:
            logger.warning(f"Could not DM {user} for key request")

    @tasks.loop(hours=1)
    async def hourly_check(self):
        """Background task that runs every hour. Makes sure to fill schedules and perform reminders"""

        if not self.bot.is_ready():
            logger.warning("hourly_check: Bot not ready yet, skipping this iteration")
            return

        # Clean up past schedules and their associated DMs
        now = datetime.now(timezone.utc)
        past_schedule_ids = {
            sid for sid, s in self.bot.state.schedules.items() if s.start_time.astimezone(timezone.utc) < now
        }

        # Record completed runs to the event log before their schedules are removed
        for sid in past_schedule_ids:
            sched = self.bot.state.schedules[sid]
            roster = []
            if sched.team["tank"]:
                roster.append(sched.team["tank"].user_id)
            if sched.team["healer"]:
                roster.append(sched.team["healer"].user_id)
            roster.extend(r.user_id for r in sched.team["dps"])
            eventlog.log_event(
                "run_completed",
                ts_utc=sched.start_time,
                run_id=sid,
                level=sched.level,
                run_type=sched.run_type,
                roster=roster,
            )

        # Delete past schedule messages and their reminder messages from the key channel
        if past_schedule_ids:
            channel = self.bot.get_channel(config.KEY_CHANNEL_ID)
            if channel:
                for sid in past_schedule_ids:
                    try:
                        msg = await channel.fetch_message(sid)
                        await msg.delete()
                    except (discord.NotFound, discord.Forbidden):
                        pass
                for sid in past_schedule_ids:
                    for msg_id in self.bot.state.reminder_messages.pop(sid, []):
                        try:
                            msg = await channel.fetch_message(msg_id)
                            await msg.delete()
                        except (discord.NotFound, discord.Forbidden):
                            pass

        self.bot.state.schedules = {
            sid: s for sid, s in self.bot.state.schedules.items() if sid not in past_schedule_ids
        }

        # Delete DM messages for past schedules
        for dm_channel_id, dm_messages in list(self.bot.state.dm_map.items()):
            dm_channel = self.bot.get_channel(dm_channel_id)
            if not dm_channel:
                try:
                    dm_channel = await self.bot.fetch_channel(dm_channel_id)
                except (discord.NotFound, discord.Forbidden):
                    dm_channel = None
            if dm_channel:
                for dm_message_id, schedule_id in list(dm_messages.items()):
                    if schedule_id in past_schedule_ids:
                        try:
                            msg = await dm_channel.fetch_message(dm_message_id)
                            await msg.delete()
                        except (discord.NotFound, discord.Forbidden):
                            pass

        for dm_channel_id in list(self.bot.state.dm_map.keys()):
            self.bot.state.dm_map[dm_channel_id] = {
                mid: sid for mid, sid in self.bot.state.dm_map[dm_channel_id].items() if sid not in past_schedule_ids
            }
            if dm_channel_id in self.bot.state.dm_timestamps:
                self.bot.state.dm_timestamps[dm_channel_id] = {
                    mid: ts
                    for mid, ts in self.bot.state.dm_timestamps[dm_channel_id].items()
                    if mid in self.bot.state.dm_map[dm_channel_id]
                }

            if not self.bot.state.dm_map[dm_channel_id]:
                del self.bot.state.dm_map[dm_channel_id]
            if dm_channel_id in self.bot.state.dm_timestamps and not self.bot.state.dm_timestamps[dm_channel_id]:
                del self.bot.state.dm_timestamps[dm_channel_id]

        await self.retry_unanswered_dms()
        await self.check_schedule_conflicts()

        self.bot.state.save()

        for schedule_id, schedule in list(self.bot.state.schedules.items()):
            if not schedule.is_filled():
                # Check if schedule needs to be reposted after 24 hours of not being filled
                time_passed = (datetime.now(timezone.utc) - schedule.posted).total_seconds()
                if time_passed >= 86400:  # 24 hours
                    new_schedule_id, new_schedule = await self.repost_schedule(schedule, schedule_id)
                    del self.bot.state.schedules[schedule_id]
                    self.bot.state.schedules[new_schedule_id] = new_schedule
                    schedule_id = new_schedule_id
                    schedule = new_schedule
                if schedule_id in self.bot.state.schedules:
                    if schedule.asks >= 5 and schedule.tier_reached != RED:
                        schedule.asks = 0
                        await self.fill_remaining_spots(schedule_id)
                    elif schedule.asks < 5:
                        self.bot.state.schedules[schedule_id].asks += 1

            if schedule.is_filled():
                now = datetime.now(timezone.utc)
                difference = (schedule.start_time.astimezone(timezone.utc) - now).total_seconds()
                if difference <= 3600 * 2:
                    channel = self.bot.get_channel(config.KEY_CHANNEL_ID)
                    reminder_msg = await channel.send(
                        f"{schedule.send_reminder()} in {difference // 3600} hours and {(difference % 3600) // 60} minutes."
                    )
                    self.bot.state.reminder_messages.setdefault(schedule_id, []).append(reminder_msg.id)

    @commands.command(name="keys")
    async def cmd_keys(self, ctx: commands.Context) -> None:
        message = ctx.message
        if message.author.id in self.bot.state.raiders:
            try:
                await message.author.send(self.bot.state.raiders[message.author.id].get_current_runs())
            except discord.Forbidden:
                logger.warning(f"Could not DM {message.author} for current runs")

            try:
                await message.delete()
            except discord.Forbidden:
                logger.warning(f"Could not delete !keys commands from {message.author}")

    @commands.command(name="modify")
    async def cmd_modify(self, ctx: commands.Context) -> None:
        message = ctx.message
        is_dm = message.guild is None
        is_allowed_channel = not is_dm and message.channel.id in (config.KEY_CHANNEL_ID, config.AVAIL_CHANNEL_ID)

        if not (is_dm or is_allowed_channel):
            return

        # Delete the command from the channel (not possible in DMs)
        if not is_dm:
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                logger.warning(f"Could not delete !modify message from {message.author}")

        if message.author.id not in self.bot.state.raiders:
            try:
                await message.author.send(
                    "❌ You are not registered. Please sign up first via the availability reaction or `!key`."
                )
            except discord.Forbidden:
                pass
            return

        try:
            raider = self.bot.state.raiders[message.author.id]
            selection_view = WoWSelectionView(timeout=180)
            await message.author.send(
                "✏️ **Modify your registration**\n"
                "Select your new class, roles, and timezone.\n"
                "⚠️ You will be removed from all currently scheduled runs.",
                view=selection_view,
            )

            await selection_view.wait()

            roles = []
            if selection_view.selected_primary:
                roles.append(selection_view.selected_primary)
            if (
                selection_view.selected_secondary
                and selection_view.selected_secondary != selection_view.selected_primary
            ):
                roles.append(selection_view.selected_secondary)

            if not selection_view.selected_class or not roles or not selection_view.selected_timezone:
                await message.author.send(
                    "❌ Modification incomplete. Please use `!modify` again and fill out all fields."
                )
                return

            # Remove raider from all their current schedules
            schedules_to_delete = []
            key_channel = self.bot.get_channel(config.KEY_CHANNEL_ID)
            for schedule_id, schedule in list(self.bot.state.schedules.items()):
                if schedule in raider.current_runs:
                    fill_status = schedule.is_filled()
                    promoted = schedule.raider_remove(raider)

                    if schedule.signup == 0:
                        try:
                            key_msg = await key_channel.fetch_message(schedule_id)
                            await key_msg.delete()
                        except (discord.NotFound, discord.Forbidden):
                            pass
                        schedules_to_delete.append(schedule_id)
                    else:
                        try:
                            key_msg = await key_channel.fetch_message(schedule_id)
                            embed, view_buttons, content = schedule.send_message(self.bot.state.role_mentions, self.bot)
                            await key_msg.edit(content=content if content else None, embed=embed, view=view_buttons)
                        except (discord.NotFound, discord.Forbidden):
                            pass

                        if schedule.is_filled() != fill_status:
                            await self.notify_schedule(schedule)
                        if promoted:
                            await self._notify_promoted(promoted, schedule)

            for schedule_id in schedules_to_delete:
                del self.bot.state.schedules[schedule_id]

            # Reset run tracking and update profile
            raider.current_runs = set()
            raider.denied_runs = set()
            raider.class_play = selection_view.selected_class
            raider.roles = roles
            raider.timezone = ZoneInfo(selection_view.selected_timezone)

            self.bot.state.save()
            logger.info(
                f"Raider {raider.name} modified: class={raider.class_play} roles={raider.roles} timezone={raider.timezone}"
            )

            roles_display = ", ".join(r.upper() for r in roles)
            await message.author.send(
                f"✅ Your registration has been updated!\n"
                f"Class: **{raider.class_play.title()}**\n"
                f"Roles: **{roles_display}**\n"
                f"Timezone: **{selection_view.selected_timezone}**"
            )

        except discord.Forbidden:
            logger.warning(f"Could not DM {message.author} for !modify")

    @commands.command(name="key")
    @in_channels(config.KEY_CHANNEL_ID)
    async def cmd_key(self, ctx: commands.Context) -> None:
        message = ctx.message
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            logger.warning(f"Could not delete !key message from {message.author}")
        await self._do_key_request_flow(message.author)

    # DM reactions for schedule signup/removal
    # Note: Main channel uses buttons (handled in on_interaction), not reactions
    @commands.Cog.listener("on_reaction_add")
    async def on_spot_offer_reaction(self, reaction: discord.Reaction, user: discord.Member) -> None:
        if user.id == self.bot.user.id:
            return
        if reaction.message.channel.id not in self.bot.state.dm_map or user.id not in self.bot.state.raiders:
            return
        if reaction.emoji not in ["✅", "❌"]:
            return

        if reaction.emoji == "✅":
            schedule_id = self.bot.state.dm_map.get(reaction.message.channel.id, {}).get(reaction.message.id)
            if schedule_id is None:
                return
            raider = self.bot.state.raiders[user.id]
            schedule = self.bot.state.schedules.get(schedule_id)
            if schedule is None:
                return
            eventlog.log_event(
                "offer_accepted",
                ts_utc=schedule.start_time,
                user_id=user.id,
                tz=raider.timezone,
                run_id=schedule_id,
            )
            if raider.check_availability(schedule) and schedule not in raider.current_runs:
                displaced = schedule.try_displace_off_roler(raider, raider.roles[0])
                if displaced:
                    await self._notify_displaced(displaced, schedule, raider.roles[0])

                if schedule.is_filled():
                    # Schedule still full after displacement attempt — ask if they want fill spot
                    dm_channel = reaction.message.channel
                    ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
                    dps_names = ", ".join(r.name for r in schedule.team["dps"]) or "None"
                    roster_text = (
                        f"**Current Roster:**\n"
                        f"🛡️ Tank: {schedule.team['tank'].name if schedule.team['tank'] else 'None'}\n"
                        f"💚 Healer: {schedule.team['healer'].name if schedule.team['healer'] else 'None'}\n"
                        f"⚔️ DPS: {dps_names}"
                    )
                    fill_msg = await dm_channel.send(
                        f"⚠️ The Level {schedule.level} run on <t:{ts}:F> is already **full**.\n"
                        f"{roster_text}\n\n"
                        f"Would you like to be added as a **fill** in case someone drops?\n"
                        f"React with ✅ to join as fill or ❌ to decline."
                    )
                    await fill_msg.add_reaction("✅")
                    await fill_msg.add_reaction("❌")

                    def fill_check(r, u):
                        return u == user and str(r.emoji) in ["✅", "❌"] and r.message.id == fill_msg.id

                    try:
                        fill_reaction, _ = await self.bot.wait_for("reaction_add", timeout=120.0, check=fill_check)
                        if str(fill_reaction.emoji) == "✅":
                            schedule.raider_signup(raider)
                            raider.add_run(schedule)
                            await dm_channel.send(
                                f"✅ You've been added as a fill for the Level {schedule.level} run on "
                                f"<t:{ts}:F>. You'll be notified if a spot opens up!"
                            )
                            key_msg = await self.bot.get_channel(config.KEY_CHANNEL_ID).fetch_message(schedule_id)
                            embed, view, content = schedule.send_message(self.bot.state.role_mentions, self.bot)
                            await key_msg.edit(content=content if content else None, embed=embed, view=view)
                            logger.info("%s added as fill for schedule %s via DM", user, schedule)
                        else:
                            await dm_channel.send("❌ Understood, you won't be added to this run.")
                    except asyncio.TimeoutError:
                        pass
                else:
                    schedule.raider_signup(raider)
                    raider.add_run(schedule)
                    if schedule.is_filled():
                        await self.notify_schedule(schedule)
                    await self.message_user(raider, reaction.emoji, schedule)
                    message = await self.bot.get_channel(config.KEY_CHANNEL_ID).fetch_message(schedule_id)
                    embed, view, content = schedule.send_message(self.bot.state.role_mentions, self.bot)
                    await message.edit(content=content if content else None, embed=embed, view=view)
                    logger.info("%s signed up for schedule %s via DM", user, schedule)
        elif reaction.emoji == "❌":
            schedule_id = self.bot.state.dm_map.get(reaction.message.channel.id, {}).get(reaction.message.id)
            if schedule_id is None:
                return
            raider = self.bot.state.raiders[user.id]
            schedule = self.bot.state.schedules.get(schedule_id)
            if schedule is None:
                return
            eventlog.log_event(
                "offer_declined",
                ts_utc=schedule.start_time,
                user_id=user.id,
                tz=raider.timezone,
                run_id=schedule_id,
            )
            if schedule in raider.current_runs:
                fill_status = schedule.is_filled()
                promoted = schedule.raider_remove(raider)

                if schedule.signup == 0:
                    message = await self.bot.get_channel(config.KEY_CHANNEL_ID).fetch_message(schedule_id)
                    await message.delete()
                    del self.bot.state.schedules[schedule_id]
                    logger.info("Deleted empty schedule %s", schedule_id)
                else:
                    message = await self.bot.get_channel(config.KEY_CHANNEL_ID).fetch_message(schedule_id)
                    embed, view, content = schedule.send_message(self.bot.state.role_mentions, self.bot)
                    await message.edit(content=content if content else None, embed=embed, view=view)
                    if schedule.is_filled() != fill_status:
                        await self.notify_schedule(schedule)
                    if promoted:
                        await self._notify_promoted(promoted, schedule)

            await self.message_user(raider, reaction.emoji, schedule)
            logger.info("%s denied schedule %s via DM", user, schedule)
            del self.bot.state.dm_map[reaction.message.channel.id][reaction.message.id]
            self.bot.state.dm_timestamps.get(reaction.message.channel.id, {}).pop(reaction.message.id, None)

        self.bot.state.save()


async def setup(bot) -> None:
    await bot.add_cog(Scheduling(bot))
