# CLAUDE.md — Mythic+ Bot Context

This file reflects the **current state** of the codebase. Rewrite relevant sections when making changes — do not append a history log.

---

## Project Overview

A Discord bot (discord.py v2+, Python 3.9+) for scheduling World of Warcraft Mythic+ runs. Manages team assembly, availability tracking, DM outreach, and schedule lifecycle.

**Version:** 1.4.0
**Entry point:** `bot.py` (`MyClient` class)

---

## File Map

| File | Purpose |
|------|---------|
| `bot.py` | Main bot client, event handlers, commands, background tasks, DM flows |
| `schedule.py` | `Schedule` class — team slots, signup/removal, embed generation |
| `raider.py` | `Raider` class — player profile, availability checks, run tracking |
| `views.py` | All Discord UI: buttons, dropdowns, modals, ephemeral/persistent views |
| `utils.py` | Constants, state persistence (save/load JSON), class/role dicts |
| `eventlog.py` | Append-only availability/attendance event log for forecasting data |
| `undermine.py` | Async Undermine Exchange API client |
| `watchlist.py` | `Watch`/`Watchlist` classes — price-watch state, buy-signal detection, formatters |
| `raiderio.py` | Raider.io client + daily harvester seeding raiderio_run events |
| `forecast.py` | Availability predictor + roster/slot optimizer + dry-run preview formatter |
| `version.txt` | Current version string (triggers changelog DM on startup if changed) |
| `CHANGELOG.md` | Version history |
| `character_mappings.json` | Gitignored config (`discord_id → characters`), maintained manually, provided at runtime (like `.env`) |
| `pyproject.toml` | Dependencies, linting (ruff), pytest config |
| `tests/` | pytest suite — `test_raider.py`, `test_schedule.py`, `test_utils.py`, `conftest.py` |

---

## Core Data Model

### `Raider` (raider.py)
- `user_id`, `mention`, `name`, `class_play`, `timezone` (ZoneInfo)
- `roles` — list of roles player can fill: `["tank"]`, `["healer"]`, `["dps"]`, or combinations
- `current_runs` — set of `Schedule` objects they're signed up for
- `denied_runs` — set of `Schedule` objects they declined
- `check_availability(schedule)` — enforces 1-hour gap (single key) or 2-hour gap (multi-key) between runs

### `Schedule` (schedule.py)
- `level`, `run_type` ("one"/"multiple"), `start_time`, `date_scheduled`
- `team` — `{"tank": Raider|None, "healer": Raider|None, "dps": [up to 3], "fill": [extras]}`
- `missing` — list of unfilled roles
- `organizer_id` — Discord user ID of whoever created the run
- `note` — optional run note
- `try_displace_off_roler(raider, role)` — bumps off-role fillers for main-role players (>8 hrs before run)
- `_check_fill()` — auto-promotes fill queue to empty slots
- `send_message(role_mentions, bot)` — returns `(embed, view, content)` for posting/editing

### `MyClient` (bot.py)
- `raiders` — `Dict[user_id, Raider]`
- `schedules` — `Dict[message_id, Schedule]` (keyed by Discord message ID)
- `availability` — `Dict[emoji, List[Raider]]` — 🟢/🟡/🔴 weekly availability
- `dm_map` / `dm_timestamps` — track outstanding DM offers and retry timing
- `coordinator_id` — used only for conflict DMs
- `elevated_ids` — `{COORDINATOR_ID} | set(ADMINS)`, used for all permission checks
- `_post_avail_message()` — shared helper that posts the availability message, adds reactions, sets `availability_message_id`. Called by `!avail` and `weekly_avail_reset`.

---

## Key Workflows

### Signup Flow
1. User clicks "Sign Up" on schedule embed → `ScheduleButtonView`
2. Multi-role raiders get `RoleSelectView` to pick which role
3. `schedule.raider_signup()` assigns slot, `raider.add_run()` tracks it
4. If run fills → `notify_schedule()` DMs all members

