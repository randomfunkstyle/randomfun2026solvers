#!/usr/bin/env python3
"""Rotating-pipe-tape memory for the ICFP 2026 `memory` problem.

The N cells live as N values circulating in a pipe ring (worker -> forward pipe
-> relay room -> return pipe -> worker). The worker performs exactly ONE full
revolution per operation, so the ring always comes back to the same alignment
and cell k is simply the k-th value that comes out.

Per operation, reading `op addr [value]` from the input stream:

    r(in)->op ; X                      op==0 -> straight (READ), 1 -> CW (WRITE)
    r(in)->addr ; b ; M ; 1 ; +        BP=addr, A=addr+1
      WRITE only: N                    A=-(addr+1)
    M                                  B=+-(addr+1)          [shared]
    P1 x addr:  {r(tape), s(tape)}     pass `addr` values through untouched
    W ; X                              dispatch on sign(B): + READ / - WRITE
      READ : M litN - b                BP = N-1-addr
             r(tape) ; S               cell[addr] -> output AND back on the tape
      WRITE: N M litN - b              BP = N-1-addr
             r(in)->value ; s(tape)    new value takes slot addr
             r(tape)                   consume+discard the old value
    P2 x (N-1-addr): {r(tape), s(tape)}
    -> MAIN

Both branches need the same BP (=N-(addr+1)), which is why WRITE normalises its
sign with `N` first. The sign of B is the only thing carrying `op` across P1,
since A is clobbered by the pass-through and BP is the loop counter.
"""
from __future__ import annotations

import sys

from randomfun2026solvers.circuit import GLYPH, Circuit, Collision, E, W, N, S

# ── rows (worker interior) ────────────────────────────────────────────────────
R_INIT, R_TRANSIT1 = 0, 4
R_MAIN, R_WSETUP = 5, 6
R_P1 = 8
R_TRANSIT2 = 12
R_WTARGET, R_DISPATCH, R_RTARGET = 13, 14, 15
R_WTAPE, R_MERGE, R_P2 = 16, 17, 18

IN_ROW = R_MAIN            # input pipe anchor  (left wall)
OUT_ROW = 0                # output pipe anchor (left wall; far from every `s`)
FWD_ROW = R_P1              # forward tape anchor (right wall)
TAPE_RET_COL = 25           # return tape anchor (bottom wall column, under the loops)


def lit(n: int) -> str:
    return str(n) if n < 10 else f"`{n}`"


def geometry(n: int) -> dict[str, int]:
    """Column budget, derived from how wide the numeric literal is."""
    lw = len(lit(n))
    d = 4 + lw + 3                     # dispatch column: room for WRITE's westbound run
    tx = d + 1 + (1 + lw + 2) + 3      # tape-op columns start here (past READ's run)
    return {"lw": lw, "D": d, "TX": tx, "IW": tx + 8, "IH": R_P2 + 4}


