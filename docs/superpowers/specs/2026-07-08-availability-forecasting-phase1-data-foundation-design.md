# Availability Forecasting — Phase 1a: Data Foundation (Event Log + Retention)

**Date:** 2026-07-08
**Status:** Approved design → ready for implementation plan
**Branch:** `feature/availability-forecasting`

## Context & the bigger picture

The long-term goal is a **weekly auto-scheduler**: predict when each raider is
available (day-of-week × 2-hour block, in their local time), then automatically
assemble the single best role-valid 5-person run from the week's 🟢 pool, DM
those candidates to confirm, and create the run when ≥4/5 say yes (falling back
to the next-best slot otherwise).

That forecaster is **un-buildable today** because the bot keeps almost none of
the data it needs: availability reactions are a whole-week flag wiped every
Tuesday, and passed runs are deleted outright every hour. **You cannot forecast
from data you do not keep.**

So the project is phased:

- **Phase 1a (THIS spec):** durably log the in-guild signals and stop discarding
  run history. Build the dataset.
- **Phase 1b (deferred, next increment):** Raider.io backfill + weekly harvest
  to seed history from each raider's real M+ runs (needs a maintained
  `character_links.json`, being built after the next raid).
- **Phase 2 (deferred, after data exists):** the forecaster + auto-scheduler +
  DM-confirm flow, specced separately once real data is available.

This spec covers **only Phase 1a**. It is deliberately designed so Phase 1b's
backfill appends into the *same* event store with no rework.

## Goals

- Persist every availability/attendance signal the forecaster will need, as an
  append-only event log.
- Stamp each attendance event with its **local (weekday, 2-hour block)** so
  Phase 2 (and the Raider.io backfill) don't recompute it.
- Stop deleting run history — capture a run's timing + roster before its Discord
  message is removed.
- Change nothing about how users interact with the bot.

## Non-Goals (explicitly deferred)

- No forecasting, prediction, scoring, or auto-scheduling (Phase 2).
- No Raider.io client, `character_links.json`, backfill, or harvest (Phase 1b).
- No `!datastats` or any new user command (Phase 1b+).
- No changes to registration, the availability post, or the schedule UI.

## The Event Store

New module **`eventlog.py`** owning an append-only log file `events.jsonl`
(one JSON object per line — appends are cheap and don't rewrite the file, unlike
`state.json`). Runtime data → **gitignored** (like `state.json`/`watches.json`).

### Record shape

Every event is a flat JSON object:

```json
{
  "type": "run_completed",
  "ts_utc": "2026-07-08T02:07:47+00:00",
  "user_id": 123456789,
  "source": "discord",
  "run_id": 1399…,
  "local_weekday": 1,
  "local_block": 10,
  "tz": "America/Chicago"
}
```