### Key Request Flow
`!key` or button → `_do_key_request_flow()` DMs user:
level → day → time → run type → (optional: add pre-registered raiders) → post

### Weekly Availability Reset
Every Tuesday at noon CST, `weekly_avail_reset` (a `@tasks.loop(time=...)` task started in `setup_hook`):
1. Deletes the old availability message
2. Resets `self.availability` to `{GREEN: [], YELLOW: [], RED: []}`
3. Calls `_post_avail_message()` to post a fresh message
4. Saves state

Uses `_CST = ZoneInfo("America/Chicago")` constant for DST-aware scheduling. The task fires daily at noon CST but skips non-Tuesdays via a `weekday() != 1` guard.

### DM Outreach
- When a slot opens, `fill_remaining_spots()` DMs available raiders
- Unanswered DMs retried after 2 hours (`retry_unanswered_dms()`)
- Raiders respond ✅/❌ via reaction

### Full Run — Fill Offer DM (bot.py)
When a user tries to sign up but the run is already full, they receive a DM with:
- Warning that the run is full
- **Current roster** (Tank / Healer / DPS names) via inline roster string
- Prompt to join as fill (✅/❌ reaction)

### notify_schedule (bot.py)
Called when fill status changes (run becomes full or drops below full). DMs all current roster members. Also includes `schedule.format_dm_roster()` in the "now filled" message. Catches `discord.HTTPException` broadly. **Called AFTER the embed edit** in `remove_button` to ensure the embed always updates even if a DM fails.

### Off-Role Displacement
If a main-role player signs up and an off-role filler holds the slot (>8 hrs before run), the filler is bumped to fill queue and notified.

### Event Logging (forecasting data)
`eventlog.py` appends best-effort records to `events.jsonl` to build a dataset for a future automatic scheduler (Phase 2). Four event types are logged from the bot's flow layer:
- `avail_reaction` — 🟢/🟡/🔴 weekly availability reactions (`on_reaction_add`)
- `offer_accepted` / `offer_declined` — DM ✅/❌ responses to run offers (`on_reaction_add`)
- `run_completed` — final roster of a run whose scheduled time has passed (`hourly_check`)

`hourly_check` now writes the `run_completed` event **before** deleting a passed run's Discord message, so completed-run history is preserved instead of discarded. Logging never raises and never blocks a bot flow — failures are caught and logged, not surfaced to users.

A daily `raiderio_harvest` task (`@tasks.loop(hours=24)`, started in `setup_hook`) backfills real play-time data from Raider.io to seed the same dataset: for each mapped character in `character_mappings.json`, fetches recent + best Mythic+ runs and appends single-user `raiderio_run` events (`source="raiderio"`) to `events.jsonl`, deduped by `(user_id, run_id)`. Ignores `alt_of` — all of a person's characters count. Skips unregistered `discord_id`s, since Raider.io has no timezone data and forecasting needs one. The initial backfill runs on the first loop iteration at startup, then daily thereafter.

### Forecast Preview (Phase 2a)
A weekly `forecast_preview` task (`@tasks.loop(time=...)`, started in `setup_hook`) fires **Wednesday at noon CST** — a day after the Tuesday availability post — and produces a dry-run preview of the best-predicted weekly run:
1. **Feasibility gate** — checks whether the current green (🟢) availability pool can field a role-valid team (1 tank, 1 healer, 3 dps) via `forecast.can_field_team()`. No-ops (logs and returns) if not.
2. **Predictor** — `forecast.observations()` normalizes `events.jsonl` (`run_completed`, `offer_accepted`/`offer_declined`, `raiderio_run`) into per-user, per-(local weekday, 2-hour block) signed observations. `forecast.predict()` scores each (user, slot) pair as a recency-weighted (half-life 4 weeks), Laplace-smoothed positive fraction, backed off to a same-weekday prior, then an overall prior, then a flat base rate for users with no data.
3. **Roster/slot optimization** — `forecast.rank_slots()` walks every weekday/2h-block slot in the coming week, converts each green raider's availability probability into that slot (in their own timezone), and calls `forecast.select_team()` to pick the mean-maximizing role-valid roster (1 tank, 1 healer, 3 dps, multi-role aware). Slots are ranked by the winning team's mean probability.
4. **Dry-run DM** — `forecast.format_preview()` renders the top slot (as a Discord `<t:...:F>` timestamp) plus per-member probabilities and up to two runner-up slots, and the task DMs it to `BANKER_ID`.

