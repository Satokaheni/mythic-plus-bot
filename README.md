# Mythic+ Bot

A Discord bot for managing World of Warcraft Mythic+ raid scheduling and team coordination.

## Features

- **Availability Tracking**: Players can react with emoji to indicate their weekly availability (green/yellow/red)
- **Automatic Scheduling**: Creates and manages 7-day raid schedules with automatic signup and team assignment
- **Smart Team Assembly**: Automatically assigns players to roles (tank, healer, DPS) based on their class and preferences
- **Direct Messaging**: DMs players to confirm signups and notify them of scheduled runs with timezone conversion
- **Dynamic Availability**: Fills remaining spots by reaching out to available raiders across different availability tiers
- **Conflict Detection**: Alerts the coordinator when multiple unfilled schedules exist at the same time
- **Persistent State**: Saves bot state between restarts using pickle serialization
- **Interactive Button UI**: Modern Discord button interface for signups and removals with automatic user registration
- **Smart Registration**: First-time users are automatically prompted to select their class, roles, and timezone via DMs

## Installation

### Requirements

- Python 3.8+
- `discord.py` library
- `python-dotenv` for environment configuration

### Setup

1. Clone the repository:
```bash
git clone <repo-url>
cd mythic-plus-bot
```

2. Install dependencies:
```bash
pip install discord.py python-dotenv
```

3. Create a `.env` file in the project root with the following variables:
```env
CLIENT_KEY=your_discord_bot_token
AVAIL_CHANNEL_ID=your_availability_channel_id
KEY_CHANNEL_ID=your_key_scheduling_channel_id
GUILD_ID=your_guild_server_id
TANK_ROLE_ID=your_tank_role_id
HEALER_ROLE_ID=your_healer_role_id
DPS_ROLE_ID=your_dps_role_id
COORDINATOR_ID=your_coordinator_user_id
MYTHIC_PLUS_ID=your_mythic_plus_role_id
```

4. Run the bot:
```bash
python bot.py
```

## Usage

### Availability Signup

1. Post an availability message using the `AVAILABILITY_MESSAGE` format
2. Players react with:
   - 🟢 - Available
   - 🟡 - Maybe Available
   - 🔴 - Not Available

### Scheduling

The bot automatically creates 7-day raid schedules. Players can:
- Click the **Sign Up** button to confirm attendance
- Click the **Remove** button to remove themselves from a run
- Receive DMs asking if they can fill open spots (respond with ✅ or ❌)
- First-time users will be prompted to register their class, roles, and timezone when clicking a button

### Commands

- `!keys` - Request a Mythic+ key run with custom parameters

## User Interface

### Schedule Interaction

Each posted schedule includes an interactive embed with two buttons:

- **✅ Sign Up**: Click to join the run
  - If you're not registered, you'll receive a DM to select your class, roles, and timezone
  - If you're already registered, you'll be added to the appropriate role slot
  - Confirmation sent via DM

- **❌ Remove**: Click to remove yourself from the run
  - Removes you from the schedule and notifies via DM
  - Triggers a search for replacement players if the run was previously full

### Registration Flow

First-time users clicking any schedule button will:
1. Receive a notification in Discord to check their DMs
2. Get a DM with interactive dropdowns to select:
   - WoW Class (Warrior, Paladin, Hunter, etc.)
   - Primary Role (Tank, Healer, or DPS)
   - Secondary Role (optional, different from primary)
   - US Timezone (Eastern, Central, Mountain, Pacific, Alaska, Hawaii)
3. Submit their selections
4. Automatically be signed up for the schedule they clicked (if available)

### DM-Based Availability