- `type` — event type (below).
- `ts_utc` — ISO-8601 UTC timestamp the signal refers to (for attendance, the
  run's start time; for reactions, the reaction time).
- `user_id` — the raider the event is about (nullable for run-level events that
  fan out to a roster; see `run_completed`).
- `source` — `"discord"` now; `"raiderio"` later (Phase 1b). Present now for
  forward-compatibility.
- `run_id` — dedup/join key: the Discord message id for guild runs (Phase 1b
  will use Raider.io's `keystone_run_id`). Nullable.
- `local_weekday` (0=Mon…6=Sun), `local_block` (0–11, = local hour // 2), `tz` —
  the derived local slot from the raider's stored timezone. Null when the
  raider has no timezone yet (Phase 2 can backfill the derivation from `ts_utc`
  + `tz` later).
- Extra type-specific fields as noted below.

### Event types (Phase 1a)

| type | when | key fields | signal |
|------|------|-----------|--------|
| `avail_reaction` | user reacts 🟢/🟡/🔴 on the availability post | `emoji`, `week_of` (ISO date of that week's Tuesday) | weak weekly prior |
| `run_created` | a schedule is created | `organizer_id`, `level`, `run_type`, `run_id`; `ts_utc`=run start | positive (organizer) |
| `run_joined` | a raider is assigned a slot (signup / DM-accept assignment) | `role`, `run_id`; `ts_utc`=run start | positive |
| `offer_accepted` | raider reacts ✅ to a fill/offer DM | `run_id`; `ts_utc`=run start | positive |
| `offer_declined` | raider reacts ❌ to a fill/offer DM | `run_id`; `ts_utc`=run start | **negative** (not free then) |
| `run_completed` | a run's start time passes (in `hourly_check`, before message deletion) | `level`, `run_type`, `roster` (list of user_ids), `run_id`; one record per run | strongest positive per rostered member |

Note on `run_completed`: it is one record with a `roster` list (`user_id` null
at the top level). Phase 2 expands the roster into per-member slot observations.
Storing it once keeps the log compact and preserves the final team at run time
(distinct from `run_joined`, which captures the earlier commitment moment).

### Module API (`eventlog.py`)

Pure, testable helpers plus a thin appender:

- `local_slot(ts_utc: datetime, tz: ZoneInfo) -> tuple[int, int]` — returns
  `(weekday 0–6, block 0–11)` in local time; `block = local_hour // 2`. Pure.
- `week_of(ts_utc: datetime) -> str` — ISO date of the Tuesday-noon-CST week
  anchor containing `ts_utc` (reuses the bot's `_CST` weekly cycle). Pure.
- `log_event(type: str, *, ts_utc: datetime, user_id: int | None = None, tz: ZoneInfo | None = None, source: str = "discord", run_id: int | None = None, **fields) -> None`
  — builds the record (deriving `local_weekday`/`local_block`/`tz` when `tz` is
  given), appends one JSON line to `events.jsonl`, flushes. Never raises out to
  the caller (logs a warning on IO error) so logging can never break a bot flow.
- `read_events(path: str = "events.jsonl") -> list[dict]` — reads all events
  (skips malformed lines with a warning). For Phase 2 / tests.
- `EVENTS_PATH = "events.jsonl"` module constant.

All datetimes timezone-aware UTC internally. Appends are single-line writes in
`"a"` mode with `encoding="utf-8"`.

## Integration Points (bot.py)

Logging calls are added at the existing flow points (the implementation plan
pins exact locations; these are the conceptual hooks). Each call passes the
raider's `timezone` so the local slot is derived. Every call is best-effort
(the helper swallows IO errors).

1. **Availability reaction** — in `on_reaction_add`, the availability-channel
   branch that appends to `self.availability[emoji]`: log `avail_reaction`.
2. **Run created** — where a new `Schedule` is added to `self.schedules`
   (key-request completion and the pre-post add-raider flow): log `run_created`
   keyed by the new message id.
3. **Run joined** — in the signup assignment path (`raider_signup` call sites in
   the button/role-select views, and the DM-accept assignment): log `run_joined`
   per assigned raider.
4. **Offer accepted / declined** — in `on_reaction_add`, the `dm_map` ✅/❌
   branch: log `offer_accepted` / `offer_declined`.
5. **Run completed** — in `hourly_check`, in the block that computes
   `past_schedule_ids` and deletes messages: before removing each past schedule,
   log one `run_completed` with its roster.

## Retention Change

`hourly_check` currently deletes passed schedules and their Discord messages
outright. Keep deleting the **Discord message** (channels shouldn't accumulate
dead embeds), but **first** write the `run_completed` event so the run's timing
and roster survive in `events.jsonl`. No schedule object is retained in memory
beyond what happens today — the durable record lives in the log.

## Forward-Compatibility with Phase 1b (Raider.io)

- The Raider.io backfill will append `run_completed`/`run_joined`-style events
  with `source="raiderio"` and `run_id=<keystone_run_id>`, deduped against
  existing `run_id`s. No schema change — the `source` and `run_id` fields exist
  from day one for exactly this.
- `local_slot` and the record shape are shared, so backfilled runs bucket
  identically to in-guild runs.

## Errors & Edge Cases

- Logging is best-effort: `log_event` catches and logs IO errors, never
  propagates — a logging failure must never break signup/reaction/run flows.
- A raider with no timezone → `local_weekday`/`local_block`/`tz` are null; the
  raw `ts_utc` is still recorded so the slot can be derived later.
- Malformed lines in `events.jsonl` are skipped on read with a warning (one bad
  line never drops the rest — same lesson as the price-watch watchlist loader).
- `events.jsonl` missing → `read_events` returns `[]`.

## Testing

`tests/test_eventlog.py` (pure logic + a temp file):

- `local_slot`: known UTC + tz → expected `(weekday, block)`, including a case
  where UTC→local crosses a day boundary and a block boundary.
- `week_of`: timestamps around Tuesday-noon-CST map to the correct week anchor
  (incl. a Monday and a Wednesday).
- `log_event` → `read_events` round-trip: append several events to a temp path,
  read them back, assert fields (incl. null-tz path leaving local fields null,
  and the `run_completed` roster list).
- `read_events` skips a malformed line and returns the valid ones; missing file
  → `[]`.

bot.py integration points have no unit tests (consistent with the codebase);
verified via `python -m py_compile bot.py` and manual smoke (trigger a reaction /
signup, confirm an `events.jsonl` line appears).

## Documentation

Per project convention, on completion update:
- `CLAUDE.md` — add `eventlog.py` and `events.jsonl` to the File Map / State
  Persistence; note the retention change in the `hourly_check` workflow.
- `README.md` — brief note that the bot now logs anonymized availability/run
  events locally for future scheduling features.
- `CHANGELOG.md` — new entry.
