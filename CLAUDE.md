# CLAUDE.md — Mythic+ Bot Context

This file reflects the **current state** of the codebase. Rewrite relevant sections when making changes — do not append a history log.

---

## Project Overview

A Discord bot (discord.py v2+, Python 3.9+) for scheduling World of Warcraft Mythic+ runs. Manages team assembly, availability tracking, DM outreach, and schedule lifecycle.

**Version:** 1.11.0
**Entry point:** `bot.py` (`MyClient(commands.Bot)`; features live in `mythicbot/cogs/`)

---

## File Map

Layout: `bot.py` (the entry point, run from the repo root so the `*.json` state files, `CHANGELOG.md` and `version.txt` resolve against it) plus the `mythicbot` package — top-level `config`/`messages`/`state`/`storage`, then `scheduling/` (runs, raiders, views, event log, forecast), `market/` (price watch, sniper, token), `guild/` (Raider.io, gear audit), `cogs/` (one per feature), and `assistant/`. All imports are absolute (`from mythicbot.scheduling.schedule import Schedule`).

| File | Purpose |
|------|---------|
| `bot.py` | Thin entry point: `MyClient(commands.Bot)` — state load, persistent views, cog loading, `on_ready` log line, facade delegators to the Scheduling cog, `on_command_error`/`on_error` |
| `mythicbot/config.py` | Env-derived constants (IDs, channels, `BOT_VERSION`, intervals), `_CST`, and `_configure_logging()` |
| `mythicbot/messages.py` | User-facing message builders and changelog/version file helpers |
| `mythicbot/state.py` | `BotState` — persisted bot state (raiders, schedules, availability, dm_map, ...); `load()`/`save()` wrap `storage` |
| `mythicbot/storage.py` | Constants, state persistence (save/load JSON), class/role dicts |
| `mythicbot/scheduling/schedule.py` | `Schedule` class — team slots, signup/removal, embed generation |
| `mythicbot/scheduling/raider.py` | `Raider` class — player profile, availability checks, run tracking |
| `mythicbot/scheduling/views.py` | All Discord UI: buttons, dropdowns, modals, ephemeral/persistent views |
| `mythicbot/scheduling/eventlog.py` | Append-only availability/attendance event log for forecasting data |
| `mythicbot/scheduling/forecast.py` | Availability predictor + roster/slot optimizer + dry-run preview formatter |
| `mythicbot/market/undermine.py` | Async Undermine Exchange API client |
| `mythicbot/market/watchlist.py` | `Watch`/`Watchlist` classes — price-watch state, buy-signal detection, formatters |
| `mythicbot/market/blizzard.py` | Blizzard Game Data Auction House API client |
| `mythicbot/market/snipelist.py` | `Snipe`/`Snipelist` classes — auction-snipe state, per-realm detection, alert planning |
| `mythicbot/market/tokenwatch.py` | `TokenWatch` — WoW Token sell-threshold state, ratchet detection, formatters |
| `mythicbot/guild/raiderio.py` | Raider.io client + daily harvester seeding raiderio_run events |
| `mythicbot/guild/gearaudit.py` | Gear audit rules — enchant/socket/gem findings and officer report formatting |
| `mythicbot/cogs/__init__.py` | `EXTENSIONS` — the list of cog modules `setup_hook` loads |
| `mythicbot/cogs/_checks.py` | `commands.check` gates: `is_banker`, `is_elevated`, `in_channels` |
| `mythicbot/cogs/scheduling.py` | `Scheduling` — `!key`, `!keys`, `!modify`, key-request/DM-offer flows, fill/notify logic, spot-offer reactions, `hourly_check` loop |
| `mythicbot/cogs/availability.py` | `Availability` — `!avail`, availability posting, 🟢🟡🔴 reaction handling, `weekly_avail_reset` loop |
| `mythicbot/cogs/admin.py` | `Admin` — `!cleanup`, `!setup`, `!help`/`!tools`, `on_ready` work (role mentions, message cache warm-up, changelog, pinned key button) |
| `mythicbot/cogs/forecasting.py` | `Forecasting` — `raiderio_harvest` and `forecast_preview` loops |
| `mythicbot/cogs/pricewatch.py` | `PriceWatch` — `!watch`/`!unwatch`/`!watches`, `price_watch_check` loop (banker) |
| `mythicbot/cogs/wowtoken.py` | `WowToken` — `!token`/`!tokenalert`, `token_watch_check` loop (banker) |
| `mythicbot/cogs/sniper.py` | `Sniper` — `!snipe`/`!snipepet`/`!unsnipe`/`!snipes`, `auction_snipe_check` loop |
| `mythicbot/cogs/gear.py` | `Gear` — `!gearaudit` (elevated) |
| `mythicbot/cogs/assistant.py` | `Assistant` — `on_message` listener for plain-language messages from allowlisted users; answers, or runs a validated and (for writes) confirmed command |
| `mythicbot/assistant/claude_runner.py` | Runs one isolated headless `claude -p` call (flags, minimal env, timeout/kill, result parsing) |
| `mythicbot/assistant/intent.py` | Command allowlist, JSON schema, arg validation, system-prompt rendering — Claude's output is validated here before anything runs |
| `mythicbot/assistant/usage.py` | `DailyUsage` — per-day call counter persisted to `assistant.json` |
| `mythicbot/assistant/smoke.py` | Owner-run live check of the real `claude` CLI with the exact lockdown flags |
| `mythicbot/assistant/system_prompt.md` | System-prompt template (`{{COMMANDS}}` is filled from `intent.USAGE`) |
| `lootcouncil/` | Standalone loot council CLI (`python -m lootcouncil <itemId>`); shares no code with the bot |
| `.claude/skills/verify-mythic-plus-bot/` | Offline harness that drives the real bot against a fake Discord world. Never run `bot.py` with the production `.env`; use the harness |
| `version.txt` | Current version string (triggers changelog DM on startup if changed) |
| `CHANGELOG.md` | Version history |
| `character_mappings.json` | Gitignored config (`discord_id → characters`), maintained manually, provided at runtime (like `.env`) |
| `pyproject.toml` | Dependencies, linting (ruff), pytest config |
| `tests/` | pytest suite — `test_admin_help.py`, `test_assistant_cog.py`, `test_availability_cog.py`, `test_blizzard.py`, `test_bot.py`, `test_checks.py`, `test_claude_runner.py`, `test_config.py`, `test_eventlog.py`, `test_forecast.py`, `test_gearaudit.py`, `test_intent.py`, `test_key_request.py`, `test_lootcouncil_clients.py`, `test_lootcouncil_core.py`, `test_lootcouncil_performance.py`, `test_lootcouncil_ranker.py`, `test_raider.py`, `test_raiderio.py`, `test_roster_handlers.py`, `test_schedule.py`, `test_smoke.py`, `test_snipelist.py`, `test_state.py`, `test_tokenwatch.py`, `test_trigger.py`, `test_undermine.py`, `test_usage.py`, `test_storage.py`, `test_watchlist.py`, `conftest.py` |

