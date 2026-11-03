---
name: verify-schedule-roster
description: Exhaustively check the Mythic+ run roster invariants (one seat per raider, `missing`/`signup`/`full`/`members`/`current_runs` agreeing with the seats, fill-queue promotion) on the real `Schedule` and `Raider` classes under every order of Sign Up, Remove, DM-offer, and coordinator Add/Remove/Change Role steps, including handlers interleaved at their awaits. Use when a change touches schedule.py, raider.py, or any signup/remove/manage handler in views.py or cogs/scheduling.py, or when a run's roster looks wrong.
---

# Verify the schedule roster

A test checks the sequences someone wrote down. This check runs **every** sequence of roster steps, within the bounds below, on the real `schedule.Schedule` and `raider.Raider` objects, and prints the shortest sequence that breaks each property. It does not use a hand model of `Schedule`: each step calls the same methods, in the same order, as one stretch of a real handler between two `await`s, so a step that the bot runs without yielding is one step here, and a handler that awaits (role picker, `defer`, a DM) is split there so other handlers can run in between.

## Run it

From the repo root (Python 3.10, discord.py 2.3.1; the script warns if either differs; stdlib only otherwise, nothing to install):

```
PYTHONIOENCODING=utf-8 A:/anaconda3/python.exe .claude/skills/verify-schedule-roster/check_roster.py
```

Takes about a minute. Exit 0 prints `PASS: matches the recorded findings exactly`. Exit 1 prints `FAIL` with a reason. For a fast loop on one configuration, add `--only player:seq:far` (scope `player|all`, interleave `seq|race`, start `far|near`); `--only` exits 1 whenever anything is violated, known or not.

The script never touches Discord, `.env`, or the data files. It imports `schedule.py` and `raider.py` and nothing from `bot.py` or `cogs/`.

## Properties

Each property, then the harm when it breaks.

- **P1-one-place.** No raider holds more than one place in a run (tank, healer, a DPS seat, or the fill queue). Harm: a raider counted twice makes the run look staffed when a seat is really empty, and the group finds out at key time.
- **P2-missing-matches.** `missing` lists exactly the roles whose seats are open, once each. Harm: the NEEDS line, role pings, and DM outreach read `missing`, so a wrong entry pings for a filled role or stops recruiting for an open one.
- **P3-count-matches.** `signup` equals the number of filled seats, and `full` is true exactly when all five are filled. Harm: `full` drives the FULL status, the "now filled" DMs, and outreach; a stale value announces an unready run or never announces a ready one.
- **P4-members-match.** `members` lists each seat holder and fill raider exactly once, and no one else. Harm: Remove Raider, Change Role, delete notices, and reminders use `members`, so a seated raider missing from it holds a seat nobody can clear.
- **P5-runs-match.** A raider's `current_runs` contains the run exactly when the raider is in `members`. Harm: the Sign Up and Remove buttons gate on `current_runs`; a mismatch lets a seated raider sign up again, or tells them they are not signed up when they try to leave.
- **P6-promotion.** When a removal frees a seat, no raider left in the fill queue could have taken that seat. Harm: a queued raider who plays the freed role keeps waiting while the embed pings for that role, and is never told a spot opened.

P1-P5 are checked in every state, including the moment a handler is suspended at an await. P6 is checked after every step that calls `raider_remove`.

## What is explored (bounds and assumptions)

- **Raiders.** Default pool `T,H,D1,DT,HD` from `CATALOG` in the script: tank-only, healer-only, DPS-only, DPS-main with tank off-role, healer-main with DPS off-role. One run, level 10, one-key. Any pool member may post it, with each role they could pick.
- **Steps (scope `player`).** Sign Up click by a registered raider (guard, await, then displace and sign up, with a second await when someone is displaced); DM-offer ✅ or new-raider welcome signup (guard and displace, await when displaced or full, then sign up); Remove click (the last raider out deletes the run, which ends that path).
- **Steps (scope `all`).** The `player` steps plus coordinator/organizer Add Raider with any of the three roles, coordinator Remove Raider, and coordinator Change Role (pick the raider, await, pick a role, then remove and re-sign).
- **Interleaving.** `seq` finishes each handler before the next user action, as a unit test would. `race` lets up to 2 handlers (`player`) or 1 handler (`all`) sit suspended at an await while other steps run.
- **Time.** `far` puts the run 3 days out, so off-role displacement is live; `near` puts it 4 hours out, so it is not. Nothing else reads the clock.
- **Completeness.** All 8 configurations reach every state reachable without first breaking P1-P5: the search stops expanding a state once it breaks one of those (it is already a counterexample) and stops when no new state appears. `--depth` (default 60) is a safety bound; if it is ever hit, the verdict says `search not exhausted` and fails.
- **Assumptions.** Any registered non-member may accept a DM offer at any time (the real offer rules in `Scheduling.fill_remaining_spots` are not modeled, so this over-approximates). The welcome path's single registration is not modeled. Pending handlers on a run that was just deleted are dropped. State identity ignores the order of `members` and `missing` (only read with `in`/`remove`/`append`) and ignores `denied_runs` (written but not read by any step).

