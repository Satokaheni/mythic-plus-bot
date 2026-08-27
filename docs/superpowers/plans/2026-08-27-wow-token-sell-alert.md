# WoW Token Sell Alert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an owner-only WoW Token price tracker that DMs `BANKER_ID` when the token price rises above a threshold they set, re-alerting on each new 10,000g high.

**Architecture:** A new pure-logic module `tokenwatch.py` (dataclass + ratchet + formatters + atomic JSON persistence) mirroring the existing `watchlist.py` / `snipelist.py` shape, plus one new method on the existing `BlizzardClient`, and a thin flow layer in `bot.py` (one `@tasks.loop`, two commands). All logic worth testing lives in `tokenwatch.py`; `bot.py` only wires it up.

**Tech Stack:** Python 3.9+, discord.py 2.x, aiohttp, pytest. No new dependencies, no new environment variables.

**Spec:** `docs/superpowers/specs/2026-08-27-wow-token-sell-alert-design.md`

## Global Constraints

- **Branch:** `feature/wow-token-tracker` (already created, spec already committed).
- **Python 3.9 target.** Use `typing.Optional[...]` / `typing.Tuple[...]`. The `int | None` union syntax is a syntax error on 3.9.
- **Line length 120** (black and ruff are both configured to it).
- **Ruff rules `E, F, W, I`** — imports must be sorted; `I` will fail the build otherwise.
- **All prices are integer copper.** Render for humans only via `format_gold` imported from `watchlist`. 1 gold = 10,000 copper.
- **`STEP_COPPER = 100_000_000`** (10,000g) — the ratchet step.
- **`TOKEN_POLL_MINUTES = 20`** — Blizzard refreshes the token price about every 20 minutes.
- **Do NOT modify `version.txt` or `CHANGELOG.md`.** The owner explicitly excluded them: bumping `version.txt` triggers a changelog DM to the whole guild for an owner-only feature.
- **Do not commit** `practice.py`, `test.py`, or the modified `version.txt` — these are unrelated in-flight work in the same worktree. Always `git add` explicit paths, never `git add -A` or `git add .`.
- **`pyproject.toml` is deliberately not modified.** Its `py-modules` list already omits `blizzard`, `snipelist`, `raiderio`, and `forecast`; the bot runs as flat modules and is not pip-installed, so leaving `tokenwatch` out is consistent with existing practice.

---

### Task 1: Blizzard token-price client method

**Files:**
- Modify: `blizzard.py` (add `_parse_token_price` beside the other `_parse_*` functions; add `token_price` method to `BlizzardClient`)
- Test: `tests/test_blizzard.py`

**Interfaces:**
- Consumes: existing `BlizzardClient._get(session, path, namespace)` — handles OAuth, the 401 retry, and `?namespace={ns}-{region}&locale=en_US` construction.
- Produces: `_parse_token_price(data: dict) -> Optional[Tuple[int, int]]` and `async BlizzardClient.token_price(session) -> Optional[Tuple[int, int]]`, both returning `(price_copper, last_updated_ms)` or `None`.

The live endpoint returns exactly three keys — `_links`, `last_updated_timestamp`, `price` — and `price` is copper (US was 2,758,280,000 = 275,828g on 2026-08-27).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_blizzard.py`:

```python
def test_parse_token_price():
    data = {"_links": {}, "last_updated_timestamp": 1756328236000, "price": 2758280000}
    assert _parse_token_price(data) == (2758280000, 1756328236000)


def test_parse_token_price_missing_price_returns_none():
    assert _parse_token_price({"_links": {}, "last_updated_timestamp": 1756328236000}) is None
    assert _parse_token_price({}) is None
```

Add `_parse_token_price` to the existing import at the top of the file, keeping the names alphabetical:

```python
from blizzard import (
    _parse_auctions,
    _parse_item_info,
    _parse_realm_index,
    _parse_realm_name,
    _parse_token,
    _parse_token_price,
)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_blizzard.py -v`
Expected: FAIL — `ImportError: cannot import name '_parse_token_price' from 'blizzard'`

- [ ] **Step 3: Write minimal implementation**

In `blizzard.py`, add directly below the existing `_parse_realm_name` function:

```python
def _parse_token_price(data: dict) -> Optional[Tuple[int, int]]:
    """Parse a WoW Token index payload into (price_copper, last_updated_ms).

    Returns None when the payload carries no price, so a malformed or partial
    response is skipped rather than raising inside the poll loop.
    """
    if "price" not in data:
        return None
    return int(data["price"]), int(data.get("last_updated_timestamp", 0))
