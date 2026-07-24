# Blizzard Auction Sniper — Design

**Date:** 2026-07-23
**Status:** Approved design → ready for implementation plan

## Summary

Add a **server-specific auction sniper** to the Mythic+ bot, usable by **any
guild member**. Unlike the existing Undermine price-watch (region-wide
**commodities**: consumables and mats that share one price across the whole
region), this feature tracks **non-commodity, per-realm items** — recipes, battle
pets, mounts, gear — whose price varies realm to realm.

The bot polls Blizzard's first-party **WoW Game Data Auction House API** every
**30 minutes**, sweeping **all connected realms in the region** (83 in US as of
this writing), and **DMs every member subscribed to a snipe** when that item's
cheapest listing **on any realm** drops **below the target price they set** —
reporting *which realm* and *at what price*.

Snipes are **open to all guild members** and stored as **one record per item**
with a **subscriber list**: if several members snipe the same item, there's a
single record and each of them is DM'd (at their own target price). Removing a
snipe removes only the caller from the subscriber list; the record lives until
its last subscriber leaves. **Recipes additionally CC the `BANKER_ID`** on every
alert — the banker is often the one who actually buys them. The sweep is shared —
every realm is fetched once per cycle and filtered against the deduped union of
all subscribed items — so opening it to the guild adds no extra API load.

The signal is a **simple absolute target price** per item, not the percentile
band used for commodities. Blizzard only exposes a *current snapshot* per realm
(no per-realm price history to build a band from), and the goal is different:
"is this available *anywhere* below the price I'm willing to pay?"

This is a **parallel system** to the Undermine watcher — different data source,
different signal, its own client, state file, and command set. The two do not
share code beyond incidental helpers (e.g. `format_gold`).

## Why Blizzard (not Saddlebag / not Undermine)

- **Undermine** is region-wide commodities only — it structurally cannot price a
  single realm's recipes/pets/mounts.
- **Saddlebag** offers per-realm and region-wide endpoints but is gated behind a
  Discord-issued consent string and nudges paid tiers (~$10–20/mo).
- **Blizzard** is the authoritative source (Saddlebag/Undermine/TSM all consume
  it), self-serve OAuth, free. Verified live during design (see below).

### Verified during design (live spike, 2026-07-23)

- OAuth **client-credentials** flow works with the `BLIZZ_*` creds; token type
  `bearer`, lifetime ~24h.
- `connected-realm/index` → **83** connected realms in US.
- One realm's `/auctions` → HTTP 200, **~5.3 MB / ~28,000 auctions**, carries a
  **`Last-Modified`** header (hourly snapshot cadence; supports
  `If-Modified-Since`).
- Confirmed response shapes:
  - **Item:** `{ "id": <auctionId>, "item": { "id": <itemId> }, "buyout": <copper>, "quantity": N, "time_left": "…" }`
  - **Pet:** `item.id == 82800` carrying **`pet_species_id`** (+ `pet_breed_id`,
    `pet_level`, `pet_quality_id`). Pets are keyed by **species id**.
- Full-region sweep ≈ 83 × ~5 MB ≈ **~415 MB uncompressed** (less over gzip),
  processed one realm at a time → Pi-friendly.

## Goals

- Let **any guild member** track per-realm item IDs and battle-pet species IDs;
  DM **every subscriber** when the cheapest listing across **all** region realms
  falls below the target price *that subscriber* set.
- **One record per item, many subscribers** — sharing interest doesn't duplicate
  records or fetches.
- **Recipes also CC the banker** on every alert (the banker frequently does the
  buying).
- **Unsubscribe is per-caller** — `!unsnipe` drops only you; others keep watching
  until the last subscriber leaves and the record is deleted.
- Report the **realm, price, and quantity** of the cheapest find.
- 30-minute cadence with **conditional requests** so unchanged realms are nearly
  free (a `304` costs one small request, no download/parse).
- One ping per genuine dip, per subscriber — no every-sweep spam.
- Opening the feature to the whole guild must **not** multiply API load — the
  realm sweep is shared regardless of how many members/snipes exist.

