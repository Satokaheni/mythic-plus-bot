# Weekly availability and registration

A coordinator or admin posts the weekly availability message in `#availability`; the bot adds 🟢 (available), 🟡 (maybe), and 🔴 (not available) reactions. A registered raider who reacts is moved into that pool, replacing any earlier pick, and the reaction is logged as an `avail_reaction` event for forecasting. An unregistered member who reacts gets a DM form to pick class, primary and secondary role, and US timezone; submitting it registers them and puts them in the pool they reacted with.

## Sub-features

- `avail-post` `!avail` posts the availability message with 🟢🟡🔴 reactions and deletes the command.
- `avail-register` an unregistered reaction DMs the class/role/timezone form and registers the member on `Submit`.
- `avail-pool` a reaction puts the raider in exactly one of the 🟢/🟡/🔴 pools; a new reaction moves them.
- `avail-event` each pool change appends an `avail_reaction` event (`user_id`, `emoji`, `tz`, `week_of`) to `events.jsonl`.
- `avail-offers` a 🟢/🟡 reaction may DM offers for unfilled runs posted over an hour ago (`Scheduling.new_availability_signup_fill_schedule` in `cogs/scheduling.py`).
- `avail-weekly` `Availability.weekly_avail_reset` (`cogs/availability.py`; Tuesdays, noon CST) deletes the old post, empties the pools, and posts a fresh one.

## How to get to it (user POV)

- Coordinator or admin types `!avail` in `#availability`.
- Any member reacts 🟢, 🟡, or 🔴 on the current availability post.
- An unregistered member completes the DM form: `Choose your World of Warcraft class`, `Choose your primary role`, `Choose your secondary role (optional)`, `Choose your US timezone`, then `Submit`.
- Automatically every Tuesday at noon CST (`weekly_avail_reset`).

## Driving it with the harness

Preconditions:

- Baseline boot. The data dir has no `state.json`, which is the fresh-install case.
- The availability post must be the one whose id is `availability_message_id`; reactions on any other message (the pinned changelog) are ignored.

- **Post availability.** Coordinator types `!avail` in `#availability`. Run `msg = await post_availability(h)` (from `journeys`), which does `await h.say(h.coordinator, "!avail", "avail")` and `await h.wait_for("avail", "availability", predicate=lambda m: len(m.reactions) == 3)`. The post carries 🟢🟡🔴 and the command message is `deleted`.
- **Non-elevated !avail.** A plain member types `!avail` in `#availability`. Run `await h.say(h.user("Rando"), "!avail", "avail")`. No new post appears and the command is not deleted.
- **Register by reacting.** A new member reacts 🟢. Run `alice = h.user("Alice"); await h.react(alice, msg, "🟢")` then `form = await h.wait_for(alice, "World of Warcraft class")`. The DM form arrives with four selects and `Submit`.
- **Fill the form.** Run `await fill_class_form(h, alice, form, klass="Priest", primary="healer", secondary="dps", tz="US/Pacific")`. An ephemeral `Your Selection` reply appears. `h.state_file()["raiders"][str(alice.id)]` has `class_play == "priest"`, `roles == ["healer", "dps"]`, `timezone == "US/Pacific"`, and `alice.id` is in `availability["🟢"]`.
- **Event logged.** Run `h.state_file("events.jsonl")`. It contains `{"type": "avail_reaction", "user_id": alice.id, "emoji": "🟢", ...}`.
- **Switch pools.** Alice reacts 🔴. Run `await h.react(alice, msg, "🔴")`. In `h.state_file()["availability"]`, Alice is in `🔴` and not in `🟢`.
- **Weekly reset.** Run `await h.tick("weekly_avail_reset")`. On a Tuesday (CST) the old post is `deleted`, a new post with three reactions appears, and every pool is empty. On any other day it does nothing.
- **Proof.** Run `python $H run availability-signup`. This journey passes at 1.10.1 (8/8 checks, no `bot_errors_logged`); any failure is a regression.

## Gotchas

- **Fixed in 1.10.1: fresh install and damaged state.** `utils.load_state` now returns `{🟢: [], 🟡: [], 🔴: []}` on a missing, corrupt, or unmigratable state file, so the first reaction on a fresh install works: the pools exist from boot, a new member's DM form registers them into the pool they reacted with, and `state.json` is written. The `availability-signup` journey covers exactly this path and passes; do not weaken it.
- `!avail` itself (`Availability.cmd_avail` → `_post_avail_message`) never calls `state.save()`, so `availability_message_id` is persisted only by the next state write. A restart right after `!avail` forgets which post is current, and reactions on it are then ignored.
- `!avail` only works in `#availability`. In `#keys` it is deleted silently; elsewhere it is ignored.
- **Fixed in 1.10.1: a registered raider's pool change is persisted.** The registered branch of `Availability.on_availability_reaction` (`cogs/availability.py`) now calls `state.save()` after moving the raider, so `state.json` shows the new pool immediately and a restart keeps it. The last check of `availability-signup` ("re-reacting 🔴 moves Alice…") reads `state.json` and passes.
- Removing a reaction does nothing. Only adding a reaction changes pools.
- The 🔴 tier never triggers offers, and offers skip runs posted less than an hour ago, so `avail-offers` never fires in a fresh run without an hour-old schedule.
- `weekly_avail_reset` returns early on every day except Tuesday in `America/Chicago`. A tick proves nothing on other days.
- The form times out after 180 seconds. An incomplete form (no class or no primary role) registers nobody and sends no message.