When a schedule has open spots, the bot will DM available players:
- React with ✅ to accept and join the run
- React with ❌ to decline (won't be asked again for this run)
- No response triggers a retry after 2 hours

## Project Structure

```
mythic-plus-bot/
├── bot.py              # Main Discord bot client and event handlers
├── raider.py           # Raider dataclass representing a player
├── schedule.py         # Schedule class for raid team composition
├── utils.py            # Utility functions and constants
├── views.py            # Discord UI components (buttons, dropdowns, views)
├── state.pkl           # Persisted bot state (auto-generated)
└── README.md           # This file
```

### Key Components

- **`Raider`**: Represents a player with class, roles, timezone, and availability tracking
- **`Schedule`**: Manages a single raid with team slots (tank, healer, DPS, fill)
- **`MyClient`**: Main Discord bot class handling events, DMs, and scheduling logic
- **`ScheduleButtonView`**: Interactive button interface for schedule signups and removals
- **Discord UI Views**: Reusable selection dropdowns for class/role/timezone selection and key requests

## Configuration

The bot uses several configuration options in `.env`:

| Variable | Description |
|----------|-------------|
| `CLIENT_KEY` | Discord bot token from Discord Developer Portal |
| `AVAIL_CHANNEL_ID` | ID of the Discord channel for availability tracking |
| `KEY_CHANNEL_ID` | ID of the Discord channel for raid scheduling |
| `GUILD_ID` | Discord server (guild) ID |
| `TANK_ROLE_ID` | Discord role ID for tank position mentions |
| `HEALER_ROLE_ID` | Discord role ID for healer position mentions |
| `DPS_ROLE_ID` | Discord role ID for DPS position mentions |
| `COORDINATOR_ID` | Discord user ID of the raid coordinator |
| `MYTHIC_PLUS_ID` | Discord role ID for Mythic+ raiders |

### Raid Schedule Configuration

Modify `create_schedules()` in `utils.py` to change raid times per weekday.

## Class and Role Support

**Supported Classes**: Warrior, Paladin, Hunter, Rogue, Priest, Death Knight, Shaman, Mage, Warlock, Monk, Druid, Demon Hunter, Evoker

**Available Roles**: Tank, Healer, DPS (configured per class in `ROLES_DICT`)

## Development

### Running with Debugger

Use VS Code's Python debugger with `bot.py` as the target. Set breakpoints in event handlers like:
- `on_ready()` - Bot startup
- `on_message()` - Message handling
- `on_reaction_add()` - Reaction events (for DM responses)
- `ScheduleButtonView.signup_button()` / `remove_button()` - Button interaction handling

### Testing

1. Create a throwaway Discord server for testing
2. Configure `.env` with test channel and role IDs
3. Run the bot and interact with it directly in Discord

**Testing Checklist:**
- [ ] Availability reactions (🟢/🟡/🔴)
- [ ] Key requests via `!keys` command
- [ ] Button-based signups (registered users)
- [ ] Button-based registration flow (new users)
- [ ] Button-based removals
- [ ] DM confirmations and notifications
- [ ] Schedule message updates after interactions
- [ ] State persistence across restarts

### State Persistence

Bot state is automatically saved to `state.pkl` including:
- Registered raiders and their info
- Active schedules
- Player availability tiers
- DM message tracking
- Availability message ID

State is restored on bot startup.

## Architecture Notes

- **Single-threaded**: The bot runs as a single process with async event handlers
- **Message ID Persistence**: The availability message ID is stored in `state.pkl` to survive restarts
- **Timezone Support**: All times are stored in player timezones and converted for DM notifications
- **Pickle Serialization**: Simple file-based persistence; consider migrating to SQLite for larger deployments
- **Self-Contained Views**: Button interactions are handled within Discord UI views themselves rather than in global event handlers, following Discord.py best practices
- **Persistent Button Views**: Schedule signup/removal buttons use persistent views (`timeout=None`) that survive bot restarts

## Known Limitations

- Message IDs must be persisted externally (currently in state.pkl)
- No database backend - uses in-memory dictionaries with file persistence
- Timezone selection is limited to US timezones
- No support for recurring weekly schedules (requires manual re-creation)

## Future Enhancements

- Database persistence (SQLite/PostgreSQL)
- Web dashboard for schedule management
- Automatic weekly schedule generation
- International timezone support
- Player performance/DPS tracking
- Equipment and stat suggestions

## Support

For issues or feature requests, please create an issue in the repository.
