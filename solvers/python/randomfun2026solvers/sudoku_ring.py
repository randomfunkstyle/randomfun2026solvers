#!/usr/bin/env python3
"""Lay :data:`sudoku_cfg.WORKER` out as one room, two pipe rings and six pipes.

The design is :mod:`randomfun2026solvers.sudoku_cfg`; this module only *places*
it.  Eleven blocks, 82 glyph cells, one worker room of 26x19.

## Column discipline

All six pipes anchor on the room's **north** wall, so the Manhattan distance
from any cell to any of them is ``|x - col| + y + 1``: the ``y`` term is common
and "nearest pipe" collapses to *nearest column* at every row.  Receives and
sends choose among disjoint sets (incoming vs outgoing pipes), so there are two
independent column rules:

    receive columns   input 1   file 12   ring 23    ->  ri 0-6   rq 7-17   rr 18+
    send    columns   output 4  file 13   ring 24    ->  so 0-8   sq 9-18   sr 19+

Every midpoint is a half-integer, so no op is ever placed on a tie.
:func:`_r` and :func:`_s` refuse to write a pipe glyph outside its band, which
is what makes the layout below checkable rather than hopeful -- a mis-bound
``r`` reads a plausible number from the wrong ring and fails silently.

## Rows

    0-2   INIT `9 b 0` and the nine-word fill loop (a `counted_loop`)
    3     the fill loop's exit corridor, west into ROUND's entry
    4-10  ROUND -- 51 glyphs serpentined over seven rows.  The wraps are forced
          by the band order: `ri`(west) -> `sq`(middle) has to walk east and
          `sq` -> `ri` has to walk west, so every IO/FILE alternation costs a row.
    11-13 ROT1: a `counted_ring_horizontal` at columns 20-24, with row 11 doing
          double duty as the odd-tail return lane *and* as the entry corridor --
          a man walking east over the return lane's `>` merely re-turns east.
    14    ACCESS `r + s & - X` walked **west** from column 24, then OK on the
          same row: `X`'s zero lane continues west and runs straight into it.
    13/15 ACCESS's two failing lanes.  `X` entered heading west turns north on a
          positive A and south on a negative one; both reach BAD on row 13.
    15-17 ROT2, the phase restore, sharing row 15 with OK's run east to it.
    18    the return corridor, west then north up column 0 into ROUND.

`A > 0` is in fact unreachable at the `X` -- ``t = (WORD + P) & P`` is a subset
of ``P``'s three bits, so ``t - P <= 0`` always -- but the lane is routed to BAD
anyway, because a layout that depends on an arithmetic argument for its
*control flow* is a layout that fails silently when the argument is wrong.
"""

from __future__ import annotations

import sys

from randomfun2026solvers.circuit import Circuit, Collision
from randomfun2026solvers.dataflow_relay import relay
from randomfun2026solvers.sudoku_cfg import FILE_WORDS, RING_WORDS
from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

__all__ = ["IH", "IW", "build", "worker"]

# ── worker geometry ───────────────────────────────────────────────────────────
#: Interior of the one worker room.
IW, IH = 26, 19

#: North-wall anchor columns, in the worker's interior coordinates.
IN_COL, OUT_COL = 1, 4
FILE_IN, FILE_OUT = 12, 13
RING_IN, RING_OUT = 23, 24

#: Incoming pipes decide `r`; outgoing pipes decide `s`.  Two separate rules.
R_COLS = {"io": IN_COL, "file": FILE_IN, "ring": RING_IN}
S_COLS = {"io": OUT_COL, "file": FILE_OUT, "ring": RING_OUT}


def _nearest(cols: dict[str, int], x: int) -> str:
    """Which pipe a op at interior column `x` binds to.

    Ties break by reading order; every pipe sits on the same wall row, so that
    is leftmost.  The layout keeps every midpoint half-integral so no op is ever
    actually on a tie -- this only has to agree with the engine, not be clever.
    """
    return min(cols, key=lambda k: (abs(x - cols[k]), cols[k]))


def _r(c: Circuit, x: int, y: int, want: str) -> None:
    """Place a receive, asserting the column binds it to the `want` pipe."""
    got = _nearest(R_COLS, x)
    if got != want:
        raise Collision(f"'r' at ({x},{y}) binds {got}, wanted {want}")
    c.set(x, y, "r")


def _s(c: Circuit, x: int, y: int, want: str) -> None:
    """Place a send, asserting the column binds it to the `want` pipe."""
    got = _nearest(S_COLS, x)
    if got != want:
        raise Collision(f"'s' at ({x},{y}) binds {got}, wanted {want}")
    c.set(x, y, "s")


W = (-1, 0)


