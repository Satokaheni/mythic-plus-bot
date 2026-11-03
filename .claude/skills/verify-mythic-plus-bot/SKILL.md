---
name: verify-mythic-plus-bot
description: Drive the Mythic+ Discord bot (bot.py MyClient and its cogs) offline the way a Discord user does — type commands, react, click buttons, pick selects, fill modals — and capture proof of what the bot posted, DMed, edited, and persisted. Use to prove a change to scheduling, availability, signup, key requests, sniping, token alerts, or any command/view works end to end, beyond unit tests.
---

# Verify the Mythic+ bot

The bot's only user surface is Discord. This skill drives it through an **offline harness**:
the real `MyClient` boots (real `setup_hook`, cog loading, `on_ready`, commands, views, `state.save()`) in a
scratch data dir, and a fake Discord world stands in for the gateway. User actions go in through
the same entry points Discord uses — `client.dispatch("message" | "reaction_add")` and discord.py's
own `ViewStore.dispatch_view` / `dispatch_modal` routing for buttons, selects and modals — so a
button only works if a live view is really registered for it, exactly like production.

**Never run the bot with the repo `.env`.** Its `CLIENT_KEY` is the production bot in the real
guild. A second copy online would answer every command twice, run the hourly/weekly tasks against
real raiders, and post a changelog (`version.txt` lags `BOT_VERSION`). The harness refuses to: it
patches `dotenv.load_dotenv` to a no-op before importing `bot.py`, sets a sentinel token, aborts if
`bot.CLIENT_ID` is anything else, and patches discord.py's HTTP client and `login`/`connect` to raise.

All paths below are relative to the repo root. `H=.claude/skills/verify-mythic-plus-bot/scripts/harness.py`.
On Windows set `PYTHONIOENCODING=utf-8` (emoji in output).

## Launch

There is no long-running server: each journey boots its own bot in-process and tears it down.

- Prereqs once: `pip install -e ".[dev]"` (discord.py 2.x, python-dotenv, aiohttp, tzdata).
- Run a journey: `python $H run <journey>` · several: `python $H run help snipe` · all: `python $H run all`
  (each journey runs in its own process). `python $H list` prints the journeys.
- Ready = `on_ready` has finished: the harness waits for the bot to go quiet after `dispatch("ready")`.
  On an empty data dir that means the v`BOT_VERSION` changelog is posted+pinned in `#availability`
  (`CHANGELOG.md` is copied in, as a checkout ships it) and the key-request button is pinned in `#keys`.
- Flags: `--keep` keeps the scratch data dir for inspection; `--network` allows real Blizzard /
  Undermine / Raider.io HTTP using **only** those API keys from `.env` (allowlist in `NETWORK_ENV_ALLOWLIST`;
  never `CLIENT_KEY`). Default is offline: game-API calls fail with a recorded `network blocked` error,
  which the bot's own fallbacks handle (e.g. `!snipe` uses the typed label).
- Background loops (`hourly_check`, `weekly_avail_reset`, `price_watch_check`, `raiderio_harvest`,
  `forecast_preview`, `auction_snipe_check`, `token_watch_check`) are **intercepted, not started**.
  Run one iteration on demand with `await h.tick("hourly_check")`; `h.tick` finds each loop on the cog that owns it (or on the client).

