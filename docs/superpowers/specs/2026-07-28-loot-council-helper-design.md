# Loot Council Helper — Design

**Date:** 2026-07-28 (amended 2026-07-29: prior-art research + analysis tiers)
**Status:** Approved design → ready for implementation plan

## Summary

A standalone, object-oriented CLI tool that helps a raid loot council decide who
should get a piece of gear when it drops. When invoked with a dropped item's ID,
it finds every raider for whom that item is an upgrade (from **WoWAudit**
droptimizer wishlists), scores each candidate with a single **weighted number**
that blends *how big the upgrade is* with *how good a player they are* (from
**Warcraft Logs**), and prints two tables: the **loot ranking** (the weighted
recommendation) and a **standalone performance ranking**.

The core is packaged so `from lootcouncil import LootRanker` works — a thin CLI
now, importable into the Discord bot later.

Player performance is **role-aware**: DPS lean on parse percentile; healers and
tanks de-emphasize throughput parse (which understates them) in favor of
reliability metrics (deaths, dispels, interrupts, avoidable damage). Every score
shows its **component breakdown** so the council isn't trusting a black box.

## Scope

- **v1 (this spec):** upgrade lookup (WoWAudit) + role-aware performance from
  **Tier-1, boss-agnostic** Warcraft Logs metrics (parse %, deaths/death-order,
  interrupts, dispels, avoidable/total damage taken, survivability index).
- **v2 (future, not this spec):** **Tier-2 per-boss mechanics** — a curated table
  of avoidable ability IDs per boss turns "avoidable damage" into "failed mechanic
  X N times." `PerformanceAnalyzer` is structured so this drops in as a data file
  without reworking the scoring. The table's schema and how we source it are in
  **Prior art & the analysis tiers** below.
- **Explicitly not doing (Tier 3):** WoWAnalyzer-style per-spec rotation analysis
  (hundreds of thousands of LOC across ~38 specs, retuned every patch, no
  queryable API); Raider.io signals (dropped by request). Note this rejects
  WoWAnalyzer's *rotation engine*, not its *raid ability data* — see below.

## Goals

- `loot.py <itemId> [difficulty]` → ranked recommendation among everyone it's an
  upgrade for.
- Single weighted score tuned so that **when several candidates all have a big
  upgrade, performance decides** (the primary use case).
