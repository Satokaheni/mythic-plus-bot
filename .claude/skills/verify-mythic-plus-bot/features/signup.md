# Signup, removal, and the fill queue

Every posted run carries `Sign Up`, `Remove`, and `Manage` buttons that keep working across bot restarts. A registered raider who clicks `Sign Up` takes an open seat for their role (multi-role raiders pick which role first); when the role is taken they go to the fill queue. An unregistered member is told to check their DMs, completes the class/role form, and is signed up on their main role. When the fifth seat fills, the embed turns `FULL` and every seated raider gets a "now filled" DM with the roster. `Remove` frees the seat, promotes the first queued raider who fits an open seat (and DMs them), and deletes the run when nobody is left.

## Sub-features

- `signup-single` a single-role registered raider is signed up on their role (ephemeral `You've been signed up for this run!`).
- `signup-multi` a multi-role raider picks from `Choose the role to sign up as...` and is seated in that role (`signed up as **<Role>**`).
- `signup-welcome` an unregistered click gets ephemeral `not registered yet`, a `Welcome!` DM form, and `Registration complete! You've been signed up for the run.`
- `signup-conflict` a raider already in the run, or in another run within 1 hour (2 hours if multi-key), gets the conflict reason ephemerally.
- `signup-full` the fifth seat sets Status `✅ **FULL** - Ready to go!`, clears the role pings, and DMs each seated raider `now **filled**` with the roster.
- `signup-fill` a signup for a taken role (or a full run) lands in the `📋 Fill Queue` field.
- `remove-self` `Remove` frees the seat (`You've been removed from this run.`); the last raider out deletes the run (`Schedule deleted (no remaining signups)`).
- `remove-promote` a removal promotes the first queued raider who fits an open seat and DMs them `You've been moved off the fill queue`.
- `displace` a main-role signup more than 8 hours before the run bumps an off-role seat holder and DMs them `You've been removed from a run`.
- `signup-restart` the buttons on an old run message keep working after a restart (views re-registered from `state.json`).

## How to get to it (user POV)

- Click `Sign Up` (custom_id `signup`) or `Remove` (custom_id `remove`) on a run embed in `#keys`.
- For a multi-role raider, pick a role in the ephemeral `Choose the role to sign up as...` select (option labels `Tank`, `Healer`, `Dps`).
- An unregistered member completes the `Welcome!` DM form (`Choose your World of Warcraft class`, `Choose your primary role`, `Choose your secondary role (optional)`, `Choose your US timezone`, `Submit`).
- Accept a DM run offer with ✅ (sent by DM outreach; not reachable offline, see Gotchas).

## Driving it with the harness

Preconditions:

- A posted run: `posted, _ = await create_key_request(h, tank, note=None, register={"klass": "Warrior", "primary": "tank"})`. The run is tomorrow, so it is more than 8 hours out and displacement is live.
- Read the current embed from the channel store after each click: `h.key._messages[posted.id]` (the `posted` object is the message as first sent).

- **Restart first.** Run `await h.restart()`. `str(posted.id)` is still in `h.state_file()["schedules"]`, and later clicks on `posted` are routed to the re-registered view.
- **Welcome signup.** A new member clicks `Sign Up`. Run `await signup_new_raider(h, flex, posted, klass="Druid", primary="dps", secondary="healer")`. An ephemeral `not registered yet` reaches only `flex`, then `Welcome!` and `Registration complete` DMs; `embed_field("DPS")` contains `flex.mention`.
- **Remove.** Run `await h.click(flex, posted, "Remove")` then `await h.wait_for("key", "removed from this run", predicate=lambda m: m.ephemeral_to == flex.id)`. The embed no longer lists Flex.
- **Multi-role pick.** Run `await h.click(flex, posted, "Sign Up")`, `pick = await h.wait_for("key", "multiple roles", predicate=lambda m: m.ephemeral_to == flex.id)`, `await h.choose(flex, pick, "Choose the role to sign up as", "Healer")`, `await h.wait_for("key", "signed up as **Healer**")`. `embed_field("Healer")` contains `flex.mention`.
- **Fill the run.** Sign up three DPS: `for d in dps: await signup_new_raider(h, d, posted, klass="Mage", primary="dps")`. `embed_field("Status")` contains `FULL`, `content` is empty, and each of the five raiders has exactly one DM containing `now **filled**` and the organizer's name. `h.state_file()["schedules"][str(posted.id)]["team"]` has the tank, healer, and three DPS ids.
- **Fill queue.** A sixth DPS signs up: `await signup_new_raider(h, late, posted, klass="Rogue", primary="dps")`. `embed_field("Fill Queue")` contains `late.mention`.
- **Promotion.** One seated DPS clicks `Remove`. Run `await h.click(dps[0], posted, "Remove")` then `await h.wait_for(late, "moved off the fill queue")`. `embed_field("DPS")` now contains `late.mention` and the Fill Queue field is gone.
- **Proof.** Run `python $H run signup-fill-restart` (covers everything above except promotion).

## Gotchas

- The `Dps` option label is `role.title()`, so choose by value `dps` or label `Dps`, never `DPS`.
- A Sign Up on a full run goes straight into the fill queue with no prompt. The "run is full, join as fill?" DM that CLAUDE.md describes exists only on the DM-offer ✅ path (`Scheduling.on_spot_offer_reaction` in `mythicbot/cogs/scheduling.py`), which needs `Scheduling.fill_remaining_spots`; that sleeps an hour before sending offers, so it never fires in a harness run.
- Displacement needs the run more than 8 hours away and an off-role seat holder. A run posted for later today may silently skip it.
- The welcome signup never asks a multi-role member which role: it takes the primary if that seat is open, else the secondary if open, else queues them. Only a later click shows the role picker.
- `Remove` by the last raider deletes the run message, so later clicks on that message fail with `This interaction failed`.
- Known roster bugs, found by `.claude/skills/verify-schedule-roster` and reachable from these buttons. Check them before blaming the harness when a fill-queue or removal sequence looks wrong:
  - A queued raider who fits the freed seat can be skipped: `_check_fill` promotes the first queued raider who fits any open seat. Example: DT (DPS/tank) picks Tank while Tank is taken and is queued even though DPS is open, HD picks Healer and is queued, the healer leaves. DT moves to DPS and HD stays queued with Healer open.
  - Two Sign Up handlers for the same raider (double-click, or Sign Up plus a DM-offer ✅) can both pass the guard before either signs up, which seats the raider twice.
  - A raider seated outside their registered roles (coordinator `Add Raider` or `Change Role`, which offer all three roles) cannot leave: `Remove` answers `You've been removed from this run.` but the seat keeps them. Reproduced in this harness: Pew (DPS-only) added as Healer, then `Remove`, and both the embed and `state.json` still show Pew as healer.
  - `Change Role` to a taken role puts the raider in the fill queue and leaves their old seat open.