## Non-Goals (YAGNI)

- No admin-curated global snipe list — members subscribe/unsubscribe themselves;
  there's no "add this for everyone" command.
- No commodity/region-wide support here — that's the Undermine watcher's job.
- No percentile/history-based signal — **absolute target price only**.
- No auto-buy / no in-game action — alert only.
- No cross-realm profit/flip analysis (that's AzerothAuctionAssassin's domain).
- No realm filtering in v1 — always sweep the whole region; the alert names the
  realm.
- No per-user snipe cap in v1 (trusted guild; a soft cap can come later if the
  union of watched keys ever gets large).

## Configuration

New/existing environment variables (in `.env`):

| Var | Purpose | Default |
|-----|---------|---------|
| `BLIZZ_CLIENT_ID` | Blizzard API OAuth client id | *(required, already added)* |
| `BLIZZ_CLIENT_SECRET` | Blizzard API OAuth client secret | *(required, already added)* |
| `BLIZZ_REGION` | Region for AH data (`us`/`eu`/`kr`/`tw`) | `us` |
| `BANKER_ID` | Also DM'd on **recipe** alerts (already defined; still gates the Undermine watcher) | *(required, already present)* |

The snipe **commands** are **open to all guild members** — `BANKER_ID` is *not* a
gate here. It is used only to **CC the banker on recipe alerts**.

Tunable constants (in `blizzard.py` / `snipelist.py`):

| Constant | Meaning | Default |
|----------|---------|---------|
| `SWEEP_MINUTES` | Sweep cadence | `30` |
| `TOKEN_REFRESH_SKEW_S` | Refresh the token this many seconds before expiry | `300` |
| `PET_ITEM_ID` | The AH "caged pet" item id (pets keyed by species) | `82800` |
| `REALM_FETCH_TIMEOUT_S` | Per-realm HTTP timeout | `30` |

## Architecture

Three focused, independently testable pieces, mirroring the Undermine feature's
`undermine.py` / `watchlist.py` / `bot.py` split.

### `blizzard.py` — API client

Thin async wrapper over the Blizzard Game Data AH endpoints. Owns OAuth and the
raw fetches; returns clean data structures or signals so the caller never
crashes on a bad realm.

- `async get_token() -> str` — client-credentials token minted at
  `https://oauth.battle.net/token` (HTTP Basic `client_id:client_secret`,
  `grant_type=client_credentials`). **Cached in memory** with its expiry;
  re-minted on expiry (minus `TOKEN_REFRESH_SKEW_S`) or on a `401`.
- `async list_connected_realms() -> list[int]` —
  `GET /data/wow/connected-realm/index?namespace=dynamic-{region}`; parse the
  connected-realm ids out of the `href`s.
- `async get_realm_auctions(realm_id, if_modified_since=None) -> AuctionsResult`
  — `GET /data/wow/connected-realm/{id}/auctions?namespace=dynamic-{region}`
  with an optional `If-Modified-Since` header. Returns either
  `NOT_MODIFIED`, or `(auctions: list, last_modified: str)`.
- `async item_info(item_id) -> ItemInfo` — one cached lookup
  (`/data/wow/item/{id}?namespace=static-{region}`) returning `{name,
  is_recipe}`. **`is_recipe` = the item's class is "Recipe"** (`item_class.id ==
  9` in the item payload) — this is how the sweep knows to CC the banker.
- `async pet_name(species_id) -> str` — cached name lookup
  (`/data/wow/pet/{id}?namespace=static-{region}`). Pets are never recipes.
- Both are cached in-memory for the process lifetime (item name/class don't
  change), so it's one lookup per distinct key regardless of how many members
  subscribe.
- Base host `https://{region}.api.blizzard.com`. Uses `aiohttp` (already
  bundled). Non-200 (other than 304) → raise/return cleanly so the caller skips
  that realm.

### `snipelist.py` — state + detection logic