def worker(n: int) -> Circuit:
    G = geometry(n)
    D, TX, IW, IH = G["D"], G["TX"], G["IW"], G["IH"]
    c = Circuit(IW, IH)
    L = lit(n)
    GUT = IW - 1                       # far-right gutter: P2 exit climbs back to MAIN

    # ── INIT: A=N, BP=N, fill the ring with N zeros ────────────────────────
    x, _ = c.run(1, R_INIT, "@" + L + "b")
    c.horizontal(R_INIT, x - 1, TX)
    ex, _ = c.counted_loop(TX, R_INIT, "0s")
    c.route((ex, R_INIT), E, [(ex, R_TRANSIT1), (0, R_TRANSIT1), (0, R_MAIN)], (1, R_MAIN), E)

    # ── MAIN: read op, branch (op is exactly 0 or 1, so `straight` is safe) ─
    c.run(1, R_MAIN, "rX")
    rx, _ = c.run(3, R_MAIN, "rbM1+")
    c.turn(2, R_WSETUP, E)
    wx, _ = c.run(3, R_WSETUP, "rbM1+N")

    # both setups drop onto the P1 row, cross the shared `M`, enter P1
    c.route((rx, R_MAIN), E, [(rx + 2, R_MAIN), (rx + 2, R_P1)], (TX - 2, R_P1), E)
    c.route((wx, R_WSETUP), E, [(wx + 1, R_WSETUP), (wx + 1, R_P1)], (TX - 2, R_P1), E)
    c.run(TX - 1, R_P1, "M")
    ex, _ = c.counted_loop(TX, R_P1, "rs")
    c.route((ex, R_P1), E, [(ex, R_TRANSIT2), (0, R_TRANSIT2), (0, R_DISPATCH)],
            (D - 2, R_DISPATCH), E)

    # ── dispatch: READ turns CW (down), WRITE turns CCW (up) ───────────────
    c.run(D - 1, R_DISPATCH, "WX")

    # ── READ target: eastbound ────────────────────────────────────────────
    c.turn(D, R_RTARGET, E)
    rt, _ = c.run(D + 1, R_RTARGET, "M" + L + "-b")
    c.horizontal(R_RTARGET, rt - 1, TX + 3)
    c.run(TX + 3, R_RTARGET, "rS")
    read_exit = TX + 5

    # ── WRITE target: westbound (so r(input) lands near the left wall) ─────
    c.turn(D, R_WTARGET, W)
    wt, _ = c.run(D - 1, R_WTARGET, "NM" + L + "-b", d=W)
    c.horizontal(R_WTARGET, wt + 1, 2)
    c.run(2, R_WTARGET, "r", d=W)                       # r(input) -> value
    c.route((1, R_WTARGET), W, [(1, R_WTAPE)], (TX - 1, R_WTAPE), E)
    c.run(TX, R_WTAPE, "sr")

    # ── both targets -> P2 entry, from the west ───────────────────────────
    c.route((read_exit, R_RTARGET), E,
            [(read_exit, R_MERGE), (TX - 1, R_MERGE), (TX - 1, R_P2)], (TX, R_P2), E)
    c.route((TX + 2, R_WTAPE), E,
            [(TX + 2, R_MERGE), (TX - 1, R_MERGE), (TX - 1, R_P2)], (TX, R_P2), E)
    ex, _ = c.counted_loop(TX, R_P2, "rs")
    c.route((ex, R_P2), E, [(GUT, R_P2), (GUT, R_TRANSIT1)], (0, R_TRANSIT1), S)
    return c


# ───────────────────────────────────────────────────────── relay (turnaround)
RELAY = ["+----+",
         "|@ >v|",
         "|  sr|",
         "|  ^<|",
         "+----+"]
RELAY_IN_ROW = 2


def _draw_pipe(g: Circuit, pts: list[tuple[int, int]]) -> int:
    """Draw a pipe along the rectilinear polyline `pts` (cell centres, in flow
    order). Arrowheads at the first cell, every bend and the last cell; `-`/`|`
    bodies on straight runs. Returns the cell count (== the pipe's capacity)."""
    cells: list[tuple[int, int]] = [pts[0]]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        sx = (x1 > x0) - (x1 < x0)
        sy = (y1 > y0) - (y1 < y0)
        x, y = x0, y0
        while (x, y) != (x1, y1):
            x, y = x + sx, y + sy
            cells.append((x, y))
    n = len(cells)
    for i, (x, y) in enumerate(cells):
        din = (x - cells[i - 1][0], y - cells[i - 1][1]) if i else None
        dout = (cells[i + 1][0] - x, cells[i + 1][1] - y) if i < n - 1 else None
        if i == 0:
            ch = GLYPH[dout]
        elif i == n - 1:
            ch = GLYPH[din]
        elif din == dout:
            ch = "-" if dout[0] else "|"
        else:
            ch = GLYPH[dout]
        g.set(x, y, ch)
    return n


