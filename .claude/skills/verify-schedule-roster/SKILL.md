---
name: verify-schedule-roster
description: Exhaustively check the Mythic+ run roster invariants (one seat per raider, `missing`/`signup`/`full`/`members`/`current_runs` agreeing with the seats, fill-queue promotion) on the real `Schedule` and `Raider` classes under every order of Sign Up, Remove, DM-offer, and coordinator Add/Remove/Change Role steps, including handlers interleaved at their awaits. Use when a change touches mythicbot/scheduling/schedule.py, mythicbot/scheduling/raider.py, or any signup/remove/manage handler in mythicbot/scheduling/views.py or mythicbot/cogs/scheduling.py, or when a run's roster looks wrong.
---

# Verify the schedule roster

A test checks the sequences someone wrote down. This check runs **every** sequence of roster steps, within the bounds below, on the real `schedule.Schedule` and `raider.Raider` objects, and prints the shortest sequence that breaks each property. It does not use a hand model of `Schedule`: each step calls the same methods, in the same order, as one stretch of a real handler between two `await`s, so a step that the bot runs without yielding is one step here, and a handler that awaits (role picker, `defer`, a DM) is split there so other handlers can run in between.

## Run it

From the repo root (Python 3.10, discord.py 2.3.1; the script warns if either differs; stdlib only otherwise, nothing to install):

```
PYTHONIOENCODING=utf-8 python .claude/skills/verify-schedule-roster/check_roster.py
```

Takes about a minute. Exit 0 prints `PASS: matches the recorded findings exactly`. Exit 1 prints `FAIL` with a reason. For a fast loop on one configuration, add `--only player:seq:far` (scope `player|all`, interleave `seq|race`, start `far|near`); `--only` exits 1 whenever anything is violated, known or not.

The script never touches Discord, `.env`, or the data files. It imports `mythicbot/scheduling/schedule.py` and `mythicbot/scheduling/raider.py` and nothing from `bot.py` or `mythicbot/cogs/`.

## Properties

Each property, then the harm when it breaks.

- **P1-one-place.** No raider holds more than one place in a run (tank, healer, a DPS seat, or the fill queue). Harm: a raider counted twice makes the run look staffed when a seat is really empty, and the group finds out at key time.
- **P2-missing-matches.** `missing` lists exactly the roles whose seats are open, once each. Harm: the NEEDS line, role pings, and DM outreach read `missing`, so a wrong entry pings for a filled role or stops recruiting for an open one.
- **P3-count-matches.** `signup` equals the number of filled seats, and `full` is true exactly when all five are filled. Harm: `full` drives the FULL status, the "now filled" DMs, and outreach; a stale value announces an unready run or never announces a ready one.
- **P4-members-match.** `members` lists each seat holder and fill raider exactly once, and no one else. Harm: Remove Raider, Change Role, delete notices, and reminders use `members`, so a seated raider missing from it holds a seat nobody can clear.
- **P5-runs-match.** A raider's `current_runs` contains the run exactly when the raider is in `members`. Harm: the Sign Up and Remove buttons gate on `current_runs`; a mismatch lets a seated raider sign up again, or tells them they are not signed up when they try to leave.
- **P6-promotion.** When a removal frees a seat, no raider left in the fill queue could have taken that seat. Harm: a queued raider who plays the freed role keeps waiting while the embed pings for that role, and is never told a spot opened.

P1-P5 are checked in every state, including the moment a handler is suspended at an await. P6 is checked after every step that calls `raider_remove`.

