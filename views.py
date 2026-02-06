"""Discord UI components for WoW class/role and key request selections."""

from datetime import datetime, timedelta
from typing import Optional, TYPE_CHECKING
from logging import getLogger
import discord

from utils import ROLES_DICT, save_state

if TYPE_CHECKING:
    from schedule import Schedule
    from bot import MyClient
    from raider import Raider

logger = getLogger('discord')

# ---------------------------------
# Schedule Button View
# ---------------------------------

class ScheduleButtonView(discord.ui.View):
    """View containing signup and removal buttons for a schedule."""
    
    def __init__(self, schedule: 'Schedule', bot_client: 'MyClient'):
        super().__init__(timeout=None)  # Persistent view
        self.schedule = schedule
        self.bot_client = bot_client
    
    @discord.ui.button(label="Sign Up", style=discord.ButtonStyle.success, emoji="✅", custom_id="signup")
    async def signup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle signup button click."""
        from views import WoWSelectionView
        from raider import Raider
        
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
                "📝 You're not registered yet! Please check your DMs to select your class and roles.",
                ephemeral=True
            )
            
            try:
                # Send registration form via DM
                selection_view = WoWSelectionView(timeout=180)  # 3 minutes timeout
                await user.send(
                    "👋 Welcome! Before you can sign up for runs, please choose your **World of Warcraft class** and **roles**:",
                    view=selection_view
                )
                
                # Wait for the user to complete the form
                await selection_view.wait()
                
                # Build roles list from selections
                roles = []
                if selection_view.selected_primary:
                    roles.append(selection_view.selected_primary)
                if selection_view.selected_secondary and selection_view.selected_secondary != selection_view.selected_primary:
                    roles.append(selection_view.selected_secondary)
                
                # Validate that user completed the form
                if not selection_view.selected_class or not roles or not selection_view.selected_timezone:
                    await user.send("❌ Registration incomplete. Please try clicking the button again and fill out all fields.")
                    return
                
                # Create the new raider
                bot.raiders[user.id] = Raider(
                    user, 
                    selection_view.selected_class, 
                    roles, 
                    selection_view.selected_timezone
                )
                
                logger.info(f"New raider registered via button: {user.display_name}: class={selection_view.selected_class} roles={roles} timezone={selection_view.selected_timezone}")
                
                # Now process the signup action
                raider = bot.raiders[user.id]
                
                if raider.check_availability(schedule) and schedule not in raider.current_runs:
                    schedule.raider_signup(raider)
                    raider.add_run(schedule)
                    
                    # Update the message with new embed and view
                    embed, view, content = schedule.send_message(bot.role_mentions)
                    await interaction.message.edit(content=content if content else None, embed=embed, view=view)
                    
                    await bot.message_user(raider, '✅', schedule)
                    
                    if schedule.is_filled():
                        await bot.notify_schedule(schedule)
                    
                    save_state(bot.raiders, bot.schedules, bot.availability, bot.availability_message_id, bot.dm_map, bot.dm_timestamps)
                    logger.info("%s signed up for schedule %s", user, interaction.message.id)
                    
                    await user.send("✅ Registration complete! You've been signed up for the run.")
                else:
                    await user.send("❌ Registration complete, but you're already signed up or unavailable for this run.")
                
            except discord.Forbidden:
                logger.warning(f"Could not DM {user} for registration")
                try:
                    await interaction.followup.send(
                        "❌ I couldn't send you a DM. Please enable DMs from server members and try again.",
                        ephemeral=True
                    )
                except:
                    pass
            except Exception as e:
                logger.error(f"Error during button registration for {user}: {e}")
                try:
                    await user.send(f"❌ An error occurred during registration: {e}")
                except:
                    pass
            
            return
        
        # User is registered, process normally
        raider = bot.raiders[user.id]
        
        if raider.check_availability(schedule) and schedule not in raider.current_runs:
            schedule.raider_signup(raider)
            raider.add_run(schedule)
            
            # Update the message with new embed and view
            embed, view, content = schedule.send_message(bot.role_mentions)
            await interaction.message.edit(content=content if content else None, embed=embed, view=view)
            
            await bot.message_user(raider, '✅', schedule)
            
            if schedule.is_filled():
                await bot.notify_schedule(schedule)
            
            save_state(bot.raiders, bot.schedules, bot.availability, bot.availability_message_id, bot.dm_map, bot.dm_timestamps)
            logger.info("%s signed up for schedule %s", user, interaction.message.id)
            
            await interaction.response.send_message("✅ You've been signed up for this run!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ You're already signed up or unavailable for this run.", ephemeral=True)
    
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
                "📝 You're not registered yet, so you can't be signed up for this run.",
                ephemeral=True
            )
            return
        
        # User is registered, process normally
        raider = bot.raiders[user.id]
        
        if schedule in raider.current_runs:
            fill_status = schedule.is_filled()
            schedule.raider_remove(raider)
            raider.remove_run(schedule)
            
            if schedule.is_filled() != fill_status:
                await bot.notify_schedule(schedule)
            
            # Update the message with new embed and view
            embed, view, content = schedule.send_message(bot.role_mentions)
            await interaction.message.edit(content=content if content else None, embed=embed, view=view)
            
            await bot.message_user(raider, '❌', schedule)
            
            save_state(bot.raiders, bot.schedules, bot.availability, bot.availability_message_id, bot.dm_map, bot.dm_timestamps)
            logger.info("%s removed from schedule %s", user, interaction.message.id)
            
            await interaction.response.send_message("❌ You've been removed from this run.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ You're not signed up for this run.", ephemeral=True)

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
            custom_id="primary_role"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle primary role selection with validation against class roles."""
        if self.view:
            self.view.selected_primary = self.values[0]
        await interaction.response.defer()

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
        await interaction.response.defer()

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
        await interaction.response.defer()

class WoWTimeRangeSelect(discord.ui.Select):
    """Dropdown select for choosing start time for key request (12-hour am/pm format)."""
    def __init__(self):
        times, am, pm = [], [], []
        for hour in range(1, 12):
            am.append(f"{hour}:00 AM")
            pm.append(f"{hour}:00 PM")
        am.insert(0, "12:00 AM")
        pm.insert(0, "12:00 PM")
        times = am + pm
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
        await interaction.response.defer()


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
            logger.info(f"Selected Day: {view.selected_day} Run Type: {view.run_type} Level: {view.selected_level} Time: {view.selected_start_time}")
            await interaction.response.send_message("Please select all options before submitting.", ephemeral=True)
            return

        # If valid, proceed
        selected_date = datetime.fromisoformat(view.selected_day).strftime("%A, %B %d")
        start_str = view.selected_start_time.strftime("%I:%M %p")
        await interaction.response.send_message(
            f"Key request submitted: Day={selected_date}, Number of Runs={view.run_type}, Level={view.selected_level}, Start={start_str}",
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
        self.selected_level: Optional[str] = None
        self.selected_day: Optional[datetime] = None
        self.selected_start_time: Optional[datetime] = None  # now datetime
        self.run_type: Optional[str] = None

        self.add_item(WoWLevelSelect())
        self.add_item(WoWDaySelect())
        self.add_item(WoWTimeRangeSelect())
        self.add_item(KeyRunTypeSelect())
        self.add_item(KeyRequestSubmitButton())