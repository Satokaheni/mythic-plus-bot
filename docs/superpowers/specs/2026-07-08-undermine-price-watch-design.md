# Undermine Price-Watch — Design

**Date:** 2026-07-08
**Status:** Approved design → ready for implementation plan

## Summary

Add an owner-only price-watch feature to the Mythic+ bot. The bot polls the
[Undermine Exchange API](https://undermine.exchange/api.html) hourly for a list
of watched World of Warcraft **region-wide commodities** (bulk consumables and
crafting mats) and DMs the owner when an item's current price dips into a
self-adjusting "pounce" low — a genuine bulk-buy opportunity.

The buy signal is **relative to each item's own recent price history**, not a
fixed number, because commodity prices drift downward across an expansion. A
rolling window recomputes the baseline every cycle, and an adaptive threshold
per item auto-tunes how aggressive the "low" trigger is.

## Goals

- Watch a small set of commodity item IDs and get DM'd when to buy in bulk.
- Signal adapts week-to-week as prices trend down — no manual re-tuning.
- Threshold self-tunes per item (fast-loosen when starved, slow-tighten when
  over-firing).
- One "pounce" ping per genuine dip — no hourly spam.

## Non-Goals (YAGNI)

- No per-raider watchlists — **owner-only**, gated to the **`BANKER_ID`** user
  (the guild's AH banker; already defined in `.env`).
- No single-realm item watches — **region-wide commodities only**.
- No Blizzard/Wowhead name resolution — the owner supplies a friendly label.
  (Item **ID is the canonical key**: it pins down name *and* quality, which a
  name alone cannot.)
- No fixed absolute price thresholds.

## Configuration

New environment variables (in `.env`):

| Var | Purpose | Default |
|-----|---------|---------|
| `UNDERMINE_API_KEY` | Undermine API key (`Authorization: ApiKey …`) | *(required, already added)* |
| `UNDERMINE_REGION` | Region for commodity data (`us`/`eu`/`tw`/`kr`) | `us` |
| `BANKER_ID` | Discord user ID allowed to use price-watch (already defined) | *(required, already present)* |

Tunable constants (in `watchlist.py` or `utils.py`):

| Constant | Meaning | Default |
|----------|---------|---------|
| `BASELINE_WINDOW_DAYS` | Days of daily history for the rolling baseline | `14` |
| `MIN_HISTORY_DAYS` | Minimum days before detection runs | `7` |
| `START_PERCENTILE` | Initial low-band percentile per new watch | `35` |
| `PERCENTILE_MIN` / `PERCENTILE_MAX` | Clamp band for the adaptive threshold | `10` / `50` |
| `STARVE_DAYS` | No alert in this many days → loosen | `7` |
| `LOOSEN_STEP` | Percentile points added when starved | `+5` |
| `FLOOD_ALERTS` / `FLOOD_DAYS` | This many alerts in this many days → tighten | `2` / `7` |
| `TIGHTEN_STEP` | Percentile points removed when flooding | `-1` |
| `ADJUST_INTERVAL_HOURS` | Min time between threshold adjustments per item | `24` |

## Architecture

Three focused, independently testable pieces.

### `undermine.py` — API client

Thin async wrapper over the Undermine commodities endpoints. Single
responsibility: fetch price data for one item ID.

- `async fetch_now(item_id) -> NowResult | None`
  → `GET /v1/region/{region}/commodities/{itemId}/now.json`
  (current min price in copper + quantity; `None` if not currently listed).
- `async fetch_daily(item_id) -> list[DailyPoint]`
  → `GET /v1/region/{region}/commodities/{itemId}/daily.json`
  (daily historical price points).
- Headers: `Authorization: ApiKey {UNDERMINE_API_KEY}`, `Accept-Encoding: gzip`.
- Base URL `https://api.undermine.exchange/`, region from `UNDERMINE_REGION`.
- Uses `aiohttp` (already bundled with discord.py). Raises/returns cleanly on
  non-200 so the caller can skip that item without crashing the loop.

### `watchlist.py` — state + detection logic

Owns the watched items, the pure detection math, and persistence.

**`Watch` dataclass** (one per item ID):
- `item_id: int`
- `label: str` — owner-supplied friendly name for alerts
- `percentile: float` — current adaptive low-band percentile (starts `35`)
- `state: str` — `"idle"` | `"alerted"` (anti-spam)
- `alert_history: list[datetime]` — timestamps of fired alerts (for auto-tune)
- `last_adjusted_at: datetime | None`
- `added_at: datetime`

**`Watchlist` store:**
- In-memory `dict[int, Watch]`.
- `add(item_id, label)`, `remove(item_id)`, `all()`.
- `load()` / `save()` — own `watches.json`, atomic temp-file write (same
  pattern as `save_state`). Kept **separate from `state.json`** so
  `save_state`'s signature and its ~10 call sites are untouched.
  Datetimes serialized as ISO strings.

**Pure detection functions** (no I/O — fully unit-testable):
- `percentile(values, p) -> float`
- `evaluate(now_price, daily_points, watch) -> Signal`
  Returns whether the current price is at/below the item's low band and the
  computed median/low-band for display.
- `apply_anti_spam(signal, watch) -> bool` (should we DM now?)
- `auto_tune(watch, now) -> None` (mutates `watch.percentile`)

### `bot.py` — wiring

- New `@tasks.loop(hours=1) price_watch_check` task, started in `setup_hook`
  alongside `hourly_check`. Guards with `is_ready()` up front; wraps each
  item in `try/except` so one bad fetch never kills the sweep. Saves
  `watches.json` at the end.
- Owner-gated commands (checked against `BANKER_ID`), usable in DM or the
  key channel:
  - `!watch <itemId> [label...]` — add/update a watch.
  - `!unwatch <itemId>` — remove a watch.
  - `!watches` — list watches with current price, recent median, low band,
    current percentile, and state (or "insufficient data").
- Alert DM formatting helper.

## Buy-Signal Detection

For each watched item, each hourly cycle:

1. `P_now`, `qty` ← `fetch_now(item_id)`. If not listed, skip.
2. `points` ← last `BASELINE_WINDOW_DAYS` (14) days from `fetch_daily`.
   If fewer than `MIN_HISTORY_DAYS` (7) points, skip (report "insufficient
   data" in `!watches`).
3. `M` ← median(points); `L` ← percentile(points, `watch.percentile`).
4. **Signal fires when `P_now ≤ L`** — current price sits in the bottom
   `percentile`% of its *own* recent prices. The sliding window makes the
   baseline drift down automatically as the expansion ages.

### Anti-spam (one ping per dip)

- Watch tracks `state`. On signal while `state == "idle"`: DM the owner, set
  `state = "alerted"`, append `now` to `alert_history`.
- While `state == "alerted"`: stay silent even if still low.
- Re-arm to `"idle"` when price **recovers above the median `M`**. The next dip
  then re-alerts.

### Adaptive threshold (per item, AIMD-style)

Evaluated at most once per `ADJUST_INTERVAL_HOURS` (24) per item:

- **Too strict** — no alert in the last `STARVE_DAYS` (7) days →
  `percentile += LOOSEN_STEP` (+5). Fast loosen.
- **Too loose** — `FLOOD_ALERTS` (2)+ alerts in the last `FLOOD_DAYS` (7) days →
  `percentile += TIGHTEN_STEP` (−1). Slow tighten.
- Clamp to `[PERCENTILE_MIN, PERCENTILE_MAX]` = `[10, 50]`.

Fast-up / slow-down converges toward each item's sweet spot without thrashing.

### Alert DM contents

- Label + item ID
- Current price (formatted `g/s/c`)
- Recent median and **% below median**
- Quantity currently available
- Current low-band percentile
- Wowhead link (`https://www.wowhead.com/item={itemId}`)

## Errors & Edge Cases

- Loop mirrors `hourly_check`: `is_ready()` guard, per-item `try/except`,
  hourly cadence, gzip → comfortably under the 3,000-point/hr rate limit for a
  handful of items.
- Item not currently listed (`now.json` returns only a `seen` timestamp) →
  treated as unavailable, skipped this cycle.
- Non-200 / network error → logged, item skipped, loop continues.
- Insufficient history → no detection, surfaced in `!watches`.
- Prices are in **copper** everywhere; converted to `g/s/c` only for display.
- `watches.json` missing on first run → start with an empty watchlist.

## Testing

`tests/test_watchlist.py` (Undermine client mocked):

- `percentile` / median correctness on known arrays.
- `evaluate`: fires on a genuine dip, not on flat/rising histories, respects the
  window length.
- Anti-spam: idle→alerted on entry, silent while alerted, re-arm on recovery
  above median.
- Auto-tune: +5 when starved past 7 days, −1 when 2+ alerts in 7 days, clamp at
  10 and 50, no adjustment before 24h elapsed.
- Gold formatting (copper → `g/s/c`).
- `Watchlist` save/load round-trip (datetimes, empty file).

## Documentation

Per project convention, update on completion:
- `CLAUDE.md` — new file map entries (`undermine.py`, `watchlist.py`,
  `watches.json`), the price-watch workflow, new commands, new env vars.
- `README.md` — feature overview + setup (env vars).
- `CHANGELOG.md` — new version entry under `### Improvements`.
- `version.txt` — bumped by the bot on next startup (never edited manually).
