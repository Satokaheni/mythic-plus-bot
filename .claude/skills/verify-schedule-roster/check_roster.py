"""Bounded exhaustive state-space check of the Schedule roster invariants, run on the real code.

Every state is a real `schedule.Schedule` plus real `raider.Raider` objects. Every step calls the
same Schedule/Raider methods, in the same order, as one stretch of a real handler between two
`await`s (handler line refs are on each step). Breadth-first search over all orders of those steps
from every way of posting a run, up to a step bound, finds the shortest sequence that breaks each
property. No hand model of Schedule: the code under test is the code that runs in the bot.

Run from the repo root:  python .claude/skills/verify-schedule-roster/check_roster.py
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

import discord  # noqa: E402  (schedule.py / raider.py import it)

from mythicbot.scheduling.raider import Raider  # noqa: E402
from mythicbot.scheduling.schedule import Schedule  # noqa: E402

EXPECTED_PYTHON = (3, 10)
EXPECTED_DISCORD = "2.3.1"

# Raider catalog: name -> registered roles (roles[0] is the main role). Each name is one Discord user.
CATALOG = {
    "T": ["tank"], "H": ["healer"], "D1": ["dps"], "D2": ["dps"], "D3": ["dps"],
    "TD": ["tank", "dps"], "HD": ["healer", "dps"], "DT": ["dps", "tank"], "DH": ["dps", "healer"],
    "TH": ["tank", "healer"], "HT": ["healer", "tank"],
}
SLOT_ROLES = ("tank", "healer", "dps")

PROPERTIES = {
    "P1-one-place": (
        "No raider holds more than one place in a run (tank, healer, a DPS seat, or the fill queue).",
        "A raider counted twice makes a run look staffed when a seat is really empty, and the group "
        "finds out at key time.",
    ),
    "P2-missing-matches": (
        "`missing` lists exactly the roles whose slots are open, once each.",
        "The embed's NEEDS line, the role pings, and DM outreach all read `missing`, so a wrong entry "
        "pings for a filled role or stops recruiting for an open one.",
    ),
    "P3-count-matches": (
        "`signup` equals the number of filled seats, and `full` is true exactly when all five are filled.",
        "`full` decides the FULL status, the 'now filled' DMs, and whether outreach stops; a stale count "
        "announces a run as ready when it is not, or never announces it.",
    ),
    "P4-members-match": (
        "`members` lists each slot holder and fill raider exactly once, and no one else.",
        "Remove Raider, Change Role, the delete notices, and reminders work off `members`, so a raider "
        "missing from it sits in a seat nobody can clear, and an extra one gets DMs for a run they left.",
    ),
    "P5-runs-match": (
        "A raider's `current_runs` contains the run exactly when the raider is in its `members`.",
        "The Sign Up and Remove buttons gate on `current_runs`; a mismatch lets a seated raider sign up "
        "again or tells them they are not signed up when they try to leave.",
    ),
    "P6-promotion": (
        "When a removal frees a seat, no raider left in the fill queue could have taken that seat.",
        "A queued raider who could play the freed role is left waiting while the embed pings for that "
        "role and they are never told a spot opened.",
    ),
}

# Known findings at the sync commit: (scope, interleave, property) -> finding id (SKILL.md, Findings).
# The check exits 0 only when the violated set equals this set exactly. Remove an entry only when the
# finding is fixed in product code; never add one to silence a new violation without reporting it.
KNOWN = {}


# ---------------------------------------------------------------------------
# State: one real Schedule plus its real Raiders, cloned between branches
# ---------------------------------------------------------------------------


class World:
    """One explored state. `pending` holds handlers suspended at an await: tuples of
    (kind, raider_name[, role]) — see the step functions for what each kind resumes."""

    def __init__(self, sched, raiders, pending=(), deleted=False):
        self.s = sched
        self.r = raiders  # name -> Raider
        self.pending = tuple(sorted(pending, key=str))
        self.deleted = deleted
        self.did_remove = False  # set by steps that call raider_remove; not part of the state

    def clone(self):
        old_s = self.s
        new_s = object.__new__(Schedule)
        new_r = {n: object.__new__(Raider) for n in self.r}
        by_id = {id(self.r[n]): new_r[n] for n in self.r}

        def m(x):
            return by_id[id(x)] if x is not None else None

        d = dict(old_s.__dict__)
        d["team"] = {"tank": m(old_s.team["tank"]), "healer": m(old_s.team["healer"]),
                     "dps": [m(x) for x in old_s.team["dps"]], "fill": [m(x) for x in old_s.team["fill"]]}
        d["members"] = [m(x) for x in old_s.members]
        d["missing"] = list(old_s.missing)
        new_s.__dict__.update(d)  # level/date/start_time set before any set below hashes it
        for n, old in self.r.items():
            rd = dict(old.__dict__)
            rd["roles"] = list(old.roles)
            rd["current_runs"] = {new_s for x in old.current_runs if x is old_s}
            rd["denied_runs"] = {new_s for x in old.denied_runs if x is old_s}
            new_r[n].__dict__.update(rd)
        return World(new_s, new_r, self.pending, self.deleted)

    def name(self, raider):
        return raider.name if raider is not None else None

    def sig(self):
        s, nm = self.s, self.name
        return (
            nm(s.team["tank"]), nm(s.team["healer"]), tuple(nm(x) for x in s.team["dps"]),
            tuple(nm(x) for x in s.team["fill"]),
            # members and missing are only read with `in` / `.remove` / `.append`, so their order
            # cannot change any later step; dps and fill order can, and are kept as-is.
            tuple(sorted(nm(x) for x in s.members)), tuple(sorted(s.missing)),
            s.signup, s.full,
            tuple(n for n, r in self.r.items() if has(r.current_runs, s)),
            # denied_runs is written by add_run/remove_run but read by no step in scope; left out.
            self.pending, self.deleted,
        )

    def describe(self):
        s, nm = self.s, self.name
        if self.deleted:
            return "run deleted (last signup removed)"
        cur = sorted(n for n, r in self.r.items() if has(r.current_runs, s))
        return (f"tank={nm(s.team['tank'])} healer={nm(s.team['healer'])} "
                f"dps={[nm(x) for x in s.team['dps']]} fill={[nm(x) for x in s.team['fill']]} "
                f"members={[nm(x) for x in s.members]} missing={s.missing} signup={s.signup} full={s.full} "
                f"current_runs-has-run={cur}" + (f" in-flight={list(self.pending)}" if self.pending else ""))


def has(run_set, sched):
    """Identity membership: the checker's own bookkeeping must not depend on Schedule.__hash__."""
    return any(x is sched for x in run_set)