---

## Core Data Model

### `Raider` (mythicbot/scheduling/raider.py)
- `user_id`, `mention`, `name`, `class_play`, `timezone` (ZoneInfo)
- `roles` — list of roles player can fill: `["tank"]`, `["healer"]`, `["dps"]`, or combinations
- `current_runs` — set of `Schedule` objects they're signed up for
- `denied_runs` — set of `Schedule` objects they declined
- `check_availability(schedule)` — enforces 1-hour gap (single key) or 2-hour gap (multi-key) between runs

### `Schedule` (mythicbot/scheduling/schedule.py)
- `level`, `run_type` ("one"/"multiple"), `start_time`, `date_scheduled`
- `team` — `{"tank": Raider|None, "healer": Raider|None, "dps": [up to 3], "fill": [extras]}`
- `missing` — list of unfilled roles
- `organizer_id` — Discord user ID of whoever created the run
- `note` — optional run note
- `try_displace_off_roler(raider, role)` — removes off-role fillers for main-role players (>8 hrs before run)
- `raider_signup(raider, role=None)` — seats the raider in `role` (or their best open registered role), or queues them as fill when that seat is taken; returns `False` and changes nothing if the raider is already in `members`, `True` otherwise. Every caller checks the result and skips `add_run`, DMs, embed edits, and saves on `False` (answering "already signed up" where a reply is owed). The one deliberate exception is Change Role, which calls `raider_signup` right after `raider_remove` with no await between, so it cannot be refused
- `_check_fill(freed_role)` — promotes the first fill-queue raider whose registered roles include `freed_role` into that seat (DPS only while fewer than 3), updating `missing`; never touches other open seats
- `raider_remove(raider)` — frees the place the raider actually holds (tank, healer, a DPS seat, or fill — found with `seat_of`, regardless of registered roles), promotes into that freed seat only (removing a fill raider promotes no one), and returns the promoted raider (or `None`); callers DM them via the Scheduling cog's `_notify_promoted` (`MyClient._notify_promoted` delegates)
- `seat_of(raider)` / `open_roles()` — the raider's place (`"tank"`/`"healer"`/`"dps"`/`"fill"`/`None`) and the roles with an open seat; Change Role (`ChangeRoleTargetSelect`) offers only `open_roles()` minus the raider's own seat, says "no open roles" when there are none, and re-checks at click time; like coordinator Remove, it calls `notify_schedule` when the move changes the run's filled status
- `send_message(role_mentions, bot)` — returns `(embed, view, content)` for posting/editing

### `MyClient(commands.Bot)` + `BotState` + cogs
`MyClient` (bot.py) owns startup and shared handles; features are cogs loaded from `cogs.EXTENSIONS` in `setup_hook`.
- `client.state` — a `BotState` (mythicbot/state.py), the persisted data: `raiders` (`Dict[user_id, Raider]`), `schedules` (`Dict[message_id, Schedule]`), `availability` (`Dict[emoji, List[Raider]]`), `availability_message_id`, `dm_map` / `dm_timestamps` (outstanding DM offers and retry timing), plus runtime-only `role_mentions` and `reminder_messages`. `state.save()` writes `state.json`. `state.register_raider(user, class, roles, tz)` is the only way the three class/role DM forms (Sign Up welcome, availability reaction, key request) store a new raider: it returns the existing record if the user registered through another form while this one was open, so seats never point at an orphaned `Raider`. `Availability._post_avail_message` saves right after setting `availability_message_id`.
- `coordinator_id` — used only for conflict DMs; `elevated_ids` — `{COORDINATOR_ID} | set(ADMINS)`, used for all permission checks
- `client.blizzard` — shared Blizzard client (used by Sniper, WowToken, Gear)
- Facade delegators on `MyClient` (so `mythicbot/scheduling/views.py` stays decoupled from cog layout), all forwarding to the Scheduling cog: `notify_schedule`, `message_user`, `_do_key_request_flow`, `_notify_promoted`, `_notify_displaced`, `new_availability_signup_fill_schedule`, `_notify_schedule_changed`
- Cross-cog calls go through those `MyClient` facade delegators (which call `get_cog("Scheduling")` internally), e.g. Availability → `bot.new_availability_signup_fill_schedule`