Owns the snipes, the pure detection math, and persistence. Analogous to
`watchlist.py` but with the absolute-threshold model.

**`Snipe` dataclass — one record per item**, keyed by `(kind, key_id)`:
- `kind: str` — `"item"` | `"pet"`
- `key_id: int` — item id (for items) or **pet species id** (for pets)
- `label: str` — resolved name (from `item_info`/`pet_name`)
- `is_recipe: bool` — from `item_info`; when true, alerts also CC the banker
- `subscribers: dict[int, Subscriber]` — Discord user id → their subscription
- `banker_state: str` — `"armed"` | `"alerted"` — recipe-CC anti-spam for the
  banker (record-level, so the banker gets one CC per dip no matter how many
  members subscribe)
- `last_realm: int | None`, `last_price: int | None` — cheapest seen last cycle
  (for `!snipes` display)

**`Subscriber` dataclass** — one per member watching a record:
- `target_copper: int` — this member's own alert threshold
- `state: str` — `"armed"` | `"alerted"` (anti-spam, **per member**)
- `added_at: datetime`

So two members watching the same item share **one record** but keep **independent
targets and anti-spam state**. Re-running `!snipe` for an item you already watch
updates *your* subscriber target in place.

**`Snipelist` store:**
- In-memory `dict[tuple[str,int], Snipe]` (one entry per item).
- `subscribe(owner_id, kind, key_id, target_copper, label, is_recipe)` — create
  the record if absent, then add/replace this member's `Subscriber`.
- `unsubscribe(owner_id, kind, key_id)` — remove **only that member**; if it was
  the **last subscriber**, delete the whole record. Returns whether anything
  changed.
- `all()`, `for_owner(owner_id)` (records this member subscribes to),
  `watched_keys()` → every record's `(kind, key_id)` (already one-per-item, so
  inherently deduped — what the sweep fetches).
- `load()` / `save()` — own **`snipes.json`**, atomic temp-file write (same
  pattern as `save_state` / `watches.json`). Kept **separate from `state.json`**.
  Datetimes ISO strings; legacy-tolerant `from_dict`.

**Pure helpers (no I/O — fully unit-testable):**
- `parse_gold(text) -> int | None` — member types gold; stored as copper
  (`× 10000`). Rejects non-numeric / non-positive.
- `best_price_for(auctions, kind, key_id) -> (price, qty) | None` — scan one
  realm's auctions for a key, return the cheapest `buyout` (and its quantity).
  For pets, match `item.id == PET_ITEM_ID and item.pet_species_id == key_id`.
  Bid-only auctions (no `buyout`) are ignored.
- `evaluate(best_across_realms, subscriber) -> bool` — `best.price <
  subscriber.target_copper`.
- `apply_anti_spam(fired, state) -> (should_dm, new_state)` — armed→alerted on
  fire; silent while alerted; **re-arm to armed when the price is at/above the
  threshold or the item is absent everywhere.** Used for both a `Subscriber` and
  the record's `banker_state`.
- `format_alert(snipe, best, target_copper)` / `format_banker_alert(snipe, best,
  wanters)` — DM text (below).
- `format_snipe_line(snipe, subscriber)` — one `!snipes` list row.

### `bot.py` — wiring

- New `@tasks.loop(minutes=SWEEP_MINUTES) auction_snipe_check`, started in
  `setup_hook` alongside the other loops. `is_ready()` guard up front; whole
  sweep + each realm wrapped in `try/except` so one bad realm/token error is
  logged and never kills the loop (same discipline as `price_watch_check`).
  Saves `snipes.json` at the end.
- **Open commands** (any guild member; usable in DM or the key channel). Each
  acts on the **message author** as the subscriber:
  - `!snipe <itemId> <maxGold> [label...]` — subscribe to (or update your target
    on) an item. On first subscribe to a new item, the bot resolves the name +
    `is_recipe` via `blizzard.item_info` and creates the record.
  - `!snipepet <speciesId> <maxGold> [label...]` — subscribe to a pet.
  - `!unsnipe <id ...>` — **unsubscribe you** from one or more items/pets (by
    `key_id`). Removes only your subscription; the record survives if others
    remain, and is deleted when you were the last subscriber.
  - `!snipes` — list **the records you subscribe to**, with your target and the
    last-seen realm/price or "armed".
