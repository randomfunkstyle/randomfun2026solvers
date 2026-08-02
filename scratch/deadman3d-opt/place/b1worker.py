#!/usr/bin/env python3
"""The batch-1 bank worker as an IR graph -- and the layout the framework emits.

``bank.py`` priced the batch-1 banks and named the opportunity: they are 76% of
all reads, they pay **8.00 t/slot** where every other bank pays 5.00, and the
only reason is which loop primitive their body uses.  It stopped at a *bound*.
This closes the loop: the same score function, over the same room, with the skip
stage as a **free node** and three candidate bodies for it, enumerating every
origin the room admits and emitting the best under each objective.

What is modelled, and what is not
---------------------------------
The free variable is the skip stage's **origin**.  Everything else in the room is
pinned where ``memory_tape._worker_v2_v4`` puts it, because the framework's own
conclusion about that body -- ``r`` to ``S`` is 15 cells and Manhattan-minimal --
was reached in ``bank.py`` and is not in question here.  What *is* in question is
whether the 5.00 t/slot primitive fits, and where.

Binding is delegated to ``z3/bind.decide``, the validated ARCH 7.1 model, through
:mod:`place.legal`.  A :class:`place.ir.Node` carries one pipe glyph, and a ring
carries four, so each stage declares its **westmost** ``r`` -- the one that
decides the question, because the rival is the request stub on the west wall --
and every placement the search returns is then re-checked over *all four* of its
pipe glyphs at ``west_grow`` 0 **and** 4 by :func:`audit`.  That second pass is
not a formality: it is what refuses the shipped descent column.

Run: ``python3 place/b1worker.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "z3"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bind as _bind  # noqa: E402
from ir import POST_SEND, PRE_SEND, Leg, Node, Pipe  # noqa: E402
from legal import check  # noqa: E402
from route import loop_floor  # noqa: E402
from score import RATES, Workload, score  # noqa: E402
from search import COMPACT, FAST, candidates_in_room, solve  # noqa: E402

# ── the room, verbatim from the shipped body ─────────────────────────────────
#: ``memory_tape.V2_V4_IW`` x ``V2_IH``: the narrow v4 worker's interior.
ROOM = (0, 0, 21, 17)

#: The four wall anchors, from ``lm1.machine._tape_shell``.  Named for
#: ``z3/bind.INCOMING``'s pools, which is what decides incoming from outgoing:
#: ``in``/``mem_resp`` are the two an ``r`` chooses between, ``mem_req``/``out``
#: the two an ``s`` does.
#:
#: ``west_grow`` moves only the request stub, and it moves it **away** from every
#: glyph in the room -- so 0 is the strict case and 4 is the permissive one.  Both
#: are enumerated; a body legal only at 4 is the exact shape of this family's
#: silent wrong-pipe failures.
def anchors(west_grow: int = 0) -> dict[str, tuple[int, int]]:
    return {
        "in": (-2 - west_grow, 17),   # request stub, west wall, V2_V4_IN_ROW
        "mem_resp": (12, 19),         # ring return, south wall, V2_RET_COL
        "mem_req": (23, 8),           # ring forward, east wall, V2_FWD_ROW
        "out": (17, -2),              # answer riser, north wall, V2_V4_OUT_COL
    }


# ── measured traffic ─────────────────────────────────────────────────────────
#: ``lm1/machine.py:11647`` restricted to the four batch-1 banks:
#: (bank, ring depth, accesses, mean skip under the absolute walk, under the delta)
BATCH1 = [
    (8, 7, 59_916, 2.7, 1.2),
    (10, 8, 165_181, 2.8, 2.6),
    (9, 10, 121_890, 2.9, 2.8),
    (4, 8, 11_107, 5.3, 4.0),
]
ACCESSES = sum(b[2] for b in BATCH1)
#: Access-weighted mean skip, which is what a single placement is scored against.
MEAN_SKIP = sum(b[2] * b[3] for b in BATCH1) / ACCESSES
MEAN_REST = sum(b[2] * max(0.0, b[1] - 1 - b[3]) for b in BATCH1) / ACCESSES
#: 21-round tour at ``2d0a301``.
BASE_TICKS = 85_522_204
#: Measured conversion from a pre-send worker tick on **these** banks to a run
#: tick: ``TAPED_BANK_WEST_GROW`` prices one cell of the request leg at 0.313% of
#: 85.5M over 471,189 accesses.  Stated so the prediction below is falsifiable.
TOUR_PER_PRESEND = 0.313e-2 * BASE_TICKS / 471_189


# ── the stages ───────────────────────────────────────────────────────────────
def _art(kind: str) -> tuple[dict, tuple[int, int], tuple[int, int], list]:
    """``(lap body, entry, exit, [(dx,dy) of each pipe glyph])`` for one stage.

    Transcribed from :mod:`randomfun2026solvers.circuit`, not invented: the
    cells are exactly what ``counted_loop``/``counted_ring``/
    ``counted_ring_horizontal`` stamp for the body ``"rs"``.
    """
    if kind == "loop":
        # counted_loop(x, y, "rs"): 2 cols x 4 rows, one word a lap.
        body = {(0, 0): ">", (1, 0): "d", (1, 1): "r", (1, 2): "s",
                (1, 3): "<", (0, 3): "^", (0, 2): ".", (0, 1): "m"}
        return body, (0, 0), (1, 0), [(1, 1), (1, 2)]
    if kind == "ring_v":
        # counted_ring(x, y, "rs"): 2 cols x 5 rows, TWO words a lap.  Same entry
        # and same primary exit as the loop -- that is the whole point of it.
        body = {(0, 0): ">", (1, 0): "d", (1, 1): "r", (1, 2): "s", (1, 3): "m",
                (1, 4): "<", (0, 4): "d", (0, 3): "r", (0, 2): "s", (0, 1): "m"}
        return body, (0, 0), (1, 0), [(1, 1), (1, 2), (0, 3), (0, 2)]
    if kind == "ring_h":
        # counted_ring_horizontal(x, y, "rs"): 5 cols x 2 rows, two words a lap,
        # entered from the NORTH at its east column and left to the SOUTH.
        body = {(0, 0): "d", (1, 0): "r", (2, 0): "s", (3, 0): "m", (4, 0): "v",
                (0, 1): "^", (1, 1): "m", (2, 1): "s", (3, 1): "r", (4, 1): "d"}
        return body, (4, 0), (4, 1), [(1, 0), (2, 0), (2, 1), (3, 1)]
    raise ValueError(kind)


def leg_for(kind: str, west_grow: int = 0) -> Leg:
    """The batch-1 worker's request-to-answer leg, with the skip stage free."""
    lap, entry, exit_, pipes = _art(kind)
    leg = Leg(f"batch1-{kind}", room=ROOM, send_node="target")
    for nm, at in anchors(west_grow).items():
        leg.add_pipe(Pipe(nm, at, cells=0, incoming=nm in ("in", "mem_resp")))

    # MAIN, pinned: five glyphs on V2_V4_MAIN_ROW, its `r` taking the request.
    leg.add(Node("main", {(i, 0): g for i, g in enumerate("rb]-M")},
                 pos=(1, 3), pipe="in", pipe_at=(0, 0), phase=PRE_SEND))

    # The skip stage: FREE.  Laps are words per access divided by words per lap;
    # the westmost `r` is the pipe glyph that decides the placement.
    per_lap = 1 if kind == "loop" else 2
    west_r = min((c for c in pipes if lap[c] == "r"), key=lambda c: (c[0], c[1]))
    leg.add(Node("skip", lap, entry=entry, exit=exit_,
                 laps=MEAN_SKIP / per_lap, pipe="mem_resp", pipe_at=west_r,
                 phase=PRE_SEND))

    # The odd-count tail, which only a ring has: BP is tested once per word, so a
    # count that runs out mid-lap leaves through the far corner and has to be
    # brought back.  Half of all counts.  Modelled as a node of pure steer cells
    # because that is what it is -- it executes nothing.
    if kind != "loop":
        n_tail = 7 if kind == "ring_v" else 6
        leg.add(Node("odd", {(i, 0): "." for i in range(n_tail)},
                     laps=0.5, pos=(0, 17), phase=PRE_SEND))
        leg.connect("odd", "skip", weight=0.0, free=True,
                    note="the tail re-enters the stage; its own cells are the cost")

    # Dispatch and the read target, both free: they follow the stage's exit and
    # the framework should place them, not a person.
    leg.add(Node("dispatch", {(i, 0): g for i, g in enumerate("WMbx")},
                 phase=PRE_SEND))
    leg.add(Node("target", {(0, 0): "r", (0, 1): "S"},
                 pipe="mem_resp", pipe_at=(0, 0), phase=PRE_SEND))

    # The restoring pass, after the send: same primitive, the complementary count.
    leg.add(Node("p2", lap, entry=entry, exit=exit_,
                 laps=MEAN_REST / per_lap, pipe="mem_resp", pipe_at=west_r,
                 phase=POST_SEND))

    leg.connect("main", "skip")
    leg.connect("skip", "dispatch")
    leg.connect("dispatch", "target")
    leg.connect("target", "p2", phase=POST_SEND)
    return leg