def make_raider(name, uid):
    member = SimpleNamespace(id=uid, mention=f"<@{uid}>", display_name=name)
    return Raider(member, "mage", list(CATALOG[name]), "US/Central")


# ---------------------------------------------------------------------------
# Steps. Each is one stretch of a real handler between awaits; it mutates `w` in place and
# returns a label, or returns None when the handler's guard turns the user away (no state change).
# ---------------------------------------------------------------------------


def post_run(pool, organizer, role, start):
    """cogs/scheduling.py Scheduling._do_key_request_flow, new-run branch: Schedule(...organizer_role=role),
    then at Post Schedule raiders[user].add_run(schedule). Pre-post Add Raider is covered by `staff_add` below."""
    raiders = {n: make_raider(n, 1000 + i) for i, n in enumerate(pool)}
    s = Schedule(raiders[organizer], "10", start.strftime("%Y-%m-%d"), start, "one", organizer_role=role)
    raiders[organizer].add_run(s)
    return World(s, raiders)


def signup_guard(w, n, pick):
    """views.py ScheduleButtonView.signup_button, registered raider: guard, then await
    (role picker or defer). Resumes in `signup_apply`."""
    r = w.r[n]
    if not r.check_availability(w.s) or w.s in r.current_runs:
        return None
    w.pending = tuple(sorted(w.pending + (("signup", n, pick),), key=str))
    return f"{n} clicks Sign Up" + (f" and picks {pick}" if pick else "") + " (guard passes; handler awaits)"