- Config parsing: `BLIZZ_REGION`, `BLIZZ_CLIENT_ID/SECRET` read at startup like
  the other `_require_env` values (region defaulted). `BANKER_ID` already loaded.

## Snipe Detection (the sweep)

Each 30-minute cycle:

1. `is_ready()` guard. Ensure a valid token (`blizzard.get_token`). Load the
   realm id list (fetched once, cached; refreshed daily).
   Take `keys = snipelist.watched_keys()` — the deduped union of every member's
   `(kind, key_id)`. If empty, no-op the cycle.
2. Maintain a per-process **watched-price cache**, keyed by the *shared* item
   keys (not by user): `cache[realm_id] = {(kind,key_id): (price, qty)}` plus
   that realm's `last_modified`. For each realm:
   - `get_realm_auctions(realm, if_modified_since=cache.last_modified[realm])`.
   - On **`NOT_MODIFIED`** → reuse the cached per-realm watched prices.
   - On **200** → run `best_price_for` for every key in `keys` over that realm's
     auctions, replacing that realm's cache entry, and store the new
     `last_modified`. Discard the raw auctions immediately (low peak memory).
3. **Aggregate across realms:** for each key, take the minimum `(price, qty,
   realm)` over all realms' cached entries → the cheapest listing anywhere.
   Computed **once per item**, then reused for all its subscribers.
