# Mythic+ Bot

[![Tests](https://github.com/Satokaheni/mythic-plus-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/Satokaheni/mythic-plus-bot/actions/workflows/tests.yml)
[![Docker](https://github.com/Satokaheni/mythic-plus-bot/actions/workflows/docker.yml/badge.svg)](https://github.com/Satokaheni/mythic-plus-bot/actions/workflows/docker.yml)
![Version](https://img.shields.io/badge/version-1.7.0-blue.svg)
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
- **Event Logging**: The bot records anonymized availability and run events locally (`events.jsonl`) to power a future automatic-scheduling feature — no user-facing change
- **Raider.io Harvest**: The bot backfills and daily-harvests Mythic+ run history from Raider.io for mapped, registered raiders to seed the forecasting dataset — requires `RAIDERIO_API_KEY` and a runtime-provided `character_mappings.json`
- **Forecast Preview**: Every Wednesday at noon CST, the bot automatically predicts the best weekly Mythic+ run from availability and play-history data — assembling a role-valid team that prefers people's primary roles — and DMs a dry-run preview to the banker (no runs are auto-created yet)

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
BANKER_BUDGET_GOLD=100000
UNDERMINE_API_KEY=your_undermine_exchange_api_key
UNDERMINE_REGION=us
RAIDERIO_API_KEY=your_raiderio_api_key
RAIDERIO_REGION=us
LOG_FILE=bot.log
LOG_LEVEL=INFO
```

5. (Optional) Provide `character_mappings.json` in the project root at runtime — a gitignored, manually maintained JSON array mapping `discord_id` to character names, used to seed the forecasting dataset from Raider.io. Not required to run the bot.

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

Each alert also suggests **how much to buy** on your gold budget (`BANKER_BUDGET_GOLD`, default 100,000): it walks the current auction listings from cheapest up to the item's low band and reports the units and total cost you can grab within budget — so you stockpile at a discount without overpaying. Alerts are only sent during **waking hours (10 AM–11:59 PM Central)**; a dip that happens overnight is held and alerts the next morning if it's still a good deal.

- `!watch <itemId> [label]` — watch one item, with an optional friendly label
- `!watch <id1> <id2> <id3> …` — watch several items at once (each auto-labelled `Item <id>`)
- `!unwatch <itemId> [itemId …]` — stop watching one or more items
- `!watches` — list everything currently being watched

These commands work via DM or in the key channel, and only respond to the configured `BANKER_ID`. The watchlist is saved to `watches.json` and reloaded on startup, so it survives bot restarts.

### Availability Forecasting

The bot learns **when your raiders actually play** and predicts the best time for a run — entirely automatically, no commands.

- **Data it learns from:** every availability reaction and DM accept/decline, the roster of each completed run, and each raider's real Mythic+ history harvested daily from Raider.io. These are stored locally in `events.jsonl`, each stamped with the player's local day-of-week and 2-hour block.
- **Weekly prediction:** every **Wednesday at noon CST** (a day after the availability post), if this week's 🟢 pool can field a full team, the bot scores each candidate time by how likely each available raider is to be free then, assembles the best **role-valid** roster (1 tank, 1 healer, 3 DPS) — **preferring each person's primary role**, only using an off-role to fill a spot no main can cover — and DMs a **dry-run preview** to the `BANKER_ID`. The preview shows the top time, per-member availability, and a couple of runner-up times.
- **Dry-run only (for now):** the bot *suggests*; it does not create runs yet. Automatic multi-time polling and run creation is a planned next step.
- **Setup:** requires `RAIDERIO_API_KEY` in `.env` and a runtime-provided `character_mappings.json` (mapping Discord IDs to their WoW characters). Raiders with no linked character, or who aren't registered with a timezone, are simply skipped until they are.

### Commands

| Command | Channel | Permission | Description |
|---------|---------|------------|-------------|
| `!avail` | `AVAIL_CHANNEL` | Coordinator/Admin | Manually post a new availability message |
| `!key` | `KEY_CHANNEL` | Anyone | Start the key request flow via DM |
| `!keys` | Any | Anyone | DMs you your currently scheduled runs |
| `!modify` | `KEY_CHANNEL` or DM | Anyone | Update your class, roles, and timezone |
| `!setup` | `KEY_CHANNEL` | Coordinator/Admin | Re-post and pin the key request button |
| `!cleanup` | `AVAIL_CHANNEL` or `KEY_CHANNEL` | Coordinator/Admin | Purge both channels and reset all state (preserves raiders) |
| `!watch <itemId> [label]`<br>`!watch <id1> <id2> ...` | `KEY_CHANNEL` or DM | Banker only | Watch one item (with optional label), or several at once |
| `!unwatch <itemId> [itemId ...]` | `KEY_CHANNEL` or DM | Banker only | Stop watching one or more items |
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
├── eventlog.py         # Append-only availability/attendance event log (forecasting data)
├── raiderio.py         # Raider.io client + daily harvester seeding raiderio_run events
├── forecast.py         # Availability predictor + roster/slot optimizer + dry-run preview
├── state.json          # Persisted bot state (auto-generated)
├── watches.json        # Persisted price-watch state (auto-generated)
├── events.jsonl        # Append-only event log (auto-generated, gitignored)
├── character_mappings.json  # Raider.io discord_id -> characters map (gitignored, runtime-provided)
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
| `BANKER_BUDGET_GOLD` | Gold budget used to size price-watch buy suggestions (optional, default `100000`) |
| `UNDERMINE_API_KEY` | API key for the Undermine Exchange API |
| `UNDERMINE_REGION` | Region for price lookups (optional, default `us`) |
| `RAIDERIO_API_KEY` | API key for the Raider.io API |
| `RAIDERIO_REGION` | Region for Raider.io lookups (optional, default `us`) |
| `LOG_FILE` | Path to the rotating log file (optional, default `bot.log`) |
| `LOG_LEVEL` | Log verbosity: `DEBUG`/`INFO`/`WARNING`/… (optional, default `INFO`) |

## Class and Role Support

**Supported Classes**: Warrior, Paladin, Hunter, Rogue, Priest, Death Knight, Shaman, Mage, Warlock, Monk, Druid, Demon Hunter, Evoker

**Available Roles**: Tank, Healer, DPS

## Architecture Notes

- **Single-threaded async**: The bot runs as a single process with async event handlers and background tasks (hourly cleanup, weekly availability reset)
- **JSON Persistence**: State is stored in `state.json`; legacy `state.pkl` is automatically migrated on first boot
- **Timezone-Aware Datetimes**: All schedule times are stored as timezone-aware datetime objects; DMs display times in each raider's registered timezone
- **Persistent Views**: Button views use `timeout=None` and are re-registered on startup so interactions survive bot restarts
- **Circular Import Guards**: Cross-module type hints use `TYPE_CHECKING` guards to avoid circular imports at runtime
- **Logging**: Logs go to both a rotating file (`bot.log`, ~5 MB × 3 backups, gitignored) and the console, so headless deployments (Raspberry Pi, Docker, systemd) keep a readable on-disk log. Tune with `LOG_FILE` / `LOG_LEVEL`

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
