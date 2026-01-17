"""Discord UI components for WoW class/role and key request selections."""

from datetime import datetime, timedelta
from typing import Optional

import discord

from utils import ROLES_DICT

# ---------------------------------
# Dropdown For WoW Class and Roles
# ---------------------------------

class WoWClassSelect(discord.ui.Select):
    """Dropdown select for choosing World of Warcraft class."""
    def __init__(self):
        classes = [
            "Warrior", "Paladin", "Hunter", "Rogue", "Priest",
            "Death Knight", "Shaman", "Mage", "Warlock",
            "Monk", "Druid", "Demon Hunter", "Evoker"
        ]

        options = [discord.SelectOption(label=c, value=c.lower()) for c in classes]

        super().__init__(
            placeholder="Choose your World of Warcraft class",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="wow_class"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle class selection and store it on the parent view."""
        # store the chosen value on the parent view for retrieval
        if self.view:
            self.view.selected_class = self.values[0]
        await interaction.response.send_message(
            f"You selected **{self.values[0].title()}** as your class.",
            ephemeral=True
        )


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
            custom_id="primary_role"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle primary role selection with validation against class roles."""
        if self.view:
            if self.view.selected_class:
                allowed_roles = ROLES_DICT.get(self.view.selected_class.title(), [])
                if self.values[0] not in allowed_roles:
                    msg = (f"**{self.values[0].title()}** is not a valid role for "
                           f"**{self.view.selected_class.title()}**. "
                           f"Allowed roles: {', '.join(allowed_roles).title()}")
                    await interaction.response.send_message(msg, ephemeral=True)
                    return
            self.view.selected_primary = self.values[0]
        await interaction.response.send_message(
            f"Primary role set to **{self.values[0].upper()}**.",
            ephemeral=True
        )

class SecondaryRoleSelect(discord.ui.Select):
    """Dropdown select for choosing secondary role with class and primary role validation."""
    def __init__(self):
        roles = ["tank", "healer", "dps"]
        options = [discord.SelectOption(label=r, value=r.lower()) for r in roles]

        super().__init__(
            placeholder="Choose your secondary role (optional)",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="secondary_role"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle secondary role selection with validation against class roles and primary role."""
        if self.view:
            if self.view.selected_class:
                allowed_roles = ROLES_DICT.get(self.view.selected_class.title(), [])
                if self.values[0] not in allowed_roles:
                    msg = (f"**{self.values[0].title()}** is not a valid role for "
                           f"**{self.view.selected_class.title()}**. "
                           f"Allowed roles: {', '.join(allowed_roles).title()}")
                    await interaction.response.send_message(msg, ephemeral=True)
                    return
            if self.view.selected_primary and self.values[0] == self.view.selected_primary:
                await interaction.response.send_message("Secondary role cannot be the same as primary role.", ephemeral=True)
                return
            self.view.selected_secondary = self.values[0]
        await interaction.response.send_message(
            f"Secondary role set to **{self.values[0].upper()}**.",
            ephemeral=True
        )


class SubmitButton(discord.ui.Button):
    """Button to submit the WoW class and role selection."""
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.primary, label="Submit")

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle submit button click and stop the view."""
        await interaction.response.send_message("Selection received.", ephemeral=True)
        if self.view:
            self.view.stop()


class WoWSelectionView(discord.ui.View):
    """View containing dropdowns for WoW class and role selection."""
    def __init__(self, timeout: int = 60) -> None:
        super().__init__(timeout=timeout)
        # attributes to hold the user's choices
        self.selected_class: Optional[str] = None
        self.selected_primary: Optional[str] = None
        self.selected_secondary: Optional[str] = None

        self.add_item(WoWClassSelect())
        self.add_item(PrimaryRoleSelect())
        self.add_item(SecondaryRoleSelect())
        self.add_item(SubmitButton())


# ---------------------------------
# Dropdown For Start Key Request
# ---------------------------------

class WoWDungeonSelect(discord.ui.Select):
    """Dropdown select for choosing WoW dungeon for key request."""
    def __init__(self):
        dungeons = [
            "Magister's Terrace",
            "Maisara Caverns",
            "Nexus Point Xenas",
            "Windrunner Spire",
            "Algeth'ar Academy",
            "Seat of the Triumvirate",
            "Skyreach",
            "Pit of Saron"
        ]

        options = [discord.SelectOption(label=c, value=c.lower()) for c in dungeons]
        super().__init__(
            placeholder="Choose your dungeon",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="wow_dungeon"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle dungeon selection and store it on the parent view."""
        # store the chosen value on the parent view for retrieval
        if self.view:
            self.view.selected_dungeon = self.values[0]
        await interaction.response.send_message(
            f"You selected **{self.values[0].title()}** as your dungeon.",
            ephemeral=True
        )

class WoWLevelSelect(discord.ui.Select):
    """Dropdown select for choosing WoW key level."""
    def __init__(self):
        levels = [str(i) for i in range(1, 10)] + ["10+"]

        options = [discord.SelectOption(label=c, value=c.lower()) for c in levels]
        super().__init__(
            placeholder="Choose your level",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="wow_level"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle level selection and store it on the parent view."""
        # store the chosen value on the parent view for retrieval
        if self.view:
            self.view.selected_level = self.values[0]
        await interaction.response.send_message(
            f"You selected **{self.values[0].title()}** as your level.",
            ephemeral=True
        )

class WoWDaySelect(discord.ui.Select):
    """Dropdown select for choosing day for key request."""
    def __init__(self):
        today = datetime.now().date()
        days = []
        for i in range(7):  # Next 7 days
            date = today + timedelta(days=i)
            label = date.strftime("%A, %B %d")  # e.g., "Monday, January 16"
            if i == 0:
                label = "Today - " + label
            elif i == 1:
                label = "Tomorrow - " + label
            value = date.isoformat()  # e.g., "2026-01-16"
            days.append(discord.SelectOption(label=label, value=value))

        super().__init__(
            placeholder="Choose the day",
            min_values=1,
            max_values=1,
            options=days,
            custom_id="wow_day"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle day selection and store it on the parent view."""
        # store the chosen value on the parent view for retrieval
        if self.view:
            self.view.selected_day = self.values[0]
        selected_date = datetime.fromisoformat(self.values[0]).strftime("%A, %B %d")
        await interaction.response.send_message(
            f"You selected **{selected_date}** as the day.",
            ephemeral=True
        )

class WoWTimeRangeSelect(discord.ui.Select):
    """Dropdown select for choosing start and end times for key request."""
    def __init__(self):
        times = [f"{hour:02d}:00" for hour in range(24)]
        options = [discord.SelectOption(label=c, value=c.lower()) for c in times]
        super().__init__(
            placeholder="Choose start and end times (select 2)",
            min_values=2,
            max_values=2,
            options=options,
            custom_id="wow_time_range"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle time range selection and store datetime objects on the parent view."""
        # store the chosen values on the parent view for retrieval
        if self.view:
            if not self.view.selected_day:
                await interaction.response.send_message("Please select the day first.", ephemeral=True)
                return
            # sort the two selected times
            sorted_times = sorted(self.values)
            start_time_str = sorted_times[0]
            end_time_str = sorted_times[1]
            self.view.selected_start_time = datetime.fromisoformat(self.view.selected_day + ' ' + start_time_str)
            self.view.selected_end_time = datetime.fromisoformat(self.view.selected_day + ' ' + end_time_str)
        await interaction.response.send_message(
            f"You selected **{sorted_times[0]}** to **{sorted_times[1]}** as your time range.",
            ephemeral=True
        )


class KeyRequestSubmitButton(discord.ui.Button):
    """Button to submit the key request with validation."""
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.primary, label="Submit Key Request")

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle key request submission with validation and stop the view."""
        view = self.view
        # Validate selections
        if not (view.selected_day and view.selected_dungeon and view.selected_level and view.selected_start_time and view.selected_end_time):
            await interaction.response.send_message("Please select all options before submitting.", ephemeral=True)
            return

        # Check start time < end time
        if view.selected_start_time >= view.selected_end_time:
            await interaction.response.send_message("Start time must be before end time.", ephemeral=True)
            return

        # If valid, proceed
        selected_date = datetime.fromisoformat(view.selected_day).strftime("%A, %B %d")
        start_str = view.selected_start_time.strftime("%H:%M")
        end_str = view.selected_end_time.strftime("%H:%M")
        await interaction.response.send_message(
            f"Key request submitted: Day={selected_date}, Dungeon={view.selected_dungeon.title()}, Level={view.selected_level}, Start={start_str}, End={end_str}",
            ephemeral=True
        )
        view.stop()

    @staticmethod
    def time_to_minutes(time_str: str) -> int:
        """Convert time string to minutes since midnight."""
        hours, minutes = map(int, time_str.split(':'))
        return hours * 60 + minutes


class KeyRequestView(discord.ui.View):
    """View containing dropdowns for WoW key request submission."""
    def __init__(self, timeout: int = 300) -> None:  # 5 minutes timeout
        super().__init__(timeout=timeout)
        # attributes to hold the user's choices
        self.selected_dungeon: Optional[str] = None
        self.selected_level: Optional[str] = None
        self.selected_day: Optional[datetime] = None
        self.selected_start_time: Optional[datetime] = None  # now datetime
        self.selected_end_time: Optional[datetime] = None  # now datetime

        self.add_item(WoWDungeonSelect())
        self.add_item(WoWLevelSelect())
        self.add_item(WoWDaySelect())
        self.add_item(WoWTimeRangeSelect())
        self.add_item(KeyRequestSubmitButton())
