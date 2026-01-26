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
CHANNEL_ID=your_channel_id
TANK_ROLE_ID=your_tank_role_id
HEALER_ROLE_ID=your_healer_role_id
DPS_ROLE_ID=your_dps_role_id
COORDINATOR_ID=your_coordinator_user_id
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
- React with ✅ to confirm attendance
- React with ❌ to remove themselves from a run
- Receive DMs asking if they can fill open spots

### Commands

- `!keys` - Request a Mythic+ key run with custom parameters

## Project Structure

```
mythic-plus-bot/
├── bot.py              # Main Discord bot client and event handlers
├── raider.py           # Raider dataclass representing a player
├── schedule.py         # Schedule class for raid team composition
├── utils.py            # Utility functions and constants
├── dropdown.py         # Discord UI components (dropdowns/selections)
├── state.pkl          # Persisted bot state (auto-generated)
└── README.md          # This file
```

### Key Components

- **`Raider`**: Represents a player with class, roles, timezone, and availability tracking
- **`Schedule`**: Manages a single raid with team slots (tank, healer, DPS, fill)
- **`MyClient`**: Main Discord bot class handling events, DMs, and scheduling logic
- **Discord UI Views**: Reusable selection dropdowns for class/role/timezone selection

## Configuration

The bot uses several configuration options in `.env`:

| Variable | Description |
|----------|-------------|
| `CLIENT_KEY` | Discord bot token from Discord Developer Portal |
| `CHANNEL_ID` | ID of the Discord channel for raid scheduling |
| `TANK_ROLE_ID` | Discord role ID for tank position mentions |
| `HEALER_ROLE_ID` | Discord role ID for healer position mentions |
| `DPS_ROLE_ID` | Discord role ID for DPS position mentions |
| `COORDINATOR_ID` | Discord user ID of the raid coordinator |

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
- `on_reaction_add()` - Reaction events

### Testing

1. Create a throwaway Discord server for testing
2. Configure `.env` with test channel and role IDs
3. Run the bot and interact with it directly in Discord

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