| Cog | Commands | Listeners / flows | Loop |
|-----|----------|-------------------|------|
| Scheduling | `!key` (KEY channel), `!keys`, `!modify` | key-request flow, `message_user`, `repost_schedule`, `fill_remaining_spots`, `new_availability_signup_fill_schedule`, `notify_schedule`, `_notify_*`, `retry_unanswered_dms`, `check_schedule_conflicts`; DM spot-offer ✅/❌ reactions | `hourly_check` |
| Availability | `!avail` | `_post_avail_message`, `_log_avail_reaction`, 🟢🟡🔴 reactions incl. first-time registration | `weekly_avail_reset` |
| Admin | `!cleanup`, `!setup`, `!help`/`!tools` | `on_ready` work: role mentions, `get_message_history`, changelog post+pin, pinned key-request button | — |
| Forecasting | — | — | `raiderio_harvest`, `forecast_preview` |
| PriceWatch | `!watch`, `!unwatch`, `!watches` | — | `price_watch_check` |
| WowToken | `!token`, `!tokenalert` | — | `token_watch_check` |
| Sniper | `!snipe`, `!snipepet`, `!unsnipe`, `!snipes` | `_send_snipe_dm`, realm/price caches | `auction_snipe_check` |
| Gear | `!gearaudit` | `_run_gear_audit` | — |

Each cog starts its loops in `cog_load` and cancels them in `cog_unload`. Cog-owned stores (`watchlist`, `snipelist`, `token_watch`, `char_mappings` on Forecasting) live on their cog, not on the client.

---

## Key Workflows

### Signup Flow
1. User clicks "Sign Up" on schedule embed → `ScheduleButtonView`
2. Multi-role raiders get `RoleSelectView` to pick which role
3. `schedule.raider_signup()` assigns slot (refused if the raider is already in the run, e.g. a double-click), `raider.add_run()` tracks it
4. If run fills → `notify_schedule()` DMs all members

### Key Request Flow
`!key` or button → `_do_key_request_flow()` DMs user:
level → day → time → run type → (optional: add pre-registered raiders) → post

Before posting, `_run_at(schedules, start_time)` (mythicbot/cogs/scheduling.py) looks for a posted run starting at the same **instant** (aware datetimes, so 9 PM Eastern matches 8 PM Central). If one exists and has an open seat in one of the requester's roles, they are DMed a ✅/❌ offer to join it instead of posting a duplicate. Covered by the `key-request-join` harness journey.

### Weekly Availability Reset
Every Tuesday at noon CST, `weekly_avail_reset` (a `@tasks.loop(time=...)` task owned by the Availability cog and started in its `cog_load`):
1. Deletes the old availability message
2. Resets `self.availability` to `{GREEN: [], YELLOW: [], RED: []}`
3. Calls `_post_avail_message()` to post a fresh message
4. Saves state

Uses `_CST = ZoneInfo("America/Chicago")` constant for DST-aware scheduling. The task fires daily at noon CST but skips non-Tuesdays via a `weekday() != 1` guard.

### DM Outreach
- When a slot opens, `fill_remaining_spots()` DMs available raiders
- Unanswered DMs retried after 2 hours (`retry_unanswered_dms()`)
- Raiders respond ✅/❌ via reaction

### Full Run — Fill Offer DM (mythicbot/cogs/scheduling.py)
Clicking Sign Up on a full run puts the raider straight into the fill queue (`ScheduleButtonView.signup_button`); no fill-offer DM is sent. The fill-offer DM exists only on the DM spot-offer path: when a raider reacts ✅ to a spot offer (`Scheduling.on_spot_offer_reaction`) and the run is still full after off-role displacement, they receive a DM with:
- Warning that the run is full
- **Current roster** (Tank / Healer / DPS names) via inline roster string
- Prompt to join as fill (✅/❌ reaction)

### notify_schedule (mythicbot/cogs/scheduling.py)
Called when fill status changes (run becomes full or drops below full). DMs all current roster members. Also includes `schedule.format_dm_roster()` in the "now filled" message. Catches `discord.HTTPException` broadly. **Called AFTER the embed edit** in `remove_button` to ensure the embed always updates even if a DM fails.

### Off-Role Displacement
If a main-role player signs up and an off-role filler holds the slot (>8 hrs before run), `try_displace_off_roler` removes the filler from the run entirely (their slot, `members`, and `current_runs`; they are not moved to the fill queue) and `_notify_displaced` DMs them that they were removed. Every path that displaces (the Sign Up registered and welcome branches in `ScheduleButtonView.signup_button`, and the DM spot-offer ✅ in `Scheduling.on_spot_offer_reaction`) first requires `not schedule.has_raider(raider)`, so a raider already in the run never bumps anyone.