- Fair to healers/tanks (don't rank them on throughput parse alone).
- Transparent: show the component metrics behind every score.
- OO core importable into the bot with no rewrite.

## Non-Goals (YAGNI)

- No auto-assigning loot / no writes anywhere — advisory output only.
- No Discord integration in v1 (CLI only; the core is import-ready).
- No cross-role comparison — ranked **within role** (loot contention is
  naturally same-role/armor).
- No per-boss mechanics in v1 (that's v2).

## Verified during design (live spike, 2026-07-28)

- **WoWAudit:** base `https://wowaudit.com/v1/`, auth header `Authorization: <key>`
  (`wow_audit_api_key`). `/v1/characters` (roster: id, name, realm, class, role,
  rank, blizzard_id), `/v1/wishlists`
  (`characters[].instances[].difficulties[].wishlist.encounters[].items[]` with
  per-spec upgrade %), `/v1/period` (current season), `/v1/team`. **Wishlists are
  currently empty** (end of season — nobody is re-simming); they populate when
  raiders upload Raidbots droptimizers next season. The schema is confirmed; live
  upgrade *values* validate once sims exist.
- **Warcraft Logs (v2 GraphQL):** OAuth client-credentials
  (`WARCRAFT_LOGS_API_CLIENT_ID/SECRET`) at
  `https://www.warcraftlogs.com/oauth/token`; API at
  `https://www.warcraftlogs.com/api/v2/client`; **3,600 points/hour**.
  - `characterData.character(name, serverSlug, serverRegion).zoneRankings` →
    per-encounter `rankings[].rankPercent` (the parse), `metric` auto-selects
    `dps`/`hps` by spec. **serverSlug** = `realm.lower().replace("'","").replace(" ","")`
    (e.g. `Mal'Ganis` → `malganis`).
  - `characterData.character(...).recentReports { data { code zone{name} startTime } }`
    → the guild's raid report codes.
  - `reportData.report(code).fights(killType:Encounters)` → `id, name,
    encounterID, difficulty, kill`.
  - `reportData.report(code).table(fightIDs:[...], dataType:X)` for **Deaths**
    (entries with `name, type, timestamp, killingBlow` → death order),
    **DamageTaken** (entries with `total, totalReduced, tmi` → avoidable-damage
    proxy + survivability index), **Interrupts** / **Dispels** (per-player
    `details`). All confirmed returning real entries.

## Prior art & the analysis tiers

*(Research re-verified live 2026-07-29 against the actual repos — the original
2026-07-28 notes were lost before being written down, so treat this as the
authoritative version. Where it differs from memory, trust this.)*

We scoped the mechanics work against the two existing open-source projects that
already solved "read a WCL log, say who failed what," to reuse rather than
reinvent. The conclusion: **reuse Wipefest's data *shape* and WoWAnalyzer's
*ability IDs*; reuse neither project's code.** That splits the work into three
tiers, of which we build Tier 1 now and Tier 2 later.

### Tier 1 — boss-agnostic metrics (v1, this spec)

Everything derivable from WCL tables without knowing anything about the boss:
parse %, deaths + death order, interrupts, dispels, total damage taken, `tmi`.
No external data dependency, works on any encounter the moment it's logged,
including a brand-new tier where nobody has curated anything yet. This is why
v1 ships standalone and never blocks on the tier being solved.

### Tier 2 — per-boss avoidable mechanics (v2)

Turn "took a lot of damage" into "stood in Fel Pool 6 times." This needs one
fact per boss: **which ability IDs are avoidable player damage.** Two sources
exist for that fact:

**Wipefest — the right schema, dead data.** Wipefest drove its entire timeline
off community-authored JSON in a separate `Wipefest.EventConfigs` repo, consumed
at runtime from raw GitHub by `Wipefest.Core`. The original
`JoshYaxley/Wipefest.EventConfigs` is now **404**; a surviving public copy is
[`JoshYaxley2/Wipefest.EventConfigs`](https://github.com/JoshYaxley2/Wipefest.EventConfigs)
(33 commits, **last touched April 2018**, no LICENSE file, no README), mirrored
by a `yajinni` fork. It covers **only Tomb of Sargeras and Antorus** — Legion,
nine years stale — and the Antorus configs are mostly empty stubs (`[]`). So
**the data is unusable and the project is abandoned; the schema is excellent.**

`index.json` maps encounters to their config files, keyed by **WCL
`encounterID`** — the exact field our `report_fights()` already returns, so it
joins to our pipeline with no translation:

```json
{ "zone": "Tomb of Sargeras", "id": 2032, "name": "Goroth",
  "includes": ["tomb-of-sargeras/goroth/abilities",
               "tomb-of-sargeras/goroth/damage", ...] }
```

and each per-boss file is a flat list of tagged, filtered event definitions.
`damage.json` for Goroth is *precisely* the Tier-2 table we need:

```json
[ { "name": "Fel Pool", "tags": ["player", "damage"], "show": false,
    "eventType": "damage", "friendly": true,
    "filter": { "types": ["damage", "absorb"], "ability": { "id": 230348 } } } ]
```

Per-boss files are split by category — `abilities`, `damage`, `debuffs`,
`buffs`, `phases`, `spawns` — with `friendly: true` marking damage taken *by
players*. **Adopt this shape** (`encounterID` → list of `{name, ability_id,
event_types}`) for our own hand-curated table; it's already proven to express
what we need, and it degrades cleanly (a boss with no entry simply falls back to
the Tier-1 proxy).

**WoWAnalyzer — live data, wrong license for code.** Actively maintained through
the current tier ([`src/game/raids/`](https://github.com/WoWAnalyzer/WoWAnalyzer/tree/midnight/src/game/raids)
on the `midnight` branch; current raid `sporefall`). Each tier's `index.ts`
exports bosses with their **WCL encounter id** plus categorized ability IDs —
e.g. Rotmire (id `3159`) lists casts `Bursting Pustules` (`1221787`, annotated
"ramping raid damage"), `Awaken Fungi` (`1221622`), `Fungal Bloom` (`1221637`)
and player debuffs `Festering Vines` (`1222088`), the two fixates. That is a
current, curated, free list of the exact ability IDs a per-boss table needs, and
it's updated every patch by people who play the tier.

**Licensing — the constraint that shapes the plan.** WoWAnalyzer is
**AGPL-3.0** (confirmed at its `LICENSE`); `Wipefest.Core` is likewise AGPL-3.0.
AGPL is viral over *derived works of the code*. So:

- **Do not** vendor, port, or translate either codebase into `lootcouncil/`.
- **Do** use them as *references* to hand-author our own data file. Individual
  ability IDs and boss encounter IDs are facts about the game, not expressive
  code, and a schema is not copyrightable. Citing where we sourced each tier's
  IDs is honest and costs nothing.
- If we ever want their code, the tool would have to go AGPL too — not a
  decision to make implicitly.

This also means the "per-boss README the user writes when the raid releases"
(the original v2 plan) gets much cheaper: seed the table from WoWAnalyzer's
current-tier ability list, then hand-tune which of those are genuinely
*avoidable* — a review pass, not authorship from scratch.

### Tier 3 — per-spec rotation analysis (rejected)

"Did this player press the right buttons?" — WoWAnalyzer's actual core. Hundreds
of thousands of LOC across ~38 specs, retuned every patch, with no queryable API
(it's a browser app; the server repo is explicitly just auth + WCL/Blizzard
proxies and contains no analysis code). Reimplementing is out of the question and
scraping it isn't offered. **Permanently out of scope** — not deferred.

### Why this doesn't change the v1 build

Tier 2 slots into the single avoidable-damage component already defined in the
performance model, keyed by `encounterID`. v1 ships the Tier-1 proxy in that
slot; v2 swaps in the curated table behind the same interface. No scoring math,
no role-weight table, and no client changes are required — which is the whole
reason for building v1 first.

## Configuration

Environment variables (in `.env`, already present):

| Var | Purpose |
|-----|---------|
| `wow_audit_api_key` | WoWAudit API key (`Authorization` header) |
| `WARCRAFT_LOGS_API_CLIENT_ID` / `WARCRAFT_LOGS_API_CLIENT_SECRET` | WCL OAuth client credentials |

Tool config (a `lootcouncil/config.py` dataclass or a small `loot_config.json`,
loaded once):

| Setting | Meaning | Default |
|---------|---------|---------|
| `guild_name` / `guild_server_slug` / `guild_region` | Identify the guild's WCL reports | *(required; from the guild's WCL page)* |
| `season_zone_id` | WCL zone ID for the raid tier being analyzed | *(required per tier)* |
| `difficulty` | WCL difficulty to analyze (5 = Mythic, 4 = Heroic) | `5` |
| `weight_upgrade` / `weight_performance` | Weighted-score blend | `0.6` / `0.4` |
| `report_lookback_days` | How far back to aggregate reports | `60` |
| `cache_ttl_hours` | How long the aggregated WCL cache stays fresh | `24` |
| `role_weights` | Per-role metric weights (see Performance model) | *(table below)* |

If guild lookup by name/slug is unreliable, reports are also collectible as the
**union of the roster's `recentReports`** — the guild's raid logs — as a fallback.

## Architecture

A small package `lootcouncil/`, plus a root CLI entry `loot.py`. Each class has
one responsibility and is independently testable; the pure scoring logic is
separated from the two API clients so it can be unit-tested without network.

```
lootcouncil/
  __init__.py        # exports LootRanker, PerformanceAnalyzer, config
  config.py          # Config dataclass + load()
  wowaudit.py        # WowAuditClient
  warcraftlogs.py    # WarcraftLogsClient (OAuth + GraphQL)
  performance.py     # PerformanceAnalyzer + pure role-aware scoring
  ranker.py          # LootRanker + pure weighting/normalization
loot.py              # thin CLI
tests/test_lootcouncil.py
```

### `wowaudit.py` — `WowAuditClient`

- `roster() -> list[Character]` — `/v1/characters` (name, realm, role, class,
  rank, blizzard_id).
- `wishlists() -> Wishlists` — `/v1/wishlists`, parsed into a lookup:
  `upgrades_for(item_id, difficulty) -> dict[character_key, UpgradeInfo]` where
  `UpgradeInfo = {percentage, absolute, spec}`. Empty when no sims uploaded.
- `current_season() -> Season` — `/v1/period`.
- Sync HTTP via `requests` (the tool is a CLI, not the async bot); `Authorization`
  header. Non-200 raises a clear error.

### `warcraftlogs.py` — `WarcraftLogsClient`

- `ensure_token()` — client-credentials token, cached in memory (tokens last
  ~1yr; re-mint on 401).
- `character_parses(name, server_slug, region, zone_id) -> ParseData` — from
  `zoneRankings`: per-encounter `rankPercent`, `metric`, `bestPerformanceAverage`,
  `medianPerformanceAverage`.
- `guild_reports(cfg) -> list[ReportRef]` — report codes for the tier within
  `report_lookback_days` (via guild reports, or roster `recentReports` fallback).
- `report_fights(code, difficulty) -> list[Fight]`.
- `report_table(code, fight_ids, data_type) -> list[dict]` — Deaths, DamageTaken,
  Interrupts, Dispels.
- A `graphql(query)` helper; respects the 3,600 pt/hr budget (the aggregation is
  cached, so day-to-day loot lookups make **zero** WCL calls).

### `performance.py` — `PerformanceAnalyzer`

- `aggregate_season(cfg) -> dict[character_key, RawMetrics]` — walks the tier's
  reports/fights once and accumulates per character: parse average (from
  `zoneRankings`), deaths, **death-order rank** (from Deaths timestamps within
  each fight), interrupts, dispels, avoidable/total damage taken, survivability
  (`tmi`), fights participated. **Cached** to `lootcouncil_cache.json` with a
  timestamp; refreshed when older than `cache_ttl_hours` or on `--refresh`.
- `score(raw: RawMetrics, role: str) -> PerformanceScore` — **pure**, role-aware
  (below). Returns a 0–1 score plus the component breakdown.

### `ranker.py` — `LootRanker`

- `rank(item_id, difficulty) -> LootResult` — pulls candidates from
  `WowAuditClient.upgrades_for`, their `PerformanceScore`s, computes the weighted
  loot score, returns both rankings.
- Pure helpers: `normalize_upgrades(candidates)` (within-set 0–1),
  `weighted_score(upgrade_norm, performance, weights)`.

### `loot.py` — CLI

- `python loot.py <itemId> [--difficulty 5] [--refresh] [--weights 0.6,0.4]`
- Prints the **loot ranking** table and the **performance ranking** table, with
  component breakdowns. Read-only.

## Performance model (role-aware)

Each role's score is a weighted blend of normalized component metrics (all 0–1,
higher = better; "bad" metrics like deaths are inverted). Ranked **within role**.

| Component (per fight, season-averaged) | DPS | Healer | Tank |
|---|---|---|---|
| Parse percentile (`rankPercent`, spec-normalized) | **0.60** | 0.20 | 0.15 |
| Death-avoidance (own deaths + first-two-to-die rate, inverted) | 0.20 | 0.30 | 0.25 |
| Avoidable/total damage taken, normalized by role (inverted) | 0.20 | 0.25 | 0.30 |
| Interrupts + dispels (utility, per fight) | — | 0.25 | 0.10 |
| Survivability (`tmi`, inverted) | — | — | 0.20 |

*(Default `role_weights` — tunable in config. Rows sum to 1.0 per role. DPS lean
on parse; healers/tanks lean on reliability + utility because throughput parse
understates them.)*

- **Death-order / first-to-die:** within each fight, sort the Deaths table by
  `timestamp`; a player in the first two deaths gets a penalty for that fight;
  averaged across fights → the death-avoidance component. (This is the
  "one of the first two to die in a pull" metric.)
- **Avoidable damage (v1 proxy):** total damage taken per active time, normalized
  within role across the raid (tanks *should* take damage, so it's role-relative).
  **v2** replaces this with per-boss avoidable-ability hit counts, from a
  hand-curated `encounterID → ability IDs` table in the Wipefest config shape,
  seeded from WoWAnalyzer's current-tier ability lists (see **Prior art & the
  analysis tiers**). Bosses absent from the table fall back to this proxy.
- **Metric auto-selection:** WCL `zoneRankings.metric` already returns `hps` for
  healers and `dps` for DPS, so parse comparison is same-spec by construction.
- **Missing data:** a candidate with too few fights (< `min_fights`, default 3)
  is scored on what exists and **flagged low-confidence** in the output rather
  than silently ranked.

## Weighted loot score

```
upgrade_norm = candidate.upgrade_pct / max(candidate upgrade_pct in the set)   # 0..1 within candidates
loot_score   = weight_upgrade · upgrade_norm  +  weight_performance · performance_score
```

Normalizing the upgrade **within the candidate set** is what makes performance
the decider when several candidates all have a big upgrade (their `upgrade_norm`
all compress toward 1.0). Defaults `0.6 / 0.4`; the weights are the single tuning
knob and are surfaced in the output header.

## Outputs

Two tables to stdout (and returned as structured objects for the bot later):

1. **Loot ranking** — sorted by `loot_score`: rank, character, spec, upgrade %,
   performance score, final score, low-confidence flag.
2. **Performance ranking** — sorted by `performance_score` **within role**: the
   component breakdown (parse, deaths/first-to-die, avoidable dmg, utility,
   survivability) so the council can see *why*.

## Caching & cost

- `PerformanceAnalyzer.aggregate_season` is the only expensive step; it caches the
  per-character aggregates to `lootcouncil_cache.json` (gitignored). A normal loot
  lookup reads the cache → **no WCL calls**, instant. `--refresh` (or cache older
  than `cache_ttl_hours`) re-pulls. Well within 3,600 pts/hr.
- WoWAudit calls are cheap (roster + wishlists per run).

## Testing

`tests/test_lootcouncil.py` — pure logic, no network (API clients mocked/omitted):

- `normalize_upgrades`: within-set normalization; ties; single candidate.
- `weighted_score`: blend math; the "big upgrade for several → performance
  decides" property (equal high upgrades → ranking follows performance).
- `PerformanceAnalyzer.score`: role-aware weighting per role; inverted metrics
  (more deaths → lower score); low-confidence flag under `min_fights`.
- Death-order: from a synthetic Deaths table (timestamps), first-two flagged
  correctly per fight and averaged.
- Wishlist parsing: `upgrades_for(item_id, difficulty)` extracts the right
  per-character upgrade % from a captured `/v1/wishlists` shape (incl. the empty
  case → no candidates).
- WCL parse extraction: `rankPercent`/`metric` pulled from a captured
  `zoneRankings` shape.

*(API clients are thin HTTP wrappers, integration-verified via the design spike;
the scoring/parse logic they feed is what's unit-tested.)*

## Documentation

On completion: a `lootcouncil/README.md` (setup: the env vars, config, how to run,
how to read the tables), and note the tool in the repo `README.md`. Not part of the
bot's `CHANGELOG.md`/version (separate tool), unless/until it's folded into the bot.

## Known data risk

WoWAudit wishlists were empty during the design spike (end of season — nobody was
re-simming). The **path** to wishlist items is confirmed
(`characters[].instances[].difficulties[].wishlist.encounters[].items[]`), but the
**leaf field names** for upgrade values (`percentage`, `absolute`, `spec` vs. a
`specs[]` list) could not be confirmed. `_parse_wishlists()` in `lootcouncil/wowaudit.py`
tolerates two shapes for this reason.

**Deferred live-verification:** Once raiders upload droptimizers next season, run:

```bash
python -c "from lootcouncil.config import Config; from lootcouncil.wowaudit import WowAuditClient; import json; print(json.dumps(WowAuditClient(Config.load()).wishlists(), default=str)[:2000])"
```

and confirm the real leaf fields match `_best_upgrade`'s two tolerated shapes. If they
do not, `_best_upgrade` is the only function that needs changing.
