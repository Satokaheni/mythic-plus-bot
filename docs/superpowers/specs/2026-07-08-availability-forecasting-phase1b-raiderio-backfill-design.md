# Availability Forecasting — Phase 1b: Raider.io Backfill + Harvest

**Date:** 2026-07-08
**Status:** Approved design → ready for implementation plan
**Branch:** `feature/availability-forecasting` (continues Phase 1a)

## Context

Phase 1a built the append-only `events.jsonl` store and logs four in-guild
signals. This phase **seeds and continuously enriches** that store from
Raider.io: each mapped character's real Mythic+ runs (with `completed_at`
timestamps) become per-person attendance signals, attacking the cold-start
problem so Phase 2's forecaster has data from day one.

Depends on Phase 1a (`eventlog.py`, `log_event`/`read_events`). No change to
`eventlog.py` — `log_event` already accepts any event type + `**fields`.

## Decisions (settled during brainstorming)

- **Ignore `alt_of`** — every entry in `character_mappings.json` is a character
  to fetch; all of a person's characters' runs are attributed to their
  `discord_id`. More characters → more timing data per person.
- **Skip unregistered people.** Only harvest for `discord_id`s that are
  registered raiders (present in `self.raiders`, so a **timezone** is known).
  Raider.io does **not** provide a timezone — the one field forecasting can't do
  without — so we do not create raiders from it. Overlap today: 24 of 28 mapped
  people are registered; the other 4 are skipped and picked up automatically
  once they register.
- **New single-user event type `raiderio_run`** (not the multi-user
  `run_completed`), so each event gets a proper local `(weekday, block)`.
- **Region `us`** (all mapped realms are US).
- **Cadence:** initial harvest at startup + **daily** refresh (cheap,
  dedupe-safe; daily rather than weekly so we don't miss runs beyond Raider.io's
  ~last-10 `recent_runs` window).

## Configuration

| Item | Purpose | Default |
|------|---------|---------|
| `RAIDERIO_API_KEY` (env) | Raider.io API key (`?access_key=`) | *(required, already in `.env`)* |
| `RAIDERIO_REGION` (env) | Region for lookups | `us` |
| `character_mappings.json` | `[{character, realm_slug, discord_id, …}]` you maintain | *(required, present)* |

`character_mappings.json` is **gitignored** (contains Discord IDs — mild PII;
guild-specific config maintained like `.env`).

Env vars are read **lazily at call time** (not at import) — the lesson from the
Undermine client bug, so `.env` is always applied regardless of import order.

## Architecture

### `raiderio.py` — client + harvest

Mirrors `undermine.py`'s shape (thin async client, pure parser).

- `@dataclass(frozen=True) Run`: `run_id: int`, `completed_at: datetime` (UTC-aware), `level: int`.
- `_parse_runs(data: dict) -> List[Run]` — **pure**. Combines
  `result.mythic_plus_recent_runs` + `result.mythic_plus_best_runs`, extracts
  `keystone_run_id` → `run_id`, `completed_at` → parsed UTC datetime,
  `mythic_level` → `level`. Dedupes within the character by `run_id` (recent and
  best lists overlap). Skips entries missing required fields.
- `_parse_completed_at(s: str) -> datetime` — **pure**. Parses Raider.io's
  `"2026-04-22T06:07:47.000Z"`; Python 3.9-safe (`s.replace("Z", "+00:00")` then
  `datetime.fromisoformat`, which accepts the 3-digit `.000` fraction + offset).
- `async fetch_character_runs(session, realm_slug: str, character: str) -> List[Run]`
  — GET `https://raider.io/api/v1/characters/profile` with query
  `region=<region>&realm=<realm_slug>&name=<character>&fields=mythic_plus_recent_runs,mythic_plus_best_runs&access_key=<key>`.
  **URL-encodes** every query value (names have chars like `Reíka`, `ßovinity`).
  Best-effort: non-200 or network/parse error → log warning, return `[]` (skip
  that character; never raises out).
- `load_character_mappings(path: str = "character_mappings.json") -> List[dict]`
  — reads the JSON array; returns `[]` on missing/corrupt file (warn). Ignores
  `alt_of`.