### Event Logging (forecasting data)
`mythicbot/scheduling/eventlog.py` appends best-effort records to `events.jsonl` to build a dataset for a future automatic scheduler (Phase 2). Four event types are logged from the bot's flow layer:
- `avail_reaction` — 🟢/🟡/🔴 weekly availability reactions (cog `on_reaction_add` listeners)
- `offer_accepted` / `offer_declined` — DM ✅/❌ responses to run offers (cog `on_reaction_add` listeners)
- `run_completed` — final roster of a run whose scheduled time has passed (`hourly_check`)

`hourly_check` now writes the `run_completed` event **before** deleting a passed run's Discord message, so completed-run history is preserved instead of discarded. Logging never raises and never blocks a bot flow — failures are caught and logged, not surfaced to users.

A daily `raiderio_harvest` task (`@tasks.loop(hours=24)`, owned by the Forecasting cog and started in its `cog_load`) backfills real play-time data from Raider.io to seed the same dataset: for each mapped character in `character_mappings.json`, fetches recent + best Mythic+ runs and appends single-user `raiderio_run` events (`source="raiderio"`) to `events.jsonl`, deduped by `(user_id, run_id)`. Ignores `alt_of` — all of a person's characters count. Skips unregistered `discord_id`s, since Raider.io has no timezone data and forecasting needs one. The initial backfill runs on the first loop iteration at startup, then daily thereafter.

### Forecast Preview (Phase 2a)
A weekly `forecast_preview` task (`@tasks.loop(time=...)`, owned by the Forecasting cog and started in its `cog_load`) fires **Wednesday at noon CST** — a day after the Tuesday availability post — and produces a dry-run preview of the best-predicted weekly run:
1. **Feasibility gate** — checks whether the current green (🟢) availability pool can field a role-valid team (1 tank, 1 healer, 3 dps) via `forecast.can_field_team()`. No-ops (logs and returns) if not.
2. **Predictor** — `forecast.observations()` normalizes `events.jsonl` (`run_completed`, `offer_accepted`/`offer_declined`, `raiderio_run`) into per-user, per-(local weekday, 2-hour block) signed observations. `forecast.predict()` scores each (user, slot) pair as a recency-weighted (half-life 4 weeks), Laplace-smoothed positive fraction, backed off to a same-weekday prior, then an overall prior, then a flat base rate for users with no data.
3. **Roster/slot optimization** — `forecast.rank_slots()` walks every weekday/2h-block slot in the coming week, converts each green raider's availability probability into that slot (in their own timezone), and calls `forecast.select_team()` to pick the mean-maximizing role-valid roster (1 tank, 1 healer, 3 dps, multi-role aware). Slots are ranked by the winning team's mean probability.
4. **Dry-run DM** — `forecast.format_preview()` renders the top slot (as a Discord `<t:...:F>` timestamp) plus per-member probabilities and up to two runner-up slots, and the task DMs it to `BANKER_ID`.

This is **dry-run only**: no team DMs are sent to roster members and no `Schedule` is created — going live is Phase 2b. Best-effort: broad `except Exception` around the predict/DM logic ensures a failure never kills the loop.

### Price Watch (Undermine)
Owner-only feature gated to `BANKER_ID` — tracks region-wide commodity prices on the Undermine Exchange API (region from `UNDERMINE_REGION`, default `us`; auth via `UNDERMINE_API_KEY`).
- `price_watch_check` — an hourly background task that sweeps every watched item ID, fetches the current price + auction ladder and the item's last 14 days of daily price history, and evaluates a buy signal: fires when the **bulk fill price** is **strictly below** a rolling low band (a percentile of that item's own daily history).
- **Bulk-aware signal** — the watched price is the VWAP to fill a target quantity, not the single cheapest lot. `watchlist.bulk_price(auctions, target_qty)` walks the ladder cheapest-first and blends prices over exactly `target_qty` units, so a thin cheap lot can't trigger a buy you can't actually fill at that price. If fewer than `target_qty` units are listed the signal is **not fillable** (a depth gate) and never fires. Target quantity resolves per call site as `watch.target_qty or BANKER_BULK_QTY`. When `evaluate` is called with no auction ladder it degrades to the legacy cheapest-lot behavior. `Signal` carries `price` (bulk VWAP), `fillable`, `units_available`, and `target_qty`.
- **Per-item adaptive threshold** — each `Watch` tracks its own percentile, starting at 35. Re-evaluated at most once per day: loosens by +5 if the item has gone 7 days without an alert, tightens by −1 if it has fired 2+ alerts in 7 days. Clamped to the range [10, 50]. A watch must be at least 7 days old before it's allowed to loosen (`STARVE_DAYS` age guard), so new watches don't loosen before they've had a real chance to fire.
- **Anti-spam** — only one DM is sent per genuine dip; the watch re-arms only after the price recovers back above the item's median.
- **Quiet hours** — alerts only sent when `watchlist.in_alert_window(now_cst)` is true (10 AM–11:59 PM CST, `ALERT_START_HOUR`/`ALERT_END_HOUR`). Outside the window `process_signal` is skipped entirely, so a still-good dip re-fires on the next in-window hourly check (deferred, not dropped).
- **Budget buy suggestion** — `NowResult`/`Signal` carry the auction `auctions` ladder; `format_alert(watch, signal, budget_copper)` adds a "buy up to N for M" line via `watchlist.suggest_buy(auctions, low_band, budget_copper)`, which walks the ladder cheapest-first up to the low band. Budget from `BANKER_BUDGET_GOLD` env (default 100,000 gold → `BANKER_BUDGET_COPPER`). Independent of the bulk target — this line is budget-bound, not target-bound.
- **Command grammar** — `watchlist.parse_watch_command(args)` (pure/tested) parses `!watch` into `{"kind": "single"|"multi"|"error", ...}`. The `-x<qty>` flag (quantity **glued**, never spaced) binds to the item id immediately before it. One id → `single` (`item_id`, `label`, `target_qty`), may carry a trailing label. Two or more ids → `multi` (`items`: list of `(item_id, target_qty|None)`), each with its own optional `-x` target, no custom labels. Ids without a `-x` resolve to `BANKER_BULK_QTY` at the call site. Error dicts carry a `reason` (`bad_flag` / `multi_label` / `no_item`) that `mythicbot/cogs/pricewatch.py` maps to an actionable message. Glued-only is deliberate: a spaced `-x 100` in the multi run would silently swallow the next item id as the quantity (a real reported bug). Single-item re-watch with `-x` updates the target in place; multi skips already-watched ids.
- State persisted to `watches.json` (`Watch.target_qty` round-trips; legacy entries load as `None` → global default).
- Commands: `!watch <itemId> [-x<qty>] [label]` or `!watch <id1> -x<qty> <id2> -x<qty> ...` (multi, per-item targets), `!unwatch <itemId> [itemId ...]` (multi), `!watches` — DM or key-channel, banker-only.

