#!/usr/bin/env python3
"""The batch-2 **wide** ring worker, through the framework -- FAST.

``b1worker.py`` did this for the narrow body and stopped at its skip stage.  The
wide body has never been through the router at all: :mod:`place.bank` priced its
*ring* (5.00 t/slot, at floor) and nothing else, and every other cell in the room
was placed by hand, one nitpick at a time.  This module prices the whole lap.

The body is ``memory_tape.worker_v2_jump(n, park_const=True, protocol="v4")`` --
34 x 17, three banks (``n`` = 22, 59, 135; the other batch-2 banks rotate and use
``worker_v2_rot``).  It is selected only by ``deadman-3d_hires``'s ``taped``
tier, through ``machine.TAPED_PROTOCOL`` = ``v5``.

The anchors are *measured*, not modelled
----------------------------------------
:func:`anchors` is not transcribed from ``memory_tape``'s constants and then
hoped over.  It is what ``fast_littleman`` itself reports as ``dst_attach`` /
``src_attach`` for the four pipes of a built bank room, read back out of the
shipped 614x402 machine, and :func:`selftest` re-derives all sixteen ``r``/``s``
bindings of the shipped body from it and checks them against the engine's own
answers.  ``b1worker.anchors`` is one cell further out on every wall -- harmless
there, because a constant offset on *both* rivals cancels, but it is the sort of
thing that stops cancelling the moment a pipe moves, so this one is pinned to the
engine.

What the room's geometry actually decides
-----------------------------------------
Three facts, and every layout in here is a consequence of them:

1. **The request arrives on the WEST wall (row 2) and the tape ring lives EAST
   and SOUTH** (forward on the east wall at row 7, return on the south wall at
   column 21).  So an ``r`` that must take the request cannot go far east, and an
   ``r``/``s`` that must take the ring cannot come far west, and the man has to
   walk between the two on every single access.  :func:`in_limit` and
   :func:`mem_limit` give the exact frontier, per row, at ``west_grow`` 0 and 4.

2. **The live leg is ``r`` -> ``S``**, and it is 29 cells wide of which
   *fourteen* are MAIN's run east to the ring's entry -- pure distance, no
   operation, on the critical path of every read.  MAIN sits at column 1 because
   that is where the entry gutter used to drop him, not because its ``r`` needs
   to be there: on row 5 the request binding holds out to **column 12**.

3. **The tail is not free but it is cheap** -- 0.024 %/tick against the live
   leg's 0.27 -- so trading a post-send cell for a pre-send one is worth taking
   at up to 11:1, and the framework's objective says so rather than a person.

Run: ``python3 place/b2worker.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent / "z3"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dispatch import bbox_floor  # noqa: E402
from ir import PRE_SEND, Leg, Node, Pipe  # noqa: E402
from score import RATES, Workload, score  # noqa: E402

# ── the room ─────────────────────────────────────────────────────────────────
#: ``memory_tape.V2_JUMP_IW`` x ``V2_JUMP_V4_IH``, in the worker's **own**
#: coordinates -- the ones ``worker_v2_jump`` draws in.  ``tape_block`` then
#: grows the room ``west_grow`` columns to the west, which moves the request stub
#: and nothing else; :func:`anchors` takes that as its argument.
ROOM = (0, 0, 33, 16)


def anchors(west_grow: int = 4) -> dict[str, tuple[int, int]]:
    """The four pipe attach cells, in the worker's own coordinates.

    MEASURED: read off ``FastLittleman.pipes[...].dst_attach`` / ``.src_attach``
    for a built ``deadman-3d_hires`` taped bank room and translated by the body's
    own ``west_grow`` offset.  ``selftest`` checks that ``bind.decide`` over these
    reproduces the engine's binding for all sixteen shipped ``r``/``s``.

    ``west_grow`` moves the request stub **away** from every glyph in the room,
    so 0 is the permissive case for a request ``r`` and 4 the strict one -- and
    the other way round for anything that must take the ring.  Both are always
    enumerated.
    """
    return {
        "in": (-1 - west_grow, 2),   # request stub, west wall, V2_IN_ROW
        "mem_resp": (21, 17),        # ring return, south wall, V2_JUMP_RET_COL
        "mem_req": (34, 7),          # ring forward, east wall, V2_JUMP_FWD_ROW
        "out": (2, -1),              # answer riser, north wall, V2_OUT_COL
    }


#: The pool an ``r`` chooses between, and the pool an ``s`` does.  Verbatim
#: ``z3/bind.INCOMING`` restricted to this room's four pipes.
POOL = {"r": ("in", "mem_resp"), "s": ("mem_req", "out")}
#: Which pipe each glyph of this body is *meant* to take.
WANT = {"r": "mem_resp", "s": "mem_req"}


def _dist(a: tuple[int, int], c: tuple[int, int]) -> int:
    return abs(a[0] - c[0]) + abs(a[1] - c[1])


def margin(cell: tuple[int, int], glyph: str, want: str, west_grow: int) -> int:
    """Cells the rival would have to close to steal ``cell``'s binding.

    Positive means ``want`` wins outright.  **Zero means a tie**, which the
    engines decide by reading order and this framework refuses: a tie is a
    one-cell margin and the failure mode is a wrong frame, not an exception.
    Negative means the rival already won -- a silent wrong pipe.
    """
    A = anchors(west_grow)
    pool = POOL[glyph]
    rival = pool[0] if pool[1] == want else pool[1]
    dw, dr = _dist(A[want], cell), _dist(A[rival], cell)
    # reading order: the attach with the smaller y wins a tie, then smaller x.
    want_first = (A[want][1], A[want][0]) < (A[rival][1], A[rival][0])
    return (dr - dw) + (0 if want_first else -1) if dr != dw else (0 if want_first else -1)


def binds(cell: tuple[int, int], glyph: str, want: str) -> bool:
    """Strictly, at ``west_grow`` **0 and 4** both.  Tie = fail."""
    return all(margin(cell, glyph, want, wg) > 0 for wg in (0, 4))


def in_limit(row: int) -> int:
    """Eastmost column on ``row`` at which an ``r`` still takes the **request**.

    This is the frontier that decides how far east MAIN may stand, and it is the
    single most valuable number in the room: every column MAIN gains is a column
    off the live leg.
    """
    return max((x for x in range(0, 34) if binds((x, row), "r", "in")), default=-1)


def mem_limit(row: int, glyph: str = "s") -> int:
    """Westmost column on ``row`` at which ``glyph`` still takes the **ring**."""
    want = WANT[glyph]
    return min((x for x in range(0, 34) if binds((x, row), glyph, want)), default=99)


# ── measured traffic ─────────────────────────────────────────────────────────
#: The three banks this body serves, from the 21-round exact profile of the
#: shipped 614x402 machine (``place/prof.py 1 21``, ``passed=True``).
#:
#: ``(ring depth, accesses, P1 ticks/access, P2 ticks/access, read share)`` --
#: every column MEASURED off ``heat - wait`` per cell, not derived.  The ring
#: columns divide by 5.00 to give slots; they are quoted as ticks because ticks
#: is what the objective is in.
#:
#: These are per-**bank** aggregates, the same granularity as :mod:`place.bank`'s
#: ``TRACE`` and ``lm1/machine.py``'s own table, both of which are checked in.
#: Per-**slot** access counts are level data and live in ``hires_traffic.json``,
#: which ``.gitignore`` refuses; ``traffic.py`` regenerates it locally in seconds.
BANKS = [
    (22, 54_219, 18.63, 90.17, 0.610),
    (59, 31_289, 24.95, 269.40, 0.645),
    (135, 6_669, 68.74, 607.35, 0.980),
]
ACCESSES = sum(b[1] for b in BANKS)
P1_TICKS = sum(b[1] * b[2] for b in BANKS) / ACCESSES
P2_TICKS = sum(b[1] * b[3] for b in BANKS) / ACCESSES
READ_SHARE = sum(b[1] * b[4] for b in BANKS) / ACCESSES

#: 21-round tour at ``fb28da9``.  MEASURED.
BASE_TICKS = 80_083_592
#: Share of reads whose immediately preceding request hit the same bank -- the
#: dominant term in :mod:`place.score`'s queueing model, and the reason this
#: body's tail is charged at all.  MEASURED.
F_SAME = 0.403

#: The shipped body's exact walks, lifted by walking the built grid with
#: ``place.trace`` (``walls=frozenset()``, bounded by the interior -- ``-`` and
#: ``|`` are subtract and or, and treating them as walls truncates ``rb]-M``
#: at the ``-`` and reports MAIN as three cells long).
SHIPPED = {
    "pre_send": 29,     # r(1,5) .. S(25,7), both rings passed through
    "read_lap": 78,     # r -> r, zero ring laps
    "write_lap": 104,   # r -> r, zero ring laps
    "odd_tail": 5,      # the P1 re-entry corridor, row 5 columns 15..19
}

#: ``(layout, READ lap, its box, WRITE lap, its box)`` -- **walked**, off the
#: built grid, one cell at a time under the engine's own movement rule.  The
#: boxes are the bounding boxes of the cells the man actually stands on, which is
#: what :func:`place.dispatch.bbox_floor` is a theorem about; they are not the
#: room's extent and not a guess.
BOXES = [
    ("shipped main_x=1 w=4", 78, (26, 12), 104, (23, 12)),
    ("FAST    main_x=10 w=4", 58, (17, 11), 84, (17, 11)),
    ("FAST    main_x=11 w=3", 58, (17, 11), 84, (18, 11)),
    ("FAST    main_x=9  w=5", 58, (17, 11), 84, (17, 11)),
    ("FAST    main_x=12 w=2", 58, (17, 11), 84, (19, 11)),
]


# ── the leg ──────────────────────────────────────────────────────────────────
#: ``counted_ring_horizontal(x, y, "rs")``, verbatim from
#: ``randomfun2026solvers.circuit``: entered from the NORTH at its east column,
#: left to the SOUTH, two words a lap, ten cells -- 5.00 t/slot, which
#: :mod:`place.bank` proved is this primitive's floor.
RING = {(0, 0): "d", (1, 0): "r", (2, 0): "s", (3, 0): "m", (4, 0): "v",
        (0, 1): "^", (1, 1): "m", (2, 1): "s", (3, 1): "r", (4, 1): "d"}
#: Its four pipe glyphs, body-relative.  A :class:`place.ir.Node` carries one
#: pipe and a ring carries four, which is what :func:`audit` exists for.
RING_PIPES = [(1, 0), (2, 0), (2, 1), (3, 1)]

#: The ``r``/``s`` that do **not** move with MAIN or the ring: the write arm's
#: value fetch, its realign pair, P2's own four and the INIT fill's send.  They
#: are in the margin because a layout is only as legal as its tightest glyph, and
#: for this room that glyph is sometimes one of these.
FIXED_PIPE_GLYPHS = [
    ((31, 4), "s", "mem_req"),    # the INIT fill
    ((7, 9), "r", "in"),          # the write's value, off the request wire
    ((16, 13), "s", "mem_req"), ((17, 13), "r", "mem_resp"),   # the realign
    ((20, 14), "r", "mem_resp"), ((21, 14), "s", "mem_req"),   # P2
    ((21, 15), "s", "mem_req"), ((22, 15), "r", "mem_resp"),
]


def leg(main_x: int, ring_x: int, *, row: int = 5) -> Leg:
    """The live leg of one access, parameterised on the two columns that matter.

    ``main_x`` is MAIN's ``r``; ``ring_x`` is P1's west column.  Everything else
    in the pre-send half follows: the ring is entered from ``row`` at
    ``ring_x + 4``, the dispatch stands on its exit, and the read target stands
    on the dispatch's north branch, because that is where the edge cost puts them
    and the framework should be the thing that says so.
    """
    lg = Leg("b2-wide", room=ROOM, send_node="target", weight=1.0)
    for nm, at in anchors(4).items():
        lg.add_pipe(Pipe(nm, at, cells=0, incoming=nm in ("in", "mem_resp")))

    lg.add(Node("main", {(i, 0): g for i, g in enumerate("rb]-M")},
                pos=(main_x, row), pipe="in", pipe_at=(0, 0), phase=PRE_SEND))
    lg.add(Node("skip", RING, entry=(4, -1), exit=(4, 1),
                pos=(ring_x, row + 1), laps=P1_TICKS / 10.0,
                pipe="mem_resp", pipe_at=(1, 0), phase=PRE_SEND))
    # The odd-count re-entry: BP is tested once per word, so half of all counts
    # leave through the ring's north-west corner and have to be walked back to
    # its north-east entry.  Pure steer cells -- it executes nothing.
    lg.add(Node("odd", {(i, 0): "." for i in range(5)}, pos=(ring_x, row),
                laps=0.5, phase=PRE_SEND))
    lg.add(Node("dispatch", {(i, 0): g for i, g in enumerate(">WMbx")},
                pos=(ring_x + 4, row + 3), phase=PRE_SEND))
    lg.add(Node("target", {(0, 0): ">", (1, 0): "r", (2, 0): "S"},
                pos=(ring_x + 8, row + 2), pipe="mem_resp", pipe_at=(1, 0),
                phase=PRE_SEND))

    lg.connect("main", "skip")
    lg.connect("odd", "skip", weight=0.0, free=True,
               note="the tail re-enters the stage; its own cells are the cost")
    lg.connect("skip", "dispatch")
    lg.connect("dispatch", "target")
    return lg


def audit(main_x: int, ring_x: int, *, row: int = 5) -> list[str]:
    """Every pipe glyph of the placed leg, at ``west_grow`` 0 **and** 4.

    A :class:`place.ir.Node` declares one pipe; the ring carries four and the
    read target a fifth.  This is the pass that refuses a MAIN one column too far
    east and a ring one column too far west, and it refuses them at *different*
    ``west_grow`` -- which is why enumerating both is not a formality.
    """
    bad = []
    glyphs = [((main_x, row), "r", "in")]
    for dx, dy in RING_PIPES:
        g = RING[(dx, dy)]
        glyphs.append(((ring_x + dx, row + 1 + dy), g, WANT[g]))
    glyphs.append(((ring_x + 9, row + 2), "r", "mem_resp"))
    for cell, g, want in glyphs:
        for wg in (0, 4):
            m = margin(cell, g, want, wg)
            if m <= 0:
                bad.append(f"wg={wg} {g!r} at {cell} wants {want}: margin {m}"
                           + (" -- TIE, decided by reading order" if m == 0
                              else " -- SILENT WRONG PIPE"))
    return bad


def pre_send(main_x: int, ring_x: int) -> int:
    """Exact cells the man stands on from the request ``r`` to the answer ``S``.

    Layer 1 of :mod:`place.score`, written out because the closed form is the
    thing being searched: MAIN's five, the run east to the ring's entry, the
    ring's two pass-through cells, the dispatch's five and the target's three.
    """
    return 5 + (ring_x - main_x) + 2 + 5 + 3


def emit(main_x: int, ring_x: int, home: int | None = None, *, row: int = 5,
         overshoot: int = 0) -> dict:
    """The whole lap, both arms, at Layer 1.  Exact -- there is no fitting here.

    **The arms are in here on purpose.**  A first cut of this function priced
    MAIN's walk and the walk home and nothing between them, and it produced a
    strict ranking over four layouts that the machine then measured as a dead
    heat.  That is failure mode 2 from the brief -- "the model prices the walk
    and nothing downstream" -- and the reason it bites here is exact and
    arithmetical: **both target arms hang off the ring's own exit**, so a column
    of ring shift lengthens the READ arm's drop and the WRITE arm's westward run
    by exactly what it shortens the walk home.  Once the arms are priced the four
    layouts come out identical to the cell, which is the truth.

    ``overshoot`` is the shipped return's detour west past column 0 and north
    past MAIN's row, which the riser deletes.
    """
    home = main_x - 1 if home is None else home
    pre = pre_send(main_x, ring_x)
    w = 19 - ring_x                       # ``memory_tape._JUMP_V4_WEST``
    # P2's exit at (23, 16), west along the bottom row to the riser, north up it
    # to MAIN's row, and east into MAIN's entry glyph.
    ret = (23 - home) + (16 - row) + 1 + overshoot
    read_post = 14 - w                    # S -> P2's entry, exclusive
    write_post = 43 - w                   # the dispatch's x -> P2's entry
    return {
        "main_x": main_x, "ring_x": ring_x, "home": home, "west": w,
        "pre_send": pre, "return": ret,
        "read_post": read_post, "write_post": write_post,
        "read_lap": pre + read_post + 2 + ret,
        "write_lap": (pre - 3) + write_post + 2 + ret,
        "odd": SHIPPED["odd_tail"],
        "bad": audit(main_x, ring_x, row=row),
        # ... over every ``r``/``s`` in the room, the ones that move with the
        # layout and the ones that do not: the write arm's value ``r`` at (7,9)
        # and its realign ``sr`` at (16,13)/(17,13) stand still whatever MAIN
        # does, and they are what puts a floor of 3 under all of this.
        "min_margin": min(
            [margin((main_x, row), "r", "in", wg) for wg in (0, 4)]
            + [margin((ring_x + dx, row + 1 + dy), RING[(dx, dy)],
                      WANT[RING[(dx, dy)]], wg)
               for dx, dy in RING_PIPES for wg in (0, 4)]
            + [margin(c, g, wnt, wg) for c, g, wnt in FIXED_PIPE_GLYPHS
               for wg in (0, 4)]),
    }


# ── the objective ────────────────────────────────────────────────────────────
def impact(pre: int, post: int) -> float:
    """Percent of mean read latency, per access, at the measured rates.

    ``pre`` is charged at :data:`place.score.RATES.pre_send`, ``post`` at the
    **cold** post-send rate, which is *higher* than the hot one: coldness
    correlates with same-bank read-after-write pairs and for those the
    inter-request gap is zero by construction.
    """
    return pre * RATES.pre_send + post * RATES.post_send("cold")


def selftest() -> list[str]:
    """Re-derive the shipped body's sixteen bindings from :func:`anchors`.

    The engine's own answers, captured off the built machine.  If this fails the
    anchors are wrong and nothing below this line means anything.
    """
    shipped = [
        ((31, 4), "s", "mem_req"), ((1, 5), "r", "in"),
        ((16, 6), "r", "mem_resp"), ((17, 6), "s", "mem_req"),
        ((17, 7), "s", "mem_req"), ((18, 7), "r", "mem_resp"),
        ((24, 7), "r", "mem_resp"), ((7, 9), "r", "in"),
        ((21, 9), "s", "mem_req"), ((22, 9), "r", "mem_resp"),
        ((16, 13), "s", "mem_req"), ((17, 13), "r", "mem_resp"),
        ((20, 14), "r", "mem_resp"), ((21, 14), "s", "mem_req"),
        ((21, 15), "s", "mem_req"), ((22, 15), "r", "mem_resp"),
    ]
    bad = []
    for cell, g, want in shipped:
        for wg in (0, 4):
            if margin(cell, g, want, wg) <= 0:
                bad.append(f"{g!r} at {cell} does not bind {want} at wg={wg} "
                           f"(margin {margin(cell, g, want, wg)})")
    return bad


def main() -> int:
    print("=== the batch-2 WIDE ring worker, out of the framework ===", flush=True)
    bad = selftest()
    print(f"anchors self-test against the engine's own bindings: "
          f"{'OK, all 16 shipped r/s reproduce' if not bad else bad}", flush=True)
    print(f"room {ROOM}, anchors(wg=4) {anchors(4)}", flush=True)
    print(f"traffic: {ACCESSES:,} accesses over banks n=22/59/135; "
          f"P1 {P1_TICKS:.2f} t/access, P2 {P2_TICKS:.2f}, read share "
          f"{READ_SHARE:.3f}, f_same {F_SAME:.3f}  (all MEASURED)", flush=True)

    print("\n-- the binding frontier, strict at west_grow 0 AND 4 --", flush=True)
    print(f"  {'row':>3s} {'r takes `in` out to':>20s} {'s takes the ring from':>22s} "
          f"{'r takes the ring from':>22s}", flush=True)
    for r in (2, 5, 6, 7, 9, 13, 14, 16):
        print(f"  {r:3d} {in_limit(r):20d} {mem_limit(r, 's'):22d} "
              f"{mem_limit(r, 'r'):22d}", flush=True)
    print("  MAIN stands on row 5.  Its `r` stood at column 1 and the frontier is "
          "12; what caps\n  it below that is the ring's odd-count corridor, not "
          "the pipe.  Note row 2, the\n  stub's own row, where the request still "
          "wins at 15 -- that is where the write arm's\n  fifteen-column "
          "excursion could go, and cannot, without changing the dispatch.",
          flush=True)

    print("\n-- the shipped body, walked --", flush=True)
    for k, v in SHIPPED.items():
        print(f"  {k:12s} {v:4d} cells", flush=True)
    print(f"  live leg  = 5 MAIN + 14 run east + 2 ring + 5 dispatch + 3 target "
          f"= {SHIPPED['pre_send']}", flush=True)

    print("\n-- the bounding-box floor (dispatch.bbox_floor: any closed walk "
          ">= 2(dx+dy)) --", flush=True)
    print(f"  {'layout':22s} {'READ lap':>9s} {'box':>7s} {'floor':>6s} "
          f"{'slack':>6s} | {'WRITE lap':>10s} {'box':>7s} {'floor':>6s} "
          f"{'slack':>6s}", flush=True)
    # walked off the built grids, not derived -- see BOXES' provenance note.
    for nm, r, rb, wl, wb in BOXES:
        rf, wf = bbox_floor(*rb), bbox_floor(*wb)
        print(f"  {nm:22s} {r:9d} {f'{rb[0]}x{rb[1]}':>7s} {rf:6d} {r - rf:6d} | "
              f"{wl:10d} {f'{wb[0]}x{wb[1]}':>7s} {wf:6d} {wl - wf:6d}", flush=True)
    print("\n  **The READ lap is two cells above its own box before and after, so "
          "the routing was\n  never the problem -- the BOX was.**  Placement "
          "shrank it from 26x12 to 17x11 and\n  the lap fell with it, 78 -> 58, "
          "which is what a floor theorem is for: it says the\n  lap is exhausted "
          "*given the box* and sends you to look at the box instead.\n"
          "  The WRITE lap keeps 28 cells of slack at every layout, and all 28 "
          "are one thing:\n  the value `r` at column 7 is a fifteen-column "
          "excursion west and back, because on\n  row 9 the request pipe stops "
          "winning at column 8.  On row 2 -- the stub's own row --\n  it wins out "
          "to 15.  That is the next lever and it is not a placement one: the arm\n"
          "  would have to change rows, which changes which way the dispatch's "
          "`x` sends it.", flush=True)

    print("\n=== FAST: minimise ticks, footprint free ===", flush=True)
    ship = emit(1, 15, home=11, overshoot=13)
    print(f"  shipped  main_x=1  west=4  pre={ship['pre_send']} ret={ship['return']} "
          f"READ lap {ship['read_lap']} WRITE lap {ship['write_lap']}  "
          f"(walked: {SHIPPED['pre_send']}/{SHIPPED['read_lap']}/"
          f"{SHIPPED['write_lap']})", flush=True)
    rows = []
    for ring_x in range(12, 20):
        for main_x in range(0, 14):
            if main_x + 4 >= ring_x:       # MAIN would stand in the tail corridor
                continue
            e = emit(main_x, ring_x)
            if e["bad"] or e["home"] < 0:
                continue
            e["impact"] = (READ_SHARE * impact(e["pre_send"],
                                               e["read_lap"] - e["pre_send"])
                           + (1 - READ_SHARE) * impact(0, e["write_lap"]))
            rows.append(e)
    rows.sort(key=lambda e: (e["pre_send"], e["read_lap"], e["write_lap"],
                             -e["min_margin"]))
    print(f"\n  {'main_x':>6s} {'west':>4s} {'home':>4s} {'pre':>4s} {'ret':>4s} "
          f"{'READ':>5s} {'WRITE':>5s} {'margin':>6s} {'impact/access':>13s}",
          flush=True)
    for e in rows[:6]:
        print(f"  {e['main_x']:6d} {e['west']:4d} {e['home']:4d} {e['pre_send']:4d} "
              f"{e['return']:4d} {e['read_lap']:5d} {e['write_lap']:5d} "
              f"{e['min_margin']:6d} {e['impact']:13.3f}", flush=True)
    best = rows[0]
    ties = [e for e in rows if (e["pre_send"], e["read_lap"], e["write_lap"])
            == (best["pre_send"], best["read_lap"], best["write_lap"])]
    print(f"\n  FAST is a **{len(ties)}-way exact tie** at pre-send "
          f"{best['pre_send']}, READ {best['read_lap']}, WRITE "
          f"{best['write_lap']}:\n  "
          + ", ".join(f"(main_x={e['main_x']}, west={e['west']})" for e in ties)
          + ".\n  A column of ring shift lengthens both arms by exactly what it "
            "shortens the walk\n  home, so the objective cannot separate them and "
            "the framework does not pretend to.\n  Every one of them was built "
            "and measured rather than picked; the tiebreak is the\n  binding "
            "margin, which is the only thing that differs.", flush=True)
    print(f"\n  delta vs shipped: {best['pre_send'] - ship['pre_send']:+d} "
          f"pre-send, {best['read_lap'] - ship['read_lap']:+d} on the READ lap, "
          f"{best['write_lap'] - ship['write_lap']:+d} on the WRITE lap, "
          f"over {ACCESSES:,} accesses", flush=True)

    print("\n-- modelled against measured (21-round tour, same process, control "
          "first and last) --", flush=True)
    for nm, tk, mg in (("control", 80_083_592, 3),
                       ("FAST main_x=10 west=4", 79_223_703, 3),
                       ("FAST main_x=11 west=3", 79_224_321, 3),
                       ("FAST main_x=9  west=5", 79_223_125, 2),
                       ("FAST main_x=12 west=2", 79_224_939, 1)):
        print(f"  {nm:22s} {tk:12,}  {100 * (tk - BASE_TICKS) / BASE_TICKS:+7.3f}%"
              f"   binding margin {mg}", flush=True)
    spread = 79_224_939 - 79_223_125
    print(f"  the four ties span {spread:,} ticks = "
          f"{100 * spread / BASE_TICKS:.4f}% -- the tie holds to three decimals, "
          f"and\n  nothing in the room explains the residue, so the tiebreak is "
          f"the binding margin.", flush=True)
    dpre = ship["pre_send"] - best["pre_send"]
    lat = dpre * ACCESSES / RATES.reads_per_run
    print(f"\n  The whole measured win is the live leg at 1:1.  {dpre} pre-send "
          f"cells x {ACCESSES:,}\n  accesses / {RATES.reads_per_run:,.0f} reads = "
          f"{lat:.3f} ticks of mean read latency; measured\n  "
          f"{(80_083_592 - 79_223_125) / RATES.reads_per_run:.3f}.  The tail's "
          f"twenty cells account for the remaining\n  "
          f"{(80_083_592 - 79_223_125) / RATES.reads_per_run - lat:.3f} -- about "
          f"3 % of a pre-send cell each, against the rate table's 8.9 %.",
          flush=True)

    print("\n-- what is left, and the router's own answer for it --", flush=True)
    from circuit import Op, route_open
    box = (17, 5, 27, 12)
    got = None
    for length in range(6, 10):
        for ry in range(5, 13):
            for rx in range(18, 27):
                if not binds((rx, ry), "r", "mem_resp"):
                    continue
                c = route_open([Op("W"), Op("M"), Op("b"), Op("x"),
                                Op("r", at=(rx, ry)), Op("S")],
                               box=box, start=(19, 8), heading="S", max_ticks=length)
                if c and c.ticks == length:
                    got = (c, (rx, ry))
                    break
            if got:
                break
        if got:
            break
    print(f"  the dispatch and the read target, routed for real from P1's exit "
          f"(19,8) heading\n  SOUTH, with the `r` pinned to a cell that takes the "
          f"ring at west_grow 0 and 4:\n"
          f"    shipped      8 cells   `>WMbx` then `>rS`\n"
          f"    routed floor {got[0].ticks} cells   `r` at {got[1]}\n"
          + "\n".join("      " + ln for ln in got[0].render(box).split("\n")
                      if ln.strip()), flush=True)
    print("\n  Both of the shipped cells are steers: one to turn east out of the "
          "ring's exit and\n  one to turn east out of the `x`.  The router keeps "
          "the man going straight south\n  through W M b, lets the `x` do the "
          "only turn, and lands `r S` on the row below --\n  which would make the "
          "live leg **18**.  It is not a placement move, though: `x` turning\n"
          "  clockwise out of a southward heading sends the READ arm **west**, "
          "and the WRITE arm\n  east across the ring, so both arms and the parked "
          "constant's parity change with it.\n  Priced but NOT landed; it is the "
          "next thing to build, not this one.", flush=True)

    print("\n-- the IR agrees: same leg, scored by place.score --", flush=True)
    for nm, (mx, gx) in (("shipped", (1, 15)), ("FAST", (best["main_x"], best["ring_x"]))):
        lg = leg(mx, gx)
        from ir import Placement
        s = score(Placement(lg), Workload(temperature="cold", f_same=F_SAME,
                                          accesses=ACCESSES))
        print(f"  {nm:8s} ticks {s.ticks:7.2f}  floor {s.floor:7.2f}  "
              f"slack {s.slack:6.2f}  extent {s.extent[0]}x{s.extent[1]}"
              f"={s.footprint}", flush=True)

    print(f"\n  rate table: pre-send {RATES.pre_send}%/tick, post-send **cold** "
          f"{RATES.post_send_cold}%/tick\n  -- "
          f"{RATES.pre_send / RATES.post_send_cold:.1f}x, so a post-send cell is "
          f"worth trading for a pre-send one\n  at up to 11:1 and no further.",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