def signup_apply(w, n, pick):
    """views.py ScheduleButtonView.signup_button, after the role picker/defer: has_raider re-check (already
    signed up -> answer and stop); effective_role; try_displace_off_roler; if displaced, await the notice
    (resumes in `signup_finish`); else raider_signup(raider, selected_role), add_run only if it returned True."""
    r = w.r[n]
    if w.s.has_raider(r):
        return f"{n}'s Sign Up resumes: already in the run, handler answers 'already signed up'"
    effective = pick if pick is not None else r.roles[0]
    displaced = w.s.try_displace_off_roler(r, effective)
    if displaced is not None:
        w.pending = tuple(sorted(w.pending + (("signup2", n, pick),), key=str))
        return f"{n}'s Sign Up resumes: try_displace_off_roler bumps {displaced.name} (handler awaits the DM)"
    if not w.s.raider_signup(r, pick):
        return f"{n}'s Sign Up resumes: raider_signup({n}, {pick!r}) refuses (already in the run)"
    r.add_run(w.s)
    return f"{n}'s Sign Up resumes: raider_signup({n}, {pick!r})"


def signup_finish(w, n, pick):
    """views.py ScheduleButtonView.signup_button, after the `_notify_displaced` await: raider_signup, then
    add_run only if it returned True."""
    if not w.s.raider_signup(w.r[n], pick):
        return f"{n}'s Sign Up finishes: raider_signup({n}, {pick!r}) refuses (already in the run)"
    w.r[n].add_run(w.s)
    return f"{n}'s Sign Up finishes: raider_signup({n}, {pick!r})"


def auto_guard(w, n):
    """views.py ScheduleButtonView.signup_button, new-raider welcome branch, and cogs/scheduling.py
    Scheduling.on_spot_offer_reaction, ✅ branch (DM offer): guard (available, run not in current_runs,
    not has_raider), try_displace_off_roler(raider, roles[0]); if displaced, or the run is full (DM offer asks about
    the fill queue first), await (resumes in `auto_finish`); else raider_signup(raider), and add_run only
    if it returned True."""
    r = w.r[n]
    if not r.check_availability(w.s) or w.s in r.current_runs or w.s.has_raider(r):
        return None
    displaced = w.s.try_displace_off_roler(r, r.roles[0])
    if displaced is not None or w.s.is_filled():
        w.pending = tuple(sorted(w.pending + (("auto", n, None),), key=str))
        what = f"bumps {displaced.name}" if displaced is not None else "run is full, asks about the fill queue"
        return f"{n} accepts a DM run offer (or finishes welcome signup): {what} (handler awaits)"
    if not w.s.raider_signup(r):
        return None  # refused, nothing changed (the guard above just passed, with no await between)
    r.add_run(w.s)
    return f"{n} accepts a DM run offer (or finishes welcome signup): raider_signup({n})"


def auto_finish(w, n, _):
    """After the await: raider_signup(raider), then add_run only if it returned True."""
    if not w.s.raider_signup(w.r[n]):
        return f"{n}'s offer/welcome signup finishes: raider_signup({n}) refuses (already in the run)"
    w.r[n].add_run(w.s)
    return f"{n}'s offer/welcome signup finishes: raider_signup({n})"


def remove_click(w, n):
    """views.py ScheduleButtonView.remove_button: defer, then guard on current_runs and
    raider_remove with no await between; signup == 0 deletes the run."""
    r = w.r[n]
    if w.s not in r.current_runs:
        return None
    w.s.raider_remove(r)
    w.did_remove = True
    if w.s.signup == 0:
        w.deleted = True
        w.pending = ()
    return f"{n} clicks Remove: raider_remove({n})"


def staff_add(w, n, role):
    """views.py _apply_manage_add (coordinator Add Raider; ManageRoleSelect offers all three
    roles) and _apply_manual_add (organizer Add Raider; ManualAddRoleSelect offers all
    three to multi-role raiders): raider_signup(raider, role) is the guard (False -> 'already
    signed up', nothing changed), then add_run."""
    r = w.r[n]
    if not w.s.raider_signup(r, role=role):
        return None
    r.add_run(w.s)
    return f"coordinator/organizer adds {n} as {role} (raider_signup({n}, role={role!r}))"