- `async harvest(raiders: dict, session, mappings: List[dict], events_path=eventlog.EVENTS_PATH) -> int`
  — the backfill/harvest, unified and idempotent:
  1. `existing = eventlog.read_events(events_path)`; build
     `seen = {(user_id, run_id) for e in existing if e["source"] == "raiderio"}`.
  2. For each mapping entry: `discord_id = int(entry["discord_id"])`; if
     `discord_id not in raiders` → **skip** (unregistered). Else `raider =
     raiders[discord_id]`.
  3. `runs = await fetch_character_runs(session, entry["realm_slug"], entry["character"])`.
  4. For each run where `(discord_id, run.run_id) not in seen`:
     `eventlog.log_event("raiderio_run", ts_utc=run.completed_at,
     user_id=discord_id, tz=raider.timezone, source="raiderio",
     run_id=run.run_id, level=run.level, path=events_path)`; add to `seen`;
     increment count.
  5. Return the number of new events appended.

`harvest` is testable with a fake `raiders` dict, a monkeypatched
`fetch_character_runs`, and a `tmp_path` events file.

### `raiderio_run` event (written by harvest)

Single-user attendance record: `type="raiderio_run"`, `user_id=<discord_id>`,
`ts_utc=<completed_at UTC>`, `tz=<raider timezone>` (→ real `local_weekday`/
`local_block`), `source="raiderio"`, `run_id=<keystone_run_id>`, `level=<int>`.
Deduped by `(user_id, run_id)`. This is the primary bootstrap positive signal.

### `bot.py` wiring

- `RAIDERIO_API_KEY` presence is not required at startup by `_require_env` (the
  client reads it lazily); add `RAIDERIO_REGION` handling in `raiderio.py`.
- In `setup_hook`: load `self._char_mappings = raiderio.load_character_mappings()`.
- New `@tasks.loop(hours=24) raiderio_harvest` task, started in `setup_hook`
  (runs its first iteration immediately = the startup backfill, then daily):
  - `is_ready()` guard (mirrors other tasks); return early if no mappings.
  - `async with aiohttp.ClientSession() as session: added = await raiderio.harvest(self.raiders, session, self._char_mappings)`; log the count.
  - Wrapped so one failure never kills the loop.

## Errors & Edge Cases

- Missing/corrupt `character_mappings.json` → `[]`, harvest is a no-op (warn).
- Unregistered `discord_id` → skipped (no timezone).
- Character not found / API error / rate-limit → that character returns `[]`,
  others proceed.
- Duplicate runs across polls, and overlap between `recent_runs` and
  `best_runs` → removed by the `(user_id, run_id)` dedupe and per-character
  `run_id` dedupe in `_parse_runs`.
- `completed_at` is UTC; the person's timezone yields the local slot.
- Rate limit: ~33 characters/day is trivially within Raider.io's limits.

## Testing

`tests/test_raiderio.py` (client mocked; no network):
- `_parse_completed_at` parses the `…Z` millisecond format to a UTC-aware datetime.
- `_parse_runs`: combines recent + best, maps `keystone_run_id`/`completed_at`/
  `mythic_level`, dedupes overlapping `run_id`s, skips malformed entries, empty → `[]`.
- `load_character_mappings`: parses the array (ignoring `alt_of`); missing file → `[]`.
- `harvest`: with a fake `raiders` dict and monkeypatched `fetch_character_runs`
  → appends `raiderio_run` events only for registered ids, skips unregistered,
  dedupes against pre-existing events, returns the correct new-count. Uses
  `tmp_path` for the events file.

`bot.py` wiring has no unit tests (codebase convention); verified via
`py_compile` + manual (run once, confirm `events.jsonl` gains `raiderio_run`
lines and re-running adds none).

## Documentation

On completion: `CLAUDE.md` (File Map: `raiderio.py`, `character_mappings.json`;
new harvest workflow + env vars), `README.md` (Raider.io backfill note + env
vars), `CHANGELOG.md` (bump `1.2.0` → `1.3.0`), `bot.py` `BOT_VERSION`.