def assemble(n: int, tape_slots: int = 0) -> list[str]:
    """Worker + I/O rooms + relay + the tape ring.

    The ring is folded into a band under the worker so the bounding box stays
    compact (score = max(w,h)**2 x ticks). The forward pipe leaves the worker's
    RIGHT wall and descends the east side; the return pipe comes back up into the
    worker's BOTTOM wall, which is what keeps the two from having to cross.
    """
    G = geometry(n)
    IW, IH = G["IW"], G["IH"]
    g = Circuit(400, 200)
    wk = worker(n)
    WX, WY = 6, 2
    for (x, y), ch in wk.cell.items():
        g.set(WX + x, WY + y, ch)
    for x in range(-1, IW + 1):
        g.set(WX + x, WY - 1, "+" if x in (-1, IW) else "-")
        g.set(WX + x, WY + IH, "+" if x in (-1, IW) else "-")
    for y in range(IH):
        g.set(WX - 1, WY + y, "|")
        g.set(WX + IW, WY + y, "|")

    for label, row in (("I", IN_ROW), ("O", OUT_ROW)):
        ry = WY + row
        for i, r in enumerate(["+-+", f"|{label}|", "+-+"]):
            for j, ch in enumerate(r):
                g.set(WX - 6 + j, ry - 1 + i, ch)
    g.set(WX - 3, WY + IN_ROW, ">")
    g.set(WX - 2, WY + IN_ROW, ">")
    g.set(WX - 2, WY + OUT_ROW, "<")
    g.set(WX - 3, WY + OUT_ROW, "<")

    wall_x, bottom_y = WX + IW, WY + IH        # worker's right / bottom wall
    fy = WY + FWD_ROW                          # forward anchor row (right wall)
    ret_col = WX + TAPE_RET_COL                # return anchor column (bottom wall)
    east = wall_x + 3                          # fwd descent column, east of everything
    b1 = bottom_y + 7                          # fwd's westbound band row (lowest)
    r1, r2, r3 = bottom_y + 5, bottom_y + 3, bottom_y + 2   # ret's band rows

    relay_y = bottom_y + 4                     # relay box top; right wall faces the band
    for i, r in enumerate(RELAY):
        for j, ch in enumerate(r):
            g.set(1 + j, relay_y + i, ch)
    relay_wall = 1 + len(RELAY[0]) - 1         # relay's right wall column

    fwd = [(wall_x + 1, fy), (east, fy), (east, b1), (relay_wall + 1, b1)]
    ret = [(relay_wall + 1, r1), (east - 1, r1), (east - 1, r2),
           (relay_wall + 11, r2), (relay_wall + 11, r3), (ret_col, r3),
           (ret_col, bottom_y + 1)]
    n_fwd = _draw_pipe(g, fwd)
    n_ret = _draw_pipe(g, ret)
    slots = n_fwd + n_ret
    if slots < n + 1:
        raise Collision(f"tape holds {slots} slots, need >= {n + 1}")
    rows = [r.rstrip() for r in g.rows() if r.strip()]
    return rows


def tape_slots_of(n: int) -> tuple[int, int]:
    """(slots, needed) for N -- handy when tuning the fold."""
    import io, contextlib
    rows = assemble(n)
    return sum(r.count("-") + r.count("|") for r in rows), n + 1


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    if "--worker" in sys.argv[2:]:
        print(worker(n).ruler())
    else:
        print("\n".join(assemble(n, n + 4)))


# ═══════════════════════════════════════════════════════════ compact rebuild (v2)
#
# Same tape ring, retuned for score (= max(w,h)^2 x avgTicks). Two changes:
#
#  * B carries `+-(N - addr)` instead of `+-(addr+1)`, so the P2 count is |B| - 1.
#    Each target arm then needs only `b m` (READ) or `N b m` (WRITE) -- the
#    `M `100` - b` literal disappears from both arms. That is what lets the room
#    be narrow; the literal now appears once, in the setup.
#  * Packed so the per-op corridors are short: MAIN top-left by the input anchor,
#    all three loops bottom-right by the tape anchors, dispatch and both targets
#    adjacent to the loops instead of a room-width walk away.
#
# Anchors: input = LEFT wall, output = TOP wall (far from every `s`),
# tape-forward = RIGHT wall, tape-return = BOTTOM wall.

V2_IW, V2_IH = 22, 18
V2_IN_ROW = 2               # left wall
V2_OUT_COL = 2              # top wall
V2_FWD_ROW = 8              # right wall
V2_RET_COL = 12             # bottom wall