def staff_remove(w, n):
    """views.py RemoveRaiderSelect.callback: has_raider guard, raider_remove."""
    r = w.r[n]
    if not w.s.has_raider(r):
        return None
    w.s.raider_remove(r)
    w.did_remove = True
    return f"coordinator removes {n} (raider_remove({n}))"


def change_targets(s, r):
    """views.py _change_role_targets: the open roles minus the seat the raider already holds."""
    return [role for role in s.open_roles() if role != s.seat_of(r)]


def change_role_pick(w, n):
    """views.py ChangeRaiderRoleSelect.callback: has_raider guard; no open target role -> 'no open
    roles' and stop; else the role picker (await). Resumes in `change_role_apply`."""
    if not w.s.has_raider(w.r[n]) or not change_targets(w.s, w.r[n]):
        return None
    w.pending = tuple(sorted(w.pending + (("chrole", n, None),), key=str))
    return f"coordinator opens Change Role for {n} (handler awaits the role pick)"


def change_role_apply(w, n, role):
    """views.py ChangeRoleTargetSelect.callback: click-time re-check (raider still in the run and the role
    still a target, else refuse and change nothing), then raider_remove, raider_signup(role), add_run.
    `resumes` offers every role here, a superset of the menu built at pick time; the re-check is what
    turns away a role that is not open at click time."""
    r = w.r[n]
    if not w.s.has_raider(r) or role not in change_targets(w.s, r):
        return f"coordinator picks {role} for {n}: refused (not open now), nothing changed"
    w.s.raider_remove(r)
    w.did_remove = True
    w.s.raider_signup(r, role=role)
    r.add_run(w.s)
    return f"coordinator moves {n} to {role} (raider_remove, then raider_signup({n}, role={role!r}))"


# The same read-only guard expressions the step functions run, evaluated before cloning.
GUARDS = {
    "signup_guard": lambda w, n, _p: w.r[n].check_availability(w.s) and w.s not in w.r[n].current_runs,
    "auto_guard": lambda w, n: w.r[n].check_availability(w.s) and w.s not in w.r[n].current_runs,
    "remove_click": lambda w, n: w.s in w.r[n].current_runs,
    "staff_add": lambda w, n, _role: not w.s.has_raider(w.r[n]),
    "staff_remove": lambda w, n: w.s.has_raider(w.r[n]),
    "change_role_pick": lambda w, n: w.s.has_raider(w.r[n]) and bool(change_targets(w.s, w.r[n])),
}


def resumes(w):
    """Yield (step_name, fn, args, pending_kind) for every suspended handler that could resume next."""
    if w.deleted:
        return
    for kind, n, arg in w.pending:
        if kind == "signup":
            yield "signup_apply", signup_apply, (n, arg), kind
        elif kind == "signup2":
            yield "signup_finish", signup_finish, (n, arg), kind
        elif kind == "auto":
            yield "auto_finish", auto_finish, (n, arg), kind
        elif kind == "chrole":
            for role in SLOT_ROLES:
                yield "change_role_apply", change_role_apply, (n, role), kind


def actions(w, scope, interleave, max_pending):
    """Yield (step_name, fn, args, pending_kind) for every step that could happen next: a suspended
    handler resuming, or a user starting a new action. pending_kind is None for a new action."""
    if w.deleted:
        return
    yield from resumes(w)
    can_suspend = interleave and len(w.pending) < max_pending
    for n in w.r:
        picks = list(CATALOG[n]) if len(CATALOG[n]) > 1 else [None]
        if can_suspend or not interleave:
            for pick in picks:
                yield "signup_guard", signup_guard, (n, pick), None
            yield "auto_guard", auto_guard, (n,), None
        yield "remove_click", remove_click, (n,), None
        if scope == "all":
            for role in SLOT_ROLES:
                yield "staff_add", staff_add, (n, role), None
            yield "staff_remove", staff_remove, (n,), None
            if can_suspend or not interleave:
                yield "change_role_pick", change_role_pick, (n,), None


def run_to_quiescence(w, labels):
    """interleave=False: finish every suspended handler before the next user action, picking the
    role for Change Role is still a branch, so this returns a list of (world, labels)."""
    if not w.pending:
        return [(w, labels)]
    out = []
    for step, fn, args, kind in resumes(w):
        nw = nxt_without(w, kind, args)
        lab = fn(nw, *args)
        out.extend(run_to_quiescence(nw, labels + [lab]))
    return out


