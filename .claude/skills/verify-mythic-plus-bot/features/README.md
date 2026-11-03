# Mythic+ bot verification map

This directory is the maintained map of what a Discord user can do with the Mythic+ bot and how to drive each path through the offline harness. Read this index before driving the bot, then use the matching feature file as the recipe. `H=.claude/skills/verify-mythic-plus-bot/scripts/harness.py`, run from the repo root with `PYTHONIOENCODING=utf-8`.

## Baseline preconditions

- Drive the bot only through the offline harness: `python $H run <journey>` or `python $H script <scenario.py>`. Never run `python bot.py` and never load the repo `.env`; its `CLIENT_KEY` is the live production bot.
- `python $H doctor` passes: sentinel token in use, key-request button pinned by `on_ready`, all 7 background loops intercepted.
- Each run boots the bot (`MyClient`, a `commands.Bot` that loads the feature cogs in `cogs/`) in its own empty scratch data dir under `.verify/runs/` (no `state.json`, `events.jsonl`, `snipes.json`, `token_watch.json`, `watches.json`; `CHANGELOG.md` copied in). Nothing carries over between runs.
- After boot, `#availability` (`h.avail`) holds the pinned `v<BOT_VERSION>` changelog post and `#keys` (`h.key`) holds the pinned `CLICK TO CREATE A REQUEST` button.
- Personas: `h.coordinator` (coordinator, in `ELEVATED_IDS`), `h.admin` (admin, in `ELEVATED_IDS`), `h.banker` (`BANKER_ID`), and `h.user("Name")` for any new, unregistered member. `h.user("Name", dms_open=False)` makes bot DMs raise `Forbidden`.
- Game APIs (Blizzard, Undermine, Raider.io) are blocked unless the run passes `--network`.

## Driving conventions

- Start every recipe from the baseline state unless its preconditions say otherwise.
- Act only as a user acts: `h.say` (message), `h.react` (reaction), `h.click` (button by label or custom_id), `h.choose` (select by placeholder or custom_id, option by value or label), `h.submit_modal` (modal fields by label). Never call handler methods or mutate `client.state.schedules` / `client.state.raiders`.
- Register people the way users do: a 🟢/🟡/🔴 reaction on the availability post, the `Sign Up` button, or the key-request flow. Reuse the steps in `scripts/journeys.py` (`post_availability`, `register_via_availability`, `signup_new_raider`, `fill_class_form`, `create_key_request`).
- After each action, wait on the user-visible result with `h.wait_for(where, contains)`; never sleep a fixed time.
- Treat command strings, labels, and placeholders as literal. They are matched by substring (`"Choose start time"` matches `Choose start time (Central time)`).
- The harness delivers the response to a modal submitted from a channel interaction (for example `Add Raider to Run` from `Manage`) to the submitter's DM, not to the channel. Wait on `h.wait_for(user, ...)` for those replies; on live Discord they are ephemeral in the channel.
- Background loops never run on their own. Run one iteration with `await h.tick("<loop>")`. Nothing advances the clock.
- Write ad-hoc scenarios in your scratchpad, not in the repo.

## Proof and skip reporting

- Prove both what the user saw (channel post, DM, or ephemeral reply with its audience) and the side effect (`h.state_file("state.json")`, `"events.jsonl"`, `"snipes.json"`, `"token_watch.json"`).
- Every run writes `.verify/evidence/<run-id>/` with `summary.json`, `transcript.md`, `transcript.jsonl`, and `data/`. Cite the run id.
- A run that logged a bot ERROR fails even when every check passed. Read `bot_errors_logged` in `summary.json`.
- When a feature's value comes from a game API (prices, item names, gear), say the proof is offline-only, or rerun with `--network`.
- Report an unreachable path with the attempted call and the unmet precondition. Do not report a path as verified through a different entry point.
- Report a failing journey as a failure with its reason. Never edit a journey's checks to match buggy output.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with the harness` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact harness call and the observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run, and known bugs the journeys expose.

Keep implementation detail out of the map except where it changes what a verifier must do.

## Features

| Feature | File | Journey | Status at last run |
|---|---|---|---|
| Command help | [help.md](./help.md) | `help` | pass |
| Weekly availability and registration | [availability.md](./availability.md) | `availability-signup` | pass (fresh-install `KeyError` and unsaved pool change fixed in 1.10.1) |
| Key request (schedule a run) | [key-request.md](./key-request.md) | `key-request` | pass |
| Signup, removal, fill queue | [signup.md](./signup.md) | `signup-fill-restart` | pass |
| Auction sniper | [auction-sniper.md](./auction-sniper.md) | `snipe` | pass (offline label fallback) |
| WoW Token sell alert | [token-alert.md](./token-alert.md) | `banker-token` | pass (offline, no price) |

The `command-gates` journey (permission gates across commands) also passes; it has no feature file of its own.

## Not yet mapped

- Schedule management: `Manage` button → `ManageScheduleView` (organizer: `Delete Run`, `Modify Run`) and `CoordinatorManageView` (`Add Raider`, `Remove Raider`, `Change Role`).
- `!modify` (re-register class/roles/timezone; removes the raider from every run first) and `!keys`.
- `!cleanup` and `!setup`.
- DM outreach: `Scheduling.fill_remaining_spots` offers (sleeps an hour first, so never fires in a run), `retry_unanswered_dms`, the ✅/❌ offer reactions (`on_spot_offer_reaction`), and the full-run fill offer DM.
- Off-role displacement (needs a run more than 8 hours out with an off-role holder).
- Price watch: `!watch`, `!unwatch`, `!watches`, and `price_watch_check` (`cogs/pricewatch.py`, banker, Undermine API).
- `!gearaudit` (`cogs/gear.py`, coordinator/admin, Blizzard API, `character_mappings.json`).
- Background loops (each lives in its cog: `hourly_check` in `cogs/scheduling.py`, `weekly_avail_reset` in `cogs/availability.py`, `raiderio_harvest`/`forecast_preview` in `cogs/forecasting.py`): `hourly_check` (reminders, `run_completed` events, passed-run deletion), `weekly_avail_reset`, `raiderio_harvest`, `forecast_preview`, `auction_snipe_check` and `token_watch_check` alert DMs.
- Changelog announce on version bump (exercised by every boot, not asserted by a journey).
