#!/usr/bin/env python3
"""Value-ring machines for `reverse-a-list` and `sort-numbers`.

One shape solves both problems: the current list lives as *k values circulating
in a pipe ring* (worker room -> forward pipe -> relay room -> return pipe ->
worker), and the worker walks counted loops over it. Only the inner operation
differs.

    reverse-a-list   rotate k-1 values, then read the head and emit it without
                     recirculating.  Ring order v1..vk emits vk..v1.  The count
                     k lives in **B**, which survives every `r`/`s`.

    sort-numbers     two passes per output: pass 1 finds the minimum, pass 2
                     scans to the first value equal to it, emits it and replaces
                     it with a SENTINEL bigger than any input.  B is busy
                     holding the running minimum, so the count cannot live
                     there -- instead the ring carries a **header word** (= n) as
                     its first value.  Every pass consumes exactly one whole lap,
                     so the ring stays aligned and each pass starts by reading
                     the header back: the count is *readable memory made of
                     pipe*.  The round ends when the minimum comes back equal to
                     the sentinel (all n slots retired), which needs no counter
                     at all.

Pipe binding (SPEC "nearest, not nearest-ready") is what dictates the layout.
Incoming pipes are {input, ring-return}, outgoing are {output, ring-forward}, so
an `r` only ever competes with the other *incoming* pipe and an `s` with the
other *outgoing* one.  The two are separated on opposite walls:

    input        -> NORTH wall     ring-forward -> EAST wall
    ring-return  -> SOUTH wall     output       -> WEST wall

so every `r(input)` sits high, every `r(ring)` low, every `s(ring)` east and the
lone `s(output)` west.  `littleman/tools/route-check.mjs` verifies each one
against the engine.
"""

from __future__ import annotations

import sys

from randomfun2026solvers.circuit import Circuit, Collision, E, N, S, W

__all__ = ["build_reverse", "SENTINEL"]

# NOTE: `build_sort` is designed above but NOT YET IMPLEMENTED -- the header-word
# trick is the plan of record, not working code.

# A value larger than any list element (|x| <= 1e6 for reverse, 1e4 for sort).
SENTINEL = 1_000_001

# ── relay: the ring's turnaround room (6 ticks/word, same as the worker) ──────
RELAY = [
    "+----+",
    "|@ >v|",
    "|  sr|",
    "|  ^<|",
    "+----+",
]


def draw_pipe(g: Circuit, pts: list[tuple[int, int]]) -> int:
    """Draw a pipe along the rectilinear polyline `pts` (in flow order).

    Arrowheads at the first cell, every bend and the last cell; `-`/`|` bodies on
    straight runs.  The first cell's *backward* neighbour and the last cell's
    *forward* neighbour must be room borders.  Returns the cell count, which is
    the pipe's capacity.
    """
    cells: list[tuple[int, int]] = [pts[0]]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        if x0 != x1 and y0 != y1:
            raise Collision(f"pipe leg {(x0, y0)}->{(x1, y1)} is not rectilinear")
        sx = (x1 > x0) - (x1 < x0)
        sy = (y1 > y0) - (y1 < y0)
        x, y = x0, y0
        while (x, y) != (x1, y1):
            x, y = x + sx, y + sy
            cells.append((x, y))
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


def stamp(g: Circuit, ox: int, oy: int, art: list[str]) -> None:
    for y, row in enumerate(art):
        for x, ch in enumerate(row):
            if ch != " ":
                g.set(ox + x, oy + y, ch)


def walls(g: Circuit, ox: int, oy: int, iw: int, ih: int) -> None:
    """Draw a room's border around the interior rectangle at (ox,oy)+(iw,ih)."""
    for x in range(-1, iw + 1):
        g.set(ox + x, oy - 1, "+" if x in (-1, iw) else "-")
        g.set(ox + x, oy + ih, "+" if x in (-1, iw) else "-")
    for y in range(ih):
        g.set(ox - 1, oy + y, "|")
        g.set(ox + iw, oy + y, "|")


def lit(n: int) -> str:
    return str(n) if 0 <= n < 10 else f"`{n}`"