def nxt_without(w, kind, args):
    nw = w.clone()
    pend = list(nw.pending)
    pend.remove((kind, args[0], None if kind == "chrole" else args[1]))
    nw.pending = tuple(pend)
    nw.did_remove = w.did_remove
    return nw


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def open_slots(s):
    out = set()
    if s.team["tank"] is None:
        out.add("tank")
    if s.team["healer"] is None:
        out.add("healer")
    if len(s.team["dps"]) < 3:
        out.add("dps")
    return out


def seats(s):
    return ([s.team["tank"]] if s.team["tank"] else []) + ([s.team["healer"]] if s.team["healer"] else []) + list(
        s.team["dps"])


def check_state(w):
    """State properties P1-P5; returns {property: detail} for each one broken in this state."""
    if w.deleted:
        return {}
    s, bad = w.s, {}
    places = seats(s) + list(s.team["fill"])
    names = [p.name for p in places]
    dup = sorted({x for x in names if names.count(x) > 1})
    if dup:
        bad["P1-one-place"] = f"{dup} hold more than one place"
    if len(s.missing) != len(set(s.missing)) or set(s.missing) != open_slots(s):
        bad["P2-missing-matches"] = f"missing={s.missing} but open slots={sorted(open_slots(s))}"
    filled = len(seats(s))
    if s.signup != filled or s.full != (filled == 5):
        bad["P3-count-matches"] = f"signup={s.signup} full={s.full} but {filled} seats are filled"
    mem = [m.name for m in s.members]
    if len(mem) != len(set(mem)) or set(mem) != set(names):
        bad["P4-members-match"] = f"members={mem} but seats+fill={names}"
    cur = {n for n, r in w.r.items() if has(r.current_runs, s)}
    if cur != set(mem):
        bad["P5-runs-match"] = f"current_runs has the run for {sorted(cur)} but members={sorted(set(mem))}"
    return bad


def check_promotion(before, after):
    """P6 on a step that called raider_remove: the seats it freed (tank/healer held before and open
    after; a DPS seat whenever the DPS count dropped) that a raider still in the fill queue lists among
    their roles. Only freed seats count: the owner chose freed-seat-only promotion on 2026-11-03."""
    if after.deleted:
        return None
    b, a = before.s, after.s
    freed = {role for role in ("tank", "healer") if b.team[role] is not None and a.team[role] is None}
    if len(a.team["dps"]) < len(b.team["dps"]):
        freed.add("dps")
    waiting = [(f.name, role) for f in a.team["fill"] for role in f.roles if role in freed]
    if waiting:
        return f"freed {sorted(freed)} is open while {waiting} wait in the fill queue"
    return None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def explore(pool, scope, interleave, depth, max_pending, start):
    frontier = []
    parent = {}
    for org in pool:
        for role in (CATALOG[org] if len(CATALOG[org]) > 1 else [None]):
            w = post_run(pool, org, role, start)
            sig = w.sig()
            if sig not in parent:
                parent[sig] = (None, f"{org} posts a run" + (f" as {role}" if role else "") +
                               " (Schedule(...) + add_run at Post Schedule)")
                frontier.append(w)
    violations = {}  # property -> (sig, detail)

    def record(prop, sig, detail):
        if prop not in violations:
            violations[prop] = (sig, detail)

    for w in frontier:
        for prop, detail in check_state(w).items():
            record(prop, w.sig(), detail)

    level, exhausted = 0, False
    while frontier and level < depth:
        level += 1
        nxt = []
        for w in frontier:
            wsig = w.sig()
            succs = []
            for step, fn, args, kind in actions(w, scope, interleave, max_pending):
                guard = GUARDS.get(step)
                if guard is not None and not guard(w, *args):
                    continue  # the handler turns the user away; skip the clone
                nw = nxt_without(w, kind, args) if kind else w.clone()
                lab = fn(nw, *args)
                if lab is None:
                    continue
                if not interleave and nw.pending:
                    for qw, labs in run_to_quiescence(nw, [lab]):
                        succs.append((step, qw, "; then ".join(labs)))
                else:
                    succs.append((step, nw, lab))
            for step, nw, lab in succs:
                promo = check_promotion(w, nw) if nw.did_remove else None
                sig = nw.sig()
                if sig in parent:
                    if promo and "P6-promotion" not in violations:
                        violations["P6-promotion"] = (("edge", wsig, lab, sig), promo)
                    continue
                parent[sig] = (wsig, lab)
                if promo:
                    record("P6-promotion", sig, promo)
                bad = check_state(nw)
                for prop, detail in bad.items():
                    record(prop, sig, detail)
                if not bad:  # a state that already breaks P1-P5 is a counterexample; don't expand it
                    nxt.append(nw)
        frontier = nxt
    if not frontier:
        exhausted = True
    return violations, parent, len(parent), level, exhausted