This is **dry-run only**: no team DMs are sent to roster members and no `Schedule` is created — going live is Phase 2b. Best-effort: broad `except Exception` around the predict/DM logic ensures a failure never kills the loop.

### Price Watch (Undermine)
Owner-only feature gated to `BANKER_ID` — tracks region-wide commodity prices on the Undermine Exchange API (region from `UNDERMINE_REGION`, default `us`; auth via `UNDERMINE_API_KEY`).
- `price_watch_check` — an hourly background task that sweeps every watched item ID, fetches the current price and the item's last 14 days of daily price history, and evaluates a buy signal: fires when the current price is **strictly below** a rolling low band (a percentile of that item's own daily history).
- **Per-item adaptive threshold** — each `Watch` tracks its own percentile, starting at 35. Re-evaluated at most once per day: loosens by +5 if the item has gone 7 days without an alert, tightens by −1 if it has fired 2+ alerts in 7 days. Clamped to the range [10, 50]. A watch must be at least 7 days old before it's allowed to loosen (`STARVE_DAYS` age guard), so new watches don't loosen before they've had a real chance to fire.
- **Anti-spam** — only one DM is sent per genuine dip; the watch re-arms only after the price recovers back above the item's median.
- **Quiet hours** — alerts only sent when `watchlist.in_alert_window(now_cst)` is true (10 AM–11:59 PM CST, `ALERT_START_HOUR`/`ALERT_END_HOUR`). Outside the window `process_signal` is skipped entirely, so a still-good dip re-fires on the next in-window hourly check (deferred, not dropped).
- **Budget buy suggestion** — `NowResult`/`Signal` now carry the auction `auctions` ladder; `format_alert(watch, signal, budget_copper)` adds a "buy up to N for M" line via `watchlist.suggest_buy(auctions, low_band, budget_copper)`, which walks the ladder cheapest-first up to the low band. Budget from `BANKER_BUDGET_GOLD` env (default 100,000 gold → `BANKER_BUDGET_COPPER`).
- State persisted to `watches.json`.
- Commands: `!watch <itemId> [label]` or `!watch <id1> <id2> ...` (multi), `!unwatch <itemId> [itemId ...]` (multi), `!watches` — DM or key-channel, banker-only.

### Schedule Management
- **Organizer** → `ManageScheduleView`: delete or modify (level, date, time, note)
- **Coordinator/Admin** → `CoordinatorManageView`: delete run, modify run, add/remove/reassign any raider
- **Priority rule:** `is_coordinator` checked **before** `is_organizer` in `manage_button` — if the coordinator created the run, they still get `CoordinatorManageView` (full power)

---

## Embed Layout (schedule.py `send_message`)

Fields ordered for clean 3-column Discord rows:

| Row | Col 1 | Col 2 | Col 3 |
|-----|-------|-------|-------|
| 1 | 📊 Key Level | 📝 Run Type | 📋 Status |
| 2 | 🕐 Scheduled Time | 📣 Posted By | *(invisible `\u200b` spacer)* |
| 3 | 🛡️ Tank | 💚 Healer | ⚔️ DPS |

Fill Queue and Note are non-inline (full width) below if present.

---

## Permissions

```
COORDINATOR_ID    — single coordinator user ID (env var)
ADMIN_ID          — comma-separated admin user IDs (env var)
ELEVATED_IDS      — set built at startup: {COORDINATOR_ID} | set(ADMINS)
```

