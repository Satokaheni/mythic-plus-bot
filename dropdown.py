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


class USTimezoneSelect(discord.ui.Select):
    """Dropdown select for choosing US timezone."""
    def __init__(self):
        timezones = [
            ("Eastern (EST/EDT)", "US/Eastern"),
            ("Central (CST/CDT)", "US/Central"),
            ("Mountain (MST/MDT)", "US/Mountain"),
            ("Pacific (PST/PDT)", "US/Pacific"),
            ("Alaska (AKST/AKDT)", "US/Alaska"),
            ("Hawaii (HST/HDT)", "US/Hawaii")
        ]
        options = [discord.SelectOption(label=label, value=val) for label, val in timezones]
        super().__init__(
            placeholder="Choose your US timezone",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="us_timezone"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view:
            self.view.selected_timezone = self.values[0]
        await interaction.response.send_message(
            f"Timezone set to **{self.values[0]}**.", ephemeral=True
        )

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
        levels = ['Climb10', '10', '11', '12+']

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
    """Dropdown select for choosing start time for key request (12-hour am/pm format)."""
    def __init__(self):
        times = []
        for hour in range(1, 13):
            times.append(f"{hour}:00 AM")
            times.append(f"{hour}:00 PM")
        options = [discord.SelectOption(label=t, value=t) for t in times]
        super().__init__(
            placeholder="Choose start time (am/pm)",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="wow_start_time"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle start time selection and store datetime object on the parent view."""
        if self.view:
            if not self.view.selected_day:
                await interaction.response.send_message("Please select the day first.", ephemeral=True)
                return
            start_time_str = self.values[0]
            # Parse to 24-hour time for datetime
            from datetime import datetime
            dt_str = f"{self.view.selected_day} {start_time_str}"
            dt = datetime.strptime(dt_str, "%Y-%m-%d %I:%M %p")
            self.view.selected_start_time = dt
        await interaction.response.send_message(
            f"You selected **{self.values[0]}** as your start time.", ephemeral=True
        )


class KeyRunTypeSelect(discord.ui.Select):
    """Dropdown select for choosing if user wants to run one key or multiple."""
    def __init__(self):
        options = [
            discord.SelectOption(label="One Key", value="one"),
            discord.SelectOption(label="Multiple Keys", value="multiple")
        ]
        super().__init__(
            placeholder="Do you want to run one key or multiple?",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="key_run_type"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view:
            self.view.run_type = self.values[0]
        await interaction.response.send_message(
            f"You selected to run **{'one key' if self.values[0] == 'one' else 'multiple keys'}**.", ephemeral=True
        )


class KeyRequestSubmitButton(discord.ui.Button):
    """Button to submit the key request with validation."""
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.primary, label="Submit Key Request")

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle key request submission with validation and stop the view."""
        view = self.view
        # Validate selections
        if not (view.selected_day and view.selected_dungeon and view.selected_level and view.selected_start_time):
            await interaction.response.send_message("Please select all options before submitting.", ephemeral=True)
            return

        # If valid, proceed
        selected_date = datetime.fromisoformat(view.selected_day).strftime("%A, %B %d")
        start_str = view.selected_start_time.strftime("%I:%M %p")
        await interaction.response.send_message(
            f"Key request submitted: Day={selected_date}, Dungeon={view.selected_dungeon.title()}, Level={view.selected_level}, Start={start_str}",
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
        self.run_type: Optional[str] = None

        self.add_item(WoWLevelSelect())
        self.add_item(WoWDaySelect())
        self.add_item(WoWTimeRangeSelect())
        self.add_item(KeyRunTypeSelect())
        self.add_item(KeyRequestSubmitButton())