```

Then add this method to `BlizzardClient`, directly after `realm_name`:

```python
    async def token_price(self, session: aiohttp.ClientSession) -> Optional[Tuple[int, int]]:
        """Current WoW Token price in copper, plus Blizzard's update timestamp (ms epoch).

        The token is not an auction-house commodity — it has its own endpoint, and it
        is region-wide, so there is no realm parameter.
        """
        async with await self._get(session, "/data/wow/token/index", "dynamic") as resp:
            resp.raise_for_status()
            return _parse_token_price(await resp.json())
```

`Optional` and `Tuple` are already imported in `blizzard.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_blizzard.py -v`
Expected: PASS (all tests, including the pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add blizzard.py tests/test_blizzard.py
git commit -m "feat: add WoW Token price fetch to BlizzardClient"
```

---

### Task 2: TokenWatch state and persistence

**Files:**
- Create: `tokenwatch.py`
- Test: `tests/test_tokenwatch.py`

**Interfaces:**
- Consumes: `format_gold` from `watchlist` (imported now, used in Task 4).
- Produces: `TokenWatch` dataclass with fields `threshold: Optional[int]` and `last_alert: Optional[int]` (both copper, both `None` by default), plus `to_dict()`, `from_dict()`, `save(path)`, `load(path)`. Also the module constants `TOKEN_FILE`, `STEP_COPPER`, `COPPER_PER_GOLD`.

Two fields is the entire persistent state — there is no price history by design.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tokenwatch.py`:

```python
"""Tests for the WoW Token sell-alert logic."""

from tokenwatch import TokenWatch


def test_defaults_are_disabled():
    w = TokenWatch()
    assert w.threshold is None
    assert w.last_alert is None


def test_to_dict_from_dict_round_trip():
    w = TokenWatch(threshold=2_900_000_000, last_alert=3_000_000_000)
    assert TokenWatch.from_dict(w.to_dict()) == w


def test_round_trip_with_none_fields():
    w = TokenWatch()
    assert TokenWatch.from_dict(w.to_dict()) == w


def test_save_and_load(tmp_path):
    path = str(tmp_path / "token_watch.json")
    original = TokenWatch(threshold=2_900_000_000, last_alert=3_000_000_000)
    original.save(path)
    assert TokenWatch.load(path) == original


def test_load_missing_file_returns_disabled(tmp_path):
    assert TokenWatch.load(str(tmp_path / "does_not_exist.json")) == TokenWatch()