def worker_v2(n: int) -> Circuit:
    c = Circuit(V2_IW, V2_IH)
    L = lit(n)
    GUT = V2_IW - 1                       # right gutter: P2 exit climbs to MAIN

    # ── INIT (row 0) ──────────────────────────────────────────────────────
    x, _ = c.run(1, 0, "@" + L + "b")     # A=N, BP=N
    c.route((x, 0), E, [(16, 0), (16, 5)], (16, 5), E)
    fill, _ = c.counted_loop(17, 5, "0s")  # fill the ring with N zeros
    c.route((fill, 5), E, [(GUT, 5), (GUT, 1)], (0, 1), S)
    c.turn(0, 2, E)                        # left gutter -> MAIN

    # ── MAIN (row 2): r(in)->op ; X  (op==0 straight = READ, 1 = CW = WRITE)
    c.run(1, 2, "rX")
    rx, _ = c.run(3, 2, "rbM" + L + "-M")      # BP=addr, B=+(N-addr)
    c.turn(2, 3, E)
    wx, _ = c.run(3, 3, "rbM" + L + "-NM")     # BP=addr, B=-(N-addr)

    # both arms drop to row 4, run west, then enter P1 heading east
    c.route((rx, 2), E, [(15, 2), (15, 4)], (10, 4), W)
    c.route((wx, 3), E, [(15, 3), (15, 4)], (10, 4), W)
    c.turn(10, 4, S)
    c.turn(10, 5, E)
    p1, _ = c.counted_loop(11, 5, "rs")        # pass `addr` values through

    # ── dispatch (row 10), reached straight down from the P1 exit ─────────
    c.route((p1, 5), E, [(13, 5), (13, 10)], (13, 10), S)
    c.turn(13, 10, E)
    c.run(14, 10, "WX")                        # A=+-(N-addr); + READ, - WRITE

    # ── READ target (row 11, CW/south) ───────────────────────────────────
    c.turn(15, 11, E)
    c.run(16, 11, "bm")                        # BP = N-1-addr
    c.run(18, 11, "rS")                        # cell[addr] -> output AND tape

    # ── WRITE target (row 9, CCW/north) ──────────────────────────────────
    c.turn(15, 9, W)
    c.run(14, 9, "N", d=W)                     # A = N-addr  (col 13 stays clear:
    c.run(12, 9, "bm", d=W)                    # the P1->dispatch descent crosses it)
    c.horizontal(9, 10, 2)
    c.run(2, 9, "r", d=W)                      # r(in) -> value
    c.route((1, 9), W, [(1, 12)], (10, 12), E)
    c.run(11, 12, "sr")                        # new value in, old one out

    # ── both arms -> P2 entry (row 14) ───────────────────────────────────
    c.route((20, 11), E, [(20, 13), (10, 13), (10, 14)], (11, 14), E)
    c.route((13, 12), E, [(14, 12), (14, 13), (10, 13), (10, 14)], (11, 14), E)
    p2, _ = c.counted_loop(11, 14, "rs")       # pass N-1-addr values through
    c.route((p2, 14), E, [(GUT, 14), (GUT, 1)], (0, 1), S)
    return c


