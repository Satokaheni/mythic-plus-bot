# Mythic+ Bot

[![Tests](https://github.com/Satokaheni/mythic-plus-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/Satokaheni/mythic-plus-bot/actions/workflows/tests.yml)
[![Docker](https://github.com/Satokaheni/mythic-plus-bot/actions/workflows/docker.yml/badge.svg)](https://github.com/Satokaheni/mythic-plus-bot/actions/workflows/docker.yml)
![Version](https://img.shields.io/badge/version-1.1.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.9+-green.svg)
![Discord.py](https://img.shields.io/badge/discord.py-2.0+-blue.svg)

A Discord bot for managing World of Warcraft Mythic+ raid scheduling and team coordination.

## Features

- **Automatic Weekly Reset**: Availability message resets every Tuesday at noon CST — no manual intervention needed
- **Availability Tracking**: Players react with emoji to indicate weekly availability (green/yellow/red)
- **Key Request Flow**: Players create run requests via DM with level, day, time, and run type selection
- **Smart Team Assembly**: Automatically assigns players to roles (tank, healer, DPS) based on class and preferences
- **Off-Role Displacement**: Main-role players can bump secondary fillers out of slots more than 8 hours before a run
- **Fill Queue**: Players can join a full run as fill; the full-run DM includes the current roster so they know who's in
- **Direct Messaging**: DMs players to confirm signups, notify of run changes, and ask available players to fill open spots
- **DM Retry System**: Automatically resends unanswered DM requests after 2 hours
- **Conflict Detection**: Alerts the coordinator when multiple unfilled schedules overlap and could form a complete team
- **Schedule Management**: Organizers can delete or modify runs; coordinators/admins can add, remove, and reassign raiders
- **Pre-Post Raider Addition**: Organizers can add registered raiders before publishing a schedule
- **Interactive Button UI**: Embed-based schedule posts with Sign Up, Remove, and Manage buttons
- **Smart Registration**: First-time users are prompted to select class, roles, and timezone when clicking a button
- **Persistent State**: Bot state saved to `state.json` with automatic migration from legacy pickle format
- **Docker Support**: Dockerfile included for containerized deployment
- **Changelog Announcements**: Bot posts and pins a changelog message on startup when the version changes
- **Price Watch**: Owner-only tracking of Undermine Exchange commodity prices, with a DM alert when a price dips into a self-adjusting low band

## Installation

### Requirements

- Python 3.9+
- `discord.py >= 2.0`
- `python-dotenv`

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

3. Create a `.env` file in the project root:
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
ADMIN_ID=comma_separated_admin_user_ids
BANKER_ID=your_banker_user_id
UNDERMINE_API_KEY=your_undermine_exchange_api_key
UNDERMINE_REGION=us
```

4. Run the bot:
```bash
python bot.py
```

### Docker

```bash
docker build -t mythic-plus-bot .
docker run --env-file .env mythic-plus-bot
```

## Usage

### Availability Signup

The bot automatically posts a fresh availability message every **Tuesday at noon CST**. Players react with:
- 🟢 — Available
- 🟡 — Maybe Available
- 🔴 — Not Available

First-time reactions prompt automatic class/role/timezone registration via DM.

The `!avail` command (coordinator/admin only) can still be used to manually post an availability message if needed.

### Key Request Flow

Players click the **⚔️ CLICK TO CREATE A REQUEST** button pinned in the key channel, or use `!key`. The bot DMs them to select:
1. Key level (Climb10, 10, 11, 12+)
2. Day (next 7 days)
3. Start time (in their registered timezone)
4. Run type (one key or multiple keys)

If a run already exists at that time, the bot asks if the player wants to join it instead. After submitting, the organizer can optionally add registered raiders before the schedule is published.

### Schedule Buttons

Each schedule embed has three buttons:

- **✅ Sign Up** — Join the run. Unregistered users are prompted to register via DM first. Multi-role raiders choose which role to fill.
- **❌ Remove** — Leave the run. If the run was full, the bot searches for a replacement.
- **⚙️ Manage** — Available to the organizer, coordinator, and admins.

### Manage Menu

**Organizer view:**
- **Delete Run** — Removes the schedule and DMs all signed-up members
- **Modify Run** — Edit key level, date, time, and note via a modal; members are notified of changes

**Coordinator/Admin view** (also shown when the coordinator is the run organizer):
- **Add Raider** — Search by display name (partial match) and assign a role
- **Remove Raider** — Remove any signed-up raider from the run
- **Change Role** — Move a raider to a different role slot

### Price Watch

An owner-only feature for tracking Undermine Exchange commodity prices. Only the user configured as `BANKER_ID` can use it. The bot polls the Undermine Exchange API every hour for each watched item (region-wide commodities only, via `UNDERMINE_REGION`) and DMs the banker when the current price dips **below** a rolling low band computed from that item's own last 14 days of price history. Each item has its own adaptive threshold: it starts at the 35th percentile, loosens if the item goes 7 days without an alert, tightens if it alerts twice or more in 7 days, and is clamped to a 10–50 percentile range. To avoid spam, an item won't alert again until its price recovers back above the median.

- `!watch <itemId> [label]` — start watching an item, with an optional friendly label
- `!unwatch <itemId>` — stop watching an item
- `!watches` — list everything currently being watched

These commands work via DM or in the key channel, and only respond to the configured `BANKER_ID`.

### Commands

| Command | Channel | Permission | Description |
|---------|---------|------------|-------------|
| `!avail` | `AVAIL_CHANNEL` | Coordinator/Admin | Manually post a new availability message |
| `!key` | `KEY_CHANNEL` | Anyone | Start the key request flow via DM |
| `!keys` | Any | Anyone | DMs you your currently scheduled runs |
| `!modify` | `KEY_CHANNEL` or DM | Anyone | Update your class, roles, and timezone |
| `!setup` | `KEY_CHANNEL` | Coordinator/Admin | Re-post and pin the key request button |
| `!cleanup` | `AVAIL_CHANNEL` or `KEY_CHANNEL` | Coordinator/Admin | Purge both channels and reset all state (preserves raiders) |
| `!watch <itemId> [label]` | `KEY_CHANNEL` or DM | Banker only | Start watching an item's price |
| `!unwatch <itemId>` | `KEY_CHANNEL` or DM | Banker only | Stop watching an item |
| `!watches` | `KEY_CHANNEL` or DM | Banker only | List currently watched items |

## Project Structure

```
mythic-plus-bot/
├── bot.py              # Main Discord bot client and event handlers
├── raider.py           # Raider class representing a player
├── schedule.py         # Schedule class for raid team composition
├── utils.py            # Utility functions and constants
├── views.py            # Discord UI components (buttons, dropdowns, modals, views)
├── undermine.py        # Async Undermine Exchange API client
├── watchlist.py        # Watch/Watchlist state, buy-signal detection, formatters
├── state.json          # Persisted bot state (auto-generated)
├── watches.json        # Persisted price-watch state (auto-generated)
├── version.txt         # Tracks last deployed version for changelog announcements
├── CHANGELOG.md        # Version history
├── Dockerfile          # Container build file
├── .dockerignore       # Docker build exclusions
└── README.md           # This file
```

## Configuration

| Variable | Description |
|----------|-------------|
| `CLIENT_KEY` | Discord bot token from Discord Developer Portal |
| `AVAIL_CHANNEL_ID` | Channel ID for weekly availability tracking |
| `KEY_CHANNEL_ID` | Channel ID for schedule posts and key requests |
| `GUILD_ID` | Discord server (guild) ID |
| `TANK_ROLE_ID` | Discord role ID for tank position mentions |
| `HEALER_ROLE_ID` | Discord role ID for healer position mentions |
| `DPS_ROLE_ID` | Discord role ID for DPS position mentions |
| `COORDINATOR_ID` | User ID of the raid coordinator |
| `MYTHIC_PLUS_ID` | Role ID for the Mythic+ raider role (used in availability message ping) |
| `ADMIN_ID` | Comma-separated user IDs with coordinator-level manage permissions |
| `BANKER_ID` | User ID allowed to use the price watch commands (`!watch`, `!unwatch`, `!watches`) |
| `UNDERMINE_API_KEY` | API key for the Undermine Exchange API |
| `UNDERMINE_REGION` | Region for price lookups (optional, default `us`) |

## Class and Role Support

**Supported Classes**: Warrior, Paladin, Hunter, Rogue, Priest, Death Knight, Shaman, Mage, Warlock, Monk, Druid, Demon Hunter, Evoker

**Available Roles**: Tank, Healer, DPS

## Architecture Notes

- **Single-threaded async**: The bot runs as a single process with async event handlers and background tasks (hourly cleanup, weekly availability reset)
- **JSON Persistence**: State is stored in `state.json`; legacy `state.pkl` is automatically migrated on first boot
- **Timezone-Aware Datetimes**: All schedule times are stored as timezone-aware datetime objects; DMs display times in each raider's registered timezone
- **Persistent Views**: Button views use `timeout=None` and are re-registered on startup so interactions survive bot restarts
- **Circular Import Guards**: Cross-module type hints use `TYPE_CHECKING` guards to avoid circular imports at runtime

## Development

### Running Locally

```bash
python bot.py
```

### Linting

```bash
pip install ruff
ruff check .
ruff format .
```

### Testing

```bash
pip install pytest
pytest
```

**Manual Testing Checklist:**
- [ ] Availability auto-reset fires on Tuesday at noon CST
- [ ] Availability reactions (🟢/🟡/🔴) and first-time registration
- [ ] Key request flow via button and `!key` command
- [ ] Pre-post raider addition before schedule publish
- [ ] Button signup (registered and unregistered users)
- [ ] Button removal and fill search
- [ ] Full-run DM includes current roster
- [ ] Organizer manage: delete and modify run
- [ ] Coordinator manage: add, remove, and change raider role
- [ ] Coordinator who created a run gets coordinator manage menu
- [ ] DM outreach and retry for unfilled schedules
- [ ] Off-role displacement (8h+ before run)
- [ ] Fill queue signup and prompt
- [ ] `!modify` — class/role/timezone update
- [ ] `!cleanup` — channel purge and state reset
- [ ] State persistence across restarts
- [ ] Changelog post on version bump

## Support

For issues or feature requests, please create an issue in the repository.
