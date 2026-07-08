"""Discord UI components for WoW class/role and key request selections."""

from datetime import datetime, timedelta
from logging import getLogger
from typing import TYPE_CHECKING, Optional

import discord

from utils import save_state

if TYPE_CHECKING:
    from bot import MyClient
    from raider import Raider
    from schedule import Schedule

logger = getLogger("discord")

# ---------------------------------
# Schedule Button View
# ---------------------------------


class ScheduleButtonView(discord.ui.View):
    """View containing signup and removal buttons for a schedule."""

    def __init__(self, schedule: "Schedule", bot_client: "MyClient"):
        super().__init__(timeout=None)  # Persistent view
        self.schedule = schedule
        self.bot_client = bot_client

    @discord.ui.button(label="Sign Up", style=discord.ButtonStyle.success, emoji="✅", custom_id="signup")
    async def signup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle signup button click."""
        from raider import Raider
        from views import WoWSelectionView

        user = interaction.user
        schedule = self.schedule
        bot = self.bot_client

        # Message must be a schedule
        if interaction.message.id not in bot.schedules:
            await interaction.response.send_message("❌ This is not a valid schedule.", ephemeral=True)
            return

        # Handle unregistered users - prompt them to register
        if user.id not in bot.raiders:
            # Respond to the interaction first to prevent timeout
            await interaction.response.send_message(
                "📝 You're not registered yet! Please check your DMs to select your class and roles.", ephemeral=True
            )

            try:
                # Send registration form via DM
                selection_view = WoWSelectionView(timeout=180)  # 3 minutes timeout
                await user.send(
                    "👋 Welcome! Before you can sign up for runs, please choose your **World of Warcraft class** and **roles** if you have only one role please ignore the secondary selection:",
                    view=selection_view,
                )

                # Wait for the user to complete the form
                await selection_view.wait()

                # Build roles list from selections
                roles = []
                if selection_view.selected_primary:
                    roles.append(selection_view.selected_primary)
                if (
                    selection_view.selected_secondary
                    and selection_view.selected_secondary != selection_view.selected_primary
                ):
                    roles.append(selection_view.selected_secondary)

                # Validate that user completed the form
                if not selection_view.selected_class or not roles or not selection_view.selected_timezone:
                    await user.send(
                        "❌ Registration incomplete. Please try clicking the button again and fill out all fields."
                    )
                    return

                # Create the new raider
                bot.raiders[user.id] = Raider(
                    user, selection_view.selected_class, roles, selection_view.selected_timezone
                )

                logger.info(
                    f"New raider registered via button: {user.display_name}: class={selection_view.selected_class} roles={roles} timezone={selection_view.selected_timezone}"
                )

                # Now process the signup action
                raider = bot.raiders[user.id]
                logger.info(f"Raider: {raider} current runs: {raider.current_runs} sign up: {schedule}")
                if raider.check_availability(schedule) and schedule not in raider.current_runs:
                    displaced = schedule.try_displace_off_roler(raider, raider.roles[0])
                    if displaced:
                        await bot._notify_displaced(displaced, schedule, raider.roles[0])
                    schedule.raider_signup(raider)
                    raider.add_run(schedule)

                    # Update the message with new embed and view
                    embed, view, content = schedule.send_message(bot.role_mentions, bot)
                    await interaction.message.edit(content=content if content else None, embed=embed, view=view)

                    await bot.message_user(raider, "✅", schedule)

                    if schedule.is_filled():
                        await bot.notify_schedule(schedule)

                    save_state(
                        bot.raiders,
                        bot.schedules,
                        bot.availability,
                        bot.availability_message_id,
                        bot.dm_map,
                        bot.dm_timestamps,
                    )
                    logger.info("%s signed up for schedule %s", user, interaction.message.id)

                    await user.send("✅ Registration complete! You've been signed up for the run.")
                else:
                    await user.send(
                        "❌ Registration complete, but you're already signed up or unavailable for this run."
                    )

            except discord.Forbidden:
                logger.warning(f"Could not DM {user} for registration")
                try:
                    await interaction.followup.send(
                        "❌ I couldn't send you a DM. Please enable DMs from server members and try again.",
                        ephemeral=True,
                    )
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"Error during button registration for {user}: {e}")
                try:
                    await user.send(f"❌ An error occurred during registration: {e}")
                except Exception:
                    pass

            return

        # User is registered, process normally
        raider = bot.raiders[user.id]
        logger.info(f"Raider: {raider} current runs: {raider.current_runs} sign up: {schedule}")

        # Check availability before prompting for role
        if not raider.check_availability(schedule) or schedule in raider.current_runs:
            await interaction.response.defer(ephemeral=True)
            conflict_reason = raider.get_schedule_conflict_reason(schedule)
            await interaction.followup.send(conflict_reason or "❌ Unable to sign up for this run.", ephemeral=True)
            return

        # If raider has multiple roles, ask which one they want to fill
        selected_role = None
        if len(raider.roles) > 1:
            role_view = RoleSelectView(raider.roles)
            await interaction.response.send_message(
                "🎭 You have multiple roles. Which role would you like to sign up as?", view=role_view, ephemeral=True
            )
            await role_view.wait()

            if role_view.selected_role is None:
                await interaction.edit_original_response(
                    content="❌ Role selection timed out. Please try again.", view=None
                )
                return

            selected_role = role_view.selected_role
        else:
            await interaction.response.defer(ephemeral=True)

        effective_role = selected_role if selected_role is not None else raider.roles[0]
        displaced = schedule.try_displace_off_roler(raider, effective_role)
        if displaced:
            await bot._notify_displaced(displaced, schedule, effective_role)

        schedule.raider_signup(raider, selected_role)
        raider.add_run(schedule)

        # Update the message with new embed and view
        embed, view, content = schedule.send_message(bot.role_mentions, bot)
        await interaction.message.edit(content=content if content else None, embed=embed, view=view)

        await bot.message_user(raider, "✅", schedule)

        if schedule.is_filled():
            await bot.notify_schedule(schedule)

        save_state(
            bot.raiders, bot.schedules, bot.availability, bot.availability_message_id, bot.dm_map, bot.dm_timestamps
        )
        logger.info("%s signed up for schedule %s", user, interaction.message.id)

        if selected_role:
            await interaction.edit_original_response(
                content=f"✅ You've been signed up as **{selected_role.title()}**!", view=None
            )
        else:
            await interaction.followup.send("✅ You've been signed up for this run!", ephemeral=True)

    @discord.ui.button(label="Remove", style=discord.ButtonStyle.danger, emoji="❌", custom_id="remove")
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle remove button click."""
        user = interaction.user
        schedule = self.schedule
        bot = self.bot_client

        # Message must be a schedule
        if interaction.message.id not in bot.schedules:
            await interaction.response.send_message("❌ This is not a valid schedule.", ephemeral=True)
            return

        # Handle unregistered users
        if user.id not in bot.raiders:
            await interaction.response.send_message(
                "📝 You're not registered yet, so you can't be signed up for this run.", ephemeral=True
            )
            return

        # User is registered, process normally
        raider = bot.raiders[user.id]

        # Defer immediately to prevent interaction timeout
        await interaction.response.defer(ephemeral=True)

        if schedule in raider.current_runs:
            fill_status = schedule.is_filled()
            schedule.raider_remove(raider)
            raider.remove_run(schedule)

            # If schedule is now empty, delete it
            if schedule.signup == 0:
                await interaction.message.delete()
                del bot.schedules[interaction.message.id]
                logger.info(
                    "%s removed from schedule %s - schedule now empty and deleted", user, interaction.message.id
                )
                await interaction.followup.send(
                    "❌ You've been removed. Schedule deleted (no remaining signups).", ephemeral=True
                )
            else:
                # Update the message with new embed and view
                embed, view, content = schedule.send_message(bot.role_mentions, bot)
                await interaction.message.edit(content=content if content else None, embed=embed, view=view)
                await interaction.followup.send("❌ You've been removed from this run.", ephemeral=True)

            # Notify remaining members of fill status change AFTER the embed is updated
            if schedule.is_filled() != fill_status:
                await bot.notify_schedule(schedule)

            await bot.message_user(raider, "❌", schedule)

            save_state(
                bot.raiders, bot.schedules, bot.availability, bot.availability_message_id, bot.dm_map, bot.dm_timestamps
            )
            logger.info("%s removed from schedule %s", user, interaction.message.id)
        else:
            await interaction.followup.send("❌ You're not signed up for this run.", ephemeral=True)

    @discord.ui.button(label="Manage", style=discord.ButtonStyle.secondary, emoji="⚙️", custom_id="manage_schedule")
    async def manage_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle manage button — organizer only."""
        bot = self.bot_client
        schedule = self.schedule

        if interaction.message.id not in bot.schedules:
            await interaction.response.send_message("❌ This is not a valid schedule.", ephemeral=True)
            return

        is_organizer = interaction.user.id == schedule.organizer_id
        is_coordinator = interaction.user.id in self.bot_client.elevated_ids
        if not is_organizer and not is_coordinator:
            await interaction.response.send_message(
                "❌ Only the organizer or coordinator can manage this schedule.", ephemeral=True
            )
            return

        if is_coordinator:
            view = CoordinatorManageView(schedule, bot, interaction.message)
        else:
            view = ManageScheduleView(schedule, bot, interaction.message)

        await interaction.response.send_message(
            "⚙️ **Manage Schedule** — What would you like to do?", view=view, ephemeral=True
        )


# ---------------------------------
# Schedule Management Views
# ---------------------------------


class ManageScheduleView(discord.ui.View):
    """Ephemeral view for the organizer to delete or modify a schedule."""

    def __init__(self, schedule: "Schedule", bot_client: "MyClient", message: discord.Message):
        super().__init__(timeout=120)
        self.schedule = schedule
        self.bot_client = bot_client
        self.message = message

    @discord.ui.button(label="Delete Run", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "⚠️ Are you sure you want to delete this run? All members will be notified.",
            view=ConfirmDeleteView(self.schedule, self.bot_client, self.message),
            ephemeral=True,
        )

    @discord.ui.button(label="Modify Run", style=discord.ButtonStyle.primary, emoji="✏️")
    async def modify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ModifyScheduleModal(self.schedule, self.bot_client, self.message))


class ConfirmDeleteView(discord.ui.View):
    """Ephemeral confirmation view for permanently deleting a schedule."""

    def __init__(self, schedule: "Schedule", bot_client: "MyClient", message: discord.Message):
        super().__init__(timeout=60)
        self.schedule = schedule
        self.bot_client = bot_client
        self.message = message

    @discord.ui.button(label="Yes, Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        from datetime import timezone

        bot = self.bot_client
        schedule = self.schedule

        await interaction.response.defer(ephemeral=True)

        ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
        await bot._notify_schedule_changed(
            schedule,
            f"❌ **Run Cancelled**\nThe scheduled run (Key Level: **{schedule.level}**, <t:{ts}:F>) has been **cancelled** by the organizer.",
            schedule.organizer_id,
        )

        for member in list(schedule.members):
            member.current_runs.discard(schedule)

        try:
            await self.message.delete()
        except discord.NotFound:
            pass

        if self.message.id in bot.schedules:
            del bot.schedules[self.message.id]

        save_state(
            bot.raiders, bot.schedules, bot.availability, bot.availability_message_id, bot.dm_map, bot.dm_timestamps
        )
        logger.info("Schedule %s deleted by organizer", self.message.id)

        await interaction.followup.send("✅ Run deleted. All members have been notified.", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ Deletion cancelled.", view=None)


class ModifyScheduleModal(discord.ui.Modal, title="Modify Schedule"):
    """Modal for editing a schedule's key level, date, time, and note."""

    def __init__(self, schedule: "Schedule", bot_client: "MyClient", message: discord.Message):
        super().__init__()
        self.schedule = schedule
        self.bot_client = bot_client
        self.message = message

        local_dt = schedule.start_time
        date_str = local_dt.strftime("%Y-%m-%d")
        time_str = local_dt.strftime("%I:%M %p").lstrip("0")

        self.level_input = discord.ui.TextInput(
            label="Key Level",
            default=schedule.level,
            required=True,
            max_length=20,
        )
        self.date_input = discord.ui.TextInput(
            label="Date",
            placeholder="YYYY-MM-DD",
            default=date_str,
            required=True,
            max_length=10,
        )
        self.time_input = discord.ui.TextInput(
            label="Start Time (24h HH:MM or H:MM AM/PM)",
            placeholder="e.g. 19:00 or 7:00 PM",
            default=time_str,
            required=True,
            max_length=10,
        )
        self.note_input = discord.ui.TextInput(
            label="Note (optional)",
            default=schedule.note or "",
            required=False,
            max_length=200,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.level_input)
        self.add_item(self.date_input)
        self.add_item(self.time_input)
        self.add_item(self.note_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from datetime import datetime as dt
        from datetime import timezone

        bot = self.bot_client
        schedule = self.schedule

        new_level = self.level_input.value.strip()
        new_note = self.note_input.value.strip() or None
        date_str = self.date_input.value.strip()
        time_str = self.time_input.value.strip()

        # Parse date
        try:
            new_date = dt.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message("❌ Invalid date format. Use YYYY-MM-DD.", ephemeral=True)
            return

        # Parse time (accept 24h or 12h AM/PM)
        new_time = None
        for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p"):
            try:
                new_time = dt.strptime(time_str, fmt)
                break
            except ValueError:
                continue
        if new_time is None:
            await interaction.response.send_message(
                "❌ Invalid time format. Use HH:MM (24h) or H:MM AM/PM.", ephemeral=True
            )
            return

        orig_tz = schedule.start_time.tzinfo
        new_start_time = dt(new_date.year, new_date.month, new_date.day, new_time.hour, new_time.minute, tzinfo=orig_tz)

        if new_start_time.astimezone(timezone.utc) < dt.now(timezone.utc):
            await interaction.response.send_message(
                "❌ The new date/time is in the past. Please choose a future time.", ephemeral=True
            )
            return

        changes = []
        if new_level != schedule.level:
            changes.append(f"Key Level: **{schedule.level}** → **{new_level}**")
        if new_start_time != schedule.start_time:
            old_ts = int(schedule.start_time.astimezone(timezone.utc).timestamp())
            new_ts = int(new_start_time.astimezone(timezone.utc).timestamp())
            changes.append(f"Time: <t:{old_ts}:F> → <t:{new_ts}:F>")
        if new_note != schedule.note:
            old_note_display = schedule.note or "*(none)*"
            new_note_display = new_note or "*(none)*"
            changes.append(f"Note: {old_note_display} → {new_note_display}")

        if not changes:
            await interaction.response.send_message("✅ No changes made.", ephemeral=True)
            return

        schedule.level = new_level
        schedule.note = new_note
        schedule.start_time = new_start_time
        schedule.date_scheduled = new_start_time.replace(hour=0, minute=0, second=0, microsecond=0)

        embed, view, content = schedule.send_message(bot.role_mentions, bot)
        try:
            await self.message.edit(content=content if content else None, embed=embed, view=view)
        except discord.NotFound:
            pass

        new_ts = int(new_start_time.astimezone(timezone.utc).timestamp())
        changes_text = "\n".join(f"• {c}" for c in changes)
        await bot._notify_schedule_changed(
            schedule,
            f"✏️ **Run Modified**\nThe scheduled run at <t:{new_ts}:F> has been updated:\n{changes_text}",
            schedule.organizer_id,
        )

        save_state(
            bot.raiders, bot.schedules, bot.availability, bot.availability_message_id, bot.dm_map, bot.dm_timestamps
        )
        logger.info("Schedule %s modified by organizer", self.message.id)

        await interaction.response.send_message(f"✅ Run updated!\n{changes_text}", ephemeral=True)


# ---------------------------------
# Coordinator Schedule Management
# ---------------------------------


async def _apply_manage_add(
    raider: "Raider",
    schedule: "Schedule",
    bot_client: "MyClient",
    key_msg: discord.Message,
    interaction: discord.Interaction,
    role: str = None,
) -> None:
    """Add a raider to a schedule from the coordinator manage menu."""
    if schedule.has_raider(raider):
        await interaction.response.send_message(
            f"❌ **{raider.name}** is already signed up for this run.", ephemeral=True
        )
        return

    schedule.raider_signup(raider, role=role)
    raider.add_run(schedule)

    if schedule.team["tank"] == raider:
        role_display = "Tank"
    elif schedule.team["healer"] == raider:
        role_display = "Healer"
    elif raider in schedule.team["dps"]:
        role_display = "DPS"
    else:
        role_display = "Fill"

    embed, view, content = schedule.send_message(bot_client.role_mentions, bot_client)
    try:
        await key_msg.edit(content=content if content else None, embed=embed, view=view)
    except discord.NotFound:
        pass

    if schedule.is_filled():
        await bot_client.notify_schedule(schedule)

    await bot_client.message_user(raider, "✅", schedule)
    save_state(
        bot_client.raiders,
        bot_client.schedules,
        bot_client.availability,
        bot_client.availability_message_id,
        bot_client.dm_map,
        bot_client.dm_timestamps,
    )
    await interaction.response.send_message(f"✅ **{raider.name}** added as **{role_display}**!", ephemeral=True)


class ManageRaiderSelect(discord.ui.Select):
    """Dropdown for resolving multiple name matches during a coordinator add."""

    def __init__(self, raiders: list, schedule: "Schedule", bot_client: "MyClient", key_msg: discord.Message):
        self._raiders_map = {str(r.user_id): r for r in raiders}
        options = [
            discord.SelectOption(
                label=r.name[:100],
                value=str(r.user_id),
                description=f"{r.class_play.title()} | {'/'.join(r.roles)}"[:100],
            )
            for r in raiders[:25]
        ]
        super().__init__(placeholder="Select a raider…", min_values=1, max_values=1, options=options)
        self.schedule = schedule
        self.bot_client = bot_client
        self.key_msg = key_msg

    async def callback(self, interaction: discord.Interaction) -> None:
        raider = self._raiders_map[self.values[0]]
        await interaction.response.send_message(
            f"Which role should **{raider.name}** fill?",
            view=ManageRoleSelectView(raider, self.schedule, self.bot_client, self.key_msg),
            ephemeral=True,
        )
        self.view.stop()


class ManageRaiderSelectView(discord.ui.View):
    """View wrapping ManageRaiderSelect for coordinator add flows."""

    def __init__(self, raiders: list, schedule: "Schedule", bot_client: "MyClient", key_msg: discord.Message):
        super().__init__(timeout=60)
        self.add_item(ManageRaiderSelect(raiders, schedule, bot_client, key_msg))


class ManageRoleSelect(discord.ui.Select):
    """Dropdown for choosing which role to add a raider as (coordinator flow)."""

    def __init__(self, raider: "Raider", schedule: "Schedule", bot_client: "MyClient", key_msg: discord.Message):
        self.raider = raider
        self.schedule = schedule
        self.bot_client = bot_client
        self.key_msg = key_msg
        options = [
            discord.SelectOption(
                label=role.title(), value=role, emoji={"tank": "🛡️", "healer": "💚", "dps": "⚔️"}.get(role)
            )
            for role in ["tank", "healer", "dps"]
        ]
        super().__init__(placeholder="Select a role…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _apply_manage_add(
            self.raider, self.schedule, self.bot_client, self.key_msg, interaction, role=self.values[0]
        )
        self.view.stop()


class ManageRoleSelectView(discord.ui.View):
    """View wrapping ManageRoleSelect for coordinator add flows."""

    def __init__(self, raider: "Raider", schedule: "Schedule", bot_client: "MyClient", key_msg: discord.Message):
        super().__init__(timeout=60)
        self.add_item(ManageRoleSelect(raider, schedule, bot_client, key_msg))


class ConfirmManageRaiderView(discord.ui.View):
    """Confirmation prompt shown when a partial name search returns exactly one match (coordinator flow)."""

    def __init__(self, raider: "Raider", schedule: "Schedule", bot_client: "MyClient", key_msg: discord.Message):
        super().__init__(timeout=60)
        self.raider = raider
        self.schedule = schedule
        self.bot_client = bot_client
        self.key_msg = key_msg

    @discord.ui.button(label="Yes, Add", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
        raider = self.raider
        await interaction.response.send_message(
            f"Which role should **{raider.name}** fill?",
            view=ManageRoleSelectView(raider, self.schedule, self.bot_client, self.key_msg),
            ephemeral=True,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ Add cancelled.", view=None)
        self.stop()


class ManageAddRaiderModal(discord.ui.Modal, title="Add Raider to Run"):
    """Modal for coordinator to add a raider by display name (partial match supported)."""

    name_input = discord.ui.TextInput(
        label="Display Name (partial match OK)",
        placeholder="e.g. John",
        required=True,
        max_length=100,
    )

    def __init__(self, schedule: "Schedule", bot_client: "MyClient", key_msg: discord.Message):
        super().__init__()
        self.schedule = schedule
        self.bot_client = bot_client
        self.key_msg = key_msg

    async def on_submit(self, interaction: discord.Interaction) -> None:
        query = self.name_input.value.strip().lower()
        matches = [
            r for r in self.bot_client.raiders.values() if query in r.name.lower() and not self.schedule.has_raider(r)
        ]
        if not matches:
            await interaction.response.send_message(
                f"❌ No registered raiders matching **{self.name_input.value.strip()}** found "
                "(or they're already signed up).",
                ephemeral=True,
            )
            return
        if len(matches) == 1:
            raider = matches[0]
            await interaction.response.send_message(
                f"Found **{raider.name}** ({raider.class_play.title()} | {'/'.join(r.title() for r in raider.roles)}). Is this the right person?",
                view=ConfirmManageRaiderView(raider, self.schedule, self.bot_client, self.key_msg),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Multiple raiders match **{self.name_input.value.strip()}**. Please select one:",
                view=ManageRaiderSelectView(matches, self.schedule, self.bot_client, self.key_msg),
                ephemeral=True,
            )


class RemoveRaiderSelect(discord.ui.Select):
    """Dropdown of current schedule members for coordinator removal."""

    def __init__(self, schedule: "Schedule", bot_client: "MyClient", key_msg: discord.Message):
        self.schedule = schedule
        self.bot_client = bot_client
        self.key_msg = key_msg

        options = []
        for raider in schedule.members:
            if schedule.team["tank"] == raider:
                role = "Tank"
            elif schedule.team["healer"] == raider:
                role = "Healer"
            elif raider in schedule.team["dps"]:
                role = "DPS"
            elif raider in schedule.team["fill"]:
                role = "Fill"
            else:
                role = "Member"
            options.append(
                discord.SelectOption(
                    label=raider.name[:100],
                    value=str(raider.user_id),
                    description=f"{role} | {raider.class_play.title()}"[:100],
                )
            )

        super().__init__(
            placeholder="Select a raider to remove…",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        user_id = int(self.values[0])
        raider = self.bot_client.raiders.get(user_id)
        schedule = self.schedule
        bot = self.bot_client

        if not raider or not schedule.has_raider(raider):
            await interaction.response.send_message("❌ Raider not found in this run.", ephemeral=True)
            return

        fill_status = schedule.is_filled()
        schedule.raider_remove(raider)
        raider.remove_run(schedule)

        if schedule.is_filled() != fill_status:
            await bot.notify_schedule(schedule)

        embed, view, content = schedule.send_message(bot.role_mentions, bot)
        try:
            await self.key_msg.edit(content=content if content else None, embed=embed, view=view)
        except discord.NotFound:
            pass

        save_state(
            bot.raiders, bot.schedules, bot.availability, bot.availability_message_id, bot.dm_map, bot.dm_timestamps
        )
        await interaction.response.send_message(f"✅ **{raider.name}** has been removed from the run.", ephemeral=True)
        self.view.stop()


class RemoveRaiderView(discord.ui.View):
    """View wrapping RemoveRaiderSelect for coordinator removal flows."""

    def __init__(self, schedule: "Schedule", bot_client: "MyClient", key_msg: discord.Message):
        super().__init__(timeout=60)
        self.add_item(RemoveRaiderSelect(schedule, bot_client, key_msg))


class ChangeRaiderRoleSelect(discord.ui.Select):
    """Dropdown of current schedule members for coordinator role-change."""

    def __init__(self, schedule: "Schedule", bot_client: "MyClient", key_msg: discord.Message):
        self.schedule = schedule
        self.bot_client = bot_client
        self.key_msg = key_msg

        options = []
        for raider in schedule.members:
            if schedule.team["tank"] == raider:
                current = "Tank"
            elif schedule.team["healer"] == raider:
                current = "Healer"
            elif raider in schedule.team["dps"]:
                current = "DPS"
            else:
                current = "Fill"
            options.append(
                discord.SelectOption(
                    label=raider.name[:100],
                    value=str(raider.user_id),
                    description=f"Currently: {current} | {raider.class_play.title()}"[:100],
                )
            )

        super().__init__(
            placeholder="Select a raider to change role…", min_values=1, max_values=1, options=options[:25]
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        user_id = int(self.values[0])
        raider = self.bot_client.raiders.get(user_id)
        if not raider or not self.schedule.has_raider(raider):
            await interaction.response.send_message("❌ Raider not found in this run.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Change **{raider.name}** to which role?",
            view=ChangeRoleTargetView(raider, self.schedule, self.bot_client, self.key_msg),
            ephemeral=True,
        )
        self.view.stop()


class ChangeRaiderRoleView(discord.ui.View):
    """View wrapping ChangeRaiderRoleSelect."""

    def __init__(self, schedule: "Schedule", bot_client: "MyClient", key_msg: discord.Message):
        super().__init__(timeout=60)
        self.add_item(ChangeRaiderRoleSelect(schedule, bot_client, key_msg))


class ChangeRoleTargetSelect(discord.ui.Select):
    """Dropdown for choosing the new role slot for a raider."""

    def __init__(self, raider: "Raider", schedule: "Schedule", bot_client: "MyClient", key_msg: discord.Message):
        self.raider = raider
        self.schedule = schedule
        self.bot_client = bot_client
        self.key_msg = key_msg
        options = [
            discord.SelectOption(
                label=role.title(), value=role, emoji={"tank": "🛡️", "healer": "💚", "dps": "⚔️"}.get(role)
            )
            for role in ["tank", "healer", "dps"]
        ]
        super().__init__(placeholder="Select new role…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        new_role = self.values[0]
        raider = self.raider
        schedule = self.schedule
        bot = self.bot_client

        schedule.raider_remove(raider)
        raider.remove_run(schedule)
        schedule.raider_signup(raider, role=new_role)
        raider.add_run(schedule)

        if schedule.team["tank"] == raider:
            role_display = "Tank"
        elif schedule.team["healer"] == raider:
            role_display = "Healer"
        elif raider in schedule.team["dps"]:
            role_display = "DPS"
        else:
            role_display = "Fill"

        embed, view, content = schedule.send_message(bot.role_mentions, bot)
        try:
            await self.key_msg.edit(content=content if content else None, embed=embed, view=view)
        except discord.NotFound:
            pass
        save_state(
            bot.raiders, bot.schedules, bot.availability, bot.availability_message_id, bot.dm_map, bot.dm_timestamps
        )
        await interaction.response.send_message(
            f"✅ **{raider.name}** has been moved to **{role_display}**.", ephemeral=True
        )
        self.view.stop()


class ChangeRoleTargetView(discord.ui.View):
    """View wrapping ChangeRoleTargetSelect."""

    def __init__(self, raider: "Raider", schedule: "Schedule", bot_client: "MyClient", key_msg: discord.Message):
        super().__init__(timeout=60)
        self.add_item(ChangeRoleTargetSelect(raider, schedule, bot_client, key_msg))


class CoordinatorManageView(discord.ui.View):
    """Ephemeral view for the coordinator to add, remove, or change roles of raiders in a schedule."""

    def __init__(self, schedule: "Schedule", bot_client: "MyClient", message: discord.Message):
        super().__init__(timeout=120)
        self.schedule = schedule
        self.bot_client = bot_client
        self.message = message

    @discord.ui.button(label="Delete Run", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(
            "⚠️ Are you sure you want to delete this run? All members will be notified.",
            view=ConfirmDeleteView(self.schedule, self.bot_client, self.message),
            ephemeral=True,
        )

    @discord.ui.button(label="Modify Run", style=discord.ButtonStyle.primary, emoji="✏️")
    async def modify_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(ModifyScheduleModal(self.schedule, self.bot_client, self.message))

    @discord.ui.button(label="Add Raider", style=discord.ButtonStyle.success, emoji="➕")
    async def add_raider_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(ManageAddRaiderModal(self.schedule, self.bot_client, self.message))

    @discord.ui.button(label="Remove Raider", style=discord.ButtonStyle.secondary, emoji="➖")
    async def remove_raider_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not self.schedule.members:
            await interaction.response.send_message("❌ No one is signed up for this run.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Select a raider to remove:",
            view=RemoveRaiderView(self.schedule, self.bot_client, self.message),
            ephemeral=True,
        )

    @discord.ui.button(label="Change Role", style=discord.ButtonStyle.primary, emoji="🔄")
    async def change_role_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not self.schedule.members:
            await interaction.response.send_message("❌ No one is signed up for this run.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Select a raider to change role:",
            view=ChangeRaiderRoleView(self.schedule, self.bot_client, self.message),
            ephemeral=True,
        )


# ---------------------------------
# Role Selection for Multi-Role Raiders
# ---------------------------------


class RoleSelect(discord.ui.Select):
    """Dropdown for a raider to choose which of their roles to sign up as."""

    def __init__(self, roles: list):
        options = [
            discord.SelectOption(
                label=role.title(), value=role, emoji={"tank": "🛡️", "healer": "💚", "dps": "⚔️"}.get(role)
            )
            for role in roles
        ]
        super().__init__(
            placeholder="Choose the role to sign up as...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.selected_role = self.values[0]
        await interaction.response.defer()
        self.view.stop()


class RoleSelectView(discord.ui.View):
    """Ephemeral view that asks a multi-role raider which role they want to fill."""

    def __init__(self, roles: list, timeout: int = 60):
        super().__init__(timeout=timeout)
        self.selected_role: Optional[str] = None
        self.add_item(RoleSelect(roles))


# ---------------------------------
# Dropdown For WoW Class and Roles
# ---------------------------------


class WoWClassSelect(discord.ui.Select):
    """Dropdown select for choosing World of Warcraft class."""

    def __init__(self):
        classes = [
            "Warrior",
            "Paladin",
            "Hunter",
            "Rogue",
            "Priest",
            "Death Knight",
            "Shaman",
            "Mage",
            "Warlock",
            "Monk",
            "Druid",
            "Demon Hunter",
            "Evoker",
        ]

        options = [discord.SelectOption(label=c, value=c.lower()) for c in classes]

        super().__init__(
            placeholder="Choose your World of Warcraft class",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="wow_class",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle class selection and store it on the parent view."""
        # store the chosen value on the parent view for retrieval
        if self.view:
            self.view.selected_class = self.values[0]
        await interaction.response.defer()


class PrimaryRoleSelect(discord.ui.Select):
    """Dropdown select for choosing primary role with class validation."""

    def __init__(self):
        roles = ["tank", "healer", "dps"]
        options = [discord.SelectOption(label=r, value=r.lower()) for r in roles]

        super().__init__(
            placeholder="Choose your primary role",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="primary_role",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle primary role selection with validation against class roles."""
        if self.view:
            self.view.selected_primary = self.values[0]
        await interaction.response.defer()


class SecondaryRoleSelect(discord.ui.Select):
    """Dropdown select for choosing secondary role with class and primary role validation."""

    def __init__(self):
        roles = ["tank", "healer", "dps", "none"]
        options = [discord.SelectOption(label=r, value=r.lower()) for r in roles]

        super().__init__(
            placeholder="Choose your secondary role (optional)",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="secondary_role",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle secondary role selection with validation against class roles and primary role."""
        if self.view:
            if self.view.selected_primary and self.values[0] == self.view.selected_primary:
                await interaction.response.send_message(
                    "Secondary role cannot be the same as primary role.", ephemeral=True
                )
                return
            self.view.selected_secondary = self.values[0] if self.values[0] != "none" else None
        await interaction.response.defer()


class USTimezoneSelect(discord.ui.Select):
    """Dropdown select for choosing US timezone."""

    def __init__(self):
        timezones = [
            ("Eastern (EST/EDT)", "US/Eastern"),
            ("Central (CST/CDT)", "US/Central"),
            ("Mountain (MST/MDT)", "US/Mountain"),
            ("Pacific (PST/PDT)", "US/Pacific"),
            ("Alaska (AKST/AKDT)", "US/Alaska"),
            ("Hawaii (HST/HDT)", "US/Hawaii"),
        ]
        options = [discord.SelectOption(label=label, value=val) for label, val in timezones]
        super().__init__(
            placeholder="Choose your US timezone", min_values=1, max_values=1, options=options, custom_id="us_timezone"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view:
            self.view.selected_timezone = self.values[0]
        await interaction.response.defer()


class SubmitButton(discord.ui.Button):
    """Button to submit the WoW class and role selection."""

    def __init__(self):
        super().__init__(style=discord.ButtonStyle.primary, label="Submit")

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle submit button click and stop the view."""
        view = self.view

        # Build response message with user's selections
        message = "**Your Selection:**\n"
        if view.selected_class:
            message += f"Class: **{view.selected_class.title()}**\n"
        if view.selected_primary:
            message += f"Primary Role: **{view.selected_primary.upper()}**\n"
        if view.selected_secondary:
            message += f"Secondary Role: **{view.selected_secondary.upper()}**\n"
        if view.selected_timezone:
            message += f"Timezone: **{view.selected_timezone}**"

        await interaction.response.send_message(message, ephemeral=True)
        if view:
            view.stop()


class WoWSelectionView(discord.ui.View):
    """View containing dropdowns for WoW class, role, and timezone selection."""

    def __init__(self, timeout: int = 60) -> None:
        super().__init__(timeout=timeout)
        # attributes to hold the user's choices
        self.selected_class: Optional[str] = None
        self.selected_primary: Optional[str] = None
        self.selected_secondary: Optional[str] = None
        self.selected_timezone: Optional[str] = None

        self.add_item(WoWClassSelect())
        self.add_item(PrimaryRoleSelect())
        self.add_item(SecondaryRoleSelect())
        self.add_item(USTimezoneSelect())
        self.add_item(SubmitButton())


# ---------------------------------
# Dropdown For Start Key Request
# ---------------------------------


class WoWLevelSelect(discord.ui.Select):
    """Dropdown select for choosing WoW key level."""

    def __init__(self):
        levels = ["Climb10", "10", "11", "12+"]

        options = [discord.SelectOption(label=c, value=c.lower()) for c in levels]
        super().__init__(
            placeholder="Choose your level", min_values=1, max_values=1, options=options, custom_id="wow_level"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle level selection and store it on the parent view."""
        # store the chosen value on the parent view for retrieval
        if self.view:
            self.view.selected_level = self.values[0]
        await interaction.response.defer()


class WoWDaySelect(discord.ui.Select):
    """Dropdown select for choosing day for key request. Only shows today and future dates."""

    def __init__(self):
        today = datetime.now().date()
        days = []
        for i in range(7):  # Next 7 days starting from today
            date = today + timedelta(days=i)
            label = date.strftime("%A, %B %d")  # e.g., "Monday, January 16"
            if i == 0:
                label += " (Today)"
            value = date.isoformat()  # e.g., "2026-01-16"
            days.append(discord.SelectOption(label=label, value=value))

        super().__init__(placeholder="Choose the day", min_values=1, max_values=1, options=days, custom_id="wow_day")

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle day selection and store it on the parent view."""
        # store the chosen value on the parent view for retrieval
        if self.view:
            self.view.selected_day = self.values[0]
        await interaction.response.defer()


class WoWTimeRangeSelect(discord.ui.Select):
    """Dropdown select for choosing start time for key request (12-hour am/pm format)."""

    def __init__(self, timezone_str: str = "US/Eastern"):
        from zoneinfo import ZoneInfo

        self.timezone = ZoneInfo(timezone_str)
        self.timezone_str = timezone_str

        times, am, pm = [], [], []
        for hour in range(1, 12):
            am.append(f"{hour}:00 AM")
            pm.append(f"{hour}:00 PM")
        am.insert(0, "12:00 AM")
        pm.insert(0, "12:00 PM")
        times = am + pm
        options = [discord.SelectOption(label=t, value=t) for t in times]

        # Extract timezone abbreviation for display (e.g., "US/Eastern" -> "EST" or "EDT")
        tz_name = timezone_str.split("/")[-1] if "/" in timezone_str else timezone_str

        super().__init__(
            placeholder=f"Choose start time ({tz_name} time)",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="wow_start_time",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle start time selection and store timezone-aware datetime object on the parent view."""
        if self.view:
            if not self.view.selected_day:
                await interaction.response.send_message("Please select the day first.", ephemeral=True)
                return
            start_time_str = self.values[0]
            # Parse to 24-hour time for datetime
            from datetime import datetime

            dt_str = f"{self.view.selected_day} {start_time_str}"
            dt = datetime.strptime(dt_str, "%Y-%m-%d %I:%M %p")

            # Make the datetime timezone-aware using the raider's timezone
            dt = dt.replace(tzinfo=self.timezone)

            # Reject times in the past when today is selected (compare in UTC)
            from datetime import timezone as tz

            if dt.astimezone(tz.utc) < datetime.now(tz.utc):
                await interaction.response.send_message(
                    "⚠️ That time has already passed. Please choose a future time.", ephemeral=True
                )
                return

            self.view.selected_start_time = dt
        await interaction.response.defer()


class KeyRunTypeSelect(discord.ui.Select):
    """Dropdown select for choosing if user wants to run one key or multiple."""

    def __init__(self):
        options = [
            discord.SelectOption(label="One Key", value="one"),
            discord.SelectOption(label="Multiple Keys", value="multiple"),
        ]
        super().__init__(
            placeholder="Do you want to run one key or multiple?",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="key_run_type",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view:
            self.view.run_type = self.values[0]
        await interaction.response.defer()


class KeyRequestSubmitButton(discord.ui.Button):
    """Button to submit the key request with validation."""

    def __init__(self):
        super().__init__(style=discord.ButtonStyle.primary, label="Submit Key Request")

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle key request submission with validation and stop the view."""
        view = self.view
        # Validate selections
        if not (view.selected_day and view.run_type and view.selected_level and view.selected_start_time):
            logger.info(
                f"Selected Day: {view.selected_day} Run Type: {view.run_type} Level: {view.selected_level} Time: {view.selected_start_time}"
            )
            await interaction.response.send_message("Please select all options before submitting.", ephemeral=True)
            return

        # Reject submissions where the chosen date/time is in the past
        from datetime import timezone as tz

        if view.selected_start_time.astimezone(tz.utc) < datetime.now(tz.utc):
            await interaction.response.send_message(
                "⚠️ The selected date and time are in the past. Please choose a future time.", ephemeral=True
            )
            return

        # If valid, ask if they want to add a note
        await interaction.response.send_message(
            "✅ Selections confirmed! Would you like to add a note to the schedule?", view=ConfirmNoteView(view)
        )

    @staticmethod
    def time_to_minutes(time_str: str) -> int:
        """Convert time string to minutes since midnight."""
        hours, minutes = map(int, time_str.split(":"))
        return hours * 60 + minutes


class NoteModal(discord.ui.Modal, title="Add a Note"):
    """Modal for adding an optional note to the key request."""

    note = discord.ui.TextInput(
        label="Note",
        placeholder="e.g. 'Need +2 or higher for vault'",
        required=False,
        max_length=200,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, parent_view: "KeyRequestView"):
        super().__init__()
        self._parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self._parent_view.note = self.note.value.strip() or None
        await interaction.response.defer()
        self._parent_view.stop()


class ConfirmNoteView(discord.ui.View):
    """Asks the user if they want to add a note before submitting the key request."""

    def __init__(self, parent_view: "KeyRequestView"):
        super().__init__(timeout=60)
        self._parent_view = parent_view

    @discord.ui.button(label="Yes, Add Note", style=discord.ButtonStyle.primary, emoji="📝")
    async def add_note(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(NoteModal(self._parent_view))

    @discord.ui.button(label="No, Continue", style=discord.ButtonStyle.secondary, emoji="✅")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self._parent_view.stop()

    async def on_timeout(self):
        self._parent_view.stop()


class KeyRequestView(discord.ui.View):
    """View containing dropdowns for WoW key request submission."""

    def __init__(self, timezone_str: str = "US/Eastern", timeout: int = 300) -> None:  # 5 minutes timeout
        super().__init__(timeout=timeout)
        # attributes to hold the user's choices
        self.selected_level: Optional[str] = None
        self.selected_day: Optional[datetime] = None
        self.selected_start_time: Optional[datetime] = None  # now datetime
        self.run_type: Optional[str] = None
        self.timezone_str = timezone_str
        self.note: Optional[str] = None

        self.add_item(WoWLevelSelect())
        self.add_item(WoWDaySelect())
        self.add_item(WoWTimeRangeSelect(timezone_str))
        self.add_item(KeyRunTypeSelect())
        self.add_item(KeyRequestSubmitButton())


# ---------------------------------
# Persistent Button — KEY_CHANNEL
# ---------------------------------


class KeyRequestButtonView(discord.ui.View):
    """Persistent view with a single button posted in KEY_CHANNEL to start a key request."""

    def __init__(self, bot_client: "MyClient"):
        super().__init__(timeout=None)  # Persistent — survives bot restarts
        self.bot_client = bot_client

    @discord.ui.button(
        label="CLICK TO CREATE A REQUEST", style=discord.ButtonStyle.success, emoji="⚔️", custom_id="create_key_request"
    )
    async def create_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Open the key request flow via DM when the button is clicked."""
        try:
            await interaction.response.send_message("📬 Check your DMs to create a key request!", ephemeral=True)
        except discord.errors.NotFound:
            return  # Interaction expired (e.g. bot restarted after the click)
        await self.bot_client._do_key_request_flow(interaction.user)


# ---------------------------------
# Manual Raider Addition
# ---------------------------------


async def _apply_manual_add(
    raider: "Raider",
    schedule: "Schedule",
    bot_client: "MyClient",
    key_msg: discord.Message,
    interaction: discord.Interaction,
    add_view: "AddRaiderView",
    role: str = None,
) -> None:
    """Sign up a raider manually and refresh the schedule post."""
    if schedule.has_raider(raider):
        await interaction.response.send_message(f"❌ **{raider.name}** is already signed up for this run.")
        return

    schedule.raider_signup(raider, role=role)
    raider.add_run(schedule)
    add_view.added_count += 1

    if schedule.team["tank"] == raider:
        role_display = "Tank"
    elif schedule.team["healer"] == raider:
        role_display = "Healer"
    elif raider in schedule.team["dps"]:
        role_display = "DPS"
    else:
        role_display = "Fill"

    if key_msg is not None:
        embed, view, content = schedule.send_message(bot_client.role_mentions, bot_client)
        try:
            await key_msg.edit(content=content if content else None, embed=embed, view=view)
        except discord.NotFound:
            pass
        if schedule.is_filled():
            await bot_client.notify_schedule(schedule)
        await bot_client.message_user(raider, "✅", schedule)
        save_state(
            bot_client.raiders,
            bot_client.schedules,
            bot_client.availability,
            bot_client.availability_message_id,
            bot_client.dm_map,
            bot_client.dm_timestamps,
        )

    remaining = 4 - add_view.added_count
    if remaining <= 0:
        if getattr(add_view, "auto_stop_on_max", True):
            add_view.stop()
        suffix = (
            "" if getattr(add_view, "auto_stop_on_max", True) else "\n\nClick **Post Schedule** to publish the run."
        )
        await interaction.response.send_message(
            f"✅ **{raider.name}** added as **{role_display}**!\n\n✅ Maximum of 4 raiders added.{suffix}"
        )
    else:
        await interaction.response.send_message(
            f"✅ **{raider.name}** added as **{role_display}**! "
            f"({remaining} manual add{'s' if remaining != 1 else ''} remaining)"
        )


class RaiderSelect(discord.ui.Select):
    """Dropdown for selecting one raider from multiple display-name matches."""

    def __init__(
        self,
        raiders: list,
        schedule: "Schedule",
        bot_client: "MyClient",
        key_msg: discord.Message,
        add_view: "AddRaiderView",
    ):
        self._raiders_map = {str(r.user_id): r for r in raiders}
        options = [
            discord.SelectOption(
                label=r.name[:100],
                value=str(r.user_id),
                description=f"{r.class_play.title()} | {'/'.join(r.roles)}"[:100],
            )
            for r in raiders[:25]
        ]
        super().__init__(placeholder="Select a raider…", min_values=1, max_values=1, options=options)
        self.schedule = schedule
        self.bot_client = bot_client
        self.key_msg = key_msg
        self.add_view = add_view

    async def callback(self, interaction: discord.Interaction) -> None:
        raider = self._raiders_map[self.values[0]]
        if len(raider.roles) > 1:
            await interaction.response.send_message(
                f"**{raider.name}** has multiple roles. Which role should they fill?",
                view=ManualAddRoleSelectView(raider, self.schedule, self.bot_client, self.key_msg, self.add_view),
            )
        else:
            await _apply_manual_add(raider, self.schedule, self.bot_client, self.key_msg, interaction, self.add_view)
        self.view.stop()


class RaiderSelectView(discord.ui.View):
    """View containing RaiderSelect for resolving multiple name matches."""

    def __init__(
        self,
        raiders: list,
        schedule: "Schedule",
        bot_client: "MyClient",
        key_msg: discord.Message,
        add_view: "AddRaiderView",
    ):
        super().__init__(timeout=60)
        self.add_item(RaiderSelect(raiders, schedule, bot_client, key_msg, add_view))


class ManualAddRoleSelect(discord.ui.Select):
    """Dropdown for choosing which role to add a raider as (organizer flow)."""

    def __init__(
        self,
        raider: "Raider",
        schedule: "Schedule",
        bot_client: "MyClient",
        key_msg: discord.Message,
        add_view: "AddRaiderView",
    ):
        self.raider = raider
        self.schedule = schedule
        self.bot_client = bot_client
        self.key_msg = key_msg
        self.add_view = add_view
        options = [
            discord.SelectOption(
                label=role.title(), value=role, emoji={"tank": "🛡️", "healer": "💚", "dps": "⚔️"}.get(role)
            )
            for role in ["tank", "healer", "dps"]
        ]
        super().__init__(placeholder="Select a role…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _apply_manual_add(
            self.raider, self.schedule, self.bot_client, self.key_msg, interaction, self.add_view, role=self.values[0]
        )
        self.view.stop()


class ManualAddRoleSelectView(discord.ui.View):
    """View wrapping ManualAddRoleSelect for organizer add flows."""

    def __init__(
        self,
        raider: "Raider",
        schedule: "Schedule",
        bot_client: "MyClient",
        key_msg: discord.Message,
        add_view: "AddRaiderView",
    ):
        super().__init__(timeout=60)
        self.add_item(ManualAddRoleSelect(raider, schedule, bot_client, key_msg, add_view))


class ConfirmAddRaiderView(discord.ui.View):
    """Confirmation prompt shown when a partial name search returns exactly one match (organizer flow)."""

    def __init__(
        self,
        raider: "Raider",
        schedule: "Schedule",
        bot_client: "MyClient",
        key_msg: discord.Message,
        add_view: "AddRaiderView",
    ):
        super().__init__(timeout=60)
        self.raider = raider
        self.schedule = schedule
        self.bot_client = bot_client
        self.key_msg = key_msg
        self.add_view = add_view

    @discord.ui.button(label="Yes, Add", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
        raider = self.raider
        if len(raider.roles) > 1:
            await interaction.response.send_message(
                f"**{raider.name}** has multiple roles. Which role should they fill?",
                view=ManualAddRoleSelectView(raider, self.schedule, self.bot_client, self.key_msg, self.add_view),
            )
        else:
            await _apply_manual_add(raider, self.schedule, self.bot_client, self.key_msg, interaction, self.add_view)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ Add cancelled.", view=None)
        self.stop()


class AddRaiderModal(discord.ui.Modal, title="Add Raider to Run"):
    """Modal for entering a raider display name (partial match supported)."""

    name_input = discord.ui.TextInput(
        label="Display Name (partial match OK)",
        placeholder="e.g. John",
        required=True,
        max_length=100,
    )

    def __init__(
        self,
        schedule: "Schedule",
        bot_client: "MyClient",
        key_msg: discord.Message,
        add_view: "AddRaiderView",
    ):
        super().__init__()
        self.schedule = schedule
        self.bot_client = bot_client
        self.key_msg = key_msg
        self.add_view = add_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        query = self.name_input.value.strip().lower()
        matches = [
            r for r in self.bot_client.raiders.values() if query in r.name.lower() and not self.schedule.has_raider(r)
        ]
        if not matches:
            await interaction.response.send_message(
                f"❌ No registered raiders matching **{self.name_input.value.strip()}** found "
                "(or they're already signed up)."
            )
            return
        if len(matches) == 1:
            raider = matches[0]
            await interaction.response.send_message(
                f"Found **{raider.name}** ({raider.class_play.title()} | {'/'.join(r.title() for r in raider.roles)}). Is this the right person?",
                view=ConfirmAddRaiderView(raider, self.schedule, self.bot_client, self.key_msg, self.add_view),
            )
        else:
            await interaction.response.send_message(
                f"Multiple raiders match **{self.name_input.value.strip()}**. Please select one:",
                view=RaiderSelectView(matches, self.schedule, self.bot_client, self.key_msg, self.add_view),
            )


class AddRaiderView(discord.ui.View):
    """DM view that lets the organizer manually add up to 4 registered raiders to a run."""

    auto_stop_on_max = True

    def __init__(
        self,
        schedule: "Schedule",
        bot_client: "MyClient",
        key_msg: discord.Message,
        timeout: int = 300,
    ):
        super().__init__(timeout=timeout)
        self.schedule = schedule
        self.bot_client = bot_client
        self.key_msg = key_msg
        self.added_count: int = 0

    @discord.ui.button(label="Add Raider", style=discord.ButtonStyle.primary, emoji="➕")
    async def add_raider_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if self.added_count >= 4:
            await interaction.response.send_message("✅ You've already added the maximum of 4 raiders.")
            self.stop()
            return
        await interaction.response.send_modal(AddRaiderModal(self.schedule, self.bot_client, self.key_msg, self))

    @discord.ui.button(label="Done", style=discord.ButtonStyle.secondary, emoji="✅")
    async def done_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ Done adding raiders!", view=None)
        self.stop()


class PrePostAddRaiderView(discord.ui.View):
    """DM view for adding up to 4 raiders before the schedule is posted to the key channel.

    The schedule is only published when the organizer clicks Post Schedule.
    """

    auto_stop_on_max = False

    def __init__(
        self,
        schedule: "Schedule",
        bot_client: "MyClient",
        key_channel: discord.TextChannel,
        timeout: int = 300,
    ):
        super().__init__(timeout=timeout)
        self.schedule = schedule
        self.bot_client = bot_client
        self.key_channel = key_channel
        self.added_count: int = 0
        self.message_id: Optional[int] = None

    @discord.ui.button(label="Add Raider", style=discord.ButtonStyle.primary, emoji="➕")
    async def add_raider_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if self.added_count >= 4:
            await interaction.response.send_message(
                "✅ You've added the maximum of 4 raiders. Click **Post Schedule** to publish."
            )
            return
        await interaction.response.send_modal(AddRaiderModal(self.schedule, self.bot_client, None, self))

    @discord.ui.button(label="Post Schedule", style=discord.ButtonStyle.success, emoji="📢")
    async def post_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        embed, view_buttons, content = self.schedule.send_message(self.bot_client.role_mentions, self.bot_client)
        msg = await self.key_channel.send(
            content=content if content else None,
            embed=embed,
            view=view_buttons,
        )
        self.bot_client.schedules[msg.id] = self.schedule
        self.message_id = msg.id
        # Register the view by message_id so button routing works immediately and after restarts
        self.bot_client.add_view(ScheduleButtonView(self.schedule, self.bot_client), message_id=msg.id)
        # Persist state now so the schedule survives a bot restart regardless of
        # whether the interaction response below succeeds (it can raise NotFound if
        # the 3-second window expired while waiting for key_channel.send).
        save_state(
            self.bot_client.raiders,
            self.bot_client.schedules,
            self.bot_client.availability,
            self.bot_client.availability_message_id,
            self.bot_client.dm_map,
            self.bot_client.dm_timestamps,
        )
        await interaction.response.edit_message(content="✅ Schedule posted to the key channel!", view=None)
        self.stop()