def assemble_v2(n: int, fold: int = 2) -> list[str]:
    """Compact build: worker_v2 + I/O rooms + relay + the folded tape ring.

    `fold` widens the return pipe's zig-zag; :func:`build_v2` searches it for the
    smallest fold that still holds the N+1 values the ring needs (a WRITE is
    briefly holding N+1 because it sends the new value before consuming the old).
    """
    IW, IH = V2_IW, V2_IH
    g = Circuit(400, 200)
    wk = worker_v2(n)
    WX, WY = 7, 7
    for (x, y), ch in wk.cell.items():
        g.set(WX + x, WY + y, ch)
    for x in range(-1, IW + 1):
        g.set(WX + x, WY - 1, "+" if x in (-1, IW) else "-")
        g.set(WX + x, WY + IH, "+" if x in (-1, IW) else "-")
    for y in range(IH):
        g.set(WX - 1, WY + y, "|")
        g.set(WX + IW, WY + y, "|")

    # input room, left of the worker; output room, above it
    iy = WY + V2_IN_ROW
    for i, r in enumerate(["+-+", "|I|", "+-+"]):
        for j, ch in enumerate(r):
            g.set(WX - 6 + j, iy - 1 + i, ch)
    g.set(WX - 3, iy, ">")
    g.set(WX - 2, iy, ">")
    ox = WX + V2_OUT_COL
    for i, r in enumerate(["+-+", "|O|", "+-+"]):
        for j, ch in enumerate(r):
            g.set(ox - 1 + j, WY - 6 + i, ch)
    g.set(ox, WY - 2, "^")
    g.set(ox, WY - 3, "^")

    # ── tape ring, folded into a band under the worker ────────────────────
    bottom_y = WY + IH
    fy = WY + V2_FWD_ROW
    ret_col = WX + V2_RET_COL
    east = WX + IW + 2
    b_fwd = bottom_y + 6                     # fwd's westbound row (lowest)
    r_a, r_b, r_c = bottom_y + 4, bottom_y + 3, bottom_y + 2
    relay_y = bottom_y + 3
    for i, r in enumerate(RELAY):
        for j, ch in enumerate(r):
            g.set(1 + j, relay_y + i, ch)
    relay_wall = len(RELAY[0])               # relay's right wall column
    adj = relay_wall + 1                     # pipes terminate/start one cell out

    # fwd: east off the right wall, down the east side, west into the relay
    n_fwd = _draw_pipe(g, [(WX + IW + 1, fy), (east, fy), (east, b_fwd),
                           (adj, b_fwd)])
    # ret: out of the relay, zig-zag west/east across the band, then NORTH into
    # the worker's bottom wall (the last cell must point into the room).
    n_ret = _draw_pipe(g, [(adj, r_a), (east - 1, r_a), (east - 1, r_b),
                           (adj + fold, r_b), (adj + fold, r_c),
                           (ret_col, r_c), (ret_col, bottom_y + 1)])
    if n_fwd + n_ret < n + 1:
        raise Collision(f"tape holds {n_fwd + n_ret} slots, need >= {n + 1}")
    return [r.rstrip() for r in g.rows() if r.strip()]


def build_v2(n: int) -> list[str]:
    """Smallest compact build whose ring holds >= N+1 values."""
    last = None
    for fold in (0, 2, 4, 6, 8, 10):
        try:
            return assemble_v2(n, fold)
        except Collision as exc:
            last = exc
            if "slots" not in str(exc):
                raise
    raise Collision(f"no fold gives enough tape slots: {last}")


# ═════════════════════════════════════════════════════ two-value rings (v3)
#
# Ticks are the whole score here: of ~899 ticks/op, ~800 is the tape
# pass-through (100 values x 8 ticks). So both pass-through loops become
# `counted_ring`s: 2 values per lap, 5 ticks/value. BP still counts values, so no
# division or extra state -- each ring just has TWO exits (BP can reach 0 at
# either test) that must be routed to the same continuation.
#
# The rings cost 2 rows each. That is paid for by moving the output room from
# ABOVE the worker to the LEFT, beside the input room, which frees 5 rows. Note
# area^2 is width-bound at 32 either way, so the point of the move is the rows,
# not the footprint.
#
# Anchors: input = LEFT wall (row 4), output = LEFT wall (row 0, far from every
# `s`), tape-forward = RIGHT wall, tape-return = BOTTOM wall.

V3_IW, V3_IH = 22, 23
V3_IN_ROW, V3_OUT_ROW = 4, 0          # both on the left wall
V3_FWD_ROW = 9                        # right wall
V3_RET_COL = 11                       # bottom wall
RX = 10                               # both rings live at columns 10-11
FILLX = 15                            # fill loop, beside the P1 ring
P1Y, P2Y = 7, 17                      # ring top rows
DISP = 13                             # dispatch row
TRANSIT = 2                           # the one west-bound corridor row
CLIMB = V3_IW - 1                     # shared north-bound gutter