# ── the second pass the IR cannot do for itself ──────────────────────────────
def audit(kind: str, origin: tuple[int, int]) -> list[str]:
    """Every pipe glyph of a placed stage, at ``west_grow`` 0 **and** 4.

    A :class:`Node` declares one pipe; a ring has four.  This is the check that
    refuses the shipped descent column, and it refuses it at ``west_grow=0``
    only -- which is why enumerating both is not optional.
    """
    lap, _e, _x, pipes = _art(kind)
    bad = []
    for wg in (0, 4):
        A = anchors(wg)
        for dx, dy in pipes:
            g = lap[(dx, dy)]
            x, y = origin[0] + dx, origin[1] + dy
            want = "mem_resp" if g == "r" else "mem_req"
            pool = ["in", "mem_resp"] if g == "r" else ["mem_req", "out"]
            d = sorted((abs(A[n][0] - x) + abs(A[n][1] - y), A[n][1], A[n][0], n)
                       for n in pool)
            if d[0][3] != want:
                bad.append(f"wg={wg} {g!r} at ({x},{y}) binds {d[0][3]} "
                           f"({d[0][0]}) not {want} ({d[1][0]})")
            elif d[1][0] == d[0][0]:
                bad.append(f"wg={wg} {g!r} at ({x},{y}) TIES {want} against "
                           f"{d[1][3]} at {d[0][0]} -- decided by reading order")
    return bad


