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
from typing import TYPE_CHECKING

from randomfun2026solvers.circuit import Circuit, Collision
from randomfun2026solvers.dataflow_relay import relay
from randomfun2026solvers.sudoku_cfg import FILE_WORDS, RING_WORDS
from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

if TYPE_CHECKING:  # pragma: no cover
    from randomfun2026solvers.man_debug import DebugMap

__all__ = ["IH", "IW", "build", "build_grid", "worker"]

# ── worker geometry ───────────────────────────────────────────────────────────
#: Interior of the one worker room.
IW, IH = 25, 18

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
    # The setup path does not get a corridor row of its own: it drops down the
    # one column the main loop never touches and merges into ROT2's own return.
    c.set(0, 0, "@")
    c.run(1, 0, "9b0")                       # A = 9; BP = 9; A = 0
    c.horizontal(0, 3, 20)
    c.counted_loop(20, 0, "s")               # 9 x { s(ring) }, exit (22,0) east
    _s(c, 21, 1, "ring")
    c.horizontal(0, 21, 24)
    c.set(24, 0, "v")
    c.vertical(24, 0, 17)                    # the setup highway, east of everything
    c.set(24, 17, "<")

    # ══ ROUND (rows 3-9): read `r c v`, build P, arm the rotation ═════════════
    c.set(0, 3, ">")                         # ROUND's single entry cell
    # row 3 east:  ri sq | M 3 W / M 6 + M 3 *
    _r(c, 2, 3, "io")                        # A = r
    _s(c, 9, 3, "file")                      # FILE = [r]
    c.run(10, 3, "M3W/")                     # A = r/3, B = r%3
    c.run(14, 3, "M6+M3*")                   # A = 3*(r/3 + 6) = 18 + 3*(r/3)
    c.set(20, 3, "v")
    c.set(20, 4, "<")
    # row 4 west:  sq ri
    _s(c, 18, 4, "file")                     # FILE = [r, 3q']
    _r(c, 5, 4, "io")                        # A = c
    c.set(4, 4, "v")
    c.set(4, 5, ">")
    # row 5 east: sq | M 3 W / | M | rq sq rq | + M 1 {
    _s(c, 9, 5, "file")                      # FILE = [r, 3q', c]
    c.run(10, 5, "M3W/")                     # A = c/3, B = c%3
    c.run(14, 5, "M")                        # B = c/3
    _r(c, 15, 5, "file")                     # A = r
    _s(c, 16, 5, "file")                     # r to the back: [3q', c, r]
    _r(c, 17, 5, "file")                     # A = 3q'; FILE = [c, r]
    c.run(18, 5, "+M1{")                     # A = BB = 1 << (18 + box)
    c.set(22, 5, "v")
    c.set(22, 6, "<")
    # row 6 west: sq rq | M 9 + M 1 { | sq rq | M 1 { M
    _s(c, 18, 6, "file")                     # FILE = [c, r, BB]
    _r(c, 17, 6, "file")                     # A = c;  FILE = [r, BB]
    c.run(16, 6, "M9+M1{", d=W)              # A = BC = 1 << (9 + c)
    _s(c, 10, 6, "file")                     # FILE = [r, BB, BC]
    _r(c, 9, 6, "file")                      # A = r;  FILE = [BB, BC]
    c.run(8, 6, "M1{M", d=W)                 # A = B = BR = 1 << r
    c.set(4, 6, "v")
    c.set(4, 7, ">")
    # row 7 east: rq + M rq + M
    _r(c, 7, 7, "file")                      # A = BB
    c.run(8, 7, "+M")                        # A = B = BB + BR
    _r(c, 10, 7, "file")                     # A = BC
    c.run(11, 7, "+M")                       # A = B = P -- B is pinned from here
    c.set(13, 7, "v")
    c.set(13, 8, "<")
    # row 8 west: ri
    _r(c, 5, 8, "io")                        # A = v
    c.set(4, 8, "v")
    c.set(4, 9, ">")
    # row 9 east: sq b m
    _s(c, 9, 9, "file")                      # FILE = [v]
    c.run(10, 9, "bm")                       # BP = v - 1
    c.set(12, 9, "v")
    c.set(12, 10, ">")
    c.horizontal(10, 12, 23)

    # ══ ROT1 (rows 10-12): rotate `v - 1` slots to bring WORD[v] to the head ══
    # `r` clobbers A only, so B = P rides through the loop untouched.  Row 10 is
    # both the odd-tail return lane and the way in: a man walking east over the
    # lane's `>` is merely re-told to head east.
    c.set(23, 10, "v")
    rot1 = c.counted_ring_horizontal(19, 11, "rs")
    for x, y in ((20, 11), (22, 12)):
        _r(c, x, y, "ring")
    for x, y in ((21, 11), (21, 12)):
        _s(c, x, y, "ring")
    assert rot1 == [(23, 13), (19, 10)], rot1
    c.set(19, 10, ">")                       # the odd-tail lane, back to the entry

    # ══ ACCESS (row 13, walked west): the whole round's test and set ══════════
    c.set(23, 13, "<")
    _r(c, 22, 13, "ring")                    # A = WORD[v]
    c.set(21, 13, "+")                       # A = WORD[v] + P
    _s(c, 20, 13, "ring")                    # push it back
    c.run(19, 13, "&-", d=W)                 # A = ((WORD[v] + P) & P) - P
    c.set(17, 13, "X")                       # 0 -> west = OK, else BAD

    # ── BAD (row 12): emit 0 and stop; the case ends here by the rules ────────
    c.set(17, 12, "<")                       # X's counter-clockwise (negative) lane
    c.horizontal(12, 17, 6)
    c.run(6, 12, "0", d=W)
    _s(c, 5, 12, "io")
    c.set(4, 12, "H")
    # X's clockwise (positive) lane.  Unreachable -- see the module docstring --
    # but wired, so that the control flow does not rest on the argument.
    c.set(17, 14, " ")                       # crosses OK's run east at a blank
    c.set(17, 15, "<")
    c.horizontal(15, 17, 11)
    c.set(11, 15, "^")
    c.set(11, 14, " ")
    c.set(11, 13, " ")
    c.set(11, 12, "<")                       # merges into BAD, already heading west

    # ── OK (row 13): emit 1, then arm the `9 - v` slots back to phase 0 ───────
    c.horizontal(13, 17, 10)
    c.run(9, 13, "1", d=W)
    _s(c, 8, 13, "io")
    _r(c, 7, 13, "file")                     # A = v
    c.run(6, 13, "NM9+b", d=W)               # BP = 9 - v
    c.set(1, 13, "v")
    c.set(1, 14, ">")
    c.horizontal(14, 1, 23)

    # ══ ROT2 (rows 14-16): the phase restore ═════════════════════════════════
    c.set(23, 14, "v")
    rot2 = c.counted_ring_horizontal(19, 15, "rs")
    for x, y in ((20, 15), (22, 16)):
        _r(c, x, y, "ring")
    for x, y in ((21, 15), (21, 16)):
        _s(c, x, y, "ring")
    assert rot2 == [(23, 17), (19, 14)], rot2
    c.set(19, 14, ">")                       # the odd-tail lane, back to the entry

    # ── back to ROUND, up column 0; the setup path merges in from the east ────
    c.set(23, 17, "<")
    c.horizontal(17, 23, 0)
    c.set(0, 17, "^")
    c.vertical(0, 17, 3)
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