def worker_v3(n: int) -> Circuit:
    c = Circuit(V3_IW, V3_IH)
    L = lit(n)

    # ── INIT (row 0) -> fill loop (beside P1) -> transit -> MAIN ──────────
    x, _ = c.run(1, 0, "@" + L + "b")
    c.route((x, 0), E, [(FILLX - 1, 0), (FILLX - 1, P1Y)], (FILLX - 1, P1Y), E)
    fill, _ = c.counted_loop(FILLX, P1Y, "0s")
    c.route((fill, P1Y), E, [(CLIMB, P1Y), (CLIMB, TRANSIT)], (0, TRANSIT), S)
    c.vertical(0, TRANSIT, V3_IN_ROW)
    c.turn(0, V3_IN_ROW, E)

    # ── MAIN (row 4) + the two setups ────────────────────────────────────
    c.run(1, V3_IN_ROW, "rX")
    rx, _ = c.run(3, V3_IN_ROW, "rbM" + L + "-")      # A = N-addr
    c.turn(2, 5, E)
    wx, _ = c.run(3, 5, "rbM" + L + "-N")             # A = -(N-addr)
    # Both risers merge onto row 6 heading west and share the final `M`, so B
    # ends up +-(N-addr). Sharing it frees a column for INIT's descent.
    # both risers share column 13 (READ enters it a row higher) and merge on row 6
    c.route((rx, V3_IN_ROW), E, [(wx, V3_IN_ROW), (wx, 6)], (RX + 2, 6), W)
    c.route((wx, 5), E, [(wx, 5), (wx, 6)], (RX + 2, 6), W)
    c.run(RX + 1, 6, "M", d=W)                        # B = +-(N-addr)
    c.route((RX, 6), W, [(RX - 1, 6), (RX - 1, P1Y)], (RX - 1, P1Y), S)
    c.turn(RX - 1, P1Y, E)
    p1 = c.counted_ring(RX, P1Y, "rs")

    # ── both P1 exits descend to the dispatch row, arriving eastbound ─────
    c.route((p1[0][0], P1Y), E, [(p1[0][0], DISP)], (p1[0][0], DISP), S)
    c.turn(p1[0][0], DISP, E)
    c.route(p1[1], W, [(p1[1][0], DISP)], (p1[1][0], DISP), S)
    c.turn(p1[1][0], DISP, E)
    # the west exit's run east crosses the east exit's turn -- same heading, fine
    c.route((p1[1][0], DISP), E, [], (13, DISP), E)
    c.run(14, DISP, "WX")

    # ── READ target (row 14, CW/south) ───────────────────────────────────
    c.turn(15, DISP + 1, E)
    c.run(16, DISP + 1, "bm")                         # BP = N-1-addr
    c.run(18, DISP + 1, "rS")                         # cell[addr] -> output + tape

    # ── WRITE target (row 12, CCW/north), dodging the P1 descent columns ──
    c.turn(15, DISP - 1, W)
    c.run(14, DISP - 1, "N", d=W)
    c.run(13, DISP - 1, "b", d=W)
    c.run(11, DISP - 1, "m", d=W)                     # col 12 left clear
    c.horizontal(DISP - 1, 11, 2)
    c.run(2, DISP - 1, "r", d=W)                      # r(in) -> value
    c.route((1, DISP - 1), W, [(1, 15)], (RX - 1, 15), E)
    c.run(RX, 15, "sr")                               # new value in, old one out

    # ── both targets -> P2 entry ─────────────────────────────────────────
    c.route((20, DISP + 1), E, [(20, 16), (RX - 1, 16)], (RX - 1, 16), W)
    c.route((RX + 2, 15), E, [(RX + 2, 16), (RX - 1, 16)], (RX - 1, 16), W)
    c.turn(RX - 1, 16, S)
    c.turn(RX - 1, P2Y, E)
    p2 = c.counted_ring(RX, P2Y, "rs")

    # ── both P2 exits climb the shared gutter back to the transit row ─────
    c.route((p2[0][0], P2Y), E, [(CLIMB, P2Y), (CLIMB, TRANSIT)], (0, TRANSIT), S)
    c.route(p2[1], W, [(p2[1][0], V3_IH - 1), (CLIMB, V3_IH - 1), (CLIMB, TRANSIT)],
            (0, TRANSIT), S)
    return c


