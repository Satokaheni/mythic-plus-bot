# WoW Token Sell Alert — Design

**Date:** 2026-08-27
**Status:** Approved design → ready for implementation plan

## Summary

Add an owner-only WoW Token price tracker to the Mythic+ bot. A background task
polls Blizzard's [`/data/wow/token/index`](https://develop.battle.net/documentation/world-of-warcraft/game-data-apis)
endpoint every 20 minutes and DMs the `BANKER_ID` user when the token price
rises **above a fixed threshold they set** — a "sell now" signal.

Unlike the Undermine price watch (which hunts *lows* using an adaptive
percentile band), this is a **fixed-threshold sell signal**: the owner names the
gold price at which they want to sell, and the bot tells them when the market
reaches it. While the price stays above the threshold, further alerts fire only
on **new highs**, each at least 10,000g above the last alerted price — a ratchet
that follows a rising market without spamming a flat or choppy one.

The endpoint is already reachable with the bot's existing Blizzard credentials.
Verified 2026-08-27: US 275,828g, EU 388,706g, KR 299,397g, TW 616,663g.

## Goals

- Track the current WoW Token price for the configured region.
- DM the owner when the price crosses above a threshold they set.
- Keep alerting as the price climbs, in 10,000g steps — but stay silent while
  it is flat, falling, or merely re-climbing to a level already reported.
- Re-arm automatically once the price falls back below the threshold.

## Non-Goals (YAGNI)

- **No price history.** The endpoint returns only a current price and a
  timestamp, and the fixed-threshold signal does not need a baseline. Nothing is
  recorded to disk beyond the two state fields below. Consequently there is no
  "30-day high" context, no trend reporting, and no `daily.json` equivalent.
- **No quiet hours.** Unlike `!watch` buy alerts (10 AM–midnight CST), this DMs
  at any hour. The owner asked to be told immediately.
- **No multi-user support.** Owner-only, gated to `BANKER_ID`, exactly like
  `!watch`. No per-user thresholds, no subscriber list.
- **No statistical / percentile signal.** Explicitly rejected in favor of a
  number the owner sets themselves.
- **No sell automation.** The bot reports; the human sells.
- **No runtime-configurable step size.** The 10,000g ratchet step is a module
  constant.

## Configuration

No new environment variables. The feature reuses:

| Var | Purpose | Default |
|-----|---------|---------|
| `BLIZZ_CLIENT_ID` | Blizzard Game Data API client ID | *(required, already present)* |
| `BLIZZ_CLIENT_SECRET` | Blizzard Game Data API client secret | *(required, already present)* |
| `BLIZZ_REGION` | Region whose token price is tracked | `us` |
| `BANKER_ID` | Discord user allowed to use the commands, and DM recipient | *(required, already present)* |

New module constants in `tokenwatch.py`:

| Constant | Value | Purpose |
|----------|-------|---------|
| `STEP_COPPER` | `100_000_000` (10,000g) | Minimum rise above the last alert before re-alerting |
| `TOKEN_FILE` | `token_watch.json` | State file |

New constant in `bot.py`:

| Constant | Value | Purpose |
|----------|-------|---------|
| `TOKEN_POLL_MINUTES` | `20` | Poll cadence; Blizzard refreshes the price roughly every 20 minutes |

## Architecture

Follows the established `watchlist.py` / `snipelist.py` shape: a pure,
independently testable logic module plus a thin flow layer in `bot.py`.

### `blizzard.py` — one new method

```python
def _parse_token_price(data: dict) -> Optional[Tuple[int, int]]:
    """Parse a token index payload into (price_copper, last_updated_ms)."""

class BlizzardClient:
    async def token_price(self, session) -> Optional[Tuple[int, int]]:
        """Current WoW Token price in copper, plus Blizzard's update timestamp."""
```

`token_price` reuses the existing `_get(session, "/data/wow/token/index", "dynamic")`
helper, so it inherits OAuth token caching, the 401 retry, and region/namespace
construction with no changes. Parsing lives in a module-level `_parse_token_price`
so it can be unit-tested without a session, matching the other `_parse_*` functions.

