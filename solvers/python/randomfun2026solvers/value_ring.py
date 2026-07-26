#!/usr/bin/env python3
"""Value-ring machines for `reverse-a-list` and `sort-numbers`.

One shape solves both problems: the current list lives as *k values circulating
in a pipe ring* (worker room -> forward pipe -> relay room -> return pipe ->
worker), and the worker walks counted loops over it. Only the inner operation
differs.

The canonical reverse machine is authored structurally in
:mod:`randomfun2026solvers.reverse_list_ast`; :func:`build_reverse` remains the
shared public entry point.  The direct ``Circuit`` reverse worker below is kept
as the previous-layout reference.  Sort still uses the direct generator here.

    reverse-a-list   rotate k-1 values, then read the head and emit it without
                     recirculating.  Ring order v1..vk emits vk..v1.  The count
                     k lives in **B**, which survives every `r`/`s`.

    sort-numbers     selection sort, **one lap per output**.  The running
                     minimum is *carried* in B: each value read is compared
                     against the carry and the loser is spilled back into the
                     ring, so after one lap the carry is the minimum and the ring
                     holds the other k-1 values.  B is therefore busy and the
                     count cannot live there -- instead the ring carries a
                     **header word** (= k) as its first value.  Every pass
                     consumes exactly one whole lap, so the ring stays aligned
                     and each pass starts by reading the header back: the count
                     is *readable memory made of pipe*.  Each pass writes back a
                     header of k-1, and a header of 0 both ends the round and
                     leaves the ring empty -- no drain pass, no outer counter.

Pipe binding (SPEC "nearest, not nearest-ready") dictates both layouts.
Incoming pipes are {input, ring-return}, outgoing are {output, ring-forward}, so
an `r` only competes with the other incoming pipe and an `s` with the other
outgoing pipe.  The AST reverse machine states those affinities as vertical
zones; the sort machine uses the north/east anchors documented below.
"""

from __future__ import annotations

import sys

from randomfun2026solvers.circuit import Circuit, Collision, E, N, S, W

__all__ = ["build_reverse", "build_sort", "reverse_worker", "sort_worker"]