def assemble_v3(n: int, fold: int = 2) -> list[str]:
    """v3 build: both I/O rooms on the LEFT, ring-based worker, folded tape."""
    IW, IH = V3_IW, V3_IH
    g = Circuit(400, 200)
    wk = worker_v3(n)
    WX, WY = 7, 2
    for (x, y), ch in wk.cell.items():
        g.set(WX + x, WY + y, ch)
    for x in range(-1, IW + 1):
        g.set(WX + x, WY - 1, "+" if x in (-1, IW) else "-")
        g.set(WX + x, WY + IH, "+" if x in (-1, IW) else "-")
    for y in range(IH):
        g.set(WX - 1, WY + y, "|")
        g.set(WX + IW, WY + y, "|")

    # both rooms on the left wall: O beside row 0, I beside row 4
    for label, row, glyph in (("O", V3_OUT_ROW, "<"), ("I", V3_IN_ROW, ">")):
        ry = WY + row
        for i, r in enumerate(["+-+", f"|{label}|", "+-+"]):
            for j, ch in enumerate(r):
                g.set(WX - 6 + j, ry - 1 + i, ch)
        g.set(WX - 3, ry, glyph)
        g.set(WX - 2, ry, glyph)

    bottom_y = WY + IH
    fy = WY + V3_FWD_ROW
    ret_col = WX + V3_RET_COL
    east = WX + IW + 2
    b_fwd = bottom_y + 6
    r_a, r_b, r_c = bottom_y + 4, bottom_y + 3, bottom_y + 2
    relay_y = bottom_y + 3
    for i, r in enumerate(RELAY):
        for j, ch in enumerate(r):
            g.set(1 + j, relay_y + i, ch)
    adj = len(RELAY[0]) + 1

    n_fwd = _draw_pipe(g, [(WX + IW + 1, fy), (east, fy), (east, b_fwd), (adj, b_fwd)])
    n_ret = _draw_pipe(g, [(adj, r_a), (east - 1, r_a), (east - 1, r_b),
                           (adj + fold, r_b), (adj + fold, r_c),
                           (ret_col, r_c), (ret_col, bottom_y + 1)])
    if n_fwd + n_ret < n + 1:
        raise Collision(f"tape holds {n_fwd + n_ret} slots, need >= {n + 1}")
    return [r.rstrip() for r in g.rows() if r.strip()]


def build_v3(n: int) -> list[str]:
    """Smallest v3 build whose ring holds >= N+1 values."""
    last = None
    for fold in (0, 2, 4, 6, 8, 10):
        try:
            return assemble_v3(n, fold)
        except Collision as exc:
            last = exc
            if "slots" not in str(exc):
                raise
    raise Collision(f"no fold gives enough tape slots: {last}")


# ════════════════════════════════════════════════ relative rotation (v4)
#
# v3 is Theta(N) unconditionally: a full lap per operation is what restores the
# ring's alignment. v4 tracks the alignment in a scratch register instead and
# rotates only (addr - phase) mod N -- expected gap N/2 -- and P2 disappears.
#
# Geometry note that drove the layout: tape-return sits on the RIGHT wall, not the
# bottom. With it on the bottom, a register read low in the room resolves to the
# tape (the bottom anchor gets nearer than the top one), which made the upper rows
# the only legal place for register ops and left no room to route. On the right
# wall the split is by COLUMN -- register ops in the middle, tape ops bottom-right
# -- and every row is usable.

V4_IW, V4_IH = 26, 22
V4_IN_COL, V4_REGF_COL, V4_REGR_COL = 6, 10, 13   # top wall
V4_OUT_ROW, V4_RET_ROW = 1, 15                    # left wall / right wall
V4_FWD_COL = 18                                   # bottom wall
V4_RINGX, V4_RINGY = 17, 13                       # pass-through ring (2x5)
V4_FILLX = 21                                     # fill loop


