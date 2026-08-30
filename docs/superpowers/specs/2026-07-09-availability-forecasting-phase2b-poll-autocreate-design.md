# Availability Forecasting — Phase 2b: Multi-Slot Poll + Auto-Create

**Date:** 2026-07-09
**Status:** Design captured — **not to be built yet** (deferred until the 2a dry-run has been validated for a couple of weeks; this phase DMs real people and creates real runs).
**Branch:** `feature/availability-forecasting`

## Context

Phase 2a predicts the best weekly run(s) and DMs a **dry-run** preview to the
banker. Phase 2b is the **live** evolution: instead of previewing one pick, the
bot **polls the predicted rosters across several candidate times**, then
**auto-creates a run for every time that enough people confirm** — a
"find the common time(s)" flow that can produce multiple runs in a week.

Builds directly on 2a's `forecast.rank_slots` (which already yields ranked
`(slot, role-valid Team)` pairs with mains preferred). When 2b goes live it
**replaces** the 2a dry-run preview; during validation the dry-run keeps running.

## The Flow

1. **Wednesday noon CST** (same trigger as the 2a preview): run the feasibility
   gate (green pool must field a role-valid team, else no-op) and take the
   **top 3** ranked candidate slots, each with its predicted 5-person roster.
2. **Poll the rosters.** The recipient set is the **union of the 3 predicted
   rosters** (~5–12 people). Each recipient gets **one DM** with a button poll
   listing all **3 candidate times** (day/time + that slot's roster preview);
   each time has its own **✅ / ❌ buttons** ("can make it" / "can't"). Clicking a
   button records/updates that person's answer for that time. (Buttons, not
   reactions — one clearly-labeled message, less error-prone, and reactions
   can't be tied to a specific slot within a single message.)
3. **Response window:** collect answers for **~24 hours**.
4. **Thursday noon CST — tally & auto-create.** For each candidate slot, count
   ✅ among **that slot's predicted 5-person roster**. If **≥4/5** confirmed →
   **create the run**:
   - Post it via the existing `Schedule` flow, with the confirmed members
     assigned to their predicted roles.
   - If exactly 4/5, the 5th role is left open and filled by the bot's
     **existing spot-filling / DM-outreach flow** (like any run that's one
     short).
   - **Every** qualifying slot becomes a run. A person who confirmed multiple
     qualifying slots is placed in **each** (M+ groups routinely run several
     keys back-to-back) — the normal inter-run **time-gap check is bypassed**
     for these poll-created runs, since the person explicitly said yes to each.
5. Fully automated — no banker approval step.

## Components (to build in 2b)

- **Poll `discord.ui.View`** — one row per candidate slot, each with ✅/❌
  buttons; button callbacks upsert `{user_id → answer}` for that slot and give
  an ephemeral ack. Must be **persistent** (re-registered on startup) so the
  poll survives a bot restart within its 24h window.
- **Poll state** (persisted in `state.json` or a sibling store): per active poll,
  the 3 slots each with `{predicted_roster: [user_ids], role_assignment,
  start_time, responses: {user_id: bool}}`, plus the poll's post time. Cleared
  after tally.
- **Two tasks:** the Wednesday-noon **poll task** (replaces 2a's
  `forecast_preview` when live) and a **tally task** ~24h later (Thursday noon
  CST) that evaluates thresholds and creates runs. (Alternatively a single task
  that schedules its own tally — finalize at build.)
- **Run creation helper:** given a slot's confirmed roster + role assignment,
  build and post a `Schedule` (reusing `send_message`/channel post + register in
  `self.schedules`), seed the confirmed members in their roles, and — if 4/5 —
  trigger the existing fill flow for the open role. Bypass the gap check for
  cross-run overlap.

## Configuration (to finalize at build)

- `POLL_SLOTS = 3` (candidate times offered).
- `POLL_THRESHOLD = 4` of 5 (per-slot confirmations to create a run).
- `POLL_WINDOW_HOURS = 24` (poll → tally gap).
- **Key level / run type** for auto-created runs: the predictor doesn't know the
  key level, so auto-created runs need a default — a coordinator/banker-set
  **default key level** (env or command) and default `run_type` (likely
  `"one"`). Finalize the source at build.

## Recipients & threshold — precise semantics

- **Recipients:** union of the top-3 slots' predicted rosters.
- Each recipient sees and can vote ✅/❌ on **all 3** candidate times (the "three
  options").
- A slot's run is created iff **≥4 of that slot's own predicted 5** voted ✅.
  Votes from people *not* in a given slot's predicted roster are recorded but do
  **not** count toward its threshold, and are **not** used to fill it (5th spot
  goes to normal outreach, per decision). They're kept for possible future use
  (e.g., smarter backfill, or informing next week's prediction).

## Edge Cases & Open Questions (resolve at build)

- **Bot restart mid-window:** persist poll state and re-register the poll View on
  startup so votes aren't lost and buttons keep working.
- **No slot clears 4/5:** create nothing; optionally DM the banker a summary
  ("no time cleared this week — best was Mon 10 PM at 3/5").
- **A voter unregisters / leaves** between poll and tally: drop them from counts.
- **Overlapping auto-created runs and the existing gap logic:** the poll-created
  path must set up rosters *without* invoking `check_availability`'s gap
  rejection (or pass a flag), since overlap is intentional here.
- **Relationship to 2a:** going live means the Wednesday task switches from
  "DM dry-run preview to banker" to "post the poll." Keep a config flag so the
  team can toggle back to dry-run if needed.
- **Predictor tuning (separate, tracked):** feed 🟡/🔴 availability reactions as
  weak negatives; reconsider counting `team["fill"]`. These improve *which*
  slots/rosters the poll offers but are independent of the poll mechanics.

## Testing (at build)

- Pure tally logic: given a poll's slots + responses, returns the set of slots
  that cleared `POLL_THRESHOLD` and each one's confirmed roster (unit-testable).
- Poll-state serialization round-trip.
- Run-creation helper builds a role-valid `Schedule` from a confirmed roster and
  opens the correct 5th role when 4/5 (mock Discord).
- View/task wiring verified manually (no unit tests for bot.py, per convention).

## Not in scope

Building any of the above now. This document is the agreed design to implement
when Phase 2b is flipped live after the dry-run validation period.