## Not covered

- More than one run at a time, so `Raider.check_availability`'s 1-hour and 2-hour gap rules (only its "already in this run" branch runs here), `Schedule.__hash__` collisions between runs, and `!modify` (which clears all runs).
- `Schedule.to_dict` / `from_dict` and `utils.load_state` (restart round-trips), `send_message` / embed text, `format_dm_roster`, DM delivery, `notify_schedule`, `_notify_promoted`, `BotState.save`.
- `hourly_check`, `Scheduling.fill_remaining_spots` selection, Modify Run, Delete Run, `!cleanup`.
- Pools larger than five raiders. A bug that needs six distinct raiders at once is outside the bound.
- A new bug that breaks a property already listed as a known finding in the same scope and interleave is masked by it. `player`/`seq` has P1-P5 clean, and P2, P3, P5 are clean everywhere, so those stay sensitive.

## Findings at the sync commit

These are real violations in the current code, recorded in `KNOWN` in the script so the check can detect new ones. Do not "fix" them by editing the check.

- **F1 (P6, scope `player`). A queued raider who fits the freed seat is skipped.** `Schedule._check_fill` promotes the first queued raider who fits *any* open seat and stops. A multi-role raider who picked a taken role is queued even while their other role is open, so they are ahead in line. Shortest trace: T posts as tank; H signs up as healer; DT clicks Sign Up and picks tank, which is taken, so DT goes to the fill queue although three DPS seats are open; HD clicks Sign Up and picks healer, also queued; H clicks Remove. `_check_fill` puts DT into DPS and returns; HD, a healer, stays queued with the healer seat open.
- **F2 (P1/P4, `race`). A raider can be seated twice by concurrent signups.** `ScheduleButtonView.signup_button` checks `check_availability` / `current_runs` and then awaits (`interaction.response.defer` or the role picker) before `raider_signup` and `add_run`. A second Sign Up click (a double-click), a DM-offer ✅, or a coordinator Add Raider for the same raider passes the same guard in that window. Shortest trace: T posts; H clicks Sign Up (guard passes, handler awaits); H accepts a DM offer, `raider_signup(H)` seats H as healer; H's Sign Up resumes and calls `raider_signup(H, None)`, which queues H as fill. H is now healer and fill, and in `members` twice. A double-click gives the same result one step later.
- **F3 (P1/P4, scope `all`). A raider seated outside their registered roles can never leave the seat.** `raider_remove` looks for the raider only in the slots named by `raider.roles`, but `ManageRoleSelect`, `ManualAddRoleSelect`, and `ChangeRoleTargetSelect` offer all three roles for anyone. Shortest trace: T (tank-only) posts; the coordinator uses Change Role to move T to healer; T clicks Remove. T leaves `members` and `current_runs`, but the healer seat still holds T. Confirmed end to end through the offline harness (`verify-mythic-plus-bot`): coordinator Manage, Add Raider "Pew" (DPS-only) as Healer, Pew clicks Remove and is told `You've been removed from this run.`, yet the embed and `state.json` still show Pew as healer with `members` lacking Pew. Variant: Change Role T to healer, then Change Role T to tank, and T holds both seats.
- **F4 (P6, scope `all`). Change Role into a taken role strands the raider in the queue with their old seat open.** `ChangeRoleTargetSelect.callback` calls `raider_remove` (which frees the seat and promotes nobody) and then `raider_signup(raider, role=new_role)`, which queues them when that role is taken. Shortest trace: T posts as tank; H signs up as healer; the coordinator moves T to healer. Tank is now open and T, a tank, waits in the fill queue.

## Coverage record

Sync commit: `0c75335f41a1859f2db35e304c9fe4d86c6f1abc` (branch `refactor/modularize-bot`, bot split into cogs, v1.10.1). Previously synced at `3febdaca6f014b7464b47f2612fc3af83c36f3d1` (`feature/loot-council-helper`, pre-refactor `bot.py`). Confirmed at this commit, by reading each handler, that the steps below match the code. The refactor moved the `bot.py` handlers into cogs and renamed `bot.<x>` state to `bot.state.<x>`; the call order and await points of every mirrored step are unchanged. `schedule.py` and `raider.py` did not change between the two commits.