def worker_v4(n: int) -> Circuit:
    c = Circuit(V4_IW, V4_IH)
    L = lit(n)
    GUT = V4_IW - 1

    # ── INIT: fill the tape with N zeros, seed phase := 0, fall into MAIN ───
    x, _ = c.run(1, 0, "@" + L + "b")
    # Seed the phase here, NOT on row 1: row 1 is the per-op return path, and a
    # stray `0`/`s` there would zero the READ value before it is emitted and push
    # 0 into the register ring.
    c.run(x + 1, 0, "0s")                      # A = 0 ; s(reg): phase := 0
    c.route((x + 3, 0), E, [(V4_FILLX - 1, 0), (V4_FILLX - 1, 4)],
            (V4_FILLX - 1, 4), E)
    fill, _ = c.counted_loop(V4_FILLX, 4, "0s")
    c.route((fill, 4), E, [(GUT, 4), (GUT, 1), (1, 1)], (1, 2), S)
    c.turn(1, 2, E)

    # ── MAIN: op -> +-1 flag; the arms merge so the dance below is shared ───
    c.run(5, 2, "rX")                          # r(in) -> op ; X
    c.run(7, 2, "1N")                          # READ  (op==0, straight): A = -1
    c.route((9, 2), E, [(9, 3)], (9, 3), E)
    c.turn(6, 3, E)
    c.run(7, 3, "1")                           # WRITE (op==1, CW/south): A = +1
    c.route((8, 3), E, [], (9, 3), E)          # merge, heading east on row 3
    c.route((10, 3), E, [(10, 4)], (10, 4), E)

    # ── dance 1: park flag, take phase, build t = phase+1 ──────────────────
    c.run(11, 4, "s")                          # park flag      ring [phase, flag]
    c.run(12, 4, "r")                          # A = phase      ring [flag]
    c.run(13, 4, "M1+")                        # A = phase+1 = t   (B = phase)
    c.route((16, 4), E, [(16, 5)], (15, 5), W)

    # ── dance 2: park t, read addr, delta_raw = addr - phase ───────────────
    c.run(14, 5, "s", d=W)                     # park t         ring [flag, t]
    c.route((13, 5), W, [(7, 5)], (7, 5), W)
    c.run(6, 5, "r", d=W)                      # A = addr       (B = phase)
    c.run(5, 5, "-", d=W)                      # A = addr - phase = delta_raw

    # ── correction: three arms merge; only the negative one does work ──────
    c.route((4, 5), W, [(3, 5), (3, 7)], (4, 7), E)
    c.run(5, 7, "X")                           # <0 north, ==0 straight, >0 south
    c.turn(5, 6, E)
    c.run(6, 6, "M" + L + "+")                 # A = delta_raw + 100
    MG = 17                                    # merge column
    # all three arms must LEAVE the merge cell heading east, or each keeps its own
    # heading and they never actually join
    c.route((6 + 1 + len(L) + 1, 6), E, [(MG, 6)], (MG, 7), E)
    c.route((6, 7), E, [], (MG, 7), E)         # zero arm, straight along row 7
    c.turn(5, 8, E)
    c.route((6, 8), E, [(MG, 8)], (MG, 7), E)  # positive arm rejoins from below
    c.run(MG + 1, 7, "M")                      # B = delta

    # ── dance 3: new phase = t + delta, restore ring to [phase], BP = delta ──
    # The FIFO forces r,s,r,+,s,r, so the man zig-zags between the two register
    # columns; walking back over an op would re-execute it, hence one row each.
    c.route((MG + 2, 7), E, [(MG + 2, 9), (14, 9)], (14, 9), W)
    c.run(13, 9, "r", d=W)                     # A = flag        ring [t]
    c.run(12, 9, "s", d=W)                     # re-park flag    ring [t, flag]
    c.route((11, 9), W, [(11, 10)], (11, 10), E)
    c.run(12, 10, "r")                         # A = t           ring [flag]
    c.run(13, 10, "+")                         # A = t + delta = addr+1 = phase'
    c.route((14, 10), E, [(14, 11), (12, 11)], (12, 11), W)
    c.run(11, 11, "s", d=W)                    # park phase'     ring [flag, phase]
    c.route((10, 11), W, [(10, 12)], (10, 12), E)
    c.run(11, 12, "r")                         # A = flag        ring [phase]  <- inv
    c.run(12, 12, "Wb")                        # A = delta, B = flag ; BP = delta

    # ─────────────────────────────────────────────────────────────────────
    # PARTIAL: everything above routes and traces correctly -- MAIN, the +-1 flag,
    # the whole shared phase dance, the three-way mod correction, and the exit
    # state BP=delta / B=flag. What is NOT here yet is the pass-through ring, the
    # dispatch and the two target arms.
    #
    # The blocker is anchor placement, not logic. A READ target needs `s(tape)`
    # and `s(out)` adjacent in the code but resolving to *different* pipes, so
    # they must straddle the midline between those two anchors -- and a WRITE
    # target needs `r(in)`, which only resolves to the input pipe in the upper
    # rows while its `s(tape)` only resolves in the lower ones. Placing the four
    # anchor groups (input, output, register, tape) so one per-op sweep visits
    # them in order -- rather than criss-crossing -- is the thing to solve next;
    # `S` is no longer available to collapse the READ into one instruction because
    # it would also write into the register ring.
    return c
