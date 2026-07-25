#!/usr/bin/env python3
"""A compact pipe-tape machine for `memory` (100 cells).

Same algorithm as `littleman/examples/memory2.man` -- the 100 cells circulate as
100 values in a two-room pipe ring, and every operation costs exactly one full
revolution -- but re-laid-out, and with three mechanical changes that were each
measured against the reference interpreter:

1. **The worker room is redrawn.**  memory2 spends a 22x23 interior on ~99
   glyphs, because its return lanes claim the last interior column and its three
   rotate blocks are 2 columns x 5 rows each with 4 rows of corridor between
   them.  Here the worker is a 14x12 interior and the freed area goes to the ring
   coil, so the bounding box drops from 31x32 to the size reported by
   :func:`build`.

2. **One `100` literal instead of three.**  memory2 branches on the opcode with
   `X` *before* computing `100 - addr`, so both lanes carry their own
   `` `100`- `` and startup carries a third.  Branching on the opcode's low bit
   with `x` instead (BP = op, set before the address is read) lets the whole
   `r b r M `100` - ` prologue be shared, and each lane only has to arrange for
   B to hold +(100-addr) or -(100-addr).

3. **The 100 zeros are filled by the relay man, in parallel.**  memory2's worker
   pushes them itself and cannot start decoding for 800 ticks.  Here the relay
   man emits them into the *return* pipe while the worker is already decoding
   op 1, so the fill is hidden behind the first revolution.  This is safe (not
   just fast): the worker's k-th ring read is the relay's k-th zero for all
   k <= 100 regardless of interleaving, and deadlock needs both pipes full at
   once, i.e. capacity+capacity < 100 values, which cannot happen.

Pipe binding (SPEC "nearest, not nearest-ready") drives the anchor choice.
Incoming = {input, ring-return}, outgoing = {output, ring-forward}, so an `r`
only competes with the other *incoming* pipe and an `s` with the other
*outgoing* one:

    input  -> NORTH wall      ring-forward -> EAST wall
    output -> NORTH wall      ring-return  -> SOUTH wall

so tape reads sit low (near the south wall) and input reads sit high, tape sends
sit east and the output anchor is the far-west column of the north wall -- only
`S` (which writes *every* outgoing pipe) ever reaches it.
"""

from __future__ import annotations

import sys

from randomfun2026solvers.circuit import Circuit, Collision, E, N, S, W

__all__ = ["build", "relay", "worker"]

# ── the relay room: turnaround + the zero-fill ────────────────────────────────
# Interior 7x6.  `@`100`b` then a counted `0s` loop pushes the 100 initial zeros
# into the RETURN pipe, after which the man drops into a 4x2 forwarding cycle
# (8 cells, two (r,s) pairs -> 4 ticks/value, comfortably ahead of the worker).
REL_IW, REL_IH = 7, 6


def relay() -> Circuit:
    c = Circuit(REL_IW, REL_IH)
    c.run(0, 0, "@`100`")  # A = 100
    c.set(6, 0, "v")
    c.set(6, 1, "<")
    c.run(5, 1, "b", d=W)  # BP = 100
    c.horizontal(1, 5, 0)
    c.set(0, 1, "v")
    c.set(0, 2, ">")
    fill, _ = c.counted_loop(1, 2, "0s")  # 100 zeros -> return pipe
    assert fill == 3
    c.set(3, 2, "v")
    c.vertical(3, 2, 4)
    # forwarding cycle: r,s down the top row, r,s back along the bottom
    c.run(3, 4, ">rsv")
    c.run(6, 5, "<rs^", d=W)
    return c


# ── shared helpers (mirrors value_ring.py) ───────────────────────────────────
def draw_pipe(g: Circuit, pts: list[tuple[int, int]]) -> int:
    """Draw a pipe along the rectilinear polyline `pts` (flow order).  Returns
    the cell count, which is the pipe's capacity."""
    cells = cells_of(pts)
    n = len(cells)
    glyph = {E: ">", W: "<", N: "^", S: "v"}
    for i, (x, y) in enumerate(cells):
        din = (x - cells[i - 1][0], y - cells[i - 1][1]) if i else None
        dout = (cells[i + 1][0] - x, cells[i + 1][1] - y) if i < n - 1 else None
        if i == 0:
            ch = glyph[dout]
        elif i == n - 1:
            ch = glyph[din]
        elif din == dout:
            ch = "-" if dout[0] else "|"
        else:
            ch = glyph[dout]
        g.set(x, y, ch)
    return n


def cells_of(pts: list[tuple[int, int]]) -> list[tuple[int, int]]:
    cells = [pts[0]]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        if x0 != x1 and y0 != y1:
            raise Collision(f"pipe leg {(x0, y0)}->{(x1, y1)} is not rectilinear")
        sx = (x1 > x0) - (x1 < x0)
        sy = (y1 > y0) - (y1 < y0)
        x, y = x0, y0
        while (x, y) != (x1, y1):
            x, y = x + sx, y + sy
            cells.append((x, y))
    return cells


def stamp(g: Circuit, ox: int, oy: int, art: list[str]) -> None:
    for y, row in enumerate(art):
        for x, ch in enumerate(row):
            if ch != " ":
                g.set(ox + x, oy + y, ch)


def walls(g: Circuit, ox: int, oy: int, iw: int, ih: int) -> None:
    for x in range(-1, iw + 1):
        g.set(ox + x, oy - 1, "+" if x in (-1, iw) else "-")
        g.set(ox + x, oy + ih, "+" if x in (-1, iw) else "-")
    for y in range(ih):
        g.set(ox - 1, oy + y, "|")
        g.set(ox + iw, oy + y, "|")
