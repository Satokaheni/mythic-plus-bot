"""Schedule class for managing WoW Mythic+ raid team composition."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from raider import Raider
from discord import Embed, Color
import discord

if TYPE_CHECKING:
    from discord import Interaction


class ScheduleButtonView(discord.ui.View):
    """View containing signup and removal buttons for a schedule."""
    
    def __init__(self, schedule: 'Schedule'):
        super().__init__(timeout=None)  # Persistent view
        self.schedule = schedule
    
    @discord.ui.button(label="Sign Up", style=discord.ButtonStyle.success, emoji="✅", custom_id="signup")
    async def signup_button(self, interaction: 'Interaction', button: discord.ui.Button):
        """Handle signup button click."""
        # This will be handled by the bot's on_interaction event
        await interaction.response.defer()
    
    @discord.ui.button(label="Remove", style=discord.ButtonStyle.danger, emoji="❌", custom_id="remove")
    async def remove_button(self, interaction: 'Interaction', button: discord.ui.Button):
        """Handle remove button click."""
        # This will be handled by the bot's on_interaction event
        await interaction.response.defer()


class Schedule:
    """Represents a WoW Mythic+ raid schedule with team composition and signup management."""
    def __init__(self, raider_scheduled: Raider, level: str,
                 date_scheduled: str, start_time: datetime):
        """Initialize a new schedule with the scheduling raider and details."""
        self.level = level
        # Parse date_scheduled as UTC
        self.date_scheduled = datetime.strptime(date_scheduled, "%Y-%m-%d").replace(tzinfo=raider_scheduled.timezone)
        # Ensure start_time is in correct timezone
        self.start_time = start_time.astimezone(raider_scheduled.timezone) if start_time.tzinfo else start_time.replace(tzinfo=raider_scheduled.timezone)
        self.full = False
        self.team = {
            'tank': None,
            'healer': None,
            'dps': [],
            'fill': []
        }
        self.members = []
        self.missing = ['tank', 'healer', 'dps']
        self.signup = 0
        self.tier_reached = '🟢'
        self.primary = True
        self.asks = 0
        self.posted = datetime.now(timezone.utc)
        self.raider_signup(raider_scheduled)

    def _check_fill(self):
        """Check fill raiders and assign them to available slots."""
        filled = None
        for raider in list(self.team['fill']):
            if not filled:
                roles = raider.roles
                for role in roles:
                    if role == 'dps':
                        if len(self.team['dps']) < 3:
                            self.team['dps'].append(raider)
                            self.signup += 1
                            filled = raider
                        else:
                            if not self.team[role]:
                                self.team[role] = raider
                                self.signup += 1
                                filled = raider
        if filled:
            self.team['fill'].remove(filled)

    def send_message(self, role_mentions: dict = None) -> tuple[Embed, ScheduleButtonView, str]:
        """Generate a Discord embed and button view for the schedule post.
        
        Args:
            role_mentions: Optional dict mapping role names to Discord role objects for mentions
            
        Returns:
            Tuple of (embed, view, content) where content contains role pings if needed
        """
        # Create the embed with a title and color
        # Use different colors based on fill status
        if self.is_filled():
            color = Color.green()
        elif len(self.missing) == 0 or (len(self.missing) == 1 and 'dps' in self.missing and len(self.team['dps']) >= 2):
            color = Color.orange()
        else:
            color = Color.red()
            
        embed = Embed(
            title="⚔️ Scheduled Mythic+ Run",
            color=color,
            timestamp=self.date_scheduled.astimezone(timezone.utc)
        )
        
        # Add the level field
        embed.add_field(
            name="📊 Key Level",
            value=f"**{self.level}**",
            inline=True
        )
        
        # Add status field showing missing roles
        if self.is_filled():
            status_value = "✅ **FULL** - Ready to go!"
        else:
            missing_display = []
            if 'tank' in self.missing:
                missing_display.append("🛡️ Tank")
            if 'healer' in self.missing:
                missing_display.append("💚 Healer")
            if 'dps' in self.missing:
                dps_needed = 3 - len(self.team['dps'])
                if dps_needed > 0:
                    missing_display.append(f"⚔️ DPS ({dps_needed})")
            
            status_value = f"⚠️ **NEEDS:** {', '.join(missing_display)}"
        
        embed.add_field(
            name="📋 Status",
            value=status_value,
            inline=True
        )
        
        # Add the scheduled time field
        embed.add_field(
            name="🕐 Scheduled Time",
            value=f"<t:{int(self.date_scheduled.astimezone(timezone.utc).timestamp())}:F>",
            inline=False
        )
        
        # Add team composition
        tank_value = self.team['tank'].mention if self.team['tank'] else '`🔍 NEEDED`'
        healer_value = self.team['healer'].mention if self.team['healer'] else '`🔍 NEEDED`'
        dps1_value = self.team['dps'][0].mention if len(self.team['dps']) > 0 else '`🔍 NEEDED`'
        dps2_value = self.team['dps'][1].mention if len(self.team['dps']) > 1 else '`🔍 NEEDED`'
        dps3_value = self.team['dps'][2].mention if len(self.team['dps']) > 2 else '`🔍 NEEDED`'
        
        embed.add_field(
            name="🛡️ Tank",
            value=tank_value,
            inline=True
        )
        
        embed.add_field(
            name="💚 Healer",
            value=healer_value,
            inline=True
        )
        
        embed.add_field(
            name="⚔️ DPS",
            value=f"{dps1_value}\n{dps2_value}\n{dps3_value}",
            inline=True
        )
        
        # Add fill queue if there are any
        if self.team['fill']:
            fill_list = '\n'.join([f"{raider.mention} ({', '.join(raider.roles)})" for raider in self.team['fill']])
            embed.add_field(
                name="📋 Fill Queue",
                value=fill_list,
                inline=False
            )
        
        # Add instructions footer
        embed.set_footer(text="Click 'Sign Up' to confirm attendance • Click 'Remove' to remove yourself")
        
        # Create the button view
        view = ScheduleButtonView(self)
        
        # Generate content for role mentions if schedule is not full
        content = ""
        if not self.is_filled() and role_mentions:
            mentions = []
            if 'tank' in self.missing and 'tank' in role_mentions:
                mentions.append(f"{role_mentions['tank'].mention}")
            if 'healer' in self.missing and 'healer' in role_mentions:
                mentions.append(f"{role_mentions['healer'].mention}")
            if 'dps' in self.missing and 'dps' in role_mentions:
                mentions.append(f"{role_mentions['dps'].mention}")
            
            if mentions:
                content = f"🔔 **Roles Needed:** {' '.join(mentions)}"
        
        return embed, view, content

    def send_reminder(self) -> str:
        """Generate a reminder message for the team."""
        return f"""
            Reminder for {self.team['tank'].mention} {self.team['healer'].mention} {self.team['dps'][0].mention} {self.team['dps'][1].mention} {self.team['dps'][2].mention}
        """

    def raider_signup(self, raider: Raider):
        """Add a raider to the schedule, assigning them to appropriate roles."""
        role = raider.roles[0] if raider.roles[0] in self.missing else (raider.roles[1] if len(raider.roles) > 1 and raider.roles[1] in self.missing else raider.roles[0])
        
        if role == 'tank' and not self.team['tank']:
            self.team['tank'] = raider
            self.signup += 1
            self.missing.remove('tank')

        elif role == 'healer' and not self.team['healer']:
            self.team['healer'] = raider
            self.signup += 1
            self.missing.remove('healer')

        elif role == 'dps' and len(self.team['dps']) < 3:
            self.team['dps'].append(raider)
            self.signup += 1
            if len(self.team['dps']) == 3:
                self.missing.remove('dps')

        else:
            self.team['fill'].append(raider)
        self.members.append(raider)
        if self.signup == 5:
            self.full = True

    def raider_remove(self, raider: Raider):
        """Remove a raider from the schedule and update team composition."""
        roles = raider.roles
        if raider in self.team['fill']:
            self.team['fill'].remove(raider)
        else:
            for role in roles:
                if role == 'dps' and raider in self.team['dps']:
                    self.team['dps'].remove(raider)
                    self.signup -= 1
                    if 'dps' not in self.missing:
                        self.missing.append('dps')
                    self._check_fill()
                elif role in self.team and self.team[role] == raider:
                    self.team[role] = None
                    self.signup -= 1
                    self.missing.append(role)
                    self._check_fill()
        if raider in self.members:
            self.members.remove(raider)
        if self.signup < 5:
            self.full = False

    def is_filled(self) -> bool:
        """Return True if the schedule is full (5 signups)."""
        return self.full
    
    def has_raider(self, raider: Raider) -> bool:
        """Check if a raider is already in this schedule."""
        return raider in self.members

    def try_signup(self, raider: Raider) -> bool:
        """Attempt to sign up a raider. Returns True if they were added.
        Prevents duplicate signups.
        """
        if raider in self.members:
            return False
        before = self.signup
        self.raider_signup(raider)
        return self.signup > before
    
    def __eq__(self, other) -> bool:
        """Check equality based on level, date, and start time."""
        if isinstance(other, Schedule):
            return (
                self.level == other.level and
                self.date_scheduled == other.date_scheduled and
                self.start_time == other.start_time
            )
        return NotImplemented
    
    def __hash__(self) -> int:
        """Return hash based on level, date, and start time for use in sets and dicts."""
        return hash((self.level, self.date_scheduled, self.start_time))

    def __str__(self) -> str:
        return f"{self.level}, {self.date_scheduled}, {self.members}"