Fake IDs (in `IDS` in harness.py): coordinator `h.coordinator`, banker `h.banker`, admin `h.admin`,
channels `h.avail` (#availability) and `h.key` (#keys). New users: `h.user("Name")`.

## Doctor

`python $H doctor` — read-only. Run it first, and whenever anything looks off. It checks python and
discord.py 2.x, that the discord.py internals the harness routes through exist, deps import,
`.verify/` is gitignored, then does a throwaway offline boot and confirms: sentinel token in use,
key-request button pinned by `on_ready`, all 7 loops intercepted. It also reports stale scratch dirs.
A discord.py upgrade that renames `ViewStore.dispatch_view` / `dispatch_modal` / `View._scheduled_task`
shows up here first.

## Drive

Journeys live in `scripts/journeys.py` (one per file in `features/`). For anything not covered,
write a scenario in your scratchpad (not the repo) and run `python $H script <path.py>`:

```python
async def scenario(h):
    alice = h.user("Alice")
    await h.say(alice, "!help", "key")                     # message in #keys ("avail", "dm", or a channel)
    msg = await h.wait_for("key", "Mythic+ Bot — Commands")  # bot message (new or edited) matching text
    h.check("help embed has a Scheduling field", any("Scheduling" in f.name for f in msg.embed.fields))
```

The API (all `await`ed except `user`, `check`, `state_file`, `bot_messages`):

| Call | What the user does |
|---|---|
| `h.say(user, text, where)` | sends a message; `where` = `"key"`, `"avail"`, `"dm"`, or a channel |
| `h.react(user, msg, "🟢")` | adds a reaction (also feeds `wait_for("reaction_add")` in DM flows) |
| `h.click(user, msg, "Sign Up")` | clicks a button by label or custom_id |
| `h.choose(user, msg, "Choose your primary role", "tank")` | picks select option(s) by value or label; handle = placeholder or custom_id |
| `h.submit_modal(user, {"Note": "text"})` | fills and submits the modal open for that user (field labels) |
| `h.restart()` | new `MyClient` on the same data dir; Discord-side messages persist |
| `h.tick(loop_name)` | one iteration of a background loop |
| `h.wait_for(where, contains, predicate=, timeout=)` | waits for a bot message after the last action; `where` = channel name, channel, or a user (their DM) |
| `h.state_file("state.json")` | parsed state file from the data dir (`events.jsonl` → list) |
| `h.check(name, ok, detail)` | records a pass/fail; a journey passes only if every check passes |

Stable handles in this repo: button labels (`Sign Up`, `Remove`, `Manage`, `CLICK TO CREATE A REQUEST`,
`Submit`, `Submit Key Request`, `Yes, Add Note`, `No, Continue`, `Add Raider`, `Post Schedule`) and select
placeholders / custom_ids (`wow_class`, `primary_role`, `secondary_role`, `us_timezone`, `wow_level`,
`wow_day`, `wow_start_time`, `key_run_type`). Messages expose `.text()`, `.embed`, `.embed_field(name)`,
`.components`, `.reactions`, `.deleted`, `.pinned`, `.ephemeral_to`.

Discord's rules the fake world enforces, so violations surface as they would live:
- Payload limits → `HTTPException 400` (content 2000, embed field 1024 / total 6000, 25 fields/options,
  5 rows, empty field names/values), recorded as `rejected`.
- A click with no live view registered for that message → `This interaction failed` (harness error).
- Responding to an interaction twice → `InteractionResponded`; followup before ack → `NotFound`.
- Unoffered select values, disabled components, other users' ephemeral messages → refused.
- `h.user(..., dms_open=False)` → bot DMs raise `Forbidden`.

## Evidence

Every run writes `.verify/evidence/<run-id>/` (gitignored, survives cleanup):
- `summary.json` — journey, pass/fail, every check, harness error, ERROR lines the bot logged,
  blocked network attempts, intercepted loops.
- `transcript.md` — human-readable timeline: each user action and each bot send/edit/delete/react/pin/
  interaction response, with content, embed text, components, and ephemeral audience.
- `transcript.jsonl` — the same events, machine-readable (full embed dicts).
- `data/` — the bot's files after the run: `state.json`, `events.jsonl`, `snipes.json`,
  `token_watch.json`, `watches.json`, `version.txt`, `bot.log`.

Proof standards:
- Drive the real user path (message, reaction, click, select, modal). Never call handler methods or
  mutate `client.state` (schedules, raiders, availability) or a cog's state to set up a journey — register people the way users do.
- Prove the action and the resulting state: what the user saw (channel/DM/ephemeral) **and** the side
  effect (`state.json`, `events.jsonl`, the feature's JSON file). A posted embed without the persisted
  schedule is half a proof.
- A run that logged an ERROR from the bot fails even if every check passed — read `bot_errors_logged`.
- Offline is a test mode: it skips real Discord and, by default, real game APIs. Network attempts are
  listed in `summary.json`; if a feature's value comes from an API (prices, gear, names), say the proof is
  offline-only or rerun with `--network`.
- Report a failing journey as a failure with its reason. Never edit a journey's checks to match buggy
  output; fix expectations only when the bot's output is correct and the journey assumed wrong.

## Cleanup

`python $H cleanup` removes scratch data dirs under `.verify/runs/` (normally already gone; they
linger only with `--keep` or a killed run). It never touches `.verify/evidence/`. The harness runs
in-process and opens no ports, so there is nothing else to stop; to kill a hung run, stop the
`python … harness.py` process you launched, by its PID, never by process name.

## Fidelity limits (what the harness does not prove)

- Real Discord delivery, rendering, markdown, rate limits, permissions, and the 3-second interaction
  acknowledgement deadline.
- Message-cache behavior after a real restart (discord.py only fires `on_reaction_add` for cached
  messages; the harness always dispatches). `get_message_history` warming is exercised but not required.
- Time: nothing advances the clock. `fill_remaining_spots` sleeps an hour before DMing, so its offers
  never fire in a run; `hourly_check` / reminders need `h.tick` with schedules already in the past.

## Helpers

- `scripts/harness.py` — `doctor | list | run <journey…|all> | script <file> | cleanup`, plus the
  `Harness` API above. Run with `python .claude/skills/verify-mythic-plus-bot/scripts/harness.py …`.
- `scripts/journeys.py` — the journeys and reusable steps (`post_availability`,
  `register_via_availability`, `signup_new_raider`, `fill_class_form`, `create_key_request`). Import
  them in a scenario with `from journeys import create_key_request` (scripts/ is on `sys.path`).
- `features/` — the feature map. Read `features/README.md` first.
