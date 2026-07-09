# Availability Forecasting — Phase 2a: Predictor + Automated Dry-Run Preview

**Date:** 2026-07-09
**Status:** Design → for review, then implementation plan
**Branch:** `feature/availability-forecasting` (continues Phase 1a/1b)

## Context

Phases 1a/1b built the append-only `events.jsonl` and seed it from in-guild
signals + Raider.io runs. This phase builds the **forecaster brain** and runs it
**fully automatically**, but stops short of pinging the team — it posts a
**dry-run preview** so the model can be validated against reality before it ever
DMs anyone (Phase 2b flips it live).

Depends on `eventlog.read_events` (Phase 1a) and the raider timezones in
`self.raiders`.

## Scope

**In scope (2a):**
- `forecast.py`: the **predictor** (events → per-person, per-`(weekday, block)`
  availability probability) and the **roster/slot optimizer** (best role-valid
  team + absolute run-time from the 🟢 pool).
- A weekly automated task that runs the gate → predictor → optimizer and **DMs a
  dry-run preview to `BANKER_ID`**. No team DMs, nothing created.

**Explicitly deferred to 2b:** DM-confirming the roster (≥4/5), creating the run
via the existing Schedule flow, and the next-best fallback chain when confirms
fall short.

## Trigger & Gate

- **Trigger:** `@tasks.loop(time=noon CST)` with a `weekday() == 2` (Wednesday)
  guard — one day after the Tuesday-noon availability reset. (Same
  `_CST`/daily-fire-skip-non-matching pattern as `weekly_avail_reset`.)
- **Feasibility gate (runs first):** let `green = self.availability[GREEN]`
  (real users; the bot's own seed reactions never create raiders). If a
  role-valid team of 5 (1 tank, 1 healer, 3 dps) **cannot** be assembled from
  `green`, the job **no-ops** (log + return) — no prediction, no preview. Only
  when the green pool can field a team do we predict and preview.

## The Predictor (`forecast.py`)

### 1. Normalize events → signed per-slot observations (pure)

`observations(events, raiders, now) -> List[Obs]` where
`Obs = (user_id, weekday, block, age_weeks, sign)`:

| event type | contributes | sign |
|---|---|---|
| `run_completed` | one obs per roster member, slot computed from `ts_utc` + that member's `raiders[uid].timezone` | +1 |
| `offer_accepted` | its stored `(local_weekday, local_block)` | +1 |
| `raiderio_run` | its stored `(local_weekday, local_block)` | +1 |
| `offer_declined` | its stored `(local_weekday, local_block)` | −1 |
| `avail_reaction` | **not** per-slot; feeds the base-rate prior only (weekly participation) | n/a |

- **Equal source weight** — a Raider.io run counts the same as an in-guild
  signal (decided; it's also most of the early data).
- `run_completed` is the only multi-user record; expand it using each current
  member's timezone. Members missing from `raiders` (no tz) are skipped.
- Events with null `local_weekday`/`local_block` and no derivable slot are
  skipped.
- `age_weeks = (now - ts_utc) / 7 days`.

### 2. Score a block (pure)

`predict(observations_for_user, weekday, block) -> float`:

```
w(age)  = 0.5 ** (age_weeks / HALF_LIFE)         # HALF_LIFE = 4 weeks
W⁺      = Σ w(age) over +1 obs in this block
W⁻      = Σ w(age) over −1 obs in this block
prior_p = person's base rate  (see below)
P       = (W⁺ + ALPHA * prior_p) / (W⁺ + W⁻ + ALPHA)   # ALPHA = smoothing pseudo-count
```

- **`prior_p` (daypart/base-rate fallback):** the person's recency-weighted
  positive fraction across a broader region — first their **same-weekday**
  observations, falling back to **all** their observations, then to a low global
  default `BASE_PRIOR` (e.g. `0.15`) when they have no data. This makes thin
  blocks shrink toward the person's general pattern instead of over-fitting one
  data point.
- Tunable constants: `HALF_LIFE`, `ALPHA`, `BASE_PRIOR` (all in `forecast.py`).

### 3. Roster/slot optimizer (pure)

Runs happen at one **absolute** time that maps to different local blocks per
person (timezones differ). So:

- **Candidate slots:** the 7 × 12 = 84 `(weekday, block)` pairs of the upcoming
  week, anchored in `_CST`, each resolved to a concrete UTC datetime for next
  week.
- For each candidate slot and each green raider, compute the raider's **local**
  `(weekday, block)` at that absolute time and score it with the predictor.
- **`select_team(scored_candidates) -> Team | None`:** choose 5 raiders covering
  1 tank + 1 healer + 3 dps (respecting multi-role raiders), maximizing the
  team's **mean** predicted probability. Small pool → a bounded search
  (assign scarce roles tank/healer first, then best-3 dps from the rest);
  returns `None` if roles can't be covered.
- **Rank** candidate slots by their best team's mean probability. The top slot is
  the pick; keep the top few for the preview (and, later, 2b's fallback chain).

`select_team` with all-equal probabilities also answers the **feasibility gate**
(can any role-valid team form from green?).

## Automated Dry-Run Preview (bot.py)

The weekly task, after the gate passes:
1. Build the scored candidates for the green pool, run the optimizer.
2. If no slot yields a role-valid team → log + return (shouldn't happen past the
   gate, but guard).
3. DM **`BANKER_ID`** a preview of the top pick (and 1–2 runners-up), e.g.:

   > 🔮 **Predicted run for this week** (dry-run — not scheduled)
   > 🕐 Thursday 8:00–10:00 PM CST · confidence **0.78**
   > 🛡️ Tank: Acetronic (0.81) 💚 Healer: Bronya (0.74) ⚔️ DPS: …
   > _Runner-up: Wed 8–10 PM (0.71)_

No reactions, no team DMs, nothing written to schedules. Best-effort DM (catch
`discord.HTTPException`).

## Errors & Edge Cases

- Empty/insufficient events → predictions fall back to `BASE_PRIOR`; previews
  will be low-confidence (expected during cold start — this is exactly what the
  dry-run period surfaces).
- Green raider missing a timezone → cannot map absolute→local; excluded from
  candidates (and can't anchor a team).
- Gate fails (no role-valid green team) → clean no-op.
- All prediction/optimization is pure and side-effect-free; only the final DM
  touches Discord.

## Testing

`tests/test_forecast.py` (synthetic events; no network):
- `observations`: expands a `run_completed` roster into per-member slots via tz;
  maps offer/raiderio events to their stored slots; assigns correct signs; skips
  null/timezone-less; computes `age_weeks`.
- `predict`: recency decay (recent positives outweigh old), smoothing (1 positive
  ≠ 1.0), negatives pull the score down, prior fallback for thin/no-data blocks.
- `select_team`: covers tank/healer/3dps incl. a multi-role raider filling a
  scarce role; returns `None` when roles can't be covered; maximizes mean prob.
- Slot optimizer: picks the absolute slot with the best team; per-person
  local-block mapping across differing timezones is correct.
- Feasibility gate helper: true/false on assemblable vs not.

`bot.py` wiring (the weekly task + preview DM) has no unit tests (convention);
verified via `py_compile` + manual (force the task, confirm a preview DM).

## Documentation

On completion: `CLAUDE.md` (File Map `forecast.py`; the weekly predictor/preview
workflow + gate + trigger), `README.md` (note the automated dry-run forecasting),
`CHANGELOG.md` (bump `1.3.0` → `1.4.0`), `bot.py` `BOT_VERSION`.