P6 counts only the seats the step freed: tank or healer held before and open after, and a DPS seat whenever the DPS count dropped. It does not ask whether a queued raider fits some other seat that was already open. **Owner decision, 2026-11-03:** a removal promotes only into the seat it freed (`Schedule._check_fill(freed_role)`), so "any open seat" is not the intended behavior. The property text is unchanged. On 2026-11-03 the implementation (`check_promotion`) was widened to match it: it used to count a freed DPS seat only when DPS had been full (3/3), so a DPS leaving a 2/3 group was never checked.

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
- `Schedule.to_dict` / `from_dict` and `storage.load_state` (restart round-trips, including pass 4's ghost-seat repair, which `tests/test_storage.py::test_deserialize_repairs_ghost_seat` covers), `send_message` / embed text, `format_dm_roster`, DM delivery, `notify_schedule`, `_notify_promoted`, `BotState.save`.
- `hourly_check`, `Scheduling.fill_remaining_spots` selection, Modify Run, Delete Run, `!cleanup`.
- Pools larger than five raiders. A bug that needs six distinct raiders at once is outside the bound.
- With `KNOWN` empty, no property is masked: every property is sensitive in every configuration. If a finding is ever added to `KNOWN` again, a new bug that breaks the same property in the same scope and interleave is hidden behind it.

## Findings at the sync commit

None. `KNOWN` in the script is empty, so any violation is new. F1-F4 (found at `0c75335`: promotion into the wrong seat, double seating from concurrent signups, a seat that could not be cleared when held outside the raider's registered roles, and Change Role into a taken role) were fixed on 2026-11-03 (CHANGELOG 1.10.1, `fix(roster)` commits).

## Coverage record

Sync commit: `e639c3deaab6ca288fd820eb0f4bb69bad83f546` (branch `refactor/modularize-bot`, final-review fixes on v1.11.0). Previously synced at `e7c30e9930a99837ae8704d8a894345162791b5d` (roster fixes F1-F4, v1.10.1), at `0c75335f41a1859f2db35e304c9fe4d86c6f1abc` (bot split into cogs) and before that at `3febdaca6f014b7464b47f2612fc3af83c36f3d1` (`feature/loot-council-helper`, pre-refactor `bot.py`). Confirmed at this commit, by reading each handler, that the steps below match the code, and the run's verdict is `PASS: every property holds` with `KNOWN` empty. Since `e7c30e9`, the only roster-handler changes are the `not has_raider` conjunct in the welcome branch's guard (`3d3d73e`) and in the DM-offer ✅ guard (`e639c3d`), both now in `auto_guard`; `mythicbot/storage.py` pass 4 gained the ghost-seat repair, which this check does not exercise (see Not covered). Between `0c75335` and `e7c30e9`, `mythicbot/scheduling/schedule.py` changed (`raider_signup` returns a bool and refuses existing members, `_check_fill(freed_role)`, `raider_remove` via `seat_of`, new `open_roles`) and the mirrored handlers gained the result checks and Change Role re-checks noted below; `mythicbot/scheduling/raider.py` did not change.

Called for real (imported):
- `mythicbot/scheduling/schedule.py`: `Schedule.__init__`, `raider_signup` (and its `False` return for a raider already in the run), `_check_fill(freed_role)`, `raider_remove`, `seat_of`, `open_roles`, `try_displace_off_roler`, `has_raider`, `is_filled`, `__eq__`/`__hash__` (through `current_runs` sets).
- `mythicbot/scheduling/raider.py`: `Raider.__init__`, `add_run`, `remove_run`, `check_availability`, `__eq__`/`__hash__`.

Mirrored (call order, guards, and await points copied into the step functions of `check_roster.py`; find handlers by name):
- `mythicbot/scheduling/views.py` `ScheduleButtonView.signup_button`: new-raider welcome branch and registered branch → `auto_guard`/`auto_finish`, `signup_guard`/`signup_apply`/`signup_finish`. The welcome branch's guard is `check_availability`, run not in `current_runs`, and `not has_raider` (added in `3d3d73e`), mirrored in `auto_guard`. The `await` points are `bot._notify_displaced`, the role picker (`RoleSelectView.wait`) or `defer`. After the picker/defer the registered branch re-checks `has_raider` (answers "already signed up" and stops) before displacement; both branches skip `add_run` and everything after it when `raider_signup` returns `False`.
- `mythicbot/scheduling/views.py` `ScheduleButtonView.remove_button` → `remove_click`. It now keeps the promoted raider from `raider_remove` and calls `bot._notify_promoted` after the embed edit; that does not change roster state.
- `mythicbot/scheduling/views.py` `_apply_manage_add` and `_apply_manual_add` → `staff_add`. The `raider_signup` return value is the guard (`False` → "already signed up").
- `mythicbot/scheduling/views.py` `RemoveRaiderSelect.callback` → `staff_remove`.
- `mythicbot/scheduling/views.py` `ChangeRaiderRoleSelect.callback` and `ChangeRoleTargetSelect.callback` → `change_role_pick`/`change_role_apply`, with `_change_role_targets` → `change_targets` (open roles minus the raider's own seat). The pick refuses ("no open roles") when there is no target. At click time the callback re-checks that the raider is still in the run and that the role is still a target, and refuses without changing anything otherwise. `resumes` offers all three roles, a superset of the menu built at pick time, and the re-check turns the rest away.
- `mythicbot/cogs/scheduling.py` `Scheduling.on_spot_offer_reaction`, ✅ branch (DM offer; was `bot.py` `on_reaction_add`) → `auto_guard`/`auto_finish`. Its guard gained the same `not has_raider` conjunct as the welcome branch (final-review fix), so both share `auto_guard` unchanged in shape. Within the bound that conjunct never changes a verdict: P5 holds in every expanded state, so `run in current_runs` and `has_raider` agree there. It is mirrored for fidelity; the case it exists for (a member whose `current_runs` lacks the run, as pre-F3 state could leave) is covered by `tests/test_roster_handlers.py::test_dm_offer_accept_by_existing_member_displaces_nobody`. The fill-offer DM is the await; `raider_signup` runs in the ✅ reply or in the else branch, and on `False` the raider is DMed "already signed up" and nothing else happens.
- Not mirrored, as it is outside the one-run bound: the `_do_key_request_flow` join-existing branch. It checks `check_availability` after its await and treats a `False` from `raider_signup` the same way.
- `mythicbot/cogs/scheduling.py` `Scheduling._do_key_request_flow`, new-run branch (`Schedule(...)`, then `add_run` after `PrePostAddRaiderView` returns a `message_id`) → `post_run`.

## Does my change touch this?

Run `git diff e639c3deaab6ca288fd820eb0f4bb69bad83f546 -- mythicbot/scheduling/schedule.py mythicbot/scheduling/raider.py mythicbot/scheduling/views.py bot.py mythicbot/cogs/ mythicbot/state.py` and look for any function in the coverage record, or a new caller of `raider_signup`, `raider_remove`, `try_displace_off_roler`, `add_run`, or `remove_run`. The mirrored handlers are named by function (in `mythicbot/scheduling/views.py` and `mythicbot/cogs/scheduling.py`); find them by name, not line number.

- Changed only `mythicbot/scheduling/schedule.py` / `mythicbot/scheduling/raider.py` methods: rerun; the check calls them directly.
- Changed a mirrored handler (new guard, moved `await`, new role options, new path into `raider_signup`): update the matching step function first, so it still calls the same methods in the same order between the same awaits, then rerun.

## Reading the result

- `PASS: matches the recorded findings exactly` — every property holds except the known findings, in every configuration.
- `NEW violation: scope=… interleave=… P…` — a property broke where it held before. Read the numbered steps under that property (shortest sequence, in the code's words) and replay them against the code. If the replay matches the bot's real behavior, it is a bug: report it with those steps; do not add it to `KNOWN` unless the user agrees it is a finding to track. If a step does something no real handler can do (wrong guard, an await that does not exist, a role the UI does not offer), it is a modeling error: fix that step and cite the handler line.
- `known finding F… no longer reproduces` — the code changed that behavior. If it was a deliberate fix, delete those `KNOWN` entries and the finding from this file. If not, a step function probably stopped matching the code.
- `search not exhausted` — raise `--depth`; the result is not complete.
- A traceback on import means `mythicbot/scheduling/schedule.py`/`mythicbot/scheduling/raider.py` no longer import (check that first), or their constructors changed shape (`make_raider`, `post_run`).

To show the check still has teeth after editing it, break the code on scratch and confirm the verdict fails, then restore. Examples:
- Delete the `has_raider` duplicate guard at the top of `raider_signup`: expect `NEW violation` for P1-one-place and P4-members-match in `player`/`race` and `all`/`race`. The shortest trace is a DM offer that bumps an off-roler and then seats the same raider twice. Run and caught at the sync commit (2026-11-03).
- Delete `self.team["fill"].remove(raider)` in `_check_fill`: expect `NEW violation: scope=player interleave=seq P1-one-place`.
- Delete `self.missing.remove("tank")` in `raider_signup`: expect `P2-missing-matches` in every configuration.
- Remove `raider.remove_run(self)` from `raider_remove` (trips P5), or replace `self.full = True` with `pass` (trips P3).

## Updating

Move the sync commit forward only after you have confirmed, at the new commit, that every mirrored step still matches its handler and that the run's verdict is what you expect. Update the coverage record line hints when you do. Keep property statements fixed; if a property is wrong about intended behavior, change it only with the user's agreement and say so in the hand-off.
