#!/usr/bin/env python3
"""What a merge step costs, measured — the number :mod:`subset_sum_merge` rests on.

The sorted meet-in-the-middle replaces a `2^hL * 2^hR` product with a
`2^hL + 2^hR` sum, and every one of those ~2,048 steps is a **two-way merge
step**: test the two stream heads, emit the smaller, advance that side.
:data:`subset_sum_merge.TICKS` prices one at ``compare = 12``, and the whole
8-16x estimate is that figure times the step count.  This probe is the smallest
grid that performs exactly that step, so the charge can be measured on the engine
instead of argued from glyph counts.

**What it computes.**  Input is

    k, <k ascending values for ring P>, k, <k ascending values for ring D>, 2k

and the output is the `2k` values merged in order.  Values are stored biased by
one so no stored word is ever `0`, the same convention :mod:`subset_sum_grid`
uses.

The counts are supplied three times on purpose.  ``b`` loads ``BP`` from ``A``
and every receive clobbers ``A``, so carrying one ``k`` across two fill loops and
a merge would need a spill slot the real machine has better uses for.  A probe
gets to choose its input contract, and re-sending the count costs nothing that is
being measured — the alternative would put a register juggle inside the very loop
whose cost is the point.

**The gadget being measured.**  Both heads have to survive the comparison, and
they do because of two ISA facts that happen to line up:

* ``-`` computes ``A = A - B`` and **leaves B alone**, so the loser's head is
  still live after the test;
* ``r`` writes ``A`` and does **not** clobber ``B`` — the property
  :mod:`subset_sum_scan_probe` relies on to keep its query across a receive.

So with ``A = head_P`` and ``B = head_D``::

    -   A = head_P - head_D          B still head_D
    X   A <= 0  ->  P wins           A > 0  ->  D wins

    P wins:  +  recovers head_P (= A + B),  s emits it,  r reloads A from P
    D wins:  W  puts head_D in A,           s emits it,  r reloads A from D,
             W  puts head_P back in A and the new head_D in B

Neither lane needs a spill slot and neither head is ever re-read, which is what
lets the real machine run the merge off a rotating ring: **no pointer ever backs
up**.  That is the property :mod:`subset_sum_merge` proves for the algorithm and
this probe exercises in hardware.

**What it does not measure.**  In the real generation phase stream `B` is `L + v`
rather than a second ring, so an A-step also pushes its word into the delay FIFO
and a B-step adds `v`.  Those are one ``s`` and one ``+`` on top of what is
measured here, and they are priced separately rather than folded in, so this
number stays the cost of the *merge*, not of one particular use of it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.circuit import Circuit
from randomfun2026solvers.dataflow_relay import relay
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

__all__ = ["BANDS", "IH", "IW", "RING_CAP", "build", "expected", "main", "worker"]

#: Worker interior.
IW, IH = 44, 30

#: North-wall anchor columns.  Every pipe hangs off the **north** wall, so the `y`
#: term of the Manhattan distance is common to all six and "nearest pipe"
#: collapses to "nearest column" at every row — the one-dimensional rule that a
#: block moving down a row cannot silently break.
IN_COL, OUT_COL = 3, 6
PFWD_COL, PRET_COL = 16, 19
DFWD_COL, DRET_COL = 30, 33

#: The column both lanes climb to get back to the loop head.  It is east of every
#: `r`/`s` in the room because the two reload runs span `OUT_COL`..30 between
#: them, so nothing nearer is clear of both.
RET_COL = 13

#: Safe column ranges per target, inclusive.  Midpoints are 11/12.5 (io|p) and
#: 24.5 (p|d); a tie resolves by reading order, which nothing should depend on.
BANDS: dict[str, tuple[int, int]] = {"io": (0, 10), "p": (14, 22), "d": (27, 43)}

#: Words each ring must hold: its values, and one free cell — a ring exactly as
#: long as its contents blocks a send behind its own backlog and deadlocks in
#: silence.
RING_CAP = 40


def _op(c: Circuit, x: int, y: int, glyph: str, band: str) -> None:
    lo, hi = BANDS[band]
    if not lo <= x <= hi:
        raise ValueError(
            f"{glyph!r} at ({x},{y}) is outside the {band!r} band {lo}..{hi}; "
            "it would bind to another pipe"
        )
    c.set(x, y, glyph)


def rin(c: Circuit, x: int, y: int) -> None:
    _op(c, x, y, "r", "io")


def out(c: Circuit, x: int, y: int) -> None:
    _op(c, x, y, "s", "io")


def pr(c: Circuit, x: int, y: int) -> None:
    _op(c, x, y, "r", "p")


def ps(c: Circuit, x: int, y: int) -> None:
    _op(c, x, y, "s", "p")


def dr(c: Circuit, x: int, y: int) -> None:
    _op(c, x, y, "r", "d")


def ds(c: Circuit, x: int, y: int) -> None:
    _op(c, x, y, "s", "d")


def worker() -> Circuit:
    """Read `k`, fill both rings, merge them, emit `2k` words."""
    c = Circuit(IW, IH, strict_corridors=True)

    # ══ INIT: BP = k, B = 1 (the bias every stored word carries) ═════════════
    c.set(1, 0, "@")
    c.horizontal(0, 1, IN_COL)
    rin(c, IN_COL, 0)  # A = k
    c.run(IN_COL + 1, 0, "b1M")  # BP = k; A = 1; B = 1
    c.set(8, 0, "v")
    c.set(8, 1, "<")
    c.horizontal(1, 8, 0)
    c.set(0, 1, "v")

    # ══ FILL P: k x { A = v; A = v + 1; push } ═══════════════════════════════
    c.set(0, 2, ">")  # merge: entry from above, loop from below
    c.set(1, 2, " ")
    c.set(2, 2, "d")  # BP > 0 -> south into the body
    c.set(2, 3, "m")
    c.set(2, 4, ">")
    rin(c, IN_COL, 4)  # A = v
    c.run(IN_COL + 1, 4, "+")  # A = v + 1  (B = 1 survives the receive)
    c.horizontal(4, IN_COL + 1, PFWD_COL - 1)
    ps(c, PFWD_COL - 1, 4)
    c.set(PFWD_COL, 4, "v")
    c.set(PFWD_COL, 5, "<")
    c.horizontal(5, PFWD_COL, 0)
    c.set(0, 5, "^")
    c.vertical(0, 5, 2)

    # ══ RELOAD BP = k for the second fill ════════════════════════════════════
    # `d` fell through east when BP hit zero; walk to the input and count again.
    c.set(3, 2, " ")
    c.horizontal(2, 4, 9)
    c.set(9, 2, "v")
    c.set(9, 3, ">")
    c.horizontal(3, 9, 12)
    c.set(12, 3, "v")
    c.vertical(12, 3, 6)
    c.set(12, 6, "<")
    c.horizontal(6, 12, IN_COL)
    # The man arrives heading **west**, so the ops after the receive have to be
    # laid westward too.  Putting them east of the `r` — the way the first header
    # read does it, where the man is eastbound — leaves them unreachable, and the
    # only symptom is that `BP` is never reloaded and ring D is silently empty.
    rin(c, IN_COL, 6)  # A = k, walked westward
    c.set(2, 6, "b")  # BP = k
    c.set(1, 6, "1")  # A = 1
    c.set(0, 6, "v")
    c.set(0, 7, "M")  # B = 1, the storage bias

    # ══ FILL D ═══════════════════════════════════════════════════════════════
    c.set(0, 8, ">")
    c.set(1, 8, " ")
    c.set(2, 8, "d")
    c.set(2, 9, "m")
    c.set(2, 10, ">")
    rin(c, IN_COL, 10)
    c.run(IN_COL + 1, 10, "+")
    c.horizontal(10, IN_COL + 1, DFWD_COL - 1)
    ds(c, DFWD_COL - 1, 10)
    c.set(DFWD_COL, 10, "v")
    c.set(DFWD_COL, 11, "<")
    c.horizontal(11, DFWD_COL, 0)
    c.set(0, 11, "^")
    c.vertical(0, 11, 8)

    # ══ PRIME: BP = 2k emissions, A = head_P, B = head_D ═════════════════════
    # `d` fell east out of the D fill.  Re-read k twice into BP: `b` sets BP from
    # A, so BP = 2k needs the sum built in A first.
    c.set(3, 8, " ")
    c.horizontal(8, 4, 9)
    c.set(9, 8, "v")
    c.set(9, 9, ">")
    c.horizontal(9, 9, 12)
    c.set(12, 9, "v")
    c.vertical(12, 9, 12)
    c.set(12, 12, "<")
    c.horizontal(12, 12, IN_COL)
    rin(c, IN_COL, 12)  # A = m, the emission count (westward, as above)
    c.set(2, 12, "b")  # BP = m; B is set by the prime below
    c.set(1, 12, "v")
    c.set(1, 13, ">")
    c.horizontal(13, 1, 8)
    c.set(8, 13, "v")
    c.set(8, 14, ">")

    # A = head_P, B = head_D
    c.horizontal(14, 9, PRET_COL - 3)
    pr(c, PRET_COL - 3, 14)  # A = head_P
    c.set(PRET_COL - 2, 14, "M")  # B = head_P   (parked while D is read)
    c.horizontal(14, PRET_COL - 1, DRET_COL)
    dr(c, DRET_COL + 1, 14)  # A = head_D, B = head_P
    c.set(DRET_COL + 2, 14, "W")  # A = head_P, B = head_D
    c.set(DRET_COL + 3, 14, "v")
    c.set(DRET_COL + 3, 15, "<")
    c.horizontal(15, DRET_COL + 2, 14)
    c.set(13, 15, "v")
    # `v` at the loop head, not a blank: the prime walks *through* it heading
    # south, and the return lane arrives from the east and turns south on it.
    c.set(13, 16, "v")
    c.set(13, 17, ">")  # and everyone enters the station heading EAST

    # ══ MERGE STEP — the thing being measured ════════════════════════════════
    #   (14,17) d   BP > 0 -> south into the body; exhausted -> east, halt
    #   (14,18) m   one emission accounted for
    #   (14,19) >   back to eastbound for the test
    #   (15,19) -   A = head_P - head_D,  B still head_D
    #   (16,19) X   branch on the sign
    #
    # **The station has to run eastbound.**  `d` turns the man *clockwise* when
    # `BP > 0`, exactly as the fill loops rely on, so it only reaches `m` if he
    # arrives heading east; fed a southbound man it sends him west instead and
    # the loop silently tests a man travelling the wrong way.  `X` then inherits
    # that heading, and with the man eastbound its exits are the ones
    # `machine.py`'s adapter documents:
    #
    #   A <  0   head_P < head_D   P wins   ccw       -> north
    #   A == 0   equal             P wins   straight  -> east
    #   A >  0   head_P > head_D   D wins   cw        -> south
    c.set(14, 17, "d")
    c.set(14, 18, "m")
    c.set(14, 19, ">")
    c.set(15, 19, "-")
    c.set(16, 19, "X")

    # ── P wins: recover head_P, emit it, reload A from P ─────────────────────
    # The `< 0` and `== 0` outcomes leave on different rows and converge on the
    # `v` at column 22, which the northern man turns on and the eastern man walks
    # straight through.
    c.set(16, 18, ">")  # A < 0: north out of the X, then east
    c.horizontal(18, 17, 21)
    c.set(22, 18, "v")
    c.horizontal(19, 17, 21)  # A == 0: straight east
    c.set(22, 19, "v")
    c.set(22, 20, "+")  # A = head_P  (B is still head_D)
    c.set(22, 21, "<")
    c.horizontal(21, 21, OUT_COL + 1)
    out(c, OUT_COL, 21)  # emit head_P; the man is heading west
    c.set(OUT_COL - 1, 21, "v")
    c.set(OUT_COL - 1, 22, ">")
    c.horizontal(22, OUT_COL, PRET_COL - 2)
    pr(c, PRET_COL - 1, 22)  # A = next head_P, B untouched
    c.set(PRET_COL, 22, "v")
    c.set(PRET_COL, 23, "<")
    c.horizontal(23, PRET_COL - 1, RET_COL + 1)
    c.set(RET_COL, 23, "^")  # the D lane climbs straight through this cell

    # ── D wins: swap head_D into A, emit, reload, swap back ─────────────────
    # The descent has to clear P's westward emit, and the only columns that do
    # are **west of `OUT_COL`** — anything between 6 and 22 is crossed by it.
    # Going east instead runs into P's converge column on row 20.
    c.set(16, 20, "<")  # A > 0: clockwise, south, then west out of the station
    c.horizontal(20, 15, 4)
    c.set(3, 20, "v")
    c.vertical(3, 21, 26)
    c.set(3, 27, ">")
    # `-` left the *difference* in A, not `head_P`, so this lane has to recover
    # the head before swapping — exactly as the P lane does.  Swapping straight
    # away puts the difference in B, the next step subtracts against garbage, and
    # the machine still runs and still emits: it just emits the wrong number.
    c.set(4, 27, "+")  # A = head_P  (diff + head_D)
    c.set(5, 27, "W")  # A = head_D, B = head_P
    out(c, OUT_COL, 27)  # emit head_D; the man carries on **east** from here
    c.horizontal(27, OUT_COL + 1, DRET_COL)
    dr(c, DRET_COL + 1, 27)  # A = next head_D, B = head_P
    c.set(DRET_COL + 2, 27, "W")  # A = head_P, B = next head_D
    c.set(DRET_COL + 3, 27, "v")
    c.set(DRET_COL + 3, 28, "<")
    c.horizontal(28, DRET_COL + 2, RET_COL + 1)
    c.set(RET_COL, 28, "^")

    # ── the shared climb home ────────────────────────────────────────────────
    # Straight back **west** to the column the station already sits beside, not
    # out east and around.  The long way costs ~50 corridor cells a loop, and a
    # merge step is corridor-dominated: the station itself is ten glyphs.
    c.vertical(RET_COL, 27, 24)
    c.vertical(RET_COL, 22, 18)

    # ══ DONE: BP exhausted, `d` fell east ════════════════════════════════════
    c.set(15, 17, "H")
    return c


def build() -> list[str]:
    """Worker room, I/O rooms, two relays and the four ring pipes."""
    w = worker()
    g = Circuit(96, IH + 12)
    wx, wy = 1, 10
    stamp(g, wx, wy, w.rows())
    walls(g, wx, wy, IW, IH)

    for col, label, into in ((IN_COL, "I", True), (OUT_COL, "O", False)):
        x = wx + col
        stamp(g, x - 1, 0, ["+-+", f"|{label}|", "+-+"])
        draw_pipe(g, [(x, 3), (x, wy - 2)] if into else [(x, wy - 2), (x, 3)])

    # All four stubs sit on row `wy - 2`, one per anchor column, so the two rings
    # can only avoid each other by occupying disjoint *ranges*: P's verticals stay
    # west of column 31 and D's horizontals stay east of it.  Nothing crosses, and
    # that is checked rather than eyeballed — a crossed pipe is a build error, but
    # a pipe routed through another's column is silently a different ring.
    # `relay(6, 4)` is 8x6 once walled, so relay P owns rows 1..6 — which is why
    # ring D's two horizontals run at rows 7 and 8, *under* it, rather than
    # through the row a naive layout would pick.
    stamp(g, 44, 0, relay(6, 4))  # relay ports: (43,2) in, (43,4) out
    n = draw_pipe(g, [(wx + PFWD_COL, wy - 2), (wx + PFWD_COL, 2), (43, 2)])
    n += draw_pipe(g, [(43, 4), (wx + PRET_COL, 4), (wx + PRET_COL, wy - 2)])
    if n < RING_CAP:
        raise ValueError(f"ring P holds {n} words, need >= {RING_CAP}")

    # Relay D sits *below* relay P, not beside it.  Put both on row 1 and D's
    # out-port at row 5 is boxed in — every westward run from it is blocked by
    # relay P's body, and the symptom is not a build error but `fatal: no-pipe`
    # from a send *inside* the relay, hundreds of ticks in.
    stamp(g, 84, 12, relay(6, 4))  # relay ports: (83,14) in, (83,16) out
    # Every terminus must point **into** the thing it feeds — the relay's wall on
    # the way out, the room's north wall on the way back.  A pipe that merely
    # *stops* next to its target leaves an arrow aimed at a blank, which is
    # `invalid pipe body`, and both of this ring's ends got it wrong in turn.
    m = draw_pipe(g, [(wx + DFWD_COL, wy - 2), (wx + DFWD_COL, 6), (82, 6), (82, 14), (83, 14)])
    m += draw_pipe(g, [(83, 16), (60, 16), (60, 7), (wx + DRET_COL, 7), (wx + DRET_COL, wy - 2)])
    if m < RING_CAP:
        raise ValueError(f"ring D holds {m} words, need >= {RING_CAP}")

    return [r.rstrip() for r in g.rows() if r.strip()]


def expected(p: list[int], d: list[int]) -> list[int]:
    """The two sorted lists merged, still carrying the +1 storage bias."""
    return sorted([v + 1 for v in p] + [v + 1 for v in d])


def debug_map() -> DebugMap:
    dbg = DebugMap("subset-sum merge probe — one two-way merge step, measured")
    dbg.region(
        "merge station",
        1 + 14,
        10 + 17,
        2,
        4,
        note="d m - X: both heads survive because `-` leaves B and `r` does not clobber it.",
        tags=["compute"],
    )
    dbg.region(
        "P-wins lane",
        1 + 10,
        10 + 20,
        10,
        6,
        note="+ recovers head_P, emit, reload A from P; B still holds head_D.",
        tags=["lane"],
    )
    dbg.region(
        "D-wins lane",
        1 + 15,
        10 + 20,
        20,
        9,
        note="W, emit, reload, W back — the only lane needing two swaps.",
        tags=["lane"],
    )
    return dbg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--man", type=Path)
    ap.add_argument("--html", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)

    rows = build()
    if args.man:
        args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    if args.html:
        debug_map().write_html(rows, args.html)
    if args.json:
        debug_map().write_json(args.json)
    if not (args.man or args.html or args.json):
        print("\n".join(rows))
    else:
        w = max(len(r) for r in rows)
        print(f"{w} x {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
