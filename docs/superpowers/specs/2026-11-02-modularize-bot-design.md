# Modularize the bot into commands.Bot cogs — design

Date: 2026-11-02 · Branch: `refactor/modularize-bot` · Status: approved design, pending spec review

## Intent

**What the owner asked for:** better modularization of the bot, done by parallel agents, as groundwork for a later experiment with a new way to interact with the bot (not yet chosen; planned after this lands).

**Outcome:** `bot.py` stops being a 2,265-line god object. Each feature lives in one cog file that owns its commands, listeners, background loop, and permission checks; persisted state has one owner with one `save()`. Users see no difference.

**Success criteria**
1. Every user-visible behavior is unchanged except the three deliberate deltas listed below.
2. `state.json`, `watches.json`, `snipes.json`, `token_watch.json`, `events.jsonl` formats are byte-compatible: an existing production data dir loads and saves unchanged.
3. Gate (every phase): `python -m pytest -q` passes; `ruff check .` is clean; `python .claude/skills/verify-mythic-plus-bot/scripts/harness.py run all` gives the same result as the baseline on `refactor/modularize-bot` before any change — 5 journeys pass and `availability-signup` fails only on the known fresh-install `KeyError: '🟢'`.
4. `bot.py` is an entry point (≲150 lines): config import, `MyClient` construction, cog loading, `run()`.
5. Adding a slash command later is a decorator in the owning cog, not a change to a central dispatcher.

**Assumptions (not stated by the owner):** discord.py stays at 2.x; the deploy (Dockerfile `COPY` list) is updated to include the new modules; no new runtime dependencies.

## Non-goals

- Splitting `views.py` (follow-up).
- Fixing the fresh-install availability `KeyError` (separate commit after the refactor; ships as 1.10.1).
- Slash commands, the interaction experiment, or any new feature.
- Changing pure modules: `schedule.py`, `raider.py`, `watchlist.py`, `tokenwatch.py`, `snipelist.py`, `forecast.py`, `gearaudit.py`, `eventlog.py`, `utils.py` (except as noted), API clients.

## Architecture

```
bot.py            entry: MyClient(commands.Bot), EXTENSIONS loading in setup_hook, run()
config.py         env parsing (_require_env), all IDs, ELEVATED_IDS, BANKER_BUDGET_COPPER,
                  BANKER_BULK_QTY, GEAR_AUDIT_CONCURRENCY, SNIPE_SWEEP_MINUTES, TOKEN_POLL_MINUTES,
                  BOT_VERSION, _CST, _configure_logging()
state.py          BotState
messages.py       AVAILABILITY_MESSAGE, _dm_spot_available, _dm_reminder, _read_changelog,
                  _read_last_version, _write_version
cogs/__init__.py  EXTENSIONS = ["cogs.admin", "cogs.availability", "cogs.scheduling",
                  "cogs.forecasting", "cogs.pricewatch", "cogs.wowtoken", "cogs.sniper", "cogs.gear"]
cogs/scheduling.py    Scheduling
cogs/availability.py  Availability
cogs/admin.py         Admin
cogs/forecasting.py   Forecasting
cogs/pricewatch.py    PriceWatch
cogs/wowtoken.py      WowToken      (not token.py: avoid shadowing stdlib `token`)
cogs/gear.py          Gear          (not gearaudit.py: that is the rules module it calls)
cogs/sniper.py        Sniper
```

### Ownership map (from today's `bot.py`)

| Cog | Commands | Listeners / flows | Loop |
|---|---|---|---|
| Scheduling | `!key` (KEY channel), `!keys`, `!modify` (DM / KEY / AVAIL) | `_do_key_request_flow`, `_ask_organizer_role`, `message_user`, `repost_schedule`, `fill_remaining_spots`, `new_availability_signup_fill_schedule`, `notify_schedule`, `_notify_schedule_changed`, `_notify_displaced`, `_notify_promoted`, `retry_unanswered_dms`, `check_schedule_conflicts`; `on_reaction_add` for DM spot offers (✅/❌ on messages in `dm_map`) | `hourly_check` |
| Availability | `!avail` (AVAIL, elevated) | `_post_avail_message`, `_log_avail_reaction`, `on_reaction_add` for 🟢🟡🔴 on the availability message incl. first-time registration | `weekly_avail_reset` |
| Admin | `!cleanup`, `!setup`, `!help` / `!tools` | `on_ready` work: role mentions, `get_message_history`, changelog post+pin, pinned key-request button | — |
| Forecasting | — | — | `raiderio_harvest` (keeps `before_loop` wait_until_ready), `forecast_preview` |
| PriceWatch | `!watch`, `!unwatch`, `!watches` (banker) | — | `price_watch_check` |
| WowToken | `!token`, `!tokenalert` (banker) | — | `token_watch_check` |
| Sniper | `!snipe`, `!snipepet`, `!unsnipe`, `!snipes` | `_send_snipe_dm`, realm/price caches | `auction_snipe_check` |
| Gear | `!gearaudit` (elevated) | `_run_gear_audit` | — |

Cross-cog calls go through `bot.get_cog("Scheduling")` (Availability → `new_availability_signup_fill_schedule`). `MyClient` keeps thin delegating methods for the names `views.py` calls today — `notify_schedule`, `message_user`, `_do_key_request_flow`, `_notify_promoted`, `_notify_displaced` — so views stay decoupled from cog layout.

### State

```python
class BotState:
    raiders: Dict[int, Raider]
    schedules: Dict[int, Schedule]
    availability: Dict[str, List[Raider]]
    availability_message_id: Optional[int]
    dm_map: Dict[int, Dict[int, int]]
    dm_timestamps: Dict[int, Dict[int, datetime]]
    role_mentions: Dict[str, discord.Role]   # runtime only, not persisted

    @classmethod
    def load(cls) -> "BotState"      # wraps utils.load_state(); identical results
    def save(self) -> None           # wraps utils.save_state(...) with the six persisted fields
```