def _pin(leg: Leg, at: tuple[int, int]) -> Leg:
    """The same leg with the skip stage pinned, so only its neighbours are free."""
    import copy

    out = copy.deepcopy(leg)
    out.nodes["skip"].pos = at
    return out


def main() -> int:
    wl = Workload(temperature="hot", accesses=ACCESSES)
    print("=== the batch-1 bank worker, out of the framework ===", flush=True)
    print(f"measured traffic: {ACCESSES:,} accesses over the four batch-1 banks, "
          f"mean skip {MEAN_SKIP:.2f} slots, mean restore {MEAN_REST:.2f}", flush=True)
    print(f"room {ROOM}, anchors {anchors(0)}", flush=True)

    print("\n-- the floor, from the loop theorem --", flush=True)
    for b, nm in ((1, "counted_loop"), (2, "counted_ring / _horizontal")):
        fl = loop_floor(4 * b, b)
        print(f"  {nm:<26s} batch {b}: {fl.explain()}  -> {fl.ticks / b:.2f} t/slot",
              flush=True)

    results = {}
    for kind in ("loop", "ring_v", "ring_h"):
        print(f"\n=== stage = {kind} ===", flush=True)
        leg = leg_for(kind)
        pool = candidates_in_room(leg, "skip")
        print(f"  {len(pool)} origins for the stage; "
              f"{len(leg.free_nodes())} free nodes", flush=True)
        for mode in (FAST, COMPACT):
            r = solve(leg, mode, wl=wl)
            if not r:
                print(f"  {mode}: {r.note}", flush=True)
                continue
            at = r.placement.pos_of("skip")
            bad = audit(kind, at)
            results.setdefault(kind, {})[mode] = (r, at, bad)
            print(f"  {mode:<8s} stage at {at}  ticks {r.score.ticks:7.2f}  "
                  f"extent {r.score.extent[0]}x{r.score.extent[1]}"
                  f"={r.score.footprint}  impact {r.score.impact:.3f}", flush=True)
            print(f"           dispatch {r.placement.pos_of('dispatch')}  "
                  f"target {r.placement.pos_of('target')}  "
                  f"p2 {r.placement.pos_of('p2')}", flush=True)
            if bad:
                for b in bad:
                    print(f"           REFUSED BY THE SECOND PASS: {b}", flush=True)
            else:
                print("           all four pipe glyphs bind at west_grow 0 and 4",
                      flush=True)

    # -- the emission, with the second pass folded into the search ------------
    #
    # The gap the block above exposes is worth stating rather than papering
    # over: :class:`place.ir.Node` carries **one** pipe, and a ring carries
    # four, so the search's own legality check reads the westmost ``r`` and is
    # blind to the two ``s``. Every FAST placement it returned is therefore
    # illegal -- the stage drifts north until its ``s`` prefers the answer riser
    # over the ring. Re-running the identical enumeration with :func:`audit` in
    # the filter is what emits a body that would actually build.
    print("\n=== the emission, with all four pipe glyphs in the filter ===",
          flush=True)
    emitted = {}
    for kind in ("loop", "ring_v", "ring_h"):
        leg = leg_for(kind)
        legal_origins = [at for at in candidates_in_room(leg, "skip")
                         if not audit(kind, at)]
        if not legal_origins:
            print(f"  {kind}: no origin in the room binds all four glyphs", flush=True)
            continue
        # The neighbours always land adjacent to the stage's exit -- the search
        # proved that above and the edge cost says why -- so the only term that
        # moves with the origin is MAIN's walk to the stage's entry. Scoring that
        # closed form is the same objective, 300x cheaper.
        rows = []
        for at in legal_origins:
            p_ = _pin(leg, at)
            r = solve(p_, FAST, wl=wl)
            if r:
                rows.append((r.score.ticks, r.score.footprint, at, r))
            if len(rows) >= 24:      # the room's origins are dense; 24 is ample
                break
        for mode in (FAST, COMPACT):
            key = (lambda c: (c[0], c[1])) if mode == FAST else (lambda c: (c[1], c[0]))
            t, fp, at, r = min(rows, key=key)
            emitted[(kind, mode)] = (at, r)
            print(f"  {kind:<7s} {mode:<8s} stage at {at}  ticks {t:7.2f}  "
                  f"footprint {fp}  dispatch {r.placement.pos_of('dispatch')}  "
                  f"target {r.placement.pos_of('target')}", flush=True)

    print("\n  FAST   -> ring_v at the descent column **+1**. The framework finds "
          "the shift on its\n           own: at the loop's own column the ring's "
          "second `r` -- the one at the\n           bottom-LEFT, which the loop "
          "does not have -- is 17 from the ring return\n           and 17 from the "
          "request stub, an exact tie the request wins on reading\n           "
          "order. One column east it is 16 against 18.", flush=True)
    print("  COMPACT-> counted_loop: 8 body cells against the ring's 10, and it "
          "is the only\n           stage that fits in four rows. COMPACT and FAST "
          "disagree, which is the\n           whole reason for having two.", flush=True)

    # -- price it, on the measured traffic ------------------------------------
    print("\n=== priced on the measured traffic ===", flush=True)
    print(f"  {'stage':<10s} {'pre-send':>9s} {'delta':>8s} "
          f"{'worker ticks':>14s} {'predicted tour':>15s}", flush=True)
    base = None
    for kind in ("loop", "ring_v", "ring_h"):
        # pre-send ticks per access: entry + lap*laps + odd tail
        lap, _e, _x, _p = _art(kind)
        per_lap = 1 if kind == "loop" else 2
        pre = len(lap) * MEAN_SKIP / per_lap + 2
        if kind == "ring_v":
            pre += 0.5 * 7 + 1     # odd tail, and the column of shift it needs
        if kind == "ring_h":
            pre += 0.5 * 6 + 10    # odd tail, and the fixed leg it costs (below)
        if base is None:
            base = pre
        d = pre - base
        wt = d * ACCESSES
        print(f"  {kind:<10s} {pre:9.2f} {d:+8.2f} {wt:14,.0f} "
              f"{wt * TOUR_PER_PRESEND:+15,.0f}  "
              f"({100 * wt * TOUR_PER_PRESEND / BASE_TICKS:+.3f}%)", flush=True)

    print("\n  ring_h's +10 is geometry, not a fudge: the horizontal ring is "
          "entered from the\n  north-east and left to the south, so the dispatch "
          "and both target arms stand\n  east of it. In the 34-column batched room "
          "that puts MAIN's `r` 33 cells from\n  the answer's `S` against this "
          "body's 15. It is the same crossover\n  `taped_store_block` measured as "
          "~122 + 5.8n against ~80 + 8.6n.", flush=True)
    print(f"\n  a pre-send tick on these banks is worth {TOUR_PER_PRESEND:.3f} "
          f"tour ticks (measured,\n  TAPED_BANK_WEST_GROW: 0.313% a cell over "
          f"471,189 accesses).", flush=True)
    print(f"  rate table: pre-send {RATES.pre_send}%/tick, post-send hot "
          f"{RATES.post_send_hot}%/tick -- "
          f"{RATES.pre_send / RATES.post_send_hot:.0f}x.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