### Auction Sniper (Blizzard)
Open-to-all feature for tracking per-realm items (recipes, battle pets, mounts, gear) across every realm in a region via Blizzard's official Game Data API. An `auction_snipe_check` background task (30-min interval) sweeps all realms, finds the cheapest listing per item across all realms, and DMs each subscriber when a watched item's minimum price drops below their independent per-item target. Recipes also DM `BANKER_ID` (so the banker can make bulk buys if they want). One record per tracked item; each record maintains a list of subscribers with their own target prices. State persisted to `snipes.json`.
- Commands (open to all): `!snipe <itemId> <maxGold> [label]` (track a single item, e.g. `!snipe 215147 5000 Vibrant Shard`), `!snipepet <speciesId> <maxGold> [label]` (track a battle pet by species ID), `!unsnipe <id ...>` (unwatch one or more items), `!snipes` (list your tracked items with current cheapest prices).
- Requires `BLIZZ_CLIENT_ID` and `BLIZZ_CLIENT_SECRET` from developing a client at develop.battle.net, plus optional `BLIZZ_REGION` (default `us`).

### WoW Token Sell Alert (Blizzard)
Owner-only feature gated to `BANKER_ID` — tracks the region's WoW Token price via Blizzard's
`/data/wow/token/index` endpoint (region from `BLIZZ_REGION`, reusing the existing
`BLIZZ_CLIENT_ID`/`BLIZZ_CLIENT_SECRET` credentials; no new env vars).
- `token_watch_check` — a 20-minute background task (Blizzard refreshes the price about that
  often) that fetches the current price and evaluates a **fixed-threshold sell signal**.
- **Ratchet** — fires on the first crossing above the owner's threshold, then only on new highs
  at least `STEP_COPPER` (10,000g) above the last alerted price. Because `last_alert` only moves
  upward while above the threshold, a dip-and-reclimb stays silent until it beats the last
  reported high. Falling back below the threshold re-arms it.
- **No price history and no quiet hours** — deliberate scope decisions. The endpoint returns only
  a current price, the fixed threshold needs no baseline, and the owner wants alerts at any hour.
- **DM-before-commit** — `last_alert` is persisted only after the DM is delivered, so a Discord
  failure cannot silently swallow an alert.
- State persisted to `token_watch.json` (two fields: `threshold`, `last_alert`).
- Commands: `!token`, `!tokenalert <gold>`, `!tokenalert off` — DM or key-channel, banker-only.

### Gear Audit (Blizzard)

Officer-only, on demand. `!gearaudit [character]` (coordinator/admin, `ELEVATED_IDS`) reads
`character_mappings.json`, fetches each character's equipped items from Blizzard's
`/profile/wow/character/{realm}/{name}/equipment` endpoint (reusing `BLIZZ_CLIENT_ID`/
`BLIZZ_CLIENT_SECRET`), and DMs the caller a worst-first report.

Flagged: a missing **permanent** enchant on an enchantable slot, an **empty gem socket**, a
**Tier-1 enchant**, and a **rank-1 gem**. Not flagged: Tier-2 enchants (the cap this
expansion), temporary weapon oils, and off-hands that are not weapons (`item_class.id != 2`).
`ENCHANTABLE_SLOTS` is a module constant in `mythicbot/guild/gearaudit.py` — head, shoulder, chest, legs, feet,
both rings, main hand, plus a weapon off-hand — measured against the live roster and updated
per expansion.