#: Interior rows each block owns, for the debug sidecar and the row-census test.
BLOCK_ROWS: dict[str, tuple[int, int]] = {
    "INIT": (0, 0), "FILL": (0, 2), "ROUND": (3, 9), "ROT1": (10, 12),
    "ACCESS": (13, 13), "BAD": (12, 12), "OK": (13, 13), "ROT2": (14, 16),
}


def build_grid() -> tuple[list[str], "DebugMap", dict[str, object]]:
    """The grid, a labelled overlay and the numbers worth asserting on."""
    from randomfun2026solvers.man_debug import DebugMap
    from randomfun2026solvers.sudoku_cfg import WORKER, worker_glyph_cells

    rows = build()
    d = DebugMap("sudoku-validity - transposed ring machine")
    d.region("input", *IN_ROOM, 3, 3, color="#64748b",
             note="r c v, three ints a round")
    d.region("output", *OUT_ROOM, 3, 3, color="#64748b",
             note="1 while consistent, then a single 0")
    d.region("file-relay", *FILE_RELAY, RELAY_W + 2, RELAY_H + 2, color="#0ea5e9",
             note=f"turnaround of the {FILE_WORDS}-word scratch file")
    d.region("ring-relay", *RING_RELAY, RELAY_W + 2, RELAY_H + 2, color="#0ea5e9",
             note=f"turnaround of the {RING_WORDS}-word per-value store")
    for name, (y0, y1) in BLOCK_ROWS.items():
        d.region(f"block:{name}", WX, WY + y0, IW, y1 - y0 + 1,
                 note=" ".join(WORKER[name][0]), color="#f59e0b", tags=["block"])
    for band, (lo, hi) in (("IO", (0, 6)), ("FILE", (9, 17)), ("RING", (18, IW - 1))):
        d.region(f"band:{band}", WX + lo, WY, hi - lo + 1, IH, color="#1f2937",
                 note=f"{band} pipe ops must stand here; nearest column binds")
    info = {
        "grid": (max(len(r) for r in rows), len(rows)),
        "worker": (IW, IH),
        "blocks": len(WORKER),
        "glyph_cells": worker_glyph_cells(),
    }
    return rows, d, info


if __name__ == "__main__":  # pragma: no cover - the generator's CLI
    import argparse
    from pathlib import Path

    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        print(worker().ruler())
        raise SystemExit(0)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", "--out", dest="man", type=Path, help="write the grid here")
    ap.add_argument("--html", type=Path, help="write a labelled debug overlay here")
    ap.add_argument("--json", type=Path, help="write the debug region sidecar here")
    args = ap.parse_args()
    grid, dbg, meta = build_grid()
    if args.man:
        args.man.write_text("\n".join(grid) + "\n")
    if args.html:
        dbg.write_html(grid, args.html)
    if args.json:
        dbg.write_json(args.json)
    if not (args.man or args.html or args.json):
        print("\n".join(grid))
    else:
        print(meta)