The payload has exactly three keys — `_links`, `last_updated_timestamp`, `price`.
A payload missing `price` parses to `None` rather than raising.

### `tokenwatch.py` — new module

```python
@dataclass
class TokenWatch:
    threshold: Optional[int] = None    # copper; None = alerts disabled
    last_alert: Optional[int] = None   # copper price at last DM; None = armed

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d: dict) -> "TokenWatch": ...
    def save(self, path: str = TOKEN_FILE) -> None: ...
    @classmethod
    def load(cls, path: str = TOKEN_FILE) -> "TokenWatch": ...

def evaluate(watch: TokenWatch, price: int) -> Optional[int]: ...
def parse_threshold(arg: str) -> Optional[int]: ...
def format_alert(price: int, threshold: int, previous: Optional[int]) -> str: ...
def format_status(watch: TokenWatch, price: Optional[int]) -> str: ...
```

`save` writes to a temp file and `os.replace`s it, the same atomic pattern as
`Watchlist.save`. `load` returns a default (disabled) `TokenWatch` when the file
is absent or unreadable, so a missing file is never an error.

Gold formatting reuses `from watchlist import format_gold` — already the shared
formatter (`snipelist.py` imports it the same way), so token prices render as
`275,828g` consistently with every other price the bot prints.

### `bot.py` — one task, two commands

- `token_watch_check`, a `@tasks.loop(minutes=TOKEN_POLL_MINUTES)` started in
  `setup_hook` alongside `price_watch_check`.
- `!token`, `!tokenalert` command handlers in `on_message`, gated on
  `message.author.id == BANKER_ID`, accepted in DM or the key channel — the same
  gating expression already used by `!watch`.
- A line added to the existing banker Price Watch section of `!help`.

### Data flow

```
token_watch_check (every 20 min)
  └─ BlizzardClient.token_price(session)      -> (price_copper, updated_ms)
      ├─ price < threshold and last_alert is not None
      │     └─ watch.last_alert = None; watch.save()      (re-arm, no DM)
      └─ tokenwatch.evaluate(watch, price)    -> Optional[int] (price to report)
            └─ DM BANKER_ID with format_alert(...)
                └─ on DM success: watch.last_alert = price; watch.save()
```

State is written on exactly two paths: a successful alert, and a re-arm. The
re-arm writes only when `last_alert` was actually set, so a price sitting quietly
below the threshold does not rewrite the file every 20 minutes.

## Sell-Signal Detection

`evaluate(watch, price)` returns the price to report, or `None` for silence. It
does **not** mutate the watch — the caller commits `last_alert` only after the DM
succeeds (see Errors & Edge Cases).

| Condition | Result |
|-----------|--------|
| `watch.threshold is None` | `None` — feature disabled |
| `price < watch.threshold` | `None`, **and** the caller clears `last_alert` (re-arm) |
| `price >= threshold` and `last_alert is None` | **fire** — first crossing |
| `price >= last_alert + STEP_COPPER` | **fire** — new high |
| otherwise | `None` |

Because `last_alert` only ever moves upward while the price stays above the
threshold, a dip-and-reclimb is silent until the price beats the last alerted
price by a full step. The "only new highs re-alert" behavior is a property of the
data model, not separate logic.

**Worked example** — threshold 290,000g, step 10,000g:

| Price | Outcome | `last_alert` after |
|-------|---------|--------------------|
| 285,000g | silent (below threshold) | `None` |
| 300,000g | **DM** — crossed threshold | 300,000g |
| 305,000g | silent (only +5,000g) | 300,000g |
| 310,000g | **DM** — new high | 310,000g |
| 302,000g | silent (dip, still above threshold) | 310,000g |
| 315,000g | silent (below 310,000 + 10,000) | 310,000g |
| 320,000g | **DM** — new high | 320,000g |
| 288,000g | silent, **re-arms** (below threshold) | `None` |
| 295,000g | **DM** — crossed threshold again | 295,000g |