def test_load_corrupt_file_returns_disabled(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{ not json at all", encoding="utf-8")
    assert TokenWatch.load(str(path)) == TokenWatch()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tokenwatch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tokenwatch'`

- [ ] **Step 3: Write minimal implementation**

Create `tokenwatch.py`:

```python
"""WoW Token sell-alert state, ratchet detection, and display formatting.

Unlike watchlist.py (which hunts commodity *lows* with an adaptive percentile band),
this is a fixed-threshold *sell* signal: the owner names a gold price, and the bot
reports when the market reaches it. While the price stays above the threshold,
further alerts fire only on new highs at least STEP_COPPER above the last one.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("discord")

TOKEN_FILE = "token_watch.json"
COPPER_PER_GOLD = 10_000
# Minimum rise above the last alerted price before re-alerting while above threshold.
STEP_COPPER = 10_000 * COPPER_PER_GOLD  # 10,000g


@dataclass
class TokenWatch:
    """The owner's token sell threshold and ratchet position. All prices in copper."""

    threshold: Optional[int] = None   # None -> alerts disabled
    last_alert: Optional[int] = None  # None -> armed (no alert outstanding)

    def to_dict(self) -> dict:
        return {"threshold": self.threshold, "last_alert": self.last_alert}

    @classmethod
    def from_dict(cls, d: dict) -> "TokenWatch":
        threshold = d.get("threshold")
        last_alert = d.get("last_alert")
        return cls(
            threshold=int(threshold) if threshold is not None else None,
            last_alert=int(last_alert) if last_alert is not None else None,
        )

    def save(self, path: str = TOKEN_FILE) -> None:
        data = {"version": 1, "token_watch": self.to_dict()}
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)

    @classmethod
    def load(cls, path: str = TOKEN_FILE) -> "TokenWatch":
        """Load state, or return a disabled default. Never raises — a missing or
        corrupt file must not stop the bot from starting."""
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data.get("token_watch", {}))
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError, OSError) as exc:
            logger.warning("Error loading %s: %s. Token alerts start disabled.", path, exc)
            return cls()
```

Do **not** import `format_gold` yet — nothing in this task uses it, and ruff's `F401`
would fail. Task 4 adds the import in the same edit that first uses it.

- [ ] **Step 4: Run tests and lint**

Run: `python -m pytest tests/test_tokenwatch.py -v`
Expected: PASS — 6 tests

Run: `python -m ruff check tokenwatch.py tests/test_tokenwatch.py`
Expected: no findings

- [ ] **Step 5: Commit**

```bash
git add tokenwatch.py tests/test_tokenwatch.py
git commit -m "feat: add TokenWatch state and persistence"
```

---

### Task 3: The ratchet (evaluate + should_rearm)

**Files:**
- Modify: `tokenwatch.py` (add two module-level functions after the dataclass)
- Test: `tests/test_tokenwatch.py`

**Interfaces:**
- Consumes: `TokenWatch`, `STEP_COPPER` from Task 2.
- Produces: `evaluate(watch: TokenWatch, price: int) -> Optional[int]` returning the price to report or `None`; `should_rearm(watch: TokenWatch, price: int) -> bool`.

`evaluate` **must not mutate** its argument. The caller commits `last_alert` only after the DM succeeds, so that a Discord failure cannot silently advance the ratchet and swallow an alert.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tokenwatch.py`, and extend the import at the top of the file to
`from tokenwatch import TokenWatch, evaluate, should_rearm`:

```python
G = 10_000                    # copper per gold
THRESHOLD = 290_000 * G       # 290,000g


def test_disabled_watch_never_fires():
    assert evaluate(TokenWatch(), 999_999 * G) is None


def test_below_threshold_is_silent():
    assert evaluate(TokenWatch(threshold=THRESHOLD), 285_000 * G) is None


def test_first_crossing_fires():
    assert evaluate(TokenWatch(threshold=THRESHOLD), 300_000 * G) == 300_000 * G


def test_exactly_at_threshold_fires():
    assert evaluate(TokenWatch(threshold=THRESHOLD), THRESHOLD) == THRESHOLD


def test_sub_step_rise_is_silent():
    w = TokenWatch(threshold=THRESHOLD, last_alert=300_000 * G)
    assert evaluate(w, 305_000 * G) is None


def test_full_step_rise_fires():
    w = TokenWatch(threshold=THRESHOLD, last_alert=300_000 * G)
    assert evaluate(w, 310_000 * G) == 310_000 * G


def test_dip_and_reclimb_below_last_alert_stays_silent():
    w = TokenWatch(threshold=THRESHOLD, last_alert=310_000 * G)
    assert evaluate(w, 302_000 * G) is None   # dipped, still above threshold
    assert evaluate(w, 315_000 * G) is None   # reclimbed, but under 310k + 10k


def test_evaluate_does_not_mutate_the_watch():
    w = TokenWatch(threshold=THRESHOLD, last_alert=300_000 * G)
    evaluate(w, 320_000 * G)
    assert w.last_alert == 300_000 * G


def test_should_rearm_only_when_alerted_and_below_threshold():
    assert should_rearm(TokenWatch(threshold=THRESHOLD, last_alert=300_000 * G), 285_000 * G) is True
    # already armed - nothing to reset
    assert should_rearm(TokenWatch(threshold=THRESHOLD, last_alert=None), 285_000 * G) is False
    # still above threshold
    assert should_rearm(TokenWatch(threshold=THRESHOLD, last_alert=300_000 * G), 295_000 * G) is False
    # disabled
    assert should_rearm(TokenWatch(), 100 * G) is False


def test_worked_sequence_from_the_spec():
    """Walk the spec's worked example end to end, driving the ratchet exactly as the task does."""
    w = TokenWatch(threshold=THRESHOLD)
    steps = [
        (285_000, None),      # below threshold
        (300_000, 300_000),   # crosses -> DM
        (305_000, None),      # only +5,000g
        (310_000, 310_000),   # new high -> DM
        (302_000, None),      # dip, still above threshold
        (315_000, None),      # under 310k + 10k
        (320_000, 320_000),   # new high -> DM
        (288_000, None),      # drops below threshold -> re-arms
        (295_000, 295_000),   # crosses again -> DM
    ]
    for gold, expected in steps:
        price = gold * G
        if should_rearm(w, price):
            w.last_alert = None
        fired = evaluate(w, price)
        assert fired == (expected * G if expected is not None else None), f"at {gold:,}g"
        if fired is not None:
            w.last_alert = fired
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tokenwatch.py -v`
Expected: FAIL — `ImportError: cannot import name 'evaluate' from 'tokenwatch'`

- [ ] **Step 3: Write minimal implementation**

Append to `tokenwatch.py`, after the `TokenWatch` class:

```python
def evaluate(watch: TokenWatch, price: int) -> Optional[int]:
    """Return the price to report, or None for silence. Does NOT mutate `watch`.

    Fires on the first crossing above the threshold, then only on new highs at
    least STEP_COPPER above the last alerted price. Because `last_alert` only ever
    moves upward while the price stays above the threshold, a dip-and-reclimb is
    silent until it genuinely beats the last reported high.

    The caller is responsible for committing `last_alert` — and only after the DM
    has actually been delivered.
    """
    if watch.threshold is None:
        return None
    if price < watch.threshold:
        return None
    if watch.last_alert is None:
        return price
    if price >= watch.last_alert + STEP_COPPER:
        return price
    return None


def should_rearm(watch: TokenWatch, price: int) -> bool:
    """True when the price has fallen back below the threshold and the ratchet should reset.

    Guarded on `last_alert` being set so a price resting quietly below the threshold
    does not rewrite the state file on every poll.
    """
    return watch.threshold is not None and watch.last_alert is not None and price < watch.threshold
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tokenwatch.py -v`
Expected: PASS — 16 tests

- [ ] **Step 5: Commit**

```bash
git add tokenwatch.py tests/test_tokenwatch.py
git commit -m "feat: add token sell-signal ratchet"
```

---

### Task 4: Command parsing and message formatting

**Files:**
- Modify: `tokenwatch.py` (add three module-level functions)
- Test: `tests/test_tokenwatch.py`

**Interfaces:**
- Consumes: `TokenWatch`, `COPPER_PER_GOLD`, `format_gold` from Task 2.
- Produces: `parse_threshold(arg: str) -> Optional[int]`, `format_alert(price: int, threshold: int, previous: Optional[int]) -> str`, `format_status(watch: TokenWatch, price: Optional[int]) -> str`.

`parse_threshold` returns `None` for the literal `off` and raises `ValueError` for anything that is not `off` or a positive integer gold amount, so the command handler can distinguish "disable" from "bad input".

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tokenwatch.py`. Add `import pytest` at the top of the file, and extend the tokenwatch import to
`from tokenwatch import TokenWatch, evaluate, format_alert, format_status, parse_threshold, should_rearm`:

```python
def test_parse_threshold_accepts_plain_and_comma_formatted():
    assert parse_threshold("300000") == 300_000 * G
    assert parse_threshold("300,000") == 300_000 * G
    assert parse_threshold("  300000  ") == 300_000 * G


def test_parse_threshold_off_returns_none():
    assert parse_threshold("off") is None
    assert parse_threshold("OFF") is None


def test_parse_threshold_rejects_garbage():
    for bad in ["abc", "", "300.5", "-5", "0", "3e5"]:
        with pytest.raises(ValueError):
            parse_threshold(bad)


def test_format_alert_first_crossing_mentions_crossing():
    text = format_alert(300_000 * G, 290_000 * G, None)
    assert "300,000g" in text
    assert "290,000g" in text
    assert "Crossed your threshold" in text


def test_format_alert_new_high_shows_delta():
    text = format_alert(310_000 * G, 290_000 * G, 300_000 * G)
    assert "310,000g" in text
    assert "10,000g" in text          # the delta since the last alert
    assert "Crossed your threshold" not in text


def test_format_status_disabled():
    assert "disabled" in format_status(TokenWatch(), 275_828 * G).lower()


def test_format_status_armed():
    text = format_status(TokenWatch(threshold=290_000 * G), 275_828 * G)
    assert "armed" in text
    assert "290,000g" in text
    assert "275,828g" in text


def test_format_status_alerted():
    text = format_status(TokenWatch(threshold=290_000 * G, last_alert=300_000 * G), 305_000 * G)
    assert "alerted at" in text
    assert "300,000g" in text


def test_format_status_handles_unavailable_price():
    assert "unavailable" in format_status(TokenWatch(), None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tokenwatch.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_threshold' from 'tokenwatch'`

- [ ] **Step 3: Write minimal implementation**

First add the `format_gold` import to `tokenwatch.py`, after the `typing` import and
separated by a blank line (ruff rule `I` requires first-party imports in their own block):

```python
from typing import Optional

from watchlist import format_gold
```

Then append to `tokenwatch.py`:

```python
def parse_threshold(arg: str) -> Optional[int]:
    """Parse a !tokenalert argument into copper.

    Returns None for the literal "off" (disable). Raises ValueError for anything
    that is not "off" or a positive whole number of gold, so the caller can reply
    with usage text instead of silently accepting nonsense.
    """
    cleaned = arg.strip().replace(",", "")
    if cleaned.lower() == "off":
        return None
    gold = int(cleaned)  # raises ValueError on garbage, decimals, or empty input
    if gold <= 0:
        raise ValueError("threshold must be a positive number of gold")
    return gold * COPPER_PER_GOLD


def format_alert(price: int, threshold: int, previous: Optional[int]) -> str:
    """The sell-signal DM. `previous` is the last alerted price, or None on first crossing."""
    lines = [
        "💰 **WoW Token — sell signal**",
        f"Price: **{format_gold(price)}**  (threshold: {format_gold(threshold)})",
    ]
    if previous is None:
        lines.append("Crossed your threshold.")
    else:
        lines.append(f"▲ {format_gold(price - previous)} since your last alert.")
    return "\n".join(lines)


def format_status(watch: TokenWatch, price: Optional[int]) -> str:
    """The !token reply: current price plus threshold and ratchet state."""
    now_line = f"WoW Token: **{format_gold(price)}**" if price is not None else "WoW Token: price unavailable"
    if watch.threshold is None:
        return f"{now_line}\nAlerts disabled. Set one with `!tokenalert <gold>`."
    state = "armed (waiting to cross)" if watch.last_alert is None else f"alerted at {format_gold(watch.last_alert)}"
    return f"{now_line}\nThreshold: {format_gold(watch.threshold)} — {state}."
```

- [ ] **Step 4: Run tests and lint**

Run: `python -m pytest tests/test_tokenwatch.py -v`
Expected: PASS — 25 tests

Run: `python -m ruff check tokenwatch.py tests/test_tokenwatch.py`
Expected: no findings (`format_gold` is now used, so `F401` is clear)

- [ ] **Step 5: Commit**

```bash
git add tokenwatch.py tests/test_tokenwatch.py
git commit -m "feat: add token alert parsing and formatting"
```

---

### Task 5: Background poll task

**Files:**
- Modify: `bot.py` — import (line ~22), constant (line ~86), state load in `setup_hook` (line ~1301), new task method (after `price_watch_check`, line ~900), task start in `setup_hook` (line ~1328)

**Interfaces:**
- Consumes: `BlizzardClient.token_price` (Task 1); `TokenWatch`, `evaluate`, `should_rearm`, `format_alert` (Tasks 2–4).
- Produces: `self.token_watch` on the client, used by Task 6's commands.

There is no `tests/test_bot.py` in this repo — `bot.py` is the untested flow layer by convention. Verification is compile + full suite + lint.

- [ ] **Step 1: Add the import and constant**

In `bot.py`, add to the first-party import block (alphabetical — between `snipelist` and `undermine`):

```python
import snipelist as snipelist_mod
import tokenwatch
import undermine
```

Below `SNIPE_SWEEP_MINUTES = 30` (line ~86) add:

```python
# Blizzard refreshes the WoW Token price roughly every 20 minutes.
TOKEN_POLL_MINUTES = 20
```

- [ ] **Step 2: Load state in setup_hook**

In `setup_hook`, directly after `self.snipelist = snipelist_mod.Snipelist.load()` (line ~1301):

```python
        self.token_watch = tokenwatch.TokenWatch.load()
```

- [ ] **Step 3: Add the task method**

In `bot.py`, insert directly after the `price_watch_check` method ends (after `self.watchlist.save()`, line ~901) and before `@tasks.loop(minutes=SNIPE_SWEEP_MINUTES)`:

```python
    @tasks.loop(minutes=TOKEN_POLL_MINUTES)
    async def token_watch_check(self):
        """Poll the WoW Token price; DM the banker when it rises past their sell threshold."""
        if not self.is_ready():
            logger.warning("token_watch_check: Bot not ready yet, skipping this iteration")
            return
        if self.token_watch.threshold is None:
            return

        try:
            async with aiohttp.ClientSession() as session:
                result = await self.blizzard.token_price(session)
            if result is None:
                logger.warning("token_watch_check: token payload carried no price, skipping")
                return
            price, _updated = result

            # Price fell back under the threshold: reset the ratchet so the next
            # crossing alerts again. Guarded so a quiet sub-threshold price does
            # not rewrite the state file every 20 minutes.
            if tokenwatch.should_rearm(self.token_watch, price):
                self.token_watch.last_alert = None
                self.token_watch.save()
                return

            report = tokenwatch.evaluate(self.token_watch, price)
            if report is None:
                return

            banker = await self.fetch_user(BANKER_ID)
            try:
                await banker.send(
                    tokenwatch.format_alert(report, self.token_watch.threshold, self.token_watch.last_alert)
                )
            except discord.HTTPException:
                logger.warning("token_watch_check: could not DM banker; not advancing the ratchet")
                return

            # Commit the ratchet only after the DM actually landed, so a Discord
            # failure cannot silently swallow an alert the owner never saw.
            self.token_watch.last_alert = report
            self.token_watch.save()
        except Exception as exc:  # noqa: BLE001 - a bad poll must not kill the loop
            logger.warning("token_watch_check failed: %s", exc)
```

- [ ] **Step 4: Start the task in setup_hook**

After the auction-snipe start block (line ~1340):

```python
        # Start 20-minute WoW Token sell-signal poll
        if not self.token_watch_check.is_running():
            self.token_watch_check.start()
```

- [ ] **Step 5: Verify compile, tests, and lint**

Run: `python -m py_compile bot.py`
Expected: no output (success)

Run: `python -m pytest -q`
Expected: PASS — the whole suite, no regressions

Run: `python -m ruff check bot.py tokenwatch.py`
Expected: no findings

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "feat: poll WoW Token price and DM banker on sell signal"
```

---

### Task 6: `!token` and `!tokenalert` commands

**Files:**
- Modify: `bot.py` — new handlers in `on_message` (after the `!watches` handler, line ~1500), and the `!help` embed (line ~1609)

**Interfaces:**
- Consumes: `self.token_watch` (Task 5); `parse_threshold`, `format_status` (Task 4); `BlizzardClient.token_price` (Task 1).
- Produces: nothing consumed by later tasks.

Gating matches `!watch` exactly: `message.author.id == BANKER_ID`, with in-guild messages deleted so the owner's thresholds are not broadcast to the channel.

- [ ] **Step 1: Add the command handlers**

In `on_message`, after the `!watches` handler block ends and before the next command, insert:

```python
        if message.content.startswith("!tokenalert") and message.author.id == BANKER_ID:
            if message.guild is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
            parts = message.content.split(maxsplit=1)
            if len(parts) < 2:
                await message.author.send(
                    "Usage: `!tokenalert <gold>` (e.g. `!tokenalert 300000`) or `!tokenalert off`."
                )
                return
            try:
                threshold = tokenwatch.parse_threshold(parts[1])
            except ValueError:
                await message.author.send(
                    f"`{parts[1].strip()}` isn't a valid gold amount. "
                    "Use `!tokenalert 300000`, `!tokenalert 300,000`, or `!tokenalert off`."
                )
                return
            # Setting or clearing a threshold always re-arms, so a new threshold never
            # inherits a stale ratchet position from the previous one.
            self.token_watch.threshold = threshold
            self.token_watch.last_alert = None
            self.token_watch.save()
            if threshold is None:
                await message.author.send("🔕 WoW Token alerts disabled.")
            else:
                await message.author.send(
                    f"💰 WoW Token alert set: I'll DM you when the price rises above "
                    f"**{watchlist.format_gold(threshold)}**."
                )
            return

        if message.content == "!token" and message.author.id == BANKER_ID:
            if message.guild is not None:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass
            price = None
            try:
                async with aiohttp.ClientSession() as session:
                    result = await self.blizzard.token_price(session)
                if result is not None:
                    price = result[0]
            except Exception as exc:  # noqa: BLE001 - report state even if the API is down
                logger.warning("!token: could not fetch token price: %s", exc)
            await message.author.send(tokenwatch.format_status(self.token_watch, price))
            return
```

Order matters: the `!tokenalert` check must come **before** the `!token` check, but since `!token` uses `==` and `!tokenalert` uses `startswith`, they cannot collide either way. `watchlist` is already imported in `bot.py`.

- [ ] **Step 2: Add the commands to !help**

In the `!help` embed, add a field directly after the existing "💰 Price Watch — region commodities (banker)" field:

```python
            embed.add_field(
                name="🪙 WoW Token — sell signal (banker)",
                value=(
                    "`!token` — current token price and your alert state\n"
                    "`!tokenalert <gold>` — DM me when the price rises above this\n"
                    "`!tokenalert off` — disable token alerts"
                ),
                inline=False,
            )
```

- [ ] **Step 3: Verify compile, tests, and lint**

Run: `python -m py_compile bot.py`
Expected: no output (success)

Run: `python -m pytest -q`
Expected: PASS — no regressions

Run: `python -m ruff check bot.py`
Expected: no findings

- [ ] **Step 4: Commit**

```bash
git add bot.py
git commit -m "feat: add !token and !tokenalert commands"
```

---

### Task 7: Documentation

**Files:**
- Modify: `CLAUDE.md` — file map, a new feature section, and the command table

**Interfaces:**
- Consumes: everything above. Produces: nothing.

Per the Global Constraints, `version.txt` and `CHANGELOG.md` stay untouched.

- [ ] **Step 1: Add tokenwatch.py to the file map**

In the File Map table, after the `snipelist.py` row:

```markdown
| `tokenwatch.py` | `TokenWatch` — WoW Token sell-threshold state, ratchet detection, formatters |
```

- [ ] **Step 2: Add the feature section**

After the "Auction Sniper (Blizzard)" section, add:

```markdown
### WoW Token Sell Alert (Blizzard)
Owner-only feature gated to `BANKER_ID` — tracks the region's WoW Token price via Blizzard's
`/data/wow/token/index` endpoint (region from `BLIZZ_REGION`, reusing the existing
`BLIZZ_CLIENT_ID`/`BLIZZ_CLIENT_SECRET` credentials; no new env vars).
- `token_watch_check` — a 20-minute background task (Blizzard refreshes the price about that
  often) that fetches the current price and evaluates a **fixed-threshold sell signal**.
- **Ratchet** — fires on the first crossing above the owner's threshold, then only on new highs
  at least `STEP_COPPER` (10,000g) above the last alerted price. Because `last_alert` only moves
  upward while above the threshold, a dip-and-reclimb stays silent until it beats the last
  reported high. Falling back below the threshold re-arms it.
- **No price history and no quiet hours** — deliberate scope decisions. The endpoint returns only
  a current price, the fixed threshold needs no baseline, and the owner wants alerts at any hour.
- **DM-before-commit** — `last_alert` is persisted only after the DM is delivered, so a Discord
  failure cannot silently swallow an alert.
- State persisted to `token_watch.json` (two fields: `threshold`, `last_alert`).
- Commands: `!token`, `!tokenalert <gold>`, `!tokenalert off` — DM or key-channel, banker-only.
```

- [ ] **Step 3: Add the commands to the command table**

After the `!watches` row:

```markdown
| `!token` | KEY/DM | Banker | Show current WoW Token price and alert state |
| `!tokenalert <gold>` | KEY/DM | Banker | DM when the token price rises above this (`off` to disable) |
```

- [ ] **Step 4: Verify no forbidden files changed**

Run: `git status --short`
Expected: `CLAUDE.md` modified. `version.txt`, `practice.py`, and `test.py` must still be listed as unstaged/untracked and must NOT be staged.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document the WoW Token sell alert"
```

---

## Final Verification

- [ ] Run the full suite: `python -m pytest -q` — all pass
- [ ] Lint: `python -m ruff check .` — no findings in `tokenwatch.py` or `bot.py`
- [ ] Confirm `git log --oneline main..HEAD` shows the spec commit plus 7 feature commits
- [ ] Confirm `git status --short` still shows `version.txt` modified and `practice.py` / `test.py` untracked — unrelated in-flight work, never committed