def worker() -> Circuit:  # noqa: PLR0915 - one grid, laid out row by row
    """The single worker room: the eleven blocks of :data:`sudoku_cfg.WORKER`."""
    c = Circuit(IW, IH)

    # ══ INIT (row 0) + FILL (rows 0-2) ════════════════════════════════════════
    # `s` leaves A alone, so nine empty words is one literal and a counted loop.
    c.set(0, 0, "@")
    c.run(1, 0, "9b0")                       # A = 9; BP = 9; A = 0
    c.horizontal(0, 3, 19)
    c.counted_loop(19, 0, "s")               # 9 x { s(ring) }, exit (21,0) east
    _s(c, 20, 1, "ring")
    c.horizontal(0, 20, 25)
    c.set(25, 0, "v")
    c.vertical(25, 0, 3)
    c.set(25, 3, "<")
    c.horizontal(3, 25, 0)
    c.set(0, 3, "v")
    c.set(0, 4, ">")                         # ROUND's single entry cell

    # ══ ROUND (rows 4-10): read `r c v`, build P, arm the rotation ════════════
    # row 4 east:  ri sq | M 3 W / M 6 + M 3 *
    _r(c, 2, 4, "io")                        # A = r
    _s(c, 9, 4, "file")                      # FILE = [r]
    c.run(10, 4, "M3W/")                     # A = r/3, B = r%3
    c.run(14, 4, "M6+M3*")                   # A = 3*(r/3 + 6) = 18 + 3*(r/3)
    c.set(20, 4, "v")
    c.set(20, 5, "<")
    # row 5 west:  sq ri
    _s(c, 18, 5, "file")                     # FILE = [r, 3q']
    _r(c, 5, 5, "io")                        # A = c
    c.set(4, 5, "v")
    c.set(4, 6, ">")
    # row 6 east:  sq | M 3 W / | M | rq sq rq | + M 1 {
    _s(c, 9, 6, "file")                      # FILE = [r, 3q', c]
    c.run(10, 6, "M3W/")                     # A = c/3, B = c%3
    c.run(14, 6, "M")                        # B = c/3
    _r(c, 15, 6, "file")                     # A = r
    _s(c, 16, 6, "file")                     # r to the back: [3q', c, r]
    _r(c, 17, 6, "file")                     # A = 3q'; FILE = [c, r]
    c.run(18, 6, "+M1{")                     # A = BB = 1 << (18 + box)
    c.set(22, 6, "v")
    c.set(22, 7, "<")
    # row 7 west: sq rq | M 9 + M 1 { | sq rq | M 1 { M
    _s(c, 18, 7, "file")                     # FILE = [c, r, BB]
    _r(c, 17, 7, "file")                     # A = c;  FILE = [r, BB]
    c.run(16, 7, "M9+M1{", d=W)              # A = BC = 1 << (9 + c)
    _s(c, 10, 7, "file")                     # FILE = [r, BB, BC]
    _r(c, 9, 7, "file")                      # A = r;  FILE = [BB, BC]
    c.run(8, 7, "M1{M", d=W)                 # A = B = BR = 1 << r
    c.set(4, 7, "v")
    c.set(4, 8, ">")
    # row 8 east: rq + M rq + M
    _r(c, 7, 8, "file")                      # A = BB
    c.run(8, 8, "+M")                        # A = B = BB + BR
    _r(c, 10, 8, "file")                     # A = BC
    c.run(11, 8, "+M")                       # A = B = P -- B is pinned from here
    c.set(13, 8, "v")
    c.set(13, 9, "<")
    # row 9 west: ri
    _r(c, 5, 9, "io")                        # A = v
    c.set(4, 9, "v")
    c.set(4, 10, ">")
    # row 10 east: sq b m
    _s(c, 9, 10, "file")                     # FILE = [v]
    c.run(10, 10, "bm")                      # BP = v - 1
    c.set(12, 10, "v")
    c.set(12, 11, ">")
    c.horizontal(11, 12, 24)

    # ══ ROT1 (rows 11-13): rotate `v - 1` slots to bring WORD[v] to the head ══
    # `r` clobbers A only, so B = P rides through the loop untouched.  Row 11 is
    # both the odd-tail return lane and the way in: a man walking east over the
    # lane's `>` is merely re-told to head east.
    c.set(24, 11, "v")
    rot1 = c.counted_ring_horizontal(20, 12, "rs")
    for x, y in ((21, 12), (23, 13)):
        _r(c, x, y, "ring")
    for x, y in ((22, 12), (22, 13)):
        _s(c, x, y, "ring")
    assert rot1 == [(24, 14), (20, 11)], rot1
    c.set(20, 11, ">")                       # the odd-tail lane, back to the entry

    # ══ ACCESS (row 14, walked west): the whole round's test and set ══════════
    c.set(24, 14, "<")
    _r(c, 23, 14, "ring")                    # A = WORD[v]
    c.set(22, 14, "+")                       # A = WORD[v] + P
    _s(c, 21, 14, "ring")                    # push it back
    c.run(20, 14, "&-", d=W)                 # A = ((WORD[v] + P) & P) - P
    c.set(18, 14, "X")                       # 0 -> west = OK, else BAD

    # ── BAD (row 13): emit 0 and stop; the case ends here by the rules ────────
    c.set(18, 13, "<")                       # X's counter-clockwise (negative) lane
    c.horizontal(13, 18, 6)
    c.run(6, 13, "0", d=W)
    _s(c, 5, 13, "io")
    c.set(4, 13, "H")
    # X's clockwise (positive) lane.  Unreachable -- see the module docstring --
    # but wired, so that the control flow does not rest on the argument.
    c.set(18, 15, " ")                       # crosses OK's run east at a blank
    c.set(18, 16, "<")
    c.horizontal(16, 18, 11)
    c.set(11, 16, "^")
    c.set(11, 15, " ")
    c.set(11, 14, " ")
    c.set(11, 13, "<")                       # merges into BAD, already heading west

    # ── OK (row 14): emit 1, then arm the `9 - v` slots back to phase 0 ───────
    c.horizontal(14, 18, 10)
    c.run(9, 14, "1", d=W)
    _s(c, 8, 14, "io")
    _r(c, 7, 14, "file")                     # A = v
    c.run(6, 14, "NM9+b", d=W)               # BP = 9 - v
    c.set(1, 14, "v")
    c.set(1, 15, ">")
    c.horizontal(15, 1, 24)

    # ══ ROT2 (rows 15-17): the phase restore ═════════════════════════════════
    c.set(24, 15, "v")
    rot2 = c.counted_ring_horizontal(20, 16, "rs")
    for x, y in ((21, 16), (23, 17)):
        _r(c, x, y, "ring")
    for x, y in ((22, 16), (22, 17)):
        _s(c, x, y, "ring")
    assert rot2 == [(24, 18), (20, 15)], rot2
    c.set(20, 15, ">")                       # the odd-tail lane, back to the entry

    # ── back to ROUND, up column 0 ───────────────────────────────────────────
    c.set(24, 18, "<")
    c.horizontal(18, 24, 0)
    c.set(0, 18, "^")
    c.vertical(0, 18, 4)
    return c