Called for real (imported):
- `schedule.py`: `Schedule.__init__`, `raider_signup`, `_check_fill`, `raider_remove`, `try_displace_off_roler`, `has_raider`, `is_filled`, `__eq__`/`__hash__` (through `current_runs` sets).
- `raider.py`: `Raider.__init__`, `add_run`, `remove_run`, `check_availability`, `__eq__`/`__hash__`.

Mirrored (call order, guards, and await points copied into the step functions of `check_roster.py`; find handlers by name):
- `views.py` `ScheduleButtonView.signup_button`: new-raider welcome branch and registered branch → `auto_guard`/`auto_finish`, `signup_guard`/`signup_apply`/`signup_finish`. The `await` points are `bot._notify_displaced`, the role picker (`RoleSelectView.wait`) or `defer`.
- `views.py` `ScheduleButtonView.remove_button` → `remove_click`. It now keeps the promoted raider from `raider_remove` and calls `bot._notify_promoted` after the embed edit; that does not change roster state.
- `views.py` `_apply_manage_add` and `_apply_manual_add` → `staff_add`.
- `views.py` `RemoveRaiderSelect.callback` → `staff_remove`.
- `views.py` `ChangeRaiderRoleSelect.callback` and `ChangeRoleTargetSelect.callback` → `change_role_pick`/`change_role_apply`.
- `cogs/scheduling.py` `Scheduling.on_spot_offer_reaction`, ✅ branch (DM offer; was `bot.py` `on_reaction_add`) → `auto_guard`/`auto_finish`. The fill-offer DM is the await; `raider_signup` runs in the ✅ reply or in the else branch.
- `cogs/scheduling.py` `Scheduling._do_key_request_flow`, new-run branch (`Schedule(...)`, then `add_run` after `PrePostAddRaiderView` returns a `message_id`) → `post_run`.

## Does my change touch this?

Run `git diff 0c75335f41a1859f2db35e304c9fe4d86c6f1abc -- schedule.py raider.py views.py bot.py cogs/ state.py` and look for any function in the coverage record, or a new caller of `raider_signup`, `raider_remove`, `try_displace_off_roler`, `add_run`, or `remove_run`. The mirrored handlers are named by function (in `views.py` and `cogs/scheduling.py`); find them by name, not line number.

- Changed only `schedule.py` / `raider.py` methods: rerun; the check calls them directly.
- Changed a mirrored handler (new guard, moved `await`, new role options, new path into `raider_signup`): update the matching step function first, so it still calls the same methods in the same order between the same awaits, then rerun.

## Reading the result

- `PASS: matches the recorded findings exactly` — every property holds except the known findings, in every configuration.
- `NEW violation: scope=… interleave=… P…` — a property broke where it held before. Read the numbered steps under that property (shortest sequence, in the code's words) and replay them against the code. If the replay matches the bot's real behavior, it is a bug: report it with those steps; do not add it to `KNOWN` unless the user agrees it is a finding to track. If a step does something no real handler can do (wrong guard, an await that does not exist, a role the UI does not offer), it is a modeling error: fix that step and cite the handler line.
- `known finding F… no longer reproduces` — the code changed that behavior. If it was a deliberate fix, delete those `KNOWN` entries and the finding from this file. If not, a step function probably stopped matching the code.
- `search not exhausted` — raise `--depth`; the result is not complete.
- A traceback on import means `schedule.py`/`raider.py` no longer import (check that first), or their constructors changed shape (`make_raider`, `post_run`).

To show the check still has teeth after editing it, break the code on scratch and confirm the verdict fails, then restore: for example delete `self.team["fill"].remove(raider)` in `_check_fill` (expect `NEW violation: scope=player interleave=seq P1-one-place`) or `self.missing.remove("tank")` in `raider_signup` (expect `P2-missing-matches` in every configuration). Both were run at the sync commit and caught; `raider.remove_run(self)` removed from `raider_remove` trips P5, and `self.full = True` replaced by `pass` trips P3.

## Updating

Move the sync commit forward only after you have confirmed, at the new commit, that every mirrored step still matches its handler and that the run's verdict is what you expect. Update the coverage record line hints when you do. Keep property statements fixed; if a property is wrong about intended behavior, change it only with the user's agreement and say so in the hand-off.
