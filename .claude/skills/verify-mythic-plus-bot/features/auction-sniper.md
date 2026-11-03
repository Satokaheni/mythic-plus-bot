# Auction sniper

Any member can ask the bot to watch an item or a battle pet across every realm in the region and DM them when its cheapest listing drops below their own gold target. `!snipe` and `!snipepet` add or update a target, `!snipes` lists the member's targets by DM, and `!unsnipe` removes them. One record per item is shared by all subscribers, each with an independent target, and is saved to `snipes.json`. The 30-minute `auction_snipe_check` sweep sends the alerts; recipes also alert the banker.

## Sub-features

- `snipe-add` `!snipe <itemId> <maxGold> [label]` subscribes the caller and replies `🎯 Sniping **<label>** (item <id>) under <gold>`.
- `snipe-pet` `!snipepet <speciesId> <maxGold> [label]` does the same for a battle pet species.
- `snipe-list` `!snipes` DMs `**Your snipes:**` with one line per target (target, state, last seen price).
- `snipe-remove` `!unsnipe <id ...>` removes one or more item or pet ids and replies `🚫 Stopped sniping: …`.
- `snipe-validate` a non-numeric id or bad price gets a usage or `Max price must be a positive number of gold` reply.
- `snipe-alert` `auction_snipe_check` DMs `🎯 **Snipe hit**` when the cheapest realm price beats a target (needs Blizzard data).

## How to get to it (user POV)

- Type the commands in a DM to the bot, in `#keys`, or in any channel the bot reads. There is no role gate.
- Alerts arrive by DM with no user action.

## Driving it with the harness

Preconditions:

- Baseline boot. No registration is needed.
- Offline by default: Blizzard item lookups fail with `network blocked`, and `!snipe` falls back to the typed label (or `Item <id>`).

- **Add a snipe.** A member DMs `!snipe 215147 5000 Vibrant Shard`. Run `hunter = h.user("Hunter"); await h.say(hunter, "!snipe 215147 5000 Vibrant Shard", "dm")` then `reply = await h.wait_for(hunter, "Sniping")`. The reply contains `Vibrant Shard` and `5,000`. `h.state_file("snipes.json")` contains `hunter.id` as a subscriber of item 215147.
- **List snipes.** Run `await h.say(hunter, "!snipes", "dm")` then `await h.wait_for(hunter, "Your snipes")`. The DM lists `Vibrant Shard` with `not seen yet` (offline).
- **Reject a bad price.** Run `await h.say(hunter, "!snipe 1 notgold", "dm")` then `await h.wait_for(hunter, "positive number of gold")`. Nothing is added.
- **Remove.** Run `await h.say(hunter, "!unsnipe 215147", "dm")` then `await h.wait_for(hunter, "Stopped sniping")`. `snipes.json` no longer mentions `hunter.id`; the record is deleted when its last subscriber leaves.
- **Pet.** Run `await h.say(hunter, "!snipepet 3390 2000 Some Pet", "dm")` then `await h.wait_for(hunter, "Sniping pet")`. `snipes.json` gains a `pet` record.
- **Alert sweep.** Run with `--network` and a real target above the market, then `await h.tick("auction_snipe_check")` and `await h.wait_for(hunter, "Snipe hit")`. Offline, the tick only logs a blocked request; say the alert path is unverified.
- **Proof.** Run `python $H run snipe` (offline: label fallback).

## Gotchas

- The reply goes to the channel the command came from. `!snipe` typed in `#keys` answers publicly there and the command is not deleted. `!snipes` always answers by DM.
- `!snipe` prices are parsed with `float`: `5000` and `5000.5` work, `5,000` is rejected. `!tokenalert` strips commas; `!snipe` does not.
- `!snipe ` and `!snipepet ` need a space and at least two arguments; with fewer they reply with usage. The `!snipe` handler explicitly skips `!snipepet`, so a pet id never lands in the item list.
- Re-subscribing changes the shared record's label for every subscriber, not only the caller's.
- `!unsnipe <id>` tries items first and then pets, so an id the caller tracks as both is removed only from items on the first call.
- Offline, `!snipe` with no label names the record `Item <id>`. The real item name and the recipe flag (banker alert) need `--network`.