# ── the whole machine ─────────────────────────────────────────────────────────
#: Worker room origin.  The seven rows above it carry both I/O rooms, both
#: turnaround rooms and all six pipes, which is what keeps the grid square.
WX, WY = 1, 8

#: Turnaround interiors.  4x3 walks a 10-cell perimeter carrying two words, i.e.
#: 5.0 ticks/word -- exactly the `counted_ring_horizontal` worker's own rate, so
#: neither ring is relay-bound (`dataflow_relay.ticks_per_rotation`).
RELAY_W, RELAY_H = 4, 3

#: Band geometry: (x, y) of each room's top-left *wall* cell.
FILE_RELAY, RING_RELAY = (10, 0), (17, 0)
IN_ROOM, OUT_ROOM = (0, 0), (4, 0)


def build() -> list[str]:
    """The worker, the two turnaround rooms, the I/O rooms and the six pipes."""
    w = worker()
    g = Circuit(WX + IW + 1, WY + IH + 1)
    stamp(g, WX, WY, w.rows())
    walls(g, WX, WY, IW, IH)

    stamp(g, *IN_ROOM, ["+-+", "|I|", "+-+"])
    stamp(g, *OUT_ROOM, ["+-+", "|O|", "+-+"])
    stamp(g, *FILE_RELAY, relay(RELAY_W, RELAY_H))
    stamp(g, *RING_RELAY, relay(RELAY_W, RELAY_H))

    band = WY - 2                               # the pipe row against the north wall
    col = {
        "input": WX + IN_COL, "output": WX + OUT_COL,
        "file_in": WX + FILE_IN, "file_out": WX + FILE_OUT,
        "ring_in": WX + RING_IN, "ring_out": WX + RING_OUT,
    }

    draw_pipe(g, [(1, 3), (1, band - 1), (col["input"], band - 1),
                  (col["input"], band)])
    draw_pipe(g, [(col["output"], band), (col["output"], 3)])

    # The file ring is deliberately as short as a ring can be: its latency is
    # paid between a `sq` and the `rq` that pops it, and in ROUND those are
    # sometimes ten glyphs apart.  Two cells each way is the minimum a pipe may
    # be, and 4 cells is exactly FILE_WORDS + 1.
    f_fwd = draw_pipe(g, [(col["file_out"], band), (col["file_out"], band - 1)])
    f_ret = draw_pipe(g, [(col["file_in"], band - 1), (col["file_in"], band)])
    if f_fwd + f_ret < FILE_WORDS + 1:
        raise Collision(f"file ring holds {f_fwd + f_ret}, needs {FILE_WORDS + 1}")

    # The store ring takes the long way round on purpose: nine words have to be
    # resident, and an under-capacity ring deadlocks *silently*.
    rx, ry = RING_RELAY
    east = rx + RELAY_W + 2                     # the column just east of the relay
    r_fwd = draw_pipe(g, [(col["ring_out"], band), (col["ring_out"], ry + 1),
                          (east, ry + 1)])
    r_ret = draw_pipe(g, [(east, ry + 3), (col["ring_in"], ry + 3),
                          (col["ring_in"], band)])
    if r_fwd + r_ret < RING_WORDS + 1:
        raise Collision(f"store ring holds {r_fwd + r_ret}, needs {RING_WORDS + 1}")

    return [r.rstrip() for r in g.rows()]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        print(worker().ruler())
    else:
        print("\n".join(build()))