# ── relay: U reverses at the incoming pipe (6 ticks/word) ────────────────────
RELAY_NORTH = [
    "+---+",
    "| U<|",
    "| s^|",
    "|@>^|",
    "+---+",
]
RELAY_SOUTH = [
    "+---+",
    "|@>v|",
    "| sv|",
    "| U<|",
    "+---+",
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


# ═══════════════════════════════ legacy direct reverse-a-list reference ════════
#
#   MAIN     r(in)->n ; b ; M                      BP = n, B = n
#   LOAD     x n { r(in) ; s(ring) }               ring = v1..vn
#   EMIT     1 ; - ; N ; b ; M                     A = BP = B = k-1
#            x (k-1) { r(ring) ; s(ring) }         rotate vk to the head
#            r(ring) ; s(out)                      emit vk, do NOT recirculate
#            W ; M ; X                             A = B = k-1; 0 -> MAIN, + -> EMIT
#
# Interior 12x14.  Anchors: input = north wall col 8, output = north wall col 2,
# ring-forward = east wall row 0, ring-return = east wall row 13.  Both I/O rooms
# share the 5-row north band and the whole ring coils into the east strip, which
# is what gets the bounding box down to 21x21 (see `build_reverse`).
REV_IW, REV_IH = 12, 14
REV_IN_COL, REV_OUT_COL, REV_FWD_ROW, REV_RET_ROW = 8, 2, 0, 13


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
    c.run(2, 13, "s", d=W)  # emit it (output pipe, north wall)
    c.run(1, 13, "W", d=W)  # A = k-1, B = vk
    c.set(0, 13, "^")
    c.set(0, 12, "M")  # B = k-1
    c.set(0, 11, "X")  # 0 -> MAIN (north), + -> EMIT (east)
    c.route((0, 10), N, [], (0, 0), E)  # zero lane: back to MAIN
    c.route((1, 11), E, [], (1, 7), E)  # positive lane: back to EMIT
    return c


def build_reverse() -> list[str]:
    """Render the canonical compact AST implementation."""
    from randomfun2026solvers.reverse_list_ast import build

    return build()


# ══════════════════════════════════════════════════════════ sort-numbers ══════
#
# Selection sort with the running minimum **carried in B**, one lap per output.
# The ring carries a HEADER word (the count) ahead of the values, so the count is
# readable even though B is busy and BP is write-only:
#
#   MAIN      r(in)->n ; b ; s(ring)              BP = n, ring = [n]
#             x n { r(in) ; s(ring) }             ring = [n, v1..vn]
#   HEAD      r(ring)->k ; X                      k == 0 -> MAIN (ring is empty)
#   BODY      M ; 1 ; - ; N ; b ; s(ring)         BP = k-1, new header = k-1
#             r(ring) ; M                         carry = v1
#             x (k-1) { r(ring) ; - ; X
#                 A <  0 :  W ; s(ring) ; + ; M   vi < carry: spill carry, carry = vi
#                 A >= 0 :  + ; s(ring)           vi >= carry: spill vi
#             }
#             W ; s(out)                          emit the minimum
#             -> HEAD
#
# Each pass consumes exactly one whole lap, so the ring stays aligned and the
# next pass reads the header back.  A header of 0 terminates the round *and*
# leaves the ring empty, so no drain pass and no outer counter is needed.
# Interior 19x18.  Anchors: input = north wall col 14, output = north wall col 0,
# ring-forward = east wall row 11, ring-return = east wall row 10.  The forward
# anchor sits *below* the return one so the two pipes can share a 3-column strip
# without crossing (the forward pipe's horizontal run would otherwise cut the
# return pipe's descent), and the output anchor is col 0 because at col 1 the
# MAIN `s` is 16 away from *both* outgoing pipes -- a tie, and reading order
# resolves it the wrong way (it would emit the count as program output).
SORT_IW, SORT_IH = 19, 18
SORT_IN_COL, SORT_OUT_COL, SORT_FWD_ROW, SORT_RET_ROW = 14, 0, 11, 10


def sort_worker() -> Circuit:
    c = Circuit(SORT_IW, SORT_IH)

    # ── MAIN (row 0): read n, BP = n, ship it as the ring header ────────────
    c.set(0, 0, ">")
    c.run(1, 0, "@")
    c.horizontal(0, 1, 13)
    c.run(13, 0, "rbs")  # r(input), BP = n, s(ring) header
    c.set(16, 0, "v")
    c.set(16, 1, "<")
    c.set(15, 1, " ")
    c.set(14, 1, "v")

    # ── LOAD: n x { r(input), s(ring) } ────────────────────────────────────
    load_exit, _ = c.counted_loop(14, 2, "rs")
    assert load_exit == 16
    c.route((16, 2), E, [(16, 6)], (5, 6), S)  # down the east side, west
    c.vertical(5, 6, 10)
    c.set(5, 10, ">")  # merge into the HEAD row

    # ── HEAD (row 10): read the header; 0 ends the round ───────────────────
    c.horizontal(10, 0, 5)  # (5,10) already merges in
    c.set(0, 10, ">")
    c.run(6, 10, "rX")  # A = k; 0 -> north lane, + -> BODY
    c.set(8, 10, "^")  # zero lane: back to MAIN
    c.route((8, 9), N, [(8, 1)], (0, 1), N)
    c.set(0, 0, ">")

    # ── BODY (rows 11-12): BP = k-1, new header, load the carry ────────────
    c.set(7, 11, ">")
    c.run(8, 11, "M1-Nb")  # B = k, A = k-1, BP = k-1
    c.horizontal(11, 12, 17)
    c.run(17, 11, "s")  # new header = k-1
    c.set(18, 11, "v")
    c.set(18, 12, "<")
    c.horizontal(12, 18, 12)
    c.run(12, 12, "rM", d=W)  # carry = v1
    c.horizontal(12, 11, 8)
    c.set(8, 12, "v")

    # ── the pass loop: test row 13, body row 15, three lanes ───────────────
    c.set(8, 13, ">")
    c.set(9, 13, "d")  # BP>0 -> south into the body
    c.set(9, 14, " ")
    c.set(9, 15, ">")
    c.run(10, 15, "r-X")  # A = vi - carry, B = carry
    c.run(13, 15, "+s")  # A >= 0 straight: spill vi
    c.set(12, 14, ">")
    c.run(13, 14, "Ws+M")  # A < 0 north: spill carry, carry = vi
    c.set(12, 16, ">")
    c.run(13, 16, "+s")  # A > 0 south: spill vi
    for row in (15, 16):
        c.horizontal(row, 14, 17)  # row 14's lane reaches col 16
    for row in (14, 15, 16):
        c.set(17, row, "v")  # all three lanes merge south
    c.set(17, 17, "<")
    c.horizontal(17, 17, 8)
    c.set(8, 17, "^")
    c.set(8, 16, " ")
    c.set(8, 15, " ")
    c.set(8, 14, "m")

    # ── loop exit -> emit the minimum -> HEAD ──────────────────────────────
    c.horizontal(13, 9, 14)
    c.set(14, 13, "^")
    c.vertical(14, 13, 9)
    c.set(14, 9, "<")
    c.horizontal(9, 14, 2)
    c.run(2, 9, "Ws", d=W)  # A = min, emit it (output pipe)
    c.set(0, 9, "v")
    return c


def build_sort() -> list[str]:
    """The whole `sort-numbers` machine: worker + I/O rooms + relay + ring.

    25x25.  Same coiling as :func:`build_reverse`, except the relay lives in the
    north band beside the I room (the band is 5 rows and the relay is exactly 5
    rows tall), so the east strip only has to carry the two pipes.
    """
    g = Circuit(28, 28)
    wx, wy = 1, 6
    stamp(g, wx, wy, sort_worker().rows())
    walls(g, wx, wy, SORT_IW, SORT_IH)

    icol = wx + SORT_IN_COL
    stamp(g, icol - 1, 0, ["+-+", "|I|", "+-+"])
    draw_pipe(g, [(icol, 3), (icol, 4)])
    ocol = wx + SORT_OUT_COL
    stamp(g, ocol - 1, 0, ["+-+", "|O|", "+-+"])
    draw_pipe(g, [(ocol, 4), (ocol, 3)])

    # relay: north band, east of the I room.  Both its pipes attach to its BOTTOM
    # wall, because only columns east of the worker's north-east corner have a
    # free cell under the band -- which is what fixes the relay's position.
    east = wx + SORT_IW + 1
    stamp(g, east - 1, 0, RELAY_SOUTH)
    n_fwd = draw_pipe(g, [(east, wy + SORT_FWD_ROW), (east + 2, wy + SORT_FWD_ROW), (east + 2, 5)])
    n_ret = draw_pipe(g, [(east + 1, 5), (east + 1, wy + SORT_RET_ROW), (east, wy + SORT_RET_ROW)])
    if n_fwd + n_ret + 2 < 17 + 2:
        raise Collision(f"ring holds {n_fwd + n_ret + 2} values, need >= 19")
    return [r.rstrip() for r in g.rows() if r.strip()]


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "reverse"
    builders = {
        "reverse": build_reverse,
        "sort": build_sort,
        "reverse-worker": lambda: reverse_worker().ruler().split("\n"),
        "sort-worker": lambda: sort_worker().ruler().split("\n"),
    }
    print("\n".join(builders[which]()))
