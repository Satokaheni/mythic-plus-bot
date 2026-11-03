# Key request (schedule a run)

Any member can schedule a Mythic+ run. Clicking the pinned `CLICK TO CREATE A REQUEST` button in `#keys` (or typing `!key` there) starts a DM flow: registration first if the member is new, then level, day, start time in the member's timezone, and one or multiple keys, an optional note, an optional role pick for multi-role organizers, and up to four pre-added raiders. `Post Schedule` posts the run embed in `#keys` with `Sign Up`, `Remove`, and `Manage` buttons and role pings for the open roles, and saves it to `state.json` keyed by the embed's message id.

## Sub-features

- `key-entry` the pinned button (ephemeral `Check your DMs`) or `!key` in `#keys` (command deleted) opens the DM flow.
- `key-register` an unregistered organizer gets the class/role/timezone form (`Before scheduling a key`) first.
- `key-form` the `Request a key` DM collects level, day (today plus six days), start time, and run type, and `Submit Key Request` validates them.
- `key-note` `Yes, Add Note` opens the `Add a Note` modal (`Note`, 200 chars); `No, Continue` skips it.
- `key-organizer-role` a multi-role organizer picks which role to fill (`Which role would you like to fill for this run?`).
- `key-prepost` `Add Raider` (up to 4, by display name) and `Post Schedule`; the DM is edited to `Schedule posted to the key channel!`.
- `key-post` the run embed in `#keys`: Key Level, Run Type, Status, Scheduled Time, Posted By, Tank, Healer, DPS, Note, with `🔔 Roles Needed:` pings.
- `key-conflict` the request is refused with `Cannot create schedule` when the organizer has another run within 1 hour (2 hours if either run is multi-key).

## How to get to it (user POV)

- Click `CLICK TO CREATE A REQUEST` (custom_id `create_key_request`) on the pinned message in `#keys`.
- Type `!key` in `#keys`.
- In the DM: selects `Choose your level` (`wow_level`), `Choose the day` (`wow_day`), `Choose start time (<Zone> time)` (`wow_start_time`), `Do you want to run one key or multiple?` (`key_run_type`), then buttons `Submit Key Request`, `Yes, Add Note` / `No, Continue`, `Add Raider`, `Post Schedule`.

## Driving it with the harness

Preconditions:

- Baseline boot; `#keys` has exactly one pinned message with components (the request button).
- Use a day after today so every time option is in the future.

- **Open the flow.** A new member clicks the pinned button. Run `pinned = [m for m in h.key.messages() if m.pinned and m.components][0]; await h.click(tank, pinned, "CLICK TO CREATE A REQUEST")`, then `await h.wait_for("key", "Check your DMs")`. The reply is ephemeral to that member (`ephemeral_to == tank.id`).
- **Register inside the flow.** Run `reg = await h.wait_for(tank, "Before scheduling a key")` and `await fill_class_form(h, tank, reg, klass="Warrior", primary="tank")`. The member is registered before the request form is sent.
- **Fill the request.** Run `form = await h.wait_for(tank, "Request a key")`, then `await h.choose(tank, form, "Choose your level", "10")`, `await h.choose(tank, form, "Choose the day", tomorrow_iso)`, `await h.choose(tank, form, "Choose start time", "8:00 PM")`, `await h.choose(tank, form, "Do you want to run one key or multiple?", "One Key")`, `await h.click(tank, form, "Submit Key Request")`. A DM asks `Would you like to add a note to the schedule?`.
- **Add a note.** Run `confirm = await h.wait_for(tank, "add a note"); await h.click(tank, confirm, "Yes, Add Note"); await h.submit_modal(tank, {"Note": "verify harness run"})`. The flow continues to the pre-post DM.
- **Post.** Run `prepost = await h.wait_for(tank, "Would you like to add any registered raiders"); await h.click(tank, prepost, "Post Schedule")`. `posted = await h.wait_for("key", "Scheduled Mythic+ Run")` shows `embed_field("Key Level") == "**10**"`, the organizer's mention in `Tank`, buttons `["Sign Up", "Remove", "Manage"]`, and `<@&…>` pings in `content`. The pre-post DM is edited to `Schedule posted to the key channel!` with no components.
- **Persisted.** Run `s = h.state_file()`. `s["schedules"][str(posted.id)]` exists with `date_scheduled` starting with the chosen day and `note == "verify harness run"`, and `posted.id` is in `s["raiders"][str(tank.id)]["current_runs"]`.
- **Shortcut.** `posted, day = await create_key_request(h, tank, note=..., register={...})` from `journeys` does all of the above.
- **Proof.** Run `python $H run key-request`.

## Gotchas

- `Choose your level` option values are lowercased labels: `climb10`, `10`, `11`, `12+`.
- Picking a time in the past (today, earlier hour) gets an ephemeral `That time has already passed` and leaves the time unset; `Submit Key Request` then answers `Please select all options before submitting.`
- The time select is in the organizer's registered timezone; the stored `start_time` is local to that zone. Compare instants, not wall-clock strings.
- **Known bug: the "join the existing run instead" prompt is unreachable.** `Scheduling._do_key_request_flow` (`mythicbot/cogs/scheduling.py`) looks for an existing run at the same day and time with `sched.date_scheduled == view.selected_day`, comparing a `datetime` to the `YYYY-MM-DD` string, which is never equal. A scratch scenario (Tanky posts tomorrow 8:00 PM; Healy, a healer, requests the same day and time) went straight to `Would you like to add any registered raiders` and never showed `A run already exists at this time`. Two runs at the same slot get posted.
- If the organizer does not click `Post Schedule` within 300 seconds, nothing is posted, but the pre-added raiders were already signed up on the unposted schedule; the bot discards it from their `current_runs` on timeout.
- A multi-role organizer who lets the role prompt time out cancels the whole request (`No role selected. Key request cancelled.`).
- `!key` outside `#keys` does nothing. `!avail` typed in `#keys` is deleted silently.