def trace(parent, sig):
    steps = []
    if isinstance(sig, tuple) and sig and sig[0] == "edge":
        _, src, lab, _dst = sig
        steps.append(lab)
        sig = src
    while sig is not None:
        prev, lab = parent[sig]
        steps.append(lab)
        sig = prev
    return list(reversed(steps))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pool", default="T,H,D1,DT,HD", help="raiders from CATALOG, comma-separated")
    ap.add_argument("--depth", type=int, default=60, help="max steps after posting the run")
    ap.add_argument("--max-pending", type=int, default=None,
                    help="handlers suspended at an await at once (default: 2 for player, 1 for all)")
    ap.add_argument("--only", default=None, help="run one configuration, e.g. player:seq:far")
    args = ap.parse_args(argv)

    if sys.version_info[:2] != EXPECTED_PYTHON or discord.__version__ != EXPECTED_DISCORD:
        print(f"WARNING: pinned Python {EXPECTED_PYTHON[0]}.{EXPECTED_PYTHON[1]} + discord.py {EXPECTED_DISCORD}; "
              f"running Python {sys.version.split()[0]} + discord.py {discord.__version__}")

    pool = args.pool.split(",")
    now = datetime.now(timezone.utc)
    starts = {"far": now + timedelta(days=3), "near": now + timedelta(hours=4)}
    configs = [(sc, il, st) for sc in ("player", "all") for il in ("seq", "race") for st in ("far", "near")]
    if args.only:
        configs = [tuple(args.only.split(":"))]

    found, incomplete = set(), []
    for scope, il, st in configs:
        t0 = time.time()
        pend = args.max_pending if args.max_pending is not None else (2 if scope == "player" else 1)
        viol, parent, nstates, level, exhausted = explore(
            pool, scope, il == "race", args.depth, pend, starts[st])
        bound = ("every state reachable without first breaking P1-P5" if exhausted
                 else f"NOT exhausted: only sequences up to {level} steps")
        if not exhausted:
            incomplete.append((scope, il, st))
        print(f"\n== scope={scope} interleave={il} start={st} max-pending={pend}: {nstates} states, {bound} "
              f"({time.time() - t0:.1f}s)")
        for prop in PROPERTIES:
            if prop in viol:
                sig, detail = viol[prop]
                key = (scope, il, prop)
                found.add(key)
                tag = KNOWN.get(key)
                print(f"  {prop}: VIOLATED" + (f" (known {tag})" if tag else " (NEW)") + f" - {detail}")
                for i, lab in enumerate(trace(parent, sig), 1):
                    print(f"      {i}. {lab}")
            else:
                print(f"  {prop}: holds")

    if args.only:
        return 0 if not found else 1
    new = sorted(found - set(KNOWN))
    gone = sorted(set(KNOWN) - found)
    print("\n== verdict")
    for k in new:
        print(f"  NEW violation: scope={k[0]} interleave={k[1]} {k[2]}")
    for k in gone:
        print(f"  known finding {KNOWN[k]} no longer reproduces: scope={k[0]} interleave={k[1]} {k[2]}")
    for c in incomplete:
        print(f"  search not exhausted for {':'.join(c)}: raise --depth")
    if not new and not gone and not incomplete:
        print("  PASS: matches the recorded findings exactly" if KNOWN else "  PASS: every property holds")
        return 0
    print("  FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