There is **no background task, no DM to the audited player, and no persisted state**: officers
run the command and relay the result. Characters that fail to fetch (404 = rename or transfer)
are listed separately, which doubles as a stale-mapping report. Gem grading reuses the client's
`_item_cache` via `ItemInfo.quality`; a gem whose quality can't be resolved is left ungraded
rather than flagged. Gem rank reads off item quality, but not the way rarity usually reads:
in the current gem line (all item level 295) rank 1 stat gems are UNCOMMON, rank 2 are RARE
(the `Flawless` prefix), and the meta diamonds are EPIC — so `GEM_MIN_QUALITY = "RARE"` is the
floor, and requiring EPIC would flag every correctly-gemmed character. Rarity does not track
recency either: decade-old Mists gems are EPIC at item level 32, so an outdated gem of the right
rarity is not caught.

### Natural-language assistant (experimental)

Off by default (`ASSISTANT_ENABLED`). Lets allowlisted users (`ASSISTANT_USER_IDS`, intended: the owner) talk to the bot in plain language; a headless `claude -p` call returns either an answer or a command to run. Lives in `mythicbot/cogs/assistant.py` + `mythicbot/assistant/`.

- **Trigger rule** (`should_handle`) — handled only when enabled, the author is on the allowlist and is not a bot/webhook, the message is a normal message or reply, and it is either a **1:1 DM** or any guild or group-DM message that **@mentions the bot**. A group DM without a mention, guild text without a mention, `@everyone`, and anything starting with `!` (typed commands, also after stripping the mention) are ignored: no reply and no Claude call. A bare mention replies `What can I help with?`.
- **Isolation** — `claude` runs with a private `CLAUDE_CONFIG_DIR` (`ASSISTANT_CONFIG_DIR`), an empty working dir (`ASSISTANT_WORK_DIR`), and a minimal env: only `PATH`, `SYSTEMROOT`, `HOME`, `USERPROFILE`, `TEMP`, `TMP` (each when present) plus `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_OAUTH_TOKEN`, `DISABLE_AUTOUPDATER=1`. No bot secret (`CLIENT_KEY`, `BLIZZ_*`, `UNDERMINE_*`, `RAIDERIO_*`) reaches it. The prompt goes on **stdin**, never argv; `create_subprocess_exec`, no shell. Flags: `--tools ""` (verified: only the internal `StructuredOutput` tool loads), `--strict-mcp-config`, `--json-schema`, `--max-turns 3`, `--max-budget-usd 0.05`, `--permission-mode dontAsk`, `--no-session-persistence`, `--system-prompt-file`, `--model` (`ASSISTANT_MODEL`, default `haiku`). `ASSISTANT_TIMEOUT` (default 60 s) kills a hung call.
- **Validation** (`intent.validate`) — Claude's output is untrusted. Allowlist: read `{keys, snipes, watches, token, help}`, write `{snipe, snipepet, unsnipe, watch, unwatch, tokenalert}`, flow `{key}`; anything else (e.g. `cleanup`) is rejected. Args: strings, stripped, empty dropped, at most 20, each at most 100 chars, no leading `!`, none of newline, `<@`, `@everyone`, `@here`, backtick, or control/format/line-separator characters. The built command (`!name arg ...`) is capped at 1900 characters (`MAX_COMMAND_LEN`, leaving room for the confirm prompt under Discord's 2000). A rejected or failed intent gets ``I couldn't work that out — try `!help`.`` and nothing runs.
- **Read vs write vs flow** — a read command runs directly. A write command first posts ``I'll run `!cmd ...` — confirm?`` with **Confirm / Cancel** buttons (60 s timeout, only the asker can press); Cancel or timeout runs nothing. A flow command (`key`) calls `bot._do_key_request_flow(author)` directly. Answers go out in at most 3 chunks of 2000 chars, and every reply built from Claude output uses `AllowedMentions.none()`.
- **Permissions follow the Discord author, not Claude** — every routed command is dispatched through the real command with the author's own context, and every routed command's gates run before it executes (and before any confirm prompt): `_permitted_context` builds the context and runs `can_run` first, so a refused one replies ``You don't have access to `!<name>` here.`` with no Confirm prompt (e.g. an allowlisted non-banker asking for `!watch`). After Confirm, `bot.invoke` runs the checks again.
- **`!key` gate bypass** — the flow is invoked directly rather than through the `!key` command, so `!key`'s `in_channels(KEY)` gate is skipped. That channel rule only keeps #keys tidy; the key-request button is already open to everyone and the flow itself runs in DMs, so nothing extra is exposed.
- **Banker commands in a guild** — a banker command confirmed in a guild channel deletes the user's plain-language message (the command deletes its triggering message); replies are DMs as usual.
- **Cost guard** — `DailyUsage` counts every Claude call per Central-time day in `assistant.json` (gitignored, atomic write; `state.json` and the other state files are untouched) and replies `Daily assistant limit reached.` past `ASSISTANT_DAILY_CAP` (default 100). The owner runs this on a Claude subscription, so `total_cost_usd` and `--max-budget-usd` are an *estimate against subscription usage*, not billing, and the cap protects the plan's allowance from runaway use. One call at a time: a message that arrives mid-call gets `One moment — still working on the last one.`
- **Failures** — timeout (process killed), crash, non-success result, unparseable output, or a rejected intent all produce the single failure reply; the bot keeps running. A result with `subtype: success` but `is_error: true` (how the CLI reports API/auth failures, e.g. an expired setup-token) is a failure, `claude success: is_error`. `RunResult.detail` carries the CLI's own reason (`result`, else the first `errors[]` entry, at most 200 chars) and the failure warning logs it; env and the token are never logged. A non-numeric `total_cost_usd` counts as 0.
- **Startup self-checks never stop the bot** — `Assistant.cog_load` never raises (bot.py loads extensions without a try/except, so a raise would stop every cog). If an `intent.USAGE` name is not a registered command (e.g. after a rename), it logs an ERROR naming them and sets `Assistant.disabled`; this check runs even with `ASSISTANT_ENABLED` off. When enabled, it does the same if the resolved `ASSISTANT_WORK_DIR` or `ASSISTANT_CONFIG_DIR` is inside a git work tree (a `.git` in it or any parent) or inside the bot's own directory, because Claude Code loads `CLAUDE.md` from the work dir's parents and project settings from its project root. A disabled assistant's listener returns at once: no reply, no Claude call. `tests/test_assistant_cog.py` boots the real `MyClient` with every `cogs.EXTENSIONS` entry and asserts every `USAGE` name resolves.
- **Smoke check** — `python -m mythicbot.assistant.smoke --env-file .env` (owner-run; reads only `CLAUDE_CODE_OAUTH_TOKEN`) calls the real CLI with the exact flags and fails unless the init message shows no tools beyond `StructuredOutput` and no MCP servers. It passed on the owner's Windows PC, which is where the bot runs (2.2–3.0 s, haiku, Claude Code 2.1.317).
- **Install** — the Dockerfile installs the native Claude Code CLI as `botuser` (`curl -fsSL https://claude.ai/install.sh | bash -s 2.1.317 && claude --version` under `SHELL ["/bin/bash", "-o", "pipefail", "-c"]`, so a failed download fails the build; `~/.local/bin` on `PATH`, `DISABLE_AUTOUPDATER=1`). On a non-Docker host (e.g. the owner's Windows PC) install it per https://code.claude.com/docs/en/setup and run `claude setup-token` for the OAuth token.

### Help Command
`!help` / `!tools` — list all bot commands by category (scheduling, price watch, auction sniper, account) with usage and permissions. The Assistant field appears only when `ASSISTANT_ENABLED` is on.

### Schedule Management
- **Organizer** → `ManageScheduleView`: delete or modify (level, date, time, note)
- **Coordinator/Admin** → `CoordinatorManageView`: delete run, modify run, add/remove/reassign any raider
- **Priority rule:** `is_coordinator` checked **before** `is_organizer` in `manage_button` — if the coordinator created the run, they still get `CoordinatorManageView` (full power)

---

## Embed Layout (mythicbot/scheduling/schedule.py `send_message`)

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

All permission gates (`!cleanup`, `!avail`, `!setup`, `!gearaudit`, manage button) use `ELEVATED_IDS` — coordinator and admins are treated identically.

**Exception:** schedule conflict notifications are sent **only to `COORDINATOR_ID`**, never to admins.

In `manage_button` (mythicbot/scheduling/views.py):
- `is_coordinator = user.id in bot.elevated_ids` check runs **first** → `CoordinatorManageView`
- `is_organizer`-only → `ManageScheduleView`

`MyClient` exposes `self.coordinator_id` (for conflict DMs) and `self.elevated_ids` (for permission checks).

---

## State Persistence (mythicbot/storage.py)

- File: `state.json` (atomic write via temp file)
- Deserialization is a 4-pass process: raiders → schedules → wiring (cross-references) → reconciliation
- Reconciliation also repairs **ghost seats** from pre-F3 state: before the F3 fix, a raider seated outside their registered roles who clicked Remove left `members`/`current_runs` but kept the seat. Any raider holding a tank/healer/DPS/fill seat but missing from `members` is appended to `members` and the run is added to their `current_runs`, so Sign Up refuses them and Remove can clear the seat. The state.json format is unchanged; normal state round-trips byte for byte.
- Legacy `state.pkl` migration supported
- Price-watch state is persisted separately to `watches.json` (see `mythicbot/market/watchlist.py`)
- Auction-snipe state is persisted separately to `snipes.json` (see `mythicbot/market/snipelist.py`)
- Token sell-alert state is persisted separately to `token_watch.json` (see `mythicbot/market/tokenwatch.py`)
- Assistant daily-usage counter is persisted separately to `assistant.json` (gitignored, see `mythicbot/assistant/usage.py`)
- Event log is persisted separately to `events.jsonl` (gitignored, append-only JSONL, see `mythicbot/scheduling/eventlog.py`) — not part of `state.json` and not migrated

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
BANKER_BULK_QTY       Default bulk order size the buy signal targets (default: 100; per-item via !watch -x)
UNDERMINE_API_KEY     Undermine Exchange API key
UNDERMINE_REGION      Undermine Exchange region (default: us)
BLIZZ_CLIENT_ID       Blizzard Game Data API client ID (register at develop.battle.net)
BLIZZ_CLIENT_SECRET   Blizzard Game Data API client secret
BLIZZ_REGION          Blizzard region for auction sniping (default: us)
RAIDERIO_API_KEY      Raider.io API key
RAIDERIO_REGION       Raider.io region (default: us)
ASSISTANT_ENABLED     1/true/yes/on enables the natural-language assistant (default: off)
ASSISTANT_USER_IDS    Comma-separated user IDs allowed to use the assistant
CLAUDE_CODE_OAUTH_TOKEN  Token from `claude setup-token`; passed only to the claude subprocess
CLAUDE_BIN            Claude CLI path, or a JSON argv list (default: claude)
ASSISTANT_MODEL       Model alias for the assistant (default: haiku)
ASSISTANT_TIMEOUT     Seconds before a claude call is killed (default: 60)
ASSISTANT_DAILY_CAP   Max assistant calls per Central-time day (default: 100)
ASSISTANT_WORK_DIR    Empty working dir for claude (default: ~/.mpb-assistant/work)
ASSISTANT_CONFIG_DIR  Private CLAUDE_CONFIG_DIR (default: ~/.mpb-assistant/config)
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
| `!watch <itemId> [-x<qty>] [label]`<br>`!watch <id1> -x<qty> <id2> -x<qty> ...` | KEY/DM | Banker | Watch an item (optional bulk target via `-x`), or several at once with per-item targets |
| `!unwatch <itemId>` | KEY/DM | Banker | Stop watching an item |
| `!watches` | KEY/DM | Banker | List currently watched items |
| `!token` | KEY/DM | Banker | Show current WoW Token price and alert state |
| `!tokenalert <gold>` | KEY/DM | Banker | DM when the token price rises above this (`off` to disable) |
| `!snipe <itemId> <maxGold> [label]` | KEY/DM | Anyone | Track an item on all realms, alert when price drops below target |
| `!snipepet <speciesId> <maxGold> [label]` | KEY/DM | Anyone | Track a battle pet species on all realms |
| `!unsnipe <id ...>` | KEY/DM | Anyone | Stop watching one or more items |
| `!snipes` | KEY/DM | Anyone | List your watched items with current cheapest prices |
| `!gearaudit [character]` | KEY/DM | Coord/Admin | Report missing enchants, empty sockets, and low-quality enchants/gems |
| `!help`, `!tools` | Any | Anyone | List all bot commands by category |

With the assistant enabled, allowlisted users can also trigger `!keys`, `!snipes`, `!watches`, `!token`, `!help`, `!snipe`, `!snipepet`, `!unsnipe`, `!watch`, `!unwatch`, `!tokenalert`, and `!key` in plain language (DM the bot or @mention it). Writes ask for confirmation, and every gate above still applies to the Discord author. Typed `!commands` are never routed through it.

---

## Notable Conventions

- **Circular imports** avoided via `TYPE_CHECKING` guards in mythicbot/scheduling/raider.py/schedule.py
- **Logging** — `config._configure_logging()` (called after `load_dotenv`) attaches a `RotatingFileHandler` (`LOG_FILE`, default `bot.log`, ~5 MB × 3 backups) plus a console handler to the root logger; `client.run(..., log_handler=None)` so discord.py doesn't add a duplicate. `LOG_LEVEL` env sets the level (default INFO). `bot.log*` is gitignored.
- **Schedules keyed by message ID** — not UUIDs
- **Timezones:** US only (Eastern/Central/Mountain/Pacific/Alaska/Hawaii), stored as string, loaded as ZoneInfo
- **Persistent views** (`ScheduleButtonView`, `KeyRequestButtonView`) re-registered in `MyClient.setup_hook` so buttons survive restarts
- **All datetimes UTC-aware** internally; displayed via Discord `<t:timestamp:F>` format
- **Changelog file reads** use `encoding='utf-8'` explicitly — Windows default (cp1252) corrupts em-dashes
- **Changelog bullet prefix** uses `•` not `- ` — Discord renders `- ` as markdown list syntax, stripping the dash and adding commas when copied
- **Changelog sections** — each version entry uses `### Improvements` and `### Bug Fixes` subsections; `_read_changelog` converts `###` headers to `**bold**` in the Discord post so sections are visible there too
- **version.txt** is written by the bot after posting the changelog; never edit it manually to the new version or the post will be skipped
- **BOT_VERSION coupling** — adding a new `## [x.y.z]` section to `CHANGELOG.md` also requires bumping `BOT_VERSION` in `mythicbot/config.py`, because that constant is what the Admin cog's `on_ready` compares against `version.txt` to decide whether to post. A new section with a stale `BOT_VERSION` is never announced to the guild
- **Commands are cogs** — every `!command` is a `commands.Bot` prefix command in a cog under `mythicbot/cogs/` (prefix `!`, `help_command=None`, `case_insensitive=False`). Author/channel rules are `commands.check` gates in `mythicbot/cogs/_checks.py`. Unknown commands and failed gates are silent via `MyClient.on_command_error`; anything else is logged. `MyClient` has no `on_message`: `commands.Bot`'s default runs `process_commands`
- **Deliberate behavior deltas of the cog split** — (1) trailing text after an exact command is tolerated (`!keys foo` runs `!keys`); the converse also holds: former `startswith` matches such as `!gearauditX` / `!tokenalertX` no longer trigger, and argument-taking commands (`!watch`, `!snipe`, ...) with no arguments now answer with their usage text; (2) messages from other bots are ignored (`commands.Bot` default; previously only the bot's own); (3) discord.py's built-in help is disabled so `!help` keeps the bot's embed
- **Claude output is untrusted** — the assistant never runs anything Claude names without `intent.validate` (command allowlist + arg rules) and, for writes, an owner Confirm; replies built from it use `AllowedMentions.none()` and the subprocess env never contains bot secrets