All permission gates (`!cleanup`, `!avail`, `!setup`, manage button) use `ELEVATED_IDS` — coordinator and admins are treated identically.

**Exception:** schedule conflict notifications are sent **only to `COORDINATOR_ID`**, never to admins.

In `manage_button` (views.py):
- `is_coordinator = user.id in bot.elevated_ids` check runs **first** → `CoordinatorManageView`
- `is_organizer`-only → `ManageScheduleView`

`MyClient` exposes `self.coordinator_id` (for conflict DMs) and `self.elevated_ids` (for permission checks).

---

## State Persistence (utils.py)

- File: `state.json` (atomic write via temp file)
- Deserialization is a 4-pass process: raiders → schedules → wiring (cross-references) → reconciliation
- Legacy `state.pkl` migration supported
- Price-watch state is persisted separately to `watches.json` (see `watchlist.py`)
- Event log is persisted separately to `events.jsonl` (gitignored, append-only JSONL, see `eventlog.py`) — not part of `state.json` and not migrated

---

## Environment Variables (`.env`)

```
CLIENT_KEY            Discord bot token
AVAIL_CHANNEL_ID      Availability reaction channel
KEY_CHANNEL_ID        Schedule posting channel
GUILD_ID              Discord server ID
TANK_ROLE_ID          @tank role ID
HEALER_ROLE_ID        @healer role ID
DPS_ROLE_ID           @dps role ID
COORDINATOR_ID        Coordinator user ID
MYTHIC_PLUS_ID        Mythic+ raider ping role ID
ADMIN_ID              Comma-separated admin user IDs
BANKER_ID             User ID allowed to use price-watch commands
BANKER_BUDGET_GOLD    Gold budget for price-watch buy suggestions (default: 100000)
UNDERMINE_API_KEY     Undermine Exchange API key
UNDERMINE_REGION      Undermine Exchange region (default: us)
RAIDERIO_API_KEY      Raider.io API key
RAIDERIO_REGION       Raider.io region (default: us)
```

---

## Commands

| Command | Channel | Who | Action |
|---------|---------|-----|--------|
| `!avail` | AVAIL | Coord/Admin | Post weekly availability message |
| `!key` | KEY | Anyone | Start key request DM flow |
| `!keys` | Any | Anyone | Show your scheduled runs |
| `!modify` | KEY/DM | Anyone | Update class/roles/timezone |
| `!setup` | KEY | Coord/Admin | Re-post key request button |
| `!cleanup` | AVAIL or KEY | Coord/Admin | Purge channel, reset state (keeps raiders) |
| `!watch <itemId> [label]` | KEY/DM | Banker | Start watching an item's price |
| `!unwatch <itemId>` | KEY/DM | Banker | Stop watching an item |
| `!watches` | KEY/DM | Banker | List currently watched items |

---

## Notable Conventions

- **Circular imports** avoided via `TYPE_CHECKING` guards in raider.py/schedule.py
- **Schedules keyed by message ID** — not UUIDs
- **Timezones:** US only (Eastern/Central/Mountain/Pacific/Alaska/Hawaii), stored as string, loaded as ZoneInfo
- **Persistent views** (`ScheduleButtonView`, `KeyRequestButtonView`) re-registered on `on_ready` so buttons survive restarts
- **All datetimes UTC-aware** internally; displayed via Discord `<t:timestamp:F>` format
- **Changelog file reads** use `encoding='utf-8'` explicitly — Windows default (cp1252) corrupts em-dashes
- **Changelog bullet prefix** uses `•` not `- ` — Discord renders `- ` as markdown list syntax, stripping the dash and adding commas when copied
- **Changelog sections** — each version entry uses `### Improvements` and `### Bug Fixes` subsections; `_read_changelog` converts `###` headers to `**bold**` in the Discord post so sections are visible there too
- **version.txt** is written by the bot after posting the changelog; never edit it manually to the new version or the post will be skipped
