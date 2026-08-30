# Availability Forecasting — Phase 2a (Predictor + Dry-Run Preview) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the forecaster (`forecast.py`) — predictor + roster/slot optimizer — and a fully-automated weekly task that gates on green-pool feasibility and DMs a dry-run "would-schedule" preview to `BANKER_ID`. No team DMs, nothing created (that's 2b).

**Architecture:** `forecast.py` is pure, side-effect-free logic over `eventlog` events + the `raiders` dict (for timezones), fully unit-testable with synthetic data. `bot.py` adds one `@tasks.loop` weekly task that calls it and sends one preview DM.

**Tech Stack:** Python 3.9+, discord.py v2+, pytest, `zoneinfo`. Reads Phase 1a/1b `eventlog`.

## Global Constraints

- Python 3.9+; datetimes tz-aware UTC internally; local slots via `astimezone(tz)`, `block = local_hour // 2` (0–11), `weekday()` 0=Mon..6=Sun.
- Signals & signs: `run_completed` (expand roster per member's tz) / `offer_accepted` / `raiderio_run` → **+1**; `offer_declined` → **−1**. `avail_reaction` is NOT used by the predictor in 2a (no per-slot meaning). **Equal source weight.**
- Score: `P = (W⁺ + ALPHA*prior) / (W⁺ + W⁻ + ALPHA)`, weight `w(age)=0.5**(age_weeks/HALF_LIFE)`. Constants in `forecast.py`: `HALF_LIFE=4.0`, `ALPHA=2.0`, `BASE_PRIOR=0.15`.
- `prior` = person's recency-weighted positive fraction over same-weekday obs, else all obs, else `BASE_PRIOR`.
- Team = 1 tank + 1 healer + 3 dps, distinct raiders, multi-role aware; slot "confidence" = team **mean** probability.
- Feasibility gate: proceed only if a role-valid team can form from `self.availability[GREEN]` (real users; bot seed reactions never create raiders).
- Trigger: `@tasks.loop(time=time(hour=12, minute=0, tzinfo=_CST))` + `weekday() != 2` (Wednesday) guard — mirrors `weekly_avail_reset` (which uses Tuesday=1).
- Preview → `BANKER_ID` DM, best-effort (`discord.HTTPException`). Dry-run only.
- Logger `logging.getLogger("discord")`.

## File Structure

| File | Responsibility |
|------|----------------|
| `forecast.py` (create) | `Obs`/`Team` models, constants, `observations`, `predict`, `select_team`, `can_field_team`, `rank_slots`, `format_preview`. |
| `tests/test_forecast.py` (create) | Predictor, optimizer, gate, formatting tests (synthetic). |
| `bot.py` (modify) | `import forecast`; weekly `forecast_preview` task + BANKER_ID DM; packaging. |
| `pyproject.toml`, `Dockerfile` (modify) | Package `forecast`. |
| `CLAUDE.md`, `README.md`, `CHANGELOG.md` (modify) | Docs. |

---

## Task 1: observations() — normalize events to signed per-slot obs

**Files:**
- Create: `forecast.py`
- Test: `tests/test_forecast.py`

**Interfaces:**
- Produces: `Obs(user_id:int, weekday:int, block:int, age_weeks:float, sign:int)` frozen dataclass; `observations(events: List[dict], raiders: dict, now: datetime) -> List[Obs]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_forecast.py`:

```python
"""Tests for the availability forecaster."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from forecast import Obs, observations

CST = ZoneInfo("America/Chicago")
NOW = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)


def _raider(uid, roles, tz="America/Chicago"):
    return SimpleNamespace(user_id=uid, roles=roles, timezone=ZoneInfo(tz), name=f"R{uid}")


def test_observations_single_user_events_use_stored_slot():
    events = [
        {"type": "offer_accepted", "user_id": 1, "ts_utc": "2026-07-02T01:00:00+00:00",
         "local_weekday": 2, "local_block": 9, "source": "discord", "run_id": 1},
        {"type": "offer_declined", "user_id": 1, "ts_utc": "2026-07-02T01:00:00+00:00",
         "local_weekday": 3, "local_block": 10, "source": "discord", "run_id": 2},
        {"type": "raiderio_run", "user_id": 1, "ts_utc": "2026-07-02T01:00:00+00:00",
         "local_weekday": 2, "local_block": 9, "source": "raiderio", "run_id": 3},
    ]
    obs = observations(events, {1: _raider(1, ["dps"])}, NOW)
    signs = sorted((o.weekday, o.block, o.sign) for o in obs)
    assert signs == [(2, 9, 1), (2, 9, 1), (3, 10, -1)]
    assert all(o.user_id == 1 for o in obs)
    assert all(o.age_weeks > 0 for o in obs)


def test_observations_expands_run_completed_roster_via_timezone():
    # Run at 2026-07-03 01:00 UTC. Central (UTC-5) -> 2026-07-02 20:00 (Thu=3, block 10).
    events = [{"type": "run_completed", "ts_utc": "2026-07-03T01:00:00+00:00",
               "user_id": None, "roster": [1, 2], "run_id": 9, "source": "discord",
               "local_weekday": None, "local_block": None}]
    raiders = {1: _raider(1, ["tank"]), 2: _raider(2, ["healer"], tz="America/New_York")}
    obs = observations(events, raiders, NOW)
    by_user = {o.user_id: (o.weekday, o.block, o.sign) for o in obs}
    assert by_user[1] == (3, 10, 1)   # Central: Thu 20:00 -> block 10
    assert by_user[2] == (3, 11, 1)   # Eastern: Thu 21:00 -> block 11


def test_observations_skips_null_slots_and_missing_tz():
    events = [
        {"type": "offer_accepted", "user_id": 1, "ts_utc": "2026-07-02T01:00:00+00:00",
         "local_weekday": None, "local_block": None, "source": "discord", "run_id": 1},
        {"type": "run_completed", "ts_utc": "2026-07-03T01:00:00+00:00", "roster": [99],
         "user_id": None, "run_id": 2, "source": "discord"},  # 99 not in raiders
        {"type": "avail_reaction", "user_id": 1, "ts_utc": "2026-07-02T01:00:00+00:00",
         "emoji": "🟢", "week_of": "2026-06-30", "local_weekday": 2, "local_block": 9},
    ]
    assert observations(events, {1: _raider(1, ["dps"])}, NOW) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forecast.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'forecast'`

- [ ] **Step 3: Write minimal implementation**

Create `forecast.py`:

```python
"""Availability forecaster: events -> per-person, per-(weekday, block) probabilities."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger("discord")

HALF_LIFE = 4.0      # weeks; recency decay half-life
ALPHA = 2.0          # smoothing pseudo-count
BASE_PRIOR = 0.15    # availability prior when a person has no data

_POSITIVE_TYPES = {"run_completed", "offer_accepted", "raiderio_run"}
_NEGATIVE_TYPES = {"offer_declined"}


@dataclass(frozen=True)
class Obs:
    """One signed availability observation for a user in a local (weekday, block)."""

    user_id: int
    weekday: int
    block: int
    age_weeks: float
    sign: int  # +1 positive, -1 negative


def _age_weeks(ts_utc: datetime, now: datetime) -> float:
    return (now - ts_utc).total_seconds() / (7 * 86400)


def observations(events: List[dict], raiders: dict, now: datetime) -> List[Obs]:
    """Normalize raw events into signed per-user, per-local-slot observations.

    - run_completed: one +1 obs per roster member, slot from ts_utc + that member's tz.
    - offer_accepted / raiderio_run: +1 at the event's stored (local_weekday, local_block).
    - offer_declined: -1 at its stored slot.
    - avail_reaction and slotless/tz-less events are skipped.
    """
    out: List[Obs] = []
    for e in events:
        etype = e.get("type")
        ts_raw = e.get("ts_utc")
        if ts_raw is None:
            continue
        ts = datetime.fromisoformat(ts_raw)
        age = _age_weeks(ts, now)
        if etype == "run_completed":
            for uid in e.get("roster") or []:
                raider = raiders.get(uid)
                if raider is None or getattr(raider, "timezone", None) is None:
                    continue
                local = ts.astimezone(raider.timezone)
                out.append(Obs(uid, local.weekday(), local.hour // 2, age, 1))
        elif etype in _POSITIVE_TYPES or etype in _NEGATIVE_TYPES:
            uid = e.get("user_id")
            wd = e.get("local_weekday")
            blk = e.get("local_block")
            if uid is None or wd is None or blk is None:
                continue
            sign = 1 if etype in _POSITIVE_TYPES else -1
            out.append(Obs(uid, wd, blk, age, sign))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forecast.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add forecast.py tests/test_forecast.py
git commit -m "feat: add forecaster observation normalizer"
```

---

## Task 2: predict() — recency-weighted smoothed block probability

**Files:**
- Modify: `forecast.py`
- Test: `tests/test_forecast.py`

**Interfaces:**
- Consumes: `Obs` (Task 1).
- Produces: `predict(user_obs: List[Obs], weekday: int, block: int) -> float`; helper `_base_rate(user_obs, weekday) -> float`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_forecast.py`:

```python
from forecast import predict


def _obs(weekday, block, age_weeks, sign):
    return Obs(1, weekday, block, age_weeks, sign)


def test_predict_no_data_returns_base_prior():
    from forecast import BASE_PRIOR
    assert predict([], 2, 9) == BASE_PRIOR


def test_predict_recent_positive_scores_high():
    p = predict([_obs(2, 9, 0.0, 1), _obs(2, 9, 0.0, 1), _obs(2, 9, 0.0, 1)], 2, 9)
    assert p > 0.7


def test_predict_negatives_pull_down():
    hi = predict([_obs(2, 9, 0.0, 1), _obs(2, 9, 0.0, 1)], 2, 9)
    lo = predict([_obs(2, 9, 0.0, 1), _obs(2, 9, 0.0, 1),
                  _obs(2, 9, 0.0, -1), _obs(2, 9, 0.0, -1)], 2, 9)
    assert lo < hi


def test_predict_recency_decay_old_positive_weaker():
    # A recent negative elsewhere on the same weekday holds the base-rate prior
    # below 1.0, so the block positive's recency actually moves the score
    # (an all-positive history would give prior=1.0 and mask the decay).
    recent = predict([_obs(2, 9, 0.0, 1), _obs(2, 10, 0.0, -1)], 2, 9)
    old = predict([_obs(2, 9, 52.0, 1), _obs(2, 10, 0.0, -1)], 2, 9)  # ~1yr old -> decayed
    assert old < recent


def test_predict_prior_from_same_weekday_when_block_thin():
    # No obs in (2, 5), but strong positives elsewhere on weekday 2 -> prior lifts it above BASE_PRIOR.
    from forecast import BASE_PRIOR
    user_obs = [_obs(2, 9, 0.0, 1), _obs(2, 10, 0.0, 1), _obs(2, 11, 0.0, 1)]
    assert predict(user_obs, 2, 5) > BASE_PRIOR
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forecast.py -k predict -v`
Expected: FAIL with `ImportError: cannot import name 'predict'`

- [ ] **Step 3: Write minimal implementation**

Append to `forecast.py`:

```python
def _weight(age_weeks: float) -> float:
    return 0.5 ** (age_weeks / HALF_LIFE)


def _weighted_fraction(obs: List[Obs]) -> Optional[float]:
    """Recency-weighted positive fraction over the given obs, or None if empty."""
    wpos = sum(_weight(o.age_weeks) for o in obs if o.sign > 0)
    wneg = sum(_weight(o.age_weeks) for o in obs if o.sign < 0)
    total = wpos + wneg
    if total == 0:
        return None
    return wpos / total


def _base_rate(user_obs: List[Obs], weekday: int) -> float:
    """Prior: same-weekday positive fraction, else all obs, else BASE_PRIOR."""
    same_day = _weighted_fraction([o for o in user_obs if o.weekday == weekday])
    if same_day is not None:
        return same_day
    overall = _weighted_fraction(user_obs)
    return overall if overall is not None else BASE_PRIOR


def predict(user_obs: List[Obs], weekday: int, block: int) -> float:
    """P(user available at local weekday/block), recency-weighted + smoothed."""
    block_obs = [o for o in user_obs if o.weekday == weekday and o.block == block]
    wpos = sum(_weight(o.age_weeks) for o in block_obs if o.sign > 0)
    wneg = sum(_weight(o.age_weeks) for o in block_obs if o.sign < 0)
    prior = _base_rate(user_obs, weekday)
    return (wpos + ALPHA * prior) / (wpos + wneg + ALPHA)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forecast.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add forecast.py tests/test_forecast.py
git commit -m "feat: add recency-weighted smoothed block predictor"
```

---

## Task 3: select_team() + can_field_team() — role-valid roster optimizer

**Files:**
- Modify: `forecast.py`
- Test: `tests/test_forecast.py`

**Interfaces:**
- Produces:
  - `Team(tank, healer, dps: list, mean: float)` dataclass
  - `select_team(candidates: List[tuple]) -> Optional[Team]` where each candidate is `(raider, prob: float)`; maximizes mean prob covering 1 tank + 1 healer + 3 dps, distinct raiders, multi-role aware.
  - `can_field_team(raiders: list) -> bool` (feasibility gate).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_forecast.py`:

```python
from forecast import Team, can_field_team, select_team


def test_select_team_picks_role_valid_max_mean():
    cands = [
        (_raider(1, ["tank"]), 0.9),
        (_raider(2, ["healer"]), 0.8),
        (_raider(3, ["dps"]), 0.7),
        (_raider(4, ["dps"]), 0.6),
        (_raider(5, ["dps"]), 0.5),
        (_raider(6, ["dps"]), 0.1),  # weakest dps should be left out
    ]
    team = select_team(cands)
    assert team is not None
    assert team.tank.user_id == 1 and team.healer.user_id == 2
    assert {r.user_id for r in team.dps} == {3, 4, 5}
    assert 0.6 < team.mean < 0.75


def test_select_team_multirole_fills_scarce_role():
    # Only raider 1 can tank; a multi-role raider must cover healer.
    cands = [
        (_raider(1, ["tank", "dps"]), 0.9),
        (_raider(2, ["healer", "dps"]), 0.8),
        (_raider(3, ["dps"]), 0.7),
        (_raider(4, ["dps"]), 0.6),
        (_raider(5, ["dps"]), 0.5),
    ]
    team = select_team(cands)
    assert team is not None
    assert team.tank.user_id == 1
    assert team.healer.user_id == 2
    assert {r.user_id for r in team.dps} == {3, 4, 5}


def test_select_team_none_when_roles_uncoverable():
    cands = [(_raider(i, ["dps"]), 0.5) for i in range(1, 6)]  # no tank/healer
    assert select_team(cands) is None


def test_can_field_team():
    ok = [_raider(1, ["tank"]), _raider(2, ["healer"]), _raider(3, ["dps"]),
          _raider(4, ["dps"]), _raider(5, ["dps"])]
    assert can_field_team(ok) is True
    assert can_field_team(ok[:4]) is False           # only 4
    assert can_field_team([_raider(i, ["dps"]) for i in range(5)]) is False  # no tank/healer
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forecast.py -k "select_team or can_field" -v`
Expected: FAIL with `ImportError: cannot import name 'select_team'`

- [ ] **Step 3: Write minimal implementation**

Append to `forecast.py`:

```python
@dataclass
class Team:
    """A role-valid roster, its mean predicted availability, and per-member probs."""

    tank: object
    healer: object
    dps: list
    mean: float
    probs: dict  # user_id -> predicted probability


def select_team(candidates: List[tuple]) -> Optional[Team]:
    """Pick 1 tank + 1 healer + 3 dps (distinct, multi-role aware) maximizing mean prob."""
    prob = {id(r): p for r, p in candidates}
    tanks = [r for r, _ in candidates if "tank" in r.roles]
    healers = [r for r, _ in candidates if "healer" in r.roles]
    dps_pool = [r for r, _ in candidates if "dps" in r.roles]

    best: Optional[Team] = None
    for tank in tanks:
        for healer in healers:
            if healer is tank:
                continue
            remaining = [r for r in dps_pool if r is not tank and r is not healer]
            if len(remaining) < 3:
                continue
            top3 = sorted(remaining, key=lambda r: prob[id(r)], reverse=True)[:3]
            mean = (prob[id(tank)] + prob[id(healer)] + sum(prob[id(r)] for r in top3)) / 5
            if best is None or mean > best.mean:
                members = [tank, healer, *top3]
                best = Team(
                    tank=tank,
                    healer=healer,
                    dps=top3,
                    mean=mean,
                    probs={r.user_id: prob[id(r)] for r in members},
                )
    return best


def can_field_team(raiders: list) -> bool:
    """Feasibility gate: can a role-valid team of 5 be assembled from these raiders?"""
    return select_team([(r, 1.0) for r in raiders]) is not None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forecast.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add forecast.py tests/test_forecast.py
git commit -m "feat: add role-valid team selector and feasibility gate"
```

---

## Task 4: rank_slots() + format_preview() — slot optimizer + preview text

**Files:**
- Modify: `forecast.py`
- Test: `tests/test_forecast.py`

**Interfaces:**
- Produces:
  - `rank_slots(green: list, obs_by_user: Dict[int, List[Obs]], now_cst: datetime) -> List[tuple]` → list of `(slot_dt_utc, Team)` sorted by `Team.mean` desc.
  - `format_preview(ranked: List[tuple]) -> str` — the dry-run DM text (best pick + up to 2 runners-up), empty-safe.
  - helper `_next_slot_datetime(now_cst, weekday, block) -> datetime`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_forecast.py`:

```python
from forecast import format_preview, rank_slots


def test_rank_slots_prefers_slot_where_team_is_available():
    # Everyone (Central) has strong positives at Thursday(3) block 10; nothing elsewhere.
    green = [_raider(1, ["tank"]), _raider(2, ["healer"]),
             _raider(3, ["dps"]), _raider(4, ["dps"]), _raider(5, ["dps"])]
    # Positives at Thu(3) block 10, plus a same-weekday negative at block 0 so the
    # weekday prior stays < 1 and block 10 is the uniquely best slot (an all-positive
    # history would tie every slot at 1.0 and make the "best" arbitrary).
    obs_by_user = {r.user_id: [Obs(r.user_id, 3, 10, 0.0, 1)] * 3 + [Obs(r.user_id, 3, 0, 0.0, -1)]
                   for r in green}
    now_cst = datetime(2026, 7, 8, 12, 0, tzinfo=CST)  # a Wednesday
    ranked = rank_slots(green, obs_by_user, now_cst)
    assert ranked, "expected at least one role-valid slot"
    best_dt, best_team = ranked[0]
    # best slot maps to Central Thursday block 10 for all members
    local = best_dt.astimezone(CST)
    assert local.weekday() == 3 and local.hour // 2 == 10
    assert best_team.mean > 0.6


def test_rank_slots_empty_when_no_role_valid_team():
    green = [_raider(i, ["dps"]) for i in range(1, 6)]  # no tank/healer
    obs_by_user = {r.user_id: [] for r in green}
    now_cst = datetime(2026, 7, 8, 12, 0, tzinfo=CST)
    assert rank_slots(green, obs_by_user, now_cst) == []


def test_format_preview_contains_pick_and_is_empty_safe():
    assert "no" in format_preview([]).lower()
    green = [_raider(1, ["tank"]), _raider(2, ["healer"]),
             _raider(3, ["dps"]), _raider(4, ["dps"]), _raider(5, ["dps"])]
    obs_by_user = {r.user_id: [Obs(r.user_id, 3, 10, 0.0, 1)] * 3 + [Obs(r.user_id, 3, 0, 0.0, -1)]
                   for r in green}
    ranked = rank_slots(green, obs_by_user, datetime(2026, 7, 8, 12, 0, tzinfo=CST))
    text = format_preview(ranked)
    assert "R1" in text and "R2" in text     # tank + healer names appear
    assert "dry-run" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forecast.py -k "rank_slots or format_preview" -v`
Expected: FAIL with `ImportError: cannot import name 'rank_slots'`

- [ ] **Step 3: Write minimal implementation**

Add `from datetime import datetime, timedelta, timezone` and `from zoneinfo import ZoneInfo` to the imports at the top of `forecast.py` (merge with the existing datetime import; add a module-level `_CST = ZoneInfo("America/Chicago")`). Then append:

```python
def _next_slot_datetime(now_cst: datetime, weekday: int, block: int) -> datetime:
    """Next CST-anchored datetime with the given weekday/2h-block, strictly after now (within 7 days)."""
    days_ahead = (weekday - now_cst.weekday()) % 7
    candidate = now_cst.replace(hour=block * 2, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
    if candidate <= now_cst:
        candidate += timedelta(days=7)
    return candidate


def rank_slots(green: list, obs_by_user: Dict[int, List["Obs"]], now_cst: datetime) -> List[tuple]:
    """Rank the coming week's candidate slots by the best role-valid team's mean availability."""
    ranked = []
    for weekday in range(7):
        for block in range(12):
            slot_cst = _next_slot_datetime(now_cst, weekday, block)
            candidates = []
            for raider in green:
                local = slot_cst.astimezone(raider.timezone)
                p = predict(obs_by_user.get(raider.user_id, []), local.weekday(), local.hour // 2)
                candidates.append((raider, p))
            team = select_team(candidates)
            if team is not None:
                ranked.append((slot_cst.astimezone(timezone.utc), team))
    ranked.sort(key=lambda t: t[1].mean, reverse=True)
    return ranked


def format_preview(ranked: List[tuple]) -> str:
    """Dry-run preview DM text for the top pick + up to two runners-up."""
    if not ranked:
        return "🔮 No role-valid team could be predicted from this week's available pool."
    best_dt, best = ranked[0]
    ts = int(best_dt.timestamp())
    dps_str = ", ".join(f"{r.name} ({_p(best, r)})" for r in best.dps)
    lines = [
        "🔮 **Predicted run for this week** (dry-run — not scheduled)",
        f"🕐 <t:{ts}:F> · confidence **{best.mean:.2f}**",
        f"🛡️ {best.tank.name} ({_p(best, best.tank)})  "
        f"💚 {best.healer.name} ({_p(best, best.healer)})  ⚔️ {dps_str}",
    ]
    for dt, team in ranked[1:3]:
        lines.append(f"_Runner-up: <t:{int(dt.timestamp())}:F> ({team.mean:.2f})_")
    return "\n".join(lines)


def _p(team: "Team", raider) -> str:
    """Per-member predicted probability for display."""
    return f"{team.probs.get(raider.user_id, 0.0):.2f}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forecast.py -v`
Expected: PASS. Then `python -m pytest -q` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add forecast.py tests/test_forecast.py
git commit -m "feat: add slot optimizer and dry-run preview formatter"
```

---

## Task 5: Wire the weekly forecast preview task into bot.py + packaging

**Files:**
- Modify: `bot.py` (import; new `@tasks.loop` task; start in `setup_hook`)
- Modify: `pyproject.toml`, `Dockerfile`

**Interfaces:**
- Consumes: `forecast.observations`, `forecast.can_field_team`, `forecast.rank_slots`, `forecast.format_preview`, `eventlog.read_events`, `self.availability[GREEN]`, `self.raiders`, `BANKER_ID`, `_CST`.

- [ ] **Step 1: Add imports + packaging**

- In `bot.py`, add `import forecast` to the local-module import group.
- In `pyproject.toml`, add `"forecast"` to the `[tool.setuptools] py-modules` list.
- In `Dockerfile`, add `forecast.py` to the `COPY ... ./` module line.

- [ ] **Step 2: Add the weekly forecast task**

Add to `MyClient`, near the other `@tasks.loop` tasks:

```python
    @tasks.loop(time=time(hour=12, minute=0, tzinfo=_CST))
    async def forecast_preview(self):
        """Wednesday-noon-CST dry-run: predict the best run from the green pool, DM BANKER_ID."""
        if datetime.now(_CST).weekday() != 2:  # 2 = Wednesday (day after the Tuesday availability post)
            return
        if not self.is_ready():
            return

        green = list(self.availability.get(GREEN, []))
        if not forecast.can_field_team(green):
            logger.info("forecast_preview: green pool cannot field a role-valid team; skipping")
            return

        try:
            now = datetime.now(timezone.utc)
            events = eventlog.read_events()
            obs = forecast.observations(events, self.raiders, now)
            obs_by_user = {}
            for o in obs:
                obs_by_user.setdefault(o.user_id, []).append(o)
            ranked = forecast.rank_slots(green, obs_by_user, datetime.now(_CST))
            text = forecast.format_preview(ranked)
            banker = await self.fetch_user(BANKER_ID)
            await banker.send(text)
            logger.info("forecast_preview: sent dry-run preview to banker")
        except discord.HTTPException as exc:
            logger.warning("forecast_preview: could not DM banker: %s", exc)
        except Exception as exc:  # noqa: BLE001 - preview must never kill the loop
            logger.warning("forecast_preview failed: %s", exc)
```

- [ ] **Step 3: Start the task in setup_hook**

After the other task-start blocks in `setup_hook`, add:

```python
        # Start weekly forecast dry-run preview (Wednesdays at noon CST)
        if not self.forecast_preview.is_running():
            self.forecast_preview.start()
```

- [ ] **Step 4: Verify**

Run: `python -m py_compile bot.py && python -m pip install -e ".[dev]" && python -m pytest -q`
Expected: compiles; editable install builds; full suite passes (unchanged count). Confirm from the diff: one `import forecast`, one `forecast_preview` task started once; `forecast` in py-modules + Dockerfile COPY.

- [ ] **Step 5: Manual smoke check (optional)**

Temporarily relax the weekday guard (or wait for Wednesday), ensure some green raiders + events exist, and confirm BANKER_ID receives a preview DM. Revert any temporary change.

- [ ] **Step 6: Commit**

```bash
git add bot.py pyproject.toml Dockerfile
git commit -m "feat: wire weekly forecast dry-run preview; package forecast module"
```

---

## Task 6: Documentation

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `CHANGELOG.md`, `bot.py`

- [ ] **Step 1: Update CLAUDE.md**

- File Map: add `forecast.py` ("Availability predictor + roster/slot optimizer + dry-run preview").
- Add a "Forecast Preview (Phase 2a)" subsection under Key Workflows: Wednesday-noon-CST `forecast_preview` task; feasibility gate (green pool must field a role-valid team); recency-weighted smoothed predictor; best role-valid slot/team; DMs a dry-run preview to `BANKER_ID` (no team DMs, nothing created — that's Phase 2b).

- [ ] **Step 2: Update README.md**

Add a note: the bot automatically predicts the best weekly run from availability + play-history data and DMs the banker a dry-run preview (no runs are auto-created yet).

- [ ] **Step 3: Update CHANGELOG.md + version**

Add a new top entry (bump `1.3.0` → `1.4.0`), matching style:

```markdown
## [1.4.0]

### Improvements
- The bot now predicts the best weekly Mythic+ run from availability and play-history data and DMs a dry-run preview to the banker (fully automated, no runs created yet).
```

Bump `BOT_VERSION = "1.3.0"` → `"1.4.0"` in `bot.py`. Do NOT edit `version.txt`.

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest -q`
Expected: full suite passes.

```bash
git add CLAUDE.md README.md CHANGELOG.md bot.py
git commit -m "docs: document forecast dry-run preview (v1.4.0)"
```

---

## Self-Review Notes

- **Spec coverage:** observations/normalize (Task 1), predictor (Task 2), team selector + gate (Task 3), slot optimizer + preview text (Task 4), weekly task + gate + BANKER_ID DM + packaging (Task 5), docs (Task 6). All spec sections map to a task.
- **Purity/testability:** all logic is in `forecast.py` (no Discord/IO); tests use synthetic events/raiders (`SimpleNamespace`); only the bot task does IO.
- **Spec clarification:** `avail_reaction` is not consumed by the predictor in 2a (no per-slot meaning) — the base-rate prior is derived from the +/− observations, per the predictor definition. Reserved for possible later use.
- **Per-member probs:** `Team.probs` carries each member's real predicted probability so `format_preview` shows true per-member confidence (matching the spec's example), not just the team mean.
- **Type consistency:** `Obs`, `Team`, `observations(events, raiders, now)`, `predict(user_obs, weekday, block)`, `select_team(candidates)`, `rank_slots(green, obs_by_user, now_cst)` used identically across tasks.
- **Feasibility gate** reuses `select_team` (`can_field_team`), matching the spec.