Instance attributes only (today's class-level mutable defaults on `MyClient` go away). `bot.state` is created in `setup_hook` before extensions load. The 22 `save_state(...)` call sites become `bot.state.save()` / `self.bot.state.save()`. Feature stores move onto their cog: `Watchlist` → PriceWatch, `TokenWatch` → WowToken, `Snipelist` and the snipe realm/price caches → Sniper, `_char_mappings` → Forecasting. `BlizzardClient` stays a bot attribute (`bot.blizzard`), created in `setup_hook`, because Sniper, WowToken and Gear share it and its item cache.

`views.py` gets one mechanical pass: `bot_client.raiders/schedules/availability/availability_message_id/dm_map/dm_timestamps/role_mentions` → `bot_client.state.<field>`, and `save_state(bot_client.raiders, …)` → `bot_client.state.save()`.

### Commands and checks

Prefix `!`, `case_insensitive=False`, `help_command=None`. Each command body is moved verbatim; argument parsing that exists today (`watchlist.parse_watch_command`, `split()` on content, `snipelist_mod.parse_gold`) keeps operating on the raw message content (`ctx.message.content`) so parsing is byte-identical. Channel and author rules become `commands.check` predicates in the cog (`is_banker`, `is_elevated`, `in_channels(...)`, `dm_or_channels(...)`) mirroring today's conditions exactly, including the order-sensitive branches in today's `on_message` (e.g. `!key` deletion in KEY channel; `!cleanup` replying "must be used in the availability or key channel" when used elsewhere by an elevated user — that branch is a reply, not a silent check).

`MyClient.on_command_error`: silently ignore `CommandNotFound` and `CheckFailure`; log everything else with `logger.exception` (same as today's `on_error`). `on_error` stays.

### Loops

Each `@tasks.loop` moves into its cog unchanged (intervals, `time=`, weekday guards, bodies). Started in `cog_load`, cancelled in `cog_unload`. They therefore still start during `setup_hook`, as today. Only `raiderio_harvest` waits for ready, as today.

### Deliberate behavior deltas

1. Trailing text after an exact command is tolerated (`!keys foo` now runs `!keys`). Commands with arguments are unaffected.
2. Messages authored by other bots are ignored (`commands.Bot` default). Today only the bot's own messages are ignored.
3. Built-in discord.py help is disabled so `!help` keeps the bot's embed.

## Verification

- Baseline captured first on `refactor/modularize-bot`: `harness.py run all` (expect 5 pass + known failure), `pytest -q`, `ruff check .`.
- Harness updates in the foundation phase (it lives at `.claude/skills/verify-mythic-plus-bot/`): `FakeMessage` gains `_state` (the connection state) so `commands.Context` can be built; `commands.Context.send`/`reply` are routed to the fake channel the way Discord would deliver them; `Harness.tick` finds loops on cogs (`for cog in client.cogs.values()`); `doctor`'s loop check stays (7 loops intercepted).
- `tests/test_bot.py` is updated to construct what it needs from the new modules; no test is deleted to make the suite pass.
- Every phase ends at the gate. Integration additionally runs a restart journey (`signup-fill-restart`) because persistent view re-registration moves.

## Execution

1. **Foundation** (one agent, worktree `C:\Users\zach\Work\mpb-refactor`, branch `refactor/modularize-bot`): `config.py`, `state.py`, `messages.py`, `cogs/` with all eight empty cog classes registered, `MyClient` as `commands.Bot` subclass that loads them, `views.py` state pass, harness + test updates, Dockerfile `COPY` list. Behavior still lives in `MyClient` (methods call `self.state.*`). Gate, then commit.
2. **Parallel move** (three agents, each in its own worktree branched from the foundation commit):
   - A `refactor/cog-scheduling`: Scheduling cog (+ `hourly_check`).
   - B `refactor/cog-availability-admin`: Availability, Admin, Forecasting cogs.
   - C `refactor/cog-economy`: PriceWatch, WowToken, Sniper, Gear cogs.
   Each removes its handlers from `bot.py`'s `on_message`/`on_reaction_add`/methods, moves them into its cogs, passes the gate on its branch, commits.
3. **Integration** (lead): merge A, B, C into `refactor/modularize-bot`, resolve `bot.py` conflicts (each branch deletes disjoint regions), delete the now-empty `on_message`/`on_reaction_add` from `MyClient`, gate, review, then update `CLAUDE.md` (file map, `MyClient` section, workflows' file references) and `README.md` (layout). No push.
4. **Follow-up commit**: fix the fresh-install availability `KeyError`; bump `BOT_VERSION` to 1.10.1 with a `CHANGELOG.md` entry (Bug Fixes: the fix; Improvements: internal restructure, no behavior change).

## Risks

- **Event ordering:** today one `on_reaction_add` handles both reaction kinds; split listeners run concurrently. Their predicates are disjoint (AVAIL message id vs `dm_map` membership), so no ordering dependence.
- **Command matching drift:** a `startswith` today (`!gearaudit`) vs a named command differ on inputs like `!gearauditx`. Accepted; listed under delta 1's spirit.
- **Parallel merge conflicts in `bot.py`:** the foundation leaves clearly delimited regions; each agent deletes only its own. Conflicts are resolved at integration with the gate as the arbiter.
- **Harness assumptions:** `commands.Context` touches attributes the fake world lacks. Mitigated by the foundation phase making the harness pass before any behavior moves.