# ══════════════════════════════════════════════════════ reverse-a-list ════════
#
#   MAIN     r(in)->n ; b ; M                      BP = n, B = n
#   LOAD     x n { r(in) ; s(ring) }               ring = v1..vn
#   EMIT     1 ; - ; N ; b ; M                     A = BP = B = k-1
#            x (k-1) { r(ring) ; s(ring) }         rotate vk to the head
#            r(ring) ; s(out)                      emit vk, do NOT recirculate
#            W ; M ; X                             A = B = k-1; 0 -> MAIN, + -> EMIT
#
# Interior 12x14.  Anchors: input = north wall col 8, output = west wall row 13,
# ring-forward = east wall row 7, ring-return = south wall col 10.
REV_IW, REV_IH = 12, 14
REV_IN_COL, REV_OUT_ROW, REV_FWD_ROW, REV_RET_COL = 8, 13, 7, 10


def reverse_worker() -> Circuit:
    c = Circuit(REV_IW, REV_IH)

    # ── MAIN (row 0), entered from the north lane's `>` at (0,0) ────────────
    c.set(0, 0, ">")
    c.run(1, 0, "@")  # spawn: nop, keeps heading east
    c.run(7, 0, "rbM")  # A = n (input), BP = n, B = n
    c.horizontal(0, 1, 7)
    c.set(10, 0, "v")
    c.set(10, 1, "<")
    c.set(9, 1, "v")

    # ── LOAD: n x { r(input), s(ring) } ────────────────────────────────────
    load_exit, _ = c.counted_loop(9, 2, "rs")
    assert load_exit == 11
    c.route((11, 2), E, [(11, 6)], (1, 6), S)

    # ── EMIT head (row 7): A = BP = B = k-1 ────────────────────────────────
    c.set(1, 7, ">")
    c.run(2, 7, "1-NbM")
    c.set(7, 7, "v")
    c.set(7, 8, ">")
    c.set(8, 8, " ")

    # ── ROT: (k-1) x { r(ring), s(ring) }, then read the head and emit it ──
    rot_exit, _ = c.counted_loop(9, 8, "rs")
    assert rot_exit == 11
    c.set(11, 8, "v")
    c.vertical(11, 8, 12)
    c.set(11, 12, "r")  # vk off the ring
    c.set(11, 13, "<")
    c.horizontal(13, 11, 2)
    c.run(2, 13, "s", d=W)  # emit it (output pipe, west wall)
    c.run(1, 13, "W", d=W)  # A = k-1, B = vk
    c.set(0, 13, "^")
    c.set(0, 12, "M")  # B = k-1
    c.set(0, 11, "X")  # 0 -> MAIN (north), + -> EMIT (east)
    c.route((0, 10), N, [], (0, 0), E)  # zero lane: back to MAIN
    c.route((1, 11), E, [], (1, 7), E)  # positive lane: back to EMIT
    return c


def build_reverse() -> list[str]:
    """The whole `reverse-a-list` machine: worker + I/O rooms + relay + ring."""
    g = Circuit(64, 40)
    wx, wy = 6, 6
    stamp(g, wx, wy, reverse_worker().rows())
    walls(g, wx, wy, REV_IW, REV_IH)

    # input room, above (its pipe must enter the worker's NORTH wall)
    icol = wx + REV_IN_COL
    stamp(g, icol - 1, 0, ["+-+", "|I|", "+-+"])
    draw_pipe(g, [(icol, 3), (icol, 4)])

    # output room, west (its pipe leaves the worker's WEST wall)
    orow = wy + REV_OUT_ROW
    stamp(g, 0, orow - 1, ["+-+", "|O|", "+-+"])
    draw_pipe(g, [(4, orow), (3, orow)])

    # the ring: worker east wall -> relay (east) -> band below -> worker south wall
    stamp(g, 20, 15, RELAY)
    n_fwd = draw_pipe(g, [(wx + REV_IW + 1, wy + REV_FWD_ROW), (21, 13), (21, 14)])
    n_ret = draw_pipe(
        g,
        [
            (21, 20),
            (21, 22),
            (25, 22),
            (25, 24),
            (wx + REV_RET_COL, 24),
            (wx + REV_RET_COL, wy + REV_IH + 1),
        ],
    )
    if n_fwd + n_ret < 16 + 4:
        raise Collision(f"ring holds {n_fwd + n_ret} values, need >= 20")
    return [r.rstrip() for r in g.rows() if r.strip()]


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "reverse"
    if which == "reverse-worker":
        print(reverse_worker().ruler())
    else:
        print("\n".join(build_reverse()))