4. For each record, using its cheapest-anywhere `best` and remembering
   `last_realm/last_price`:
   - **Per subscriber:** `fired = best and best.price < subscriber.target_copper`.
     `apply_anti_spam` on the subscriber's state → if it says DM now, **DM that
     member** with `format_alert`, set their state `alerted`. Collect the members
     who fired this cycle (the "wanters").
   - **Recipe banker CC:** if `snipe.is_recipe`, run `apply_anti_spam` on the
     record's `banker_state` with `fired = best and best.price < max(subscriber
     targets)` (i.e. the recipe is below at least one member's target). If it
     says DM now, **also DM `BANKER_ID`** with `format_banker_alert` listing the
     wanters and their targets. **Dedup:** if the banker is themselves a
     subscriber who was already DM'd this cycle, skip the CC.
   - Every DM is wrapped in `try/except discord.HTTPException` (blocked / closed
     DMs are logged, never fatal).
5. Save `snipes.json`.

**Why the per-realm watched-price cache matters:** with `If-Modified-Since`, an
unchanged realm returns `304` and we get no auctions for it that cycle — but its
listing still counts toward "cheapest anywhere." Caching only the *watched*
items' prices per realm (a few numbers, not the 5 MB dump) keeps the cross-realm
minimum correct while making the 30-minute cadence cheap: only realms that
actually refreshed get downloaded and re-parsed.

### Anti-spam (one ping per dip)

Applies identically to each `Subscriber.state` and to the record's
`banker_state`:

- On fire while `armed`: DM, flip to `alerted`.
- While `alerted`: silent, even if still (or more) below the threshold.
- Re-arm to `armed` when the cheapest price is **at/above the threshold** again
  (or the item is unlisted everywhere). The next genuine dip re-alerts.

Because each subscriber and the banker CC have their own state, a member who set
a higher target can re-arm/fire on a different schedule than one with a lower
target watching the same record.

### Alert DM contents

**To a subscriber** (`format_alert`):
- Snipe label + `item:<id>` / `pet:<species>`
- Your target (formatted `g/s/c`)
- **Cheapest found:** realm name + price (`g/s/c`) + quantity available there
- **% below your target**
- Wowhead link (`item=<id>` or battle-pet link for pets)

**To the banker on recipe alerts** (`format_banker_alert`) — same cheapest-found
info, plus **who wants it** (the subscribing members and their targets), so the
banker knows who to buy for. One CC per dip (record `banker_state`); skipped if
the banker is already a subscriber who was DM'd this cycle.

## Errors & Edge Cases

- Token expiry / `401` → re-mint once and retry the call; persistent failure is
  logged and the sweep is skipped this cycle.
- One realm's fetch fails (timeout/5xx) → logged, that realm reuses its cached
  prices if present (else contributes nothing this cycle), sweep continues.
- Snipe key not listed on any realm → no fire; `!snipes` shows "armed / not
  seen".
- Bid-only auctions (no `buyout`) → ignored (we alert on purchasable price).
- Prices are **copper** everywhere; `format_gold` for display only.
- `snipes.json` missing on first run → empty snipe list.
- Realm-list fetch failure at startup → sweep no-ops with a warning until it
  succeeds.
- Rate limits (~36k/hr): 83 realms × 2 sweeps/hr + token/index ≈ <200 req/hr —
  negligible.

## Testing

`tests/test_snipelist.py` (pure logic; Blizzard client not called):

- `parse_gold`: integer/decimal handling, reject non-numeric / non-positive.
- `best_price_for`: cheapest buyout wins; matches item by `item.id`; matches pet
  by `item.id == 82800 and pet_species_id`; ignores bid-only; empty realm.
- Cross-realm aggregation: minimum across several realms picks the right
  `(price, realm)`.
- `evaluate`: fires strictly below a subscriber's target, not at/above.
- Anti-spam: armed→alerted on entry, silent while alerted, re-arm when back
  at/above target or unlisted (exercised for both a subscriber and `banker_state`).
- **Subscriber model:**
  - `subscribe` twice for the same item = **one record, two subscribers**;
    `watched_keys()` yields that key once.
  - Two subscribers with different targets fire/re-arm **independently** (one's
    state change doesn't touch the other).
  - `unsubscribe` removes **only the caller**; record persists while others
    remain; **deleting the last subscriber removes the record**.
  - `for_owner` returns only records the member subscribes to.
- **Recipe banker CC:** a recipe record fires the banker CC once per dip (below
  the max subscriber target) via `banker_state`; a non-recipe never CCs; the CC
  is skipped when the banker is an already-alerted subscriber.
- `format_alert` / `format_banker_alert` / `format_snipe_line` contain the key
  facts (banker alert lists the wanters).
- `Snipelist` save/load round-trip (records + subscribers + `is_recipe` +
  `banker_state`, both kinds, datetimes, empty/missing file, malformed-entry
  tolerance).

`tests/test_blizzard.py` (HTTP mocked; parse from a **captured sample** saved
from the design spike):

- Token parse + in-memory expiry/refresh logic.
- `connected-realm/index` → realm id extraction from `href`s.
- Auctions parse: item and pet shapes; `NOT_MODIFIED` path returns the sentinel.
- `item_info`: `is_recipe` true when `item_class.id == 9`, false otherwise;
  result cached (one HTTP call for repeated lookups).

## Deployment notes (Raspberry Pi)

- Per-realm processing keeps peak memory to ~one realm's auctions (~5 MB / ~28k
  rows) at a time; discard after filtering.
- Bandwidth: first sweep pulls the whole region (~a few hundred MB over gzip);
  subsequent 30-min sweeps only download realms that changed (`304` otherwise).
  **Do not run on a metered connection.**
- Sequential realm fetches are simplest and safe; optional light concurrency can
  come later if latency matters.

## Documentation

Per project convention, update on completion:
- `CLAUDE.md` — file map (`blizzard.py`, `snipelist.py`, `snipes.json`), the
  auction-sniper workflow, new commands, new env vars.
- `README.md` — feature overview + setup (Blizzard client registration, env
  vars, the "region hardcoded, realm auto-swept" note).
- `CHANGELOG.md` — new version entry under `### Improvements`.
- `version.txt` — bumped by the bot on next startup (never edited manually).