### Alert copy

First crossing:

```
💰 **WoW Token — sell signal**
Price: **300,000g**  (threshold: 290,000g)
Crossed your threshold.
```

Subsequent new high:

```
💰 **WoW Token — sell signal**
Price: **310,000g**  (threshold: 290,000g)
▲ 10,000g since your last alert.
```

## Commands

All banker-only (`BANKER_ID`), accepted in DM or the key channel.

| Command | Action |
|---------|--------|
| `!token` | Show current price, threshold, and armed/alerted state |
| `!tokenalert <gold>` | Set the threshold. Accepts `300000` or `300,000` |
| `!tokenalert off` | Clear the threshold, disabling alerts |

Setting or clearing a threshold resets `last_alert` to `None`, so a newly set
threshold always starts armed rather than inheriting a stale ratchet position
from a previous threshold.

`parse_threshold` strips commas and surrounding whitespace, accepts a positive
integer number of **gold**, and returns copper (`gold * 10_000`). It returns
`None` for the literal `off`, and raises `ValueError` for anything else so the
command handler can reply with usage text.

`!token` fetches the live price so the owner can check the market on demand, and
reports state as one of: `alerts disabled`, `armed (waiting to cross)`, or
`alerted at <price>`.

## Errors & Edge Cases

- **Blizzard API failure or outage** — the task wraps fetch/evaluate/DM in a
  broad `except Exception`, logs a warning, and returns. State is untouched, so
  the next poll re-evaluates cleanly. The loop is never killed.
- **Missing `price` in the payload** — `_parse_token_price` returns `None`; the
  task logs and skips the iteration.
- **DM failure** — `discord.HTTPException` is caught and logged, and
  `last_alert` is **not** persisted. This is deliberate: committing the ratchet
  before a confirmed delivery would silently swallow the alert and leave the
  owner believing they had been told. The next poll retries.
- **Threshold set while already above it** — the watch starts with
  `last_alert = None` (armed), so the very next poll fires immediately. This is
  correct: the owner asked to be told when the price is above their number, and
  it already is.
- **Corrupt or absent `token_watch.json`** — `load` returns a disabled default
  and logs; it never raises.
- **Bot restart** — state is a file read on load, so a restart mid-alert keeps
  the ratchet position. This matters because the feature is being added
  specifically to survive a restart the owner is about to perform.
- **Large numbers** — token prices are ~2.76 billion copper. Python ints are
  arbitrary precision, and `format_gold` divides before formatting, so there is
  no overflow concern; the only risk is display, which the tests cover.

## Testing

New `tests/test_tokenwatch.py`, pure functions only (no Discord, no network),
matching the style of `tests/test_watchlist.py`:

- `evaluate` — disabled threshold; price below threshold; first crossing;
  qualifying step up; sub-step rise stays silent; dip-and-reclimb stays silent;
  re-arm after dropping below threshold, then re-cross fires again.
- The full worked-example sequence above, asserted as one ordered walk, to lock
  in the ratchet end to end.
- `evaluate` does not mutate its argument.
- `TokenWatch.to_dict` / `from_dict` round-trip, including `None` fields.
- `load` on a missing path returns a disabled default.
- `parse_threshold` — plain integer, comma-formatted, `off`, and rejection of
  garbage / negative / zero.
- `format_alert` — first-crossing copy vs. delta copy; both contain the price
  and threshold.

Added to `tests/test_blizzard.py`:

- `_parse_token_price` on a realistic payload, and on one missing `price`.

## Documentation

- **`CLAUDE.md`** — add the feature to the file map (`tokenwatch.py`), the
  command table, and a short "WoW Token Sell Alert" section under the existing
  price-watch material.
- **`CHANGELOG.md` and `version.txt`** — intentionally **not** updated. The
  owner asked to skip them: this is a personal, owner-only tool, and bumping
  `version.txt` would trigger a changelog DM to the whole guild for a feature
  nobody else can use.
