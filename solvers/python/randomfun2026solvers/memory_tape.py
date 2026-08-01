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
from randomfun2026solvers.man_debug import DebugMap

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


def _park_size_on_row(c: "Circuit", n: int, x0: int, y: int) -> None:
    """Stamp ``N -> B`` on a row the man walks **west**, starting at ``x0``.

    The tape size is a constant and a worker keeps A, B and BP across laps, so
    the MAIN arms do not have to fetch it: they can find it in B and spend two
    glyphs instead of ``M `N` -``. The reload has to happen somewhere the man
    already walks with nothing else to do, and the return gutter — which every
    lap crosses to get from P2 back to MAIN — is exactly that, so this costs no
    ticks at all.

    He walks it **westward**, and the engine keys a numeric literal by the
    direction of travel: it pairs a row's backticks once and gives the closing
    mark the digits as written and the opening mark them reversed. So the digits
    are stamped in reverse and the *west* mark is the one that fires; ``M``, west
    of it again, parks the value. Under ten there is no literal to reverse and a
    bare digit does it in one cell.
    """
    cells = ["M"] + ([str(n)] if n < 10 else ["`", *reversed(str(n)), "`"])
    for i, ch in enumerate(cells):
        c.set(x0 + i, y, ch)


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
COMPACT_RELAY = [
    "+----+",
    "|@>rv|",
    "| ^s<|",
    "+----+",
]
MERGE_RELAY = [
    "+----+",
    "|@>Rv|",
    "| ^s<|",
    "+----+",
]
PHASE_RELAY = [
    "+------+",
    "| v   <|",
    "|@>rXs^|",
    "|   >rv|",
    "|   ^s<|",
    "+------+",
]
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


if __name__ == "__main__" and not any(
    arg
    in (
        "--v2",
        "--v3",
        "--v3-external-init",
        "--v3-upstream-init",
        "--v3-one-shot-init",
    )
    or arg.startswith("--debug-")
    for arg in sys.argv[1:]
):
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
V2_ACK_OUT_ROW = 16         # left wall; used only by banked worker variants

# The two-value skip worker keeps the same wall protocol but uses a wider room.
# Its horizontal counted rings sit close to the tape ports and far from the
# request port, which makes every receive binding deterministic.
V2_JUMP_IW, V2_JUMP_IH = 34, 18
V2_JUMP_FWD_ROW = 7
V2_JUMP_RET_COL = 21

# The four-value worker peels BP's low two bits before a four-word bulk loop.
# Keeping all tape operations in the east half makes their nearest-pipe binding
# strict even though the room is taller than v2.
V2_JUMP4_IW, V2_JUMP4_IH = 49, 24
V2_JUMP4_FWD_ROW = 7
V2_JUMP4_RET_COL = 25


def _bit_tail_horizontal(c: Circuit, x: int, y: int, pairs: int) -> tuple[int, int]:
    """Peel one BP bit, conditionally moving ``pairs`` tape words.

    Enter heading east at ``(x, y)``. ``x`` turns clockwise/south for a set bit
    and counter-clockwise/north for a clear bit. The lower arm performs
    ``"rs" * pairs``; both arms rejoin heading east, then ``]`` shifts BP so the
    next bit becomes visible. B is untouched throughout.

    Returns the first cell after the shift, still heading east.
    """
    if pairs < 1:
        raise ValueError(f"bit-tail pairs must be positive, got {pairs}")
    merge_x = x + 2 * pairs + 1
    c.set(x, y, "x")

    c.turn(x, y - 1, E)
    c.route((x, y - 1), E, [], (merge_x, y - 1), S)

    c.turn(x, y + 1, E)
    c.run(x + 1, y + 1, "rs" * pairs)
    c.route((x + 2 * pairs + 1, y + 1), E, [], (merge_x, y + 1), N)

    c.turn(merge_x, y, E)
    c.set(merge_x + 1, y, "]")
    return merge_x + 2, y


#: The **v4 tape wire**: one word per request instead of two.
#:
#: ``v3`` hands a ring worker ``op`` then ``addr``, two pipe transactions three
#: cells apart, and MAIN spends ``r X r b - N M`` plus a stall waiting for the
#: second one. ``v4`` hands it the *packed* word ``w = 2*addr - op`` — the same
#: word :data:`~.memory_taped.TAPE_PROTOCOLS`' own ``v4`` already carries from
#: the adapter to the bank's doorstep — and MAIN becomes ``r b ] - M``, five
#: glyphs, no branch and no stall.
#:
#: The unpack is **entirely in the backpack**, which is what makes it free:
#:
#: * ``b``   parks ``w`` in BP;
#: * ``]``   is an arithmetic right shift of BP, so BP becomes ``w >> 1``, which
#:   is ``addr`` for a read (``2a >> 1``) and ``addr - 1`` for a write
#:   (``(2a-1) >> 1``) — and P1's count is exactly that;
#: * ``x``   branches on BP's **low bit**, which *is* the op, and it still reads
#:   correctly after the ``-`` because ``w - (2n+1)`` flips the parity in step
#:   with it. So the op survives P1 inside the one register P1 does not touch.
#:
#: The parked constant becomes ``2n + 1`` rather than ``n`` (:func:`_park_size_on_row`
#: stamps it on the same return gutter, so it is still free), and ``A = w - 2n - 1``
#: carries **both** the arm and the P2 count: it is odd for a read and even for a
#: write, and ``N b ] m`` recovers ``n - 1 - addr`` on either arm. Because that
#: recovery is four glyphs rather than ``b m``'s two, it moves to **after** the
#: answer's ``S`` — where :data:`~.lm1.machine.TAPED_TIGHT_RING` measured the rest
#: of the lap to be worth exactly 0.000%.
#:
#: The one asymmetry is the write's ``]``: it floors, so P1 moves ``addr - 1``
#: words and the write arm pays one extra ``r s`` ring pass to realign. Reads are
#: exact, and a bank's local addresses start at 1 (``taped_plan`` gives bank ``k``
#: ``plan[k]`` addresses and :func:`~.memory_taped.taped_store_block` builds it
#: ``plan[k] + 1`` slots deep), so the floor never underflows.
TAPE_WORKER_PROTOCOLS = ("v3", "v4")


#: **Instruments, not knobs.** Nop cells inserted into the batch-1 v4 worker's
#: lap, on one side of the answer's ``S`` or the other, so the tick price of a
#: worker cell in each half can be measured on the real machine rather than
#: inherited from a measurement taken at a different occupancy.
#:
#: ``WORKER_V4_PRE_PAD`` pushes MAIN's descent that many columns east and walks
#: back west along the free row 3 — ``2 * pad`` ticks, all of them between the
#: request's ``r`` and the answer's ``S``. ``WORKER_V4_POST_PAD`` dips the return
#: gutter that many rows south before it turns east — ``2 * pad`` ticks, all of
#: them after the answer has left. Both are 0 in every build and the room's
#: walls, ports and bounding box do not move at any value, so nothing outside
#: this worker changes: ``bank_w``/``bank_h`` and therefore the block's pitch are
#: identical.
#:
#: **The finding is that "after the send is free" has expired here.** It was
#: measured true when the store's mean read latency was ~152 and the banks were
#: 77-86% idle; at **105.6** the front banks are 82% idle and their post-send
#: tail has started to be the thing the next request waits for. Same process,
#: same moment, 21-round tour, control reproducing to the tick — each pad adds
#: ``2 * pad`` ticks to the lap of the four batch-1 banks, which answer roughly
#: three reads in four:
#:
#: | pad | ticks/access | tour | per tick |
#: |---|---|---|---|
#: | ``PRE_PAD=1`` | +2 before the ``S`` | +0.541% | 0.271% |
#: | ``PRE_PAD=3`` | +6 | +1.629% | 0.272% |
#: | ``PRE_PAD=8`` | +16 | +4.352% | 0.272% |
#: | ``POST_PAD=1`` | +2 after the ``S`` | +0.037% | 0.019% |
#: | ``POST_PAD=2`` | +4 | +0.077% | 0.019% |
#: | ``POST_PAD=3`` | +6 | +0.132% | 0.022% |
#:
#: So a post-send tick is worth **~7% of a forward one** — small, not zero, and
#: slightly *convex*, which is the shape the explanation predicts: only the
#: accesses whose gap is already shorter than the tail pay, and lengthening the
#: tail recruits more of them. Roughly 5% of accesses are in that state now.
#:
#: **What is still exactly free is post-send *pipe* time, as against post-send
#: walking.** :data:`~.lm1.machine.TAPED_TIGHT_RING` cuts every hot bank's ring
#: perimeter by a quarter and was re-run at this latency: identical tick, again.
#: A longer ring delays a *value* inside the gap; a longer tail delays the *man*,
#: and only the man can make the next request wait.
WORKER_V4_PRE_PAD = 0
WORKER_V4_POST_PAD = 0

#: The row the **v4 body's** MAIN stands on. It is one south of the request
#: stub's :data:`V2_IN_ROW`, and the gap is the whole point.
#:
#: MAIN's job is to get from the request pipe to P1's ``d``, and every cell of
#: that is a Manhattan distance the man walks in full on the read's own critical
#: path. P1 cannot move — its ``s`` binding to the ring-forward pipe is exact at
#: column 10 — so the only way to shorten the leg is to start it further south,
#: and MAIN may: ``r`` takes the *nearest* incoming pipe, not the one it stands
#: on, and from ``(1, 3)`` the request stub is 4 cells against the ring return's
#: 27. One cell off ``r`` -> ``S`` at every access, measured **-0.252%**.
#:
#: **The stub must not follow it, and this is the trap.** Moving the pipe to row 3
#: as well looks like tidying and is a silent wrong-bank: P1's own ``r`` at
#: ``(10, 6)`` sits 15 cells from the ring return, and the request stub at
#: ``V2_IN_ROW`` is 16 — a margin of one. Pull the stub a row closer and it is
#: 15 against 15, a tie, which SPEC gives to the northern segment; P1 then reads
#: the *next request* off the wire instead of a tape word. It builds, and at
#: ``bank_west_grow=4`` — which is what ``deadman-3d_hires`` ships — the four
#: extra columns hide it, so the machine passes its whole tour while any caller
#: that does not grow the wall hangs. Found exactly that way.
#:
#: **Row 3 is also the floor, and the reason is column 1.** Row 4 already carries
#: the write arm's westbound run and, at column 1, its descent to the realign
#: row; rows 4..12 of that column belong to the write. Row 3 is the last row
#: above it and it is empty from wall to wall.
V2_V4_MAIN_ROW = 3

#: The same move in the **batched** worker, whose MAIN was 21 cells and a
#: three-row descent away from its ring. Here the row is the ring's own entry row
#: (5) rather than one south of the stub, because the batched body's west half is
#: empty down to row 9 and the ring's odd-count tail already re-enters along it.
V2_V4_JUMP_MAIN_ROW = 5

def _worker_v2_v4(c: Circuit, n: int, GUT: int) -> None:
    """The batch-1 ring worker's **v4 body**: five glyphs in, five glyphs out.

    See :data:`TAPE_WORKER_PROTOCOLS` for the wire. What is laid out here is the
    other half of the win, and it is pure geometry: **everything between MAIN's
    last glyph and P1's `d`, and between P1's exit and the answer's `S`, is a
    Manhattan distance the man walks in full.** The v3 body spent 14 of its 27
    fixed ticks on exactly that -- five blanks east to a descent column, three
    rows down to P1, and six more rows down from P1's exit to a dispatch that
    sat at row 10 because its two arms wanted rows 9 and 11.

    So P1 moves as far **west** as its own bindings allow and the dispatch stands
    on P1's exit row. The binding that pins P1 is the ring-forward pipe against
    the output pipe: at ``s``'s cell (10, 7) that is 14 against 17, and one
    column further west it is 15 against 16 and the ring loses. 9/10 is a wall,
    not a preference.

    ``r`` -> ``S`` goes **27 + 8a -> 18 + 8a** ticks, and this is the worker the
    hot addresses use: the chain is hot-first and its first banks are the small
    ones, so batch 1 answers roughly three reads in four.

    **18 is the floor for this glyph sequence, and it is a floor rather than a
    best effort.** MAIN's ``r`` stands at ``(1, 3)`` and the answer's ``S`` at
    ``(14, 7)``; that is 17 moves of Manhattan distance and the man makes exactly
    17, east and south, never once doubling back. Five glyphs in MAIN, one ``d``,
    four in the dispatch and two in the target account for twelve of the
    eighteen cells and the other six are the distance between them. Nothing here
    can be shortened by moving a room — only by needing fewer glyphs, and each of
    the twelve is carrying something (:data:`TAPE_WORKER_PROTOCOLS` says what).
    The two cells that *were* slack were MAIN's row
    (:data:`V2_V4_MAIN_ROW`, -0.252%) and the turn east that used to stand
    between ``x`` and the target ``r`` (-0.274%).
    """
    # ── MAIN: one packed word, no branch, no stall ────────────────────────
    # On :data:`V2_V4_MAIN_ROW`, one row south of the request stub it reads from,
    # because the leg from here to P1's `d` is walked in full on the critical path
    # and every row MAIN gains is one cell off it. The stub stays where it is.
    y = V2_V4_MAIN_ROW
    c.turn(0, y, E)                        # the return gutter's own last cell
    c.run(1, y, "rb]-M")                   # BP = addr, A = B = w - (2n+1)
    if WORKER_V4_PRE_PAD:
        # Out along the row above and back, so the pad is 2 ticks per unit with a
        # fixed 2 on top of it; the slope is what the instrument is for.
        k = WORKER_V4_PRE_PAD
        c.route((6, y), N, [(6, y - 1), (9 + k, y - 1), (9 + k, y), (9, y)], (9, 4), S)
    else:
        c.route((6, y), E, [(9, y)], (9, 4), S)
    p1, _ = c.counted_loop(9, 5, "rs")

    # ── dispatch, on P1's own exit row: A is odd for a READ, even for a WRITE
    c.run(p1, 5, "WMbx")

    # READ (row 6). The answer leaves on the `S`; P2's count is derived after
    # it, where :data:`~.lm1.machine.TAPED_TIGHT_RING` measured the rest of the
    # lap to be worth exactly 0.000%.
    # ... and the target stands **on the `x`'s own column**, not one turn east of
    # it. The read leaves `x` heading south and the two cells it needs are the two
    # cells below: the turn east that used to stand between them was a tick of
    # the request's own critical path buying nothing, because an `S` writes every
    # outgoing pipe and so has no binding to satisfy and no column it prefers.
    # The target `r` does have one — it must take the ring's return, not the
    # request — and column 14 keeps it: 16 cells against the request pipe's 23.
    c.run(14, 6, "rS", d=S)                # (14,6) target, (14,7) the answer out
    # The tail then turns east rather than running down column 18, which is what
    # it did when it had to start from a cell three columns further east. It is
    # one cell shorter *and* it hands column 18 back empty from row 6 down.
    c.run(14, 8, "WNb", d=S)               # BP = n - 1 - addr, in two legs ...
    c.turn(14, 11, E)                      # ... because row 12 is the write's
    c.run(15, 11, "]m")                    # realign and column 14 crosses it
    c.turn(17, 11, S)                      # onto the write arm's own exit column

    # WRITE (row 4). Column 9 stays blank: MAIN's own descent crosses it, and
    # two corridors may share a blank where neither may share a glyph.
    c.turn(14, 4, W)
    c.run(13, 4, "Nb]m", d=W)              # BP = n - 1 - addr
    c.run(8, 4, "m", d=W)
    c.horizontal(4, 8, 2)
    c.run(2, 4, "r", d=W)                  # the new value, off the request pipe
    c.route((1, 4), W, [(1, 12)], (10, 12), E)
    # `]` floored, so P1 moved one word too few: the ring gets one extra `r s`
    # pass here rather than out on row 4, where its `s` would have stood one
    # cell from preferring the *output* pipe -- 15 against 16, a margin of one.
    c.run(11, 12, "MrsWsr")
    c.route((17, 12), E, [(17, 13), (11, 13)], (11, 14), S)

    p2, _ = c.counted_loop(11, 14, "rs")
    if WORKER_V4_POST_PAD:
        k = WORKER_V4_POST_PAD
        c.route((p2, 14), E,
                [(p2 + 1, 14), (p2 + 1, 14 + k), (GUT, 14 + k), (GUT, 1)], (0, 1), S)
    else:
        c.route((p2, 14), E, [(GUT, 14), (GUT, 1)], (0, 1), S)
    _park_size_on_row(c, 2 * n + 1, 1, 1)


def worker_v2(
    n: int,
    *,
    init_body: str | None = None,
    constant_input: bool = False,
    width: int = V2_IW,
    height: int = V2_IH,
    write_ack: bool = False,
    park_const: bool = False,
    protocol: str = "v3",
) -> Circuit:
    c = Circuit(width, height)
    L = lit(n)
    GUT = width - 1                       # right gutter: P2 exit climbs to MAIN
    if protocol not in TAPE_WORKER_PROTOCOLS:
        raise ValueError(
            f"unknown tape worker protocol {protocol!r}; "
            f"expected {TAPE_WORKER_PROTOCOLS!r}"
        )
    if protocol == "v4" and not park_const:
        raise ValueError("the v4 worker's constant is 2n+1 and it is parked; pass park_const")
    if protocol == "v4" and (constant_input or init_body is not None or write_ack):
        raise ValueError("the v4 worker only speaks the plain one-word request protocol")
    if park_const and (constant_input or init_body is not None):
        raise ValueError("park_const is only defined for the plain request protocol")

    # ── INIT (row 0) ──────────────────────────────────────────────────────
    init_n = "r" if constant_input else L
    x, _ = c.run(1, 0, "@" + init_n + "b")  # A=N, BP=N
    if protocol == "v4":
        # The fill runs once, so it may stand anywhere free -- and under v4 the
        # only free two columns are the room's north-east corner, because the
        # compact body has taken rows 4..6 out as far east as column 18.
        c.route((x, 0), E, [(18, 0), (18, 2)], (18, 2), E)
        fill_exit = (c.counted_loop(19, 2, "0s")[0], 2)
        c.route(fill_exit, E, [(GUT, 2), (GUT, 1)], (0, 1), S)
        # ... and the turn east off the gutter is MAIN's own first cell, which the
        # v4 body places for itself because it chooses its own row.
        _worker_v2_v4(c, n, GUT)
        return c
    c.route((x, 0), E, [(16, 0), (16, 5)], (16, 5), E)
    if init_body is not None:
        if n % 2:
            raise Collision("paired initialization requires an even memory size")
        fill_exit, _odd_fill = c.counted_ring(17, 5, init_body)
    else:
        fill, _ = c.counted_loop(17, 5, "0s")
        fill_exit = (fill, 5)
    c.route(fill_exit, E, [(GUT, 5), (GUT, 1)], (0, 1), S)
    c.turn(0, 2, E)                        # left gutter -> MAIN

    if constant_input:
        # Keep op in B while both input values are received near the lower port.
        c.run(1, 2, "rM")
        c.turn(3, 2, S)
        c.turn(3, 3, W)
        c.run(1, 3, "r", d=W)                  # lower input -> addr
        c.turn(0, 3, S)
        c.turn(0, 4, E)
        c.run(1, 4, "bWX")                     # BP=addr; branch on saved op
        read_setup, _ = c.run(4, 4, "r-M")
        c.horizontal(4, read_setup - 1, 10)
        c.turn(3, 5, E)
        write_setup, _ = c.run(4, 5, ".r-NM")
        c.route(
            (write_setup, 5),
            E,
            [(9, 5), (9, 4)],
            (10, 4),
            E,
        )
    else:
        # ── MAIN: op==0 goes straight to READ; op==1 turns CW to WRITE.
        c.run(1, 2, "rX")
        # ``park_const`` reaches the identical (A, B, BP) with the tape size N
        # already sitting in B — see :func:`_park_size_on_row`, which puts it
        # there on the return gutter every lap walks.
        rx, _ = c.run(3, 2, "rb-NM" if park_const else "rbM" + L + "-M")
        c.turn(2, 3, E)
        wx, _ = c.run(3, 3, "rb-M" if park_const else "rbM" + L + "-NM")
        # Where the two arms meet and drop to P1. Column 15 is the widest
        # literal's arm plus slack; with the literal gone the arms are the same
        # five and four cells at every N, so the descent follows them west — and
        # what that deletes is walked *twice*, east to the descent and west along
        # row 4 to P1's turn. 11 is the floor: P1's entry stands at column 10.
        mg = max(rx, wx, 11) if park_const else 15
        c.route((rx, 2), E, [(mg, 2), (mg, 4)], (10, 4), W)
        c.route((wx, 3), E, [(mg, 3), (mg, 4)], (10, 4), W)

    # Both arms reach the shared absolute south turn into P1.
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
    if constant_input:
        c.turn(18, 11, S)
        c.turn(18, 12, W)
        c.run(17, 12, "rS", d=W)               # target -> output AND tape
        read_exit = (15, 12)
    else:
        if write_ack:
            c.run(18, 11, "rS0M")              # B=0 marks READ through P2
            read_exit = (22, 11)
        else:
            c.run(18, 11, "rS")                # target -> output AND tape
            read_exit = (20, 11)

    # ── WRITE target (row 9, CCW/north) ──────────────────────────────────
    c.turn(15, 9, W)
    c.run(14, 9, "N", d=W)                     # A = N-addr  (col 13 stays clear:
    c.run(12, 9, "bm", d=W)                    # the P1->dispatch descent crosses it)
    if constant_input:
        c.horizontal(9, 10, 5)
        c.run(5, 9, "r", d=W)
        c.route((4, 9), W, [(4, 12)], (8, 12), E)
        c.run(9, 12, "sr")
        write_exit = 11
    else:
        c.horizontal(9, 10, 2)
        c.run(2, 9, "r", d=W)                  # r(in) -> value
        if write_ack:
            c.route((1, 9), W, [(1, 12)], (12, 12), E)
            c.run(13, 12, "sr1M")              # B=1 marks WRITE through P2
            write_exit = 17
        else:
            c.route((1, 9), W, [(1, 12)], (10, 12), E)
            c.run(11, 12, "sr")                # new value in, old one out
            write_exit = 13

    # ── both arms -> P2 entry ─────────────────────────────────────────────
    if constant_input:
        c.route(
            read_exit,
            W,
            [(15, 13)],
            (14, 13),
            W,
        )
    else:
        c.route(
            read_exit,
            E,
            [(read_exit[0], 13), (10, 13), (10, 14)],
            (11, 14),
            E,
        )
    if constant_input:
        c.route(
            (write_exit, 12),
            E,
            [(write_exit, 13)],
            (14, 13),
            E,
        )
    elif write_ack:
        c.route(
            (write_exit, 12),
            E,
            [(write_exit, 13), (10, 13), (10, 14)],
            (11, 14),
            E,
        )
    else:
        c.route(
            (write_exit, 12),
            E,
            [(14, 12), (14, 13), (10, 13), (10, 14)],
            (11, 14),
            E,
        )
    if constant_input:
        c.turn(14, 13, S)
        p2 = c.counted_loop_horizontal(11, 14, "rs")
        c.route(p2, S, [(GUT, 16), (GUT, 1)], (0, 1), S)
    else:
        p2, _ = c.counted_loop(11, 14, "rs")
        if write_ack:
            # Do not perturb the proven target+P2 tape timing.  B's marker is
            # examined only after the full revolution has restored alignment.
            c.route((p2, 14), E, [], (16, 14), E)
            c.run(16, 14, "WX")
            c.route((18, 14), E, [(GUT, 14), (GUT, 1)], (0, 1), S)
            c.route(
                (17, 15),
                S,
                [(17, height - 1), (2, height - 1), (2, V2_ACK_OUT_ROW)],
                (2, V2_ACK_OUT_ROW),
                W,
            )
            c.run(1, V2_ACK_OUT_ROW, "s", d=W)
            c.route(
                (0, V2_ACK_OUT_ROW),
                W,
                [(0, height - 2), (GUT, height - 2), (GUT, 1)],
                (0, 1),
                S,
            )
        else:
            c.route((p2, 14), E, [(GUT, 14), (GUT, 1)], (0, 1), S)
    if park_const:
        # After every route: ``Circuit.route`` refuses a corridor that crosses a
        # glyph, and this one deliberately stands *in* the return gutter.
        _park_size_on_row(c, n, 1, 1)
    return c


def worker_v2_jump(n: int, *, park_const: bool = False, protocol: str = "v3") -> Circuit:
    """A parameterized v2 worker whose skip loops move two tape words per lap.

    The request protocol and stored representation are identical to
    :func:`worker_v2`; only P1/P2 use
    :meth:`~randomfun2026solvers.circuit.Circuit.counted_ring_horizontal`.
    BP is still tested once per word.  Consequently zero and odd counts are
    exact, while a long skip costs about five worker ticks per word instead of
    eight.

    The room is deliberately wider but not taller.  Keeping the original
    height lets the tape-return pipe remain the closest incoming pipe to both
    skip loops, while the WRITE-value receive remains closest to the request
    pipe.
    """
    c = Circuit(V2_JUMP_IW, V2_JUMP_IH)
    L = lit(n)
    GUT = V2_JUMP_IW - 1

    # INIT lives in the otherwise-unused north-east corner.  It retains the
    # one-value loop because initialization runs once, while P1/P2 run for
    # every access.
    x, _ = c.run(1, 0, "@" + L + "b")
    c.route((x, 0), E, [(29, 0)], (29, 2), E)
    fill, _ = c.counted_loop(30, 2, "0s")
    c.route((fill, 2), E, [(GUT, 2), (GUT, 1)], (0, 1), S)
    if protocol != "v4":
        c.turn(0, 2, E)  # ... the v4 body chooses its own MAIN row and turns there

    if protocol == "v4":
        # See :data:`TAPE_WORKER_PROTOCOLS`. MAIN is the same five glyphs; what
        # differs here is that P1 is a ring in the room's east half, so the
        # descent runs straight east along MAIN's own row instead of doubling
        # back west to a merge column the two v3 arms needed.
        if not park_const:
            raise ValueError("the v4 worker's constant is 2n+1 and it is parked; pass park_const")
        # MAIN stands on :data:`V2_V4_JUMP_MAIN_ROW`, which is the ring's own
        # entry row, so the leg from the request to P1 is a single straight run
        # east instead of a run east and a descent -- three cells off ``r`` ->
        # ``S`` at every access to a batched bank. The corridor it joins is the
        # ring's **odd-count re-entry**, which already runs east along this row
        # into the same turn, so the two merge rather than collide.
        y = V2_V4_JUMP_MAIN_ROW
        c.turn(0, y, E)
        c.run(1, y, "rb]-M")
        if y == 5:
            c.horizontal(5, 5, 19)  # ... straight into the odd tail's own corridor
        else:
            c.route((6, y), E, [(23, y)], (23, 4), S)
        c.turn(23, 5, S)
        p1_exit, p1_odd = c.counted_ring_horizontal(19, 6, "rs")
        c.turn(*p1_odd, E)
        c.horizontal(5, 19, 23)

        # The dispatch stands **on** P1's own exit cell rather than three rows
        # below it: the v3 body dropped to row 10 because its READ arm needed
        # row 11 and its WRITE arm row 9, and the ring's bottom row is 7. With
        # the arms one row apart the other way round there is nothing between
        # them and the exit, and two cells of descent go with it.
        c.turn(*p1_exit, E)
        c.run(24, 8, "WMbx")

        # READ (row 7, CCW): the answer first, then P2's count, on the way home.
        c.turn(27, 7, E)
        c.run(28, 7, "rS")
        c.route((30, 7), E, [(30, 7)], (30, 8), S)
        c.run(30, 8, "WNb]m", d=S)
        c.route((30, 13), S, [(30, 13)], (23, 13), S)

        # WRITE (row 9, CW): one extra ring pass for the floored `]`, then the value.
        c.turn(27, 9, W)
        c.run(26, 9, "rsWNb]m", d=W)
        c.route((19, 9), W, [(3, 9)], (3, 12), W)
        c.run(2, 12, "r", d=W)
        c.route((1, 12), W, [(0, 12), (0, 13)], (16, 13), E)
        c.run(16, 13, "sr")
        c.turn(19, 13, E)

        p2_exit, _p2_odd = c.counted_ring_horizontal(19, 14, "rs")
        c.route(p2_exit, S, [(23, 17), (GUT, 17), (GUT, 1)], (0, 1), S)
        # ``2n``, not ``2n + 1``: the parity of ``w - c`` is what ``x`` reads, so
        # the constant chooses which way each arm turns. Even (READ) goes CCW,
        # north, which is the side the ring's own bottom row does not stand on.
        _park_size_on_row(c, 2 * n, 1, 1)
        return c

    # MAIN and the signed remaining-distance setup are byte-for-byte the v2
    # protocol.  B carries +(N-addr) for READ and -(N-addr) for WRITE.
    c.run(1, 2, "rX")
    rx, _ = c.run(3, 2, "rb-NM" if park_const else "rbM" + L + "-M")
    c.turn(2, 3, E)
    wx, _ = c.run(3, 3, "rb-M" if park_const else "rbM" + L + "-NM")
    mg = max(rx, wx, 11) if park_const else 15
    c.route((rx, 2), E, [(mg, 2), (mg, 4)], (10, 4), W)
    c.route((wx, 3), E, [(mg, 3), (mg, 4)], (10, 4), W)

    # P1.  The north exit is the odd-count tail.  It points east along the
    # shared entry corridor, re-enters with BP=0, and then takes the one south
    # exit.  Thus downstream code has a single entry for both parities.
    c.turn(10, 4, S)
    c.turn(10, 5, E)
    c.horizontal(5, 10, 23)
    c.turn(23, 5, S)
    p1_exit, p1_odd = c.counted_ring_horizontal(19, 6, "rs")
    c.turn(*p1_odd, E)

    # Dispatch.  READ turns south, WRITE north.  The target arms are placed
    # east of P1 so the WRITE arm never crosses the ring.
    c.route(p1_exit, S, [(23, 10)], (23, 10), E)
    c.run(24, 10, "WX")

    c.turn(25, 11, E)
    c.run(26, 11, "bmrS")
    read_exit = (30, 11)

    c.turn(25, 9, E)
    c.run(26, 9, "Nbm")
    write_target_exit = (29, 9)

    # Fetch the WRITE value beside the west request port, then carry it back
    # to the tape.  READ and WRITE converge on P2's entry; the same eastbound
    # corridor is also the odd-tail re-entry path.
    c.route(
        write_target_exit,
        E,
        [(32, 9), (32, 12)],
        (3, 12),
        W,
    )
    c.run(2, 12, "r", d=W)
    c.route((1, 12), W, [(0, 12), (0, 13)], (16, 13), E)
    c.run(16, 13, "sr")

    c.route(read_exit, E, [(31, 11), (31, 13)], (23, 13), S)
    c.turn(19, 13, E)
    c.turn(23, 13, S)
    p2_exit, _p2_odd = c.counted_ring_horizontal(19, 14, "rs")

    # Both the normal P2 exit and INIT share the northbound gutter back to
    # MAIN.  Only one runner can occupy this return path at a time.
    c.route(
        p2_exit,
        S,
        [(23, 17), (GUT, 17), (GUT, 1)],
        (0, 1),
        S,
    )
    if park_const:
        _park_size_on_row(c, n, 1, 1)
    return c


def worker_v2_jump4(n: int) -> Circuit:
    """A four-word tape skip with exact 0..3 tails and a live operation tag.

    P1 enters with BP=addr and B=+-(N-addr). Two :func:`_bit_tail_horizontal`
    diamonds move ``addr % 4`` values using BP's low bits, and leave
    ``floor(addr / 4)`` in BP for ``counted_ring_horizontal("rs" * 4)``.
    None of those instructions changes B, so the existing READ/WRITE dispatch
    remains valid. P2 applies the same exact decomposition after the target.
    """
    c = Circuit(V2_JUMP4_IW, V2_JUMP4_IH)
    L = lit(n)
    GUT = V2_JUMP4_IW - 1

    # INIT: a compact vertical loop in the north-east corner. Its exit owns the
    # east gutter back to MAIN; the steady-state P2 path joins that gutter lower.
    init_end, _ = c.run(1, 0, "@" + L + "b")
    c.route((init_end, 0), E, [], (46, 0), E)
    fill, _ = c.counted_loop(46, 0, "0s")
    assert fill == 48
    c.route(
        (fill, 0),
        E,
        [(GUT, 4), (45, 4), (45, 1)],
        (0, 1),
        W,
    )
    c.turn(0, 2, E)

    # MAIN: identical request protocol and signed remaining-distance tag to v2.
    c.run(1, 2, "rX")
    rx, _ = c.run(3, 2, "rbM" + L + "-M")
    c.turn(2, 3, E)
    wx, _ = c.run(3, 3, "rbM" + L + "-NM")
    c.route((rx, 2), E, [(15, 2), (15, 4)], (10, 4), W)
    c.route((wx, 3), E, [(15, 3), (15, 4)], (10, 4), W)

    # P1: peel 1 and 2 values, then move four per BP unit. Both bulk exits
    # converge by re-entering the top-right test with BP=0.
    c.turn(10, 4, S)
    c.turn(10, 5, E)
    c.horizontal(5, 10, 24)
    tail = _bit_tail_horizontal(c, 24, 5, 1)
    tail = _bit_tail_horizontal(c, *tail, 2)
    assert tail == (36, 5)
    c.route(tail, E, [], (46, 5), S)
    p1_exit, p1_other = c.counted_ring_horizontal(36, 6, "rs" * 4)
    assert p1_other == tail
    c.turn(*p1_other, E)
    c.route(p1_exit, S, [(46, 8), (33, 8), (33, 10)], (33, 10), S)

    # Dispatch and target access. Moving the target block east keeps every
    # ring-facing r/s strictly nearer to the tape pipes than to request/output.
    c.turn(33, 10, E)
    c.run(34, 10, "WX")

    c.turn(35, 11, E)
    c.run(36, 11, "bmrS")
    read_exit = (40, 11)

    c.turn(35, 9, E)
    c.run(36, 9, "Nbm")
    write_target_exit = (39, 9)

    c.route(write_target_exit, E, [(45, 9), (45, 12)], (3, 12), W)
    c.run(2, 12, "r", d=W)
    c.route((1, 12), W, [(0, 12), (0, 13)], (31, 13), E)
    c.run(31, 13, "sr")
    write_exit = (33, 13)

    # Both target arms join above P2, then approach its bit diamonds from the
    # west without crossing either conditional arm.
    c.route(read_exit, E, [(40, 14)], (23, 14), W)
    c.route(write_exit, E, [(33, 14)], (23, 14), W)
    p2_tail = _bit_tail_horizontal(c, 24, 16, 1)
    # Enter the first diamond from the shared merge without crossing its upper arm.
    c.route((23, 14), W, [(22, 14), (22, 16)], (23, 16), E)
    p2_tail = _bit_tail_horizontal(c, *p2_tail, 2)
    assert p2_tail == (36, 16)
    c.route(p2_tail, E, [], (46, 16), S)
    p2_exit, p2_other = c.counted_ring_horizontal(36, 17, "rs" * 4)
    assert p2_other == p2_tail
    c.turn(*p2_other, E)

    c.route(
        p2_exit,
        S,
        [
            (46, 21),
            (GUT, 21),
            (GUT, 4),
            (45, 4),
            (45, 1),
        ],
        (0, 1),
        S,
    )
    return c


def assemble_v2(n: int, fold: int = 2, *, init_body: str | None = None) -> list[str]:
    """Compact build: worker_v2 + I/O rooms + relay + the folded tape ring.

    `fold` widens the return pipe's zig-zag; :func:`build_v2` searches it for the
    smallest fold that still holds the N+1 values the ring needs (a WRITE is
    briefly holding N+1 because it sends the new value before consuming the old).
    """
    IW, IH = V2_IW, V2_IH
    g = Circuit(400, 200)
    wk = worker_v2(n, init_body=init_body)
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


def build_v2_fast_init(n: int) -> list[str]:
    """Build v2 with a two-zero-per-lap initializer for even memory sizes."""
    last = None
    for fold in (0, 2, 4, 6, 8, 10):
        try:
            return assemble_v2(n, fold, init_body="0s")
        except Collision as exc:
            last = exc
            if "slots" not in str(exc):
                raise
    raise Collision(f"no fold gives enough tape slots: {last}")


def build_v2_paced_init(n: int) -> list[str]:
    """Pair zero writes while matching the relay's six-tick throughput."""
    last = None
    for fold in (0, 2, 4, 6, 8, 10):
        try:
            return assemble_v2(n, fold, init_body="0.s")
        except Collision as exc:
            last = exc
            if "slots" not in str(exc):
                raise
    raise Collision(f"no fold gives enough tape slots: {last}")


def assemble_v2_compact_relay(n: int, fold: int = 0) -> list[str]:
    """Fit v2 in 31x31 by tightening both the relay and its pipe band.

    The steady relay cycle is still the minimum six ticks: receive, send, and
    four turns. Its smaller box frees one row and lets the east tape columns
    move one cell toward the worker without reducing the ring below N+1 slots.
    """
    iw, ih = V2_IW, V2_IH
    g = Circuit(400, 200)
    wk = worker_v2(n)
    wx, wy = 7, 7
    for (x, y), ch in wk.cell.items():
        g.set(wx + x, wy + y, ch)
    for x in range(-1, iw + 1):
        g.set(wx + x, wy - 1, "+" if x in (-1, iw) else "-")
        g.set(wx + x, wy + ih, "+" if x in (-1, iw) else "-")
    for y in range(ih):
        g.set(wx - 1, wy + y, "|")
        g.set(wx + iw, wy + y, "|")

    iy = wy + V2_IN_ROW
    for i, row in enumerate(["+-+", "|I|", "+-+"]):
        for j, ch in enumerate(row):
            g.set(wx - 6 + j, iy - 1 + i, ch)
    g.set(wx - 3, iy, ">")
    g.set(wx - 2, iy, ">")
    ox = wx + V2_OUT_COL
    for i, row in enumerate(["+-+", "|O|", "+-+"]):
        for j, ch in enumerate(row):
            g.set(ox - 1 + j, wy - 6 + i, ch)
    g.set(ox, wy - 2, "^")
    g.set(ox, wy - 3, "^")

    bottom_y = wy + ih
    fy = wy + V2_FWD_ROW
    ret_col = wx + V2_RET_COL
    east = wx + iw + 2
    b_fwd = bottom_y + 5
    r_a, r_b, r_c = bottom_y + 4, bottom_y + 3, bottom_y + 2
    relay_y = bottom_y + 3
    for i, row in enumerate(COMPACT_RELAY):
        for j, ch in enumerate(row):
            g.set(1 + j, relay_y + i, ch)
    adj = 1 + len(COMPACT_RELAY[0])

    n_fwd = _draw_pipe(
        g,
        [(wx + iw + 1, fy), (east, fy), (east, b_fwd), (adj, b_fwd)],
    )
    n_ret = _draw_pipe(
        g,
        [
            (adj, r_a),
            (east - 1, r_a),
            (east - 1, r_b),
            (adj + fold, r_b),
            (adj + fold, r_c),
            (ret_col, r_c),
            (ret_col, bottom_y + 1),
        ],
    )
    if n_fwd + n_ret < n + 1:
        raise Collision(f"tape holds {n_fwd + n_ret} slots, need >= {n + 1}")
    return [row.rstrip() for row in g.rows() if row.strip()]


def build_v2_compact_relay(n: int) -> list[str]:
    """Smallest v2 build using the compact relay."""
    last = None
    for fold in (0, 2, 4, 6, 8, 10):
        try:
            return assemble_v2_compact_relay(n, fold)
        except Collision as exc:
            last = exc
            if "slots" not in str(exc):
                raise
    raise Collision(f"no fold gives enough tape slots: {last}")


def build_v2_compact_relay_31(n: int) -> list[str]:
    """Remove the compact relay build's structurally dead leading columns."""
    rows = build_v2_compact_relay(n)
    left = min(i for row in rows for i, ch in enumerate(row) if not ch.isspace())
    return [row[left:].rstrip() for row in rows]


def constant_source(n: int) -> list[str]:
    """A compact room that walks N once per lap and offers it forever."""
    top = ">@" + lit(n) + "sv"
    bottom = "^" + " " * (len(top) - 3) + "s<"
    wall = "+" + "-" * len(top) + "+"
    return [wall, "|" + top + "|", "|" + bottom + "|", wall]


def assemble_v2_constant_register(n: int, fold: int = 0) -> list[str]:
    """Supply N from a dedicated repeating register instead of inline literals.

    The source continuously fills a short pipe into the worker's top wall. The
    two setup arms consume one value per operation; all other receives remain
    closer to either the input or tape-return pipe.
    """
    iw, ih = V2_IW - 2, V2_IH - 1
    g = Circuit(400, 200)
    wk = worker_v2(n, constant_input=True, width=iw, height=ih)
    wx, wy = 7, 8
    for (x, y), ch in wk.cell.items():
        g.set(wx + x, wy + y, ch)
    for x in range(-1, iw + 1):
        g.set(wx + x, wy - 1, "+" if x in (-1, iw) else "-")
        g.set(wx + x, wy + ih, "+" if x in (-1, iw) else "-")
    for y in range(ih):
        g.set(wx - 1, wy + y, "|")
        g.set(wx + iw, wy + y, "|")

    constant_in_row = 7
    iy = wy + constant_in_row
    for i, row in enumerate(["+-+", "|I|", "+-+"]):
        for j, ch in enumerate(row):
            g.set(wx - 6 + j, iy - 1 + i, ch)
    g.set(wx - 3, iy, ">")
    g.set(wx - 2, iy, ">")

    ox = wx + V2_OUT_COL
    for i, row in enumerate(["+-+", "|O|", "+-+"]):
        for j, ch in enumerate(row):
            g.set(ox - 1 + j, wy - 6 + i, ch)
    g.set(ox, wy - 2, "^")
    g.set(ox, wy - 3, "^")

    source = constant_source(n)
    source_x, source_y = wx + 5, wy - 7
    for i, row in enumerate(source):
        for j, ch in enumerate(row):
            g.set(source_x + j, source_y + i, ch)
    constant_col = wx + 6
    g.set(constant_col, wy - 3, "v")
    g.set(constant_col, wy - 2, "v")

    bottom_y = wy + ih
    fy = wy + V2_FWD_ROW
    ret_col = wx + V2_RET_COL + 1
    east = wx + iw + 2
    b_fwd = bottom_y + 5
    r_a, r_b, r_c = bottom_y + 4, bottom_y + 3, bottom_y + 2
    relay_y = bottom_y + 3
    for i, row in enumerate(COMPACT_RELAY):
        for j, ch in enumerate(row):
            g.set(1 + j, relay_y + i, ch)
    adj = 1 + len(COMPACT_RELAY[0])

    n_fwd = _draw_pipe(
        g,
        [
            (wx + iw + 1, fy),
            (east, fy),
            (east, fy + 2),
            (east - 1, fy + 2),
            (east - 1, fy + 4),
            (east, fy + 4),
            (east, fy + 6),
            (east - 1, fy + 6),
            (east - 1, fy + 8),
            (east, fy + 8),
            (east, b_fwd),
            (adj, b_fwd),
        ],
    )
    n_ret = _draw_pipe(
        g,
        [
            (adj, r_a),
            (east - 1, r_a),
            (east - 1, r_b),
            (adj + fold, r_b),
            (adj + fold, r_c),
            (ret_col, r_c),
            (ret_col, bottom_y + 1),
        ],
    )
    if n_fwd + n_ret < n + 1:
        raise Collision(f"tape holds {n_fwd + n_ret} slots, need >= {n + 1}")
    rows = [row.rstrip() for row in g.rows() if row.strip()]
    left = min(i for row in rows for i, ch in enumerate(row) if not ch.isspace())
    return [row[left:].rstrip() for row in rows]


def build_v2_constant_register(n: int) -> list[str]:
    """Smallest compact-relay build with a dedicated repeating N register."""
    last = None
    for fold in (0, 2, 4, 6, 8, 10):
        try:
            return assemble_v2_constant_register(n, fold)
        except Collision as exc:
            last = exc
            if "slots" not in str(exc):
                raise
    raise Collision(f"no fold gives enough tape slots: {last}")


V3_IW, V3_IH = 24, 20
V3_IN_ROW = 2
V3_OUT_COL = 2
V3_FWD_ROW = 8
V3_RET_COL = 12


def worker_v3(n: int, *, external_init: bool = False) -> Circuit:
    """Two-value-per-lap variant of :func:`worker_v2`.

    ``counted_ring`` exits east for an even count and west for an odd count.
    Each pair of exits is explicitly merged after the loop; no branch relies on
    a particular address parity.
    """
    c = Circuit(V3_IW, V3_IH, strict_corridors=True)
    L = lit(n)
    gut = V3_IW - 1
    return_gut = gut - 1

    if external_init:
        c.set(1, 0, "@")
        c.route(
            (2, 0),
            E,
            [(return_gut, 0), (return_gut, 1), (0, 1)],
            (0, 2),
            E,
        )
    else:
        x, _ = c.run(1, 0, "@" + L + "b")
        c.turn(return_gut, 1, W)
        c.route((x, 0), E, [(16, 0), (16, 5)], (16, 5), E)
        fill, _ = c.counted_loop(17, 5, "0s")
        c.route((fill, 5), E, [(gut, 5), (gut, 1)], (0, 1), S)
        c.turn(0, 2, E)

    c.run(1, 2, "rX")
    read_setup, _ = c.run(3, 2, "rbM" + L + "-M")
    c.turn(2, 3, E)
    write_setup, _ = c.run(3, 3, "rbM" + L + "-NM")
    c.route((write_setup, 3), E, [(15, 3), (15, 4)], (10, 4), W)
    c.route((read_setup, 2), E, [(15, 2), (15, 4)], (10, 4), W)
    c.turn(10, 4, S)
    c.turn(10, 5, E)

    p1_even, p1_odd = c.counted_ring(11, 5, "rs")
    c.route(p1_even, E, [], (13, 10), E)
    c.route(p1_odd, W, [(9, 9), (9, 10)], (13, 10), E)

    # Negating only A swaps the branch directions while preserving B's sign.
    # READ goes north and restores A before deriving N-1-addr; WRITE goes south
    # with A already positive.
    c.run(14, 10, "WNX")

    c.turn(16, 9, E)
    read_target, _ = c.run(17, 9, "NbmrS")

    c.turn(16, 11, W)
    c.run(15, 11, "bm", d=W)
    c.horizontal(11, 13, 2)
    c.run(2, 11, "r", d=W)
    c.route((1, 11), W, [(1, 12)], (10, 12), E)
    c.run(11, 12, "sr")

    c.route(
        (13, 12),
        E,
        [(14, 12), (14, 13), (10, 13), (10, 14)],
        (11, 14),
        E,
    )
    c.route(
        (read_target, 9),
        E,
        [(gut, 9), (gut, 13), (10, 13), (10, 14)],
        (11, 14),
        E,
    )

    p2_even, p2_odd = c.counted_ring(11, 14, "rs")
    c.route(p2_even, E, [(return_gut, 14), (return_gut, 1)], (0, 1), S)
    c.route(
        p2_odd,
        W,
        [(9, 18), (9, 19), (return_gut, 19)],
        (return_gut, 14),
        N,
    )
    return c


def assemble_v3(n: int, fold: int = 2) -> list[str]:
    """Assemble :func:`worker_v3` with the same persistent tape protocol."""
    iw, ih = V3_IW, V3_IH
    g = Circuit(400, 200)
    wk = worker_v3(n)
    wx, wy = 7, 7
    for (x, y), ch in wk.cell.items():
        g.set(wx + x, wy + y, ch)
    for x in range(-1, iw + 1):
        g.set(wx + x, wy - 1, "+" if x in (-1, iw) else "-")
        g.set(wx + x, wy + ih, "+" if x in (-1, iw) else "-")
    for y in range(ih):
        g.set(wx - 1, wy + y, "|")
        g.set(wx + iw, wy + y, "|")

    iy = wy + V3_IN_ROW
    for i, row in enumerate(["+-+", "|I|", "+-+"]):
        for j, ch in enumerate(row):
            g.set(wx - 6 + j, iy - 1 + i, ch)
    g.set(wx - 3, iy, ">")
    g.set(wx - 2, iy, ">")
    ox = wx + V3_OUT_COL
    for i, row in enumerate(["+-+", "|O|", "+-+"]):
        for j, ch in enumerate(row):
            g.set(ox - 1 + j, wy - 6 + i, ch)
    g.set(ox, wy - 2, "^")
    g.set(ox, wy - 3, "^")

    bottom_y = wy + ih
    fy = wy + V3_FWD_ROW
    ret_col = wx + V3_RET_COL
    east = wx + iw + 2
    b_fwd = bottom_y + 6
    r_a, r_b, r_c = bottom_y + 4, bottom_y + 3, bottom_y + 2
    relay_y = bottom_y + 3
    for i, row in enumerate(RELAY):
        for j, ch in enumerate(row):
            g.set(1 + j, relay_y + i, ch)
    adj = len(RELAY[0]) + 1
    n_fwd = _draw_pipe(
        g,
        [(wx + iw + 1, fy), (east, fy), (east, b_fwd), (adj, b_fwd)],
    )
    n_ret = _draw_pipe(
        g,
        [
            (adj, r_a),
            (east - 1, r_a),
            (east - 1, r_b),
            (adj + fold, r_b),
            (adj + fold, r_c),
            (ret_col, r_c),
            (ret_col, bottom_y + 1),
        ],
    )
    if n_fwd + n_ret < n + 1:
        raise Collision(f"tape holds {n_fwd + n_ret} slots, need >= {n + 1}")
    return [row.rstrip() for row in g.rows() if row.strip()]


def build_v3(n: int) -> list[str]:
    """Smallest two-value-loop build whose tape holds at least N+1 values."""
    last = None
    for fold in (0, 2, 4, 6, 8, 10):
        try:
            return assemble_v3(n, fold)
        except Collision as exc:
            last = exc
            if "slots" not in str(exc):
                raise
    raise Collision(f"no fold gives enough tape slots: {last}")


def initializing_relay(n: int) -> list[str]:
    """Relay that first injects ``n`` zeroes, then relays the tape forever.

    The worker can start immediately. Its first tape receive blocks on this
    room's output if initialization has not produced enough values yet, so the
    handoff is synchronized by pipe data rather than elapsed ticks.
    """
    if n % 2:
        raise Collision("paired relay initialization requires an even memory size")
    c = Circuit(12, 5)
    x, _ = c.run(0, 0, "@" + lit(n) + "b")
    even_exit, _odd_exit = c.counted_ring(x, 0, "0s")

    # The known-even count exits at the top. It enters steady `r` directly, so
    # no stale A value can append an extra zero during the phase transition.
    c.set(even_exit[0], even_exit[1], ">")
    c.set(10, 0, "r")
    c.set(11, 0, "v")
    c.set(11, 1, "<")
    c.set(10, 1, "s")
    c.set(9, 1, "^")
    return c.rows()


def one_shot_initializer(
    n: int,
    *,
    paired: bool = True,
    sentinel: bool = False,
) -> list[str]:
    """Emit ``n`` zeroes to the nearest pipe, then halt permanently."""
    if paired and n % 2:
        raise Collision("paired one-shot initialization requires an even memory size")
    c = Circuit(10, 5 if paired else 4)
    x, _ = c.run(0, 0, "@" + lit(n) + "b")
    if paired:
        even_exit, _odd_exit = c.counted_ring(x, 0, "0s")
        exit_cell = even_exit
    else:
        exit_cell = c.counted_loop(x, 0, "0s")
    if sentinel:
        c.set(exit_cell[0], exit_cell[1], "v")
        c.run(exit_cell[0], 1, "1sH", d=S)
    else:
        c.set(exit_cell[0], exit_cell[1], "H")
    return c.rows()


def assemble_v3_external_init(n: int, fold: int = 0) -> list[str]:
    """Paired-loop memory whose relay independently initializes the tape."""
    iw, ih = V3_IW, V3_IH
    g = Circuit(400, 200)
    wk = worker_v3(n, external_init=True)
    wx, wy = 7, 7
    for (x, y), ch in wk.cell.items():
        g.set(wx + x, wy + y, ch)
    for x in range(-1, iw + 1):
        g.set(wx + x, wy - 1, "+" if x in (-1, iw) else "-")
        g.set(wx + x, wy + ih, "+" if x in (-1, iw) else "-")
    for y in range(ih):
        g.set(wx - 1, wy + y, "|")
        g.set(wx + iw, wy + y, "|")

    iy = wy + V3_IN_ROW
    for i, row in enumerate(["+-+", "|I|", "+-+"]):
        for j, ch in enumerate(row):
            g.set(wx - 6 + j, iy - 1 + i, ch)
    g.set(wx - 3, iy, ">")
    g.set(wx - 2, iy, ">")
    ox = wx + V3_OUT_COL
    for i, row in enumerate(["+-+", "|O|", "+-+"]):
        for j, ch in enumerate(row):
            g.set(ox - 1 + j, wy - 6 + i, ch)
    g.set(ox, wy - 2, "^")
    g.set(ox, wy - 3, "^")

    bottom_y = wy + ih
    fy = wy + V3_FWD_ROW
    ret_col = wx + V3_RET_COL
    east = wx + iw + 2
    relay = initializing_relay(n)
    relay_x, relay_y = 1, bottom_y + 5
    for i, row in enumerate(relay):
        for j, ch in enumerate(row):
            g.set(relay_x + j, relay_y + i, ch)
    for x in range(len(relay[0]) + 2):
        g.set(
            relay_x - 1 + x,
            relay_y - 1,
            "+" if x in (0, len(relay[0]) + 1) else "-",
        )
        g.set(
            relay_x - 1 + x,
            relay_y + len(relay),
            "+" if x in (0, len(relay[0]) + 1) else "-",
        )
    for y in range(len(relay)):
        g.set(relay_x - 1, relay_y + y, "|")
        g.set(relay_x + len(relay[0]), relay_y + y, "|")

    # Both tape pipes use the relay's right wall. Keep the return endpoint above
    # the forward endpoint so its capacity snake can climb back to the worker
    # without crossing the forward pipe.
    ret_y = relay_y
    fwd_y = relay_y + len(relay) - 1
    adj = relay_x + len(relay[0]) + 1
    n_fwd = _draw_pipe(
        g,
        [(wx + iw + 1, fy), (east, fy), (east, fwd_y), (adj, fwd_y)],
    )
    n_ret = _draw_pipe(
        g,
        [
            (adj, ret_y),
            (east - 1, ret_y),
            (east - 1, bottom_y + 4),
            (adj + fold, bottom_y + 4),
            (adj + fold, bottom_y + 3),
            (east - 1, bottom_y + 3),
            (east - 1, bottom_y + 2),
            (ret_col, bottom_y + 2),
            (ret_col, bottom_y + 1),
        ],
    )
    if n_fwd + n_ret < n + 1:
        raise Collision(f"tape holds {n_fwd + n_ret} slots, need >= {n + 1}")
    return [row.rstrip() for row in g.rows() if row.strip()]


def build_v3_external_init(n: int) -> list[str]:
    """Smallest independently initialized paired-loop build."""
    last = None
    for fold in (0, 2, 4, 6, 8, 10):
        try:
            return assemble_v3_external_init(n, fold)
        except Collision as exc:
            last = exc
            if "slots" not in str(exc):
                raise
    raise Collision(f"no fold gives enough tape slots: {last}")


def assemble_v3_upstream_init(n: int) -> list[str]:
    """Initialize upstream, then forward through a separate ordinary relay.

    The initializer has one permanent job after startup: receive worker output
    and send it to the short middle pipe. The ordinary relay receives that pipe
    and sends to the worker return pipe. Both rooms therefore keep running and
    the two relay stages pipeline naturally.
    """
    iw, ih = V3_IW, V3_IH
    g = Circuit(400, 200)
    wk = worker_v3(n, external_init=True)
    wx, wy = 7, 7
    for (x, y), ch in wk.cell.items():
        g.set(wx + x, wy + y, ch)
    for x in range(-1, iw + 1):
        g.set(wx + x, wy - 1, "+" if x in (-1, iw) else "-")
        g.set(wx + x, wy + ih, "+" if x in (-1, iw) else "-")
    for y in range(ih):
        g.set(wx - 1, wy + y, "|")
        g.set(wx + iw, wy + y, "|")

    iy = wy + V3_IN_ROW
    for i, row in enumerate(["+-+", "|I|", "+-+"]):
        for j, ch in enumerate(row):
            g.set(wx - 6 + j, iy - 1 + i, ch)
    g.set(wx - 3, iy, ">")
    g.set(wx - 2, iy, ">")
    ox = wx + V3_OUT_COL
    for i, row in enumerate(["+-+", "|O|", "+-+"]):
        for j, ch in enumerate(row):
            g.set(ox - 1 + j, wy - 6 + i, ch)
    g.set(ox, wy - 2, "^")
    g.set(ox, wy - 3, "^")

    bottom_y = wy + ih
    fy = wy + V3_FWD_ROW
    ret_col = wx + V3_RET_COL
    east = wx + iw + 2

    initializer = initializing_relay(n)
    init_x, init_y = 1, bottom_y + 5
    for i, row in enumerate(initializer):
        for j, ch in enumerate(row):
            g.set(init_x + j, init_y + i, ch)
    for x in range(len(initializer[0]) + 2):
        g.set(
            init_x - 1 + x,
            init_y - 1,
            "+" if x in (0, len(initializer[0]) + 1) else "-",
        )
        g.set(
            init_x - 1 + x,
            init_y + len(initializer),
            "+" if x in (0, len(initializer[0]) + 1) else "-",
        )
    for y in range(len(initializer)):
        g.set(init_x - 1, init_y + y, "|")
        g.set(init_x + len(initializer[0]), init_y + y, "|")

    relay_x, relay_y = 16, bottom_y + 4
    for i, row in enumerate(COMPACT_RELAY):
        for j, ch in enumerate(row):
            g.set(relay_x + j, relay_y + i, ch)

    init_adj = init_x + len(initializer[0]) + 1
    relay_adj = relay_x - 1
    n_fwd = _draw_pipe(
        g,
        [(wx + iw + 1, fy), (east, fy), (east, init_y + 4), (init_adj, init_y + 4)],
    )
    n_middle = _draw_pipe(g, [(init_adj, init_y - 1), (relay_adj, init_y - 1)])
    n_ret = _draw_pipe(
        g,
        [
            (relay_x + len(COMPACT_RELAY[0]), relay_y + 3),
            (east - 1, relay_y + 3),
            (east - 1, bottom_y + 6),
            (relay_x + len(COMPACT_RELAY[0]) + 1, bottom_y + 6),
            (relay_x + len(COMPACT_RELAY[0]) + 1, bottom_y + 5),
            (east - 1, bottom_y + 5),
            (east - 1, bottom_y + 4),
            (relay_x + len(COMPACT_RELAY[0]), bottom_y + 4),
            (relay_x + len(COMPACT_RELAY[0]), bottom_y + 3),
            (east - 1, bottom_y + 3),
            (east - 1, bottom_y + 2),
            (ret_col, bottom_y + 2),
            (ret_col, bottom_y + 1),
        ],
    )
    if n_fwd + n_middle + n_ret < n + 1:
        raise Collision(
            f"tape holds {n_fwd + n_middle + n_ret} slots, need >= {n + 1}"
        )
    return [row.rstrip() for row in g.rows() if row.strip()]


def build_v3_upstream_init(n: int) -> list[str]:
    """Build the upstream-initializer plus ordinary-relay experiment."""
    return assemble_v3_upstream_init(n)


def assemble_v3_one_shot_init(
    n: int,
    east_extension: int = 3,
    *,
    paired_fill: bool = False,
) -> list[str]:
    """Use a one-shot zero producer beside the persistent tape relay."""
    iw, ih = V3_IW, V3_IH
    g = Circuit(400, 200)
    wk = worker_v3(n, external_init=True)
    wx, wy = 7, 7
    for (x, y), ch in wk.cell.items():
        g.set(wx + x, wy + y, ch)
    for x in range(-1, iw + 1):
        g.set(wx + x, wy - 1, "+" if x in (-1, iw) else "-")
        g.set(wx + x, wy + ih, "+" if x in (-1, iw) else "-")
    for y in range(ih):
        g.set(wx - 1, wy + y, "|")
        g.set(wx + iw, wy + y, "|")

    iy = wy + V3_IN_ROW
    for i, row in enumerate(["+-+", "|I|", "+-+"]):
        for j, ch in enumerate(row):
            g.set(wx - 6 + j, iy - 1 + i, ch)
    g.set(wx - 3, iy, ">")
    g.set(wx - 2, iy, ">")
    ox = wx + V3_OUT_COL
    for i, row in enumerate(["+-+", "|O|", "+-+"]):
        for j, ch in enumerate(row):
            g.set(ox - 1 + j, wy - 6 + i, ch)
    g.set(ox, wy - 2, "^")
    g.set(ox, wy - 3, "^")

    bottom_y = wy + ih
    fy = wy + V3_FWD_ROW
    east = wx + iw + 2
    merge_east = east + east_extension

    filler = one_shot_initializer(n, paired=paired_fill, sentinel=True)
    fill_x, fill_y = 1, bottom_y + 4
    for i, row in enumerate(filler):
        for j, ch in enumerate(row):
            g.set(fill_x + j, fill_y + i, ch)
    for x in range(len(filler[0]) + 2):
        g.set(
            fill_x - 1 + x,
            fill_y - 1,
            "+" if x in (0, len(filler[0]) + 1) else "-",
        )
        g.set(
            fill_x - 1 + x,
            fill_y + len(filler),
            "+" if x in (0, len(filler[0]) + 1) else "-",
        )
    for y in range(len(filler)):
        g.set(fill_x - 1, fill_y + y, "|")
        g.set(fill_x + len(filler[0]), fill_y + y, "|")

    relay_x, relay_y = 24, bottom_y + 2
    for i, row in enumerate(PHASE_RELAY):
        for j, ch in enumerate(row):
            g.set(relay_x + j, relay_y + i, ch)

    # A sentinel makes the phase change data-driven. The relay's startup `r`
    # drains only the filler pipe; after the sentinel turns at X, its steady `r`
    # drains only the CPU pipe.
    n_fwd = _draw_pipe(
        g,
        [
            (wx + iw + 1, fy),
            (merge_east, fy),
            (merge_east, bottom_y + 9),
            (fill_x + len(filler[0]) + 2, bottom_y + 9),
            (fill_x + len(filler[0]) + 2, bottom_y + 8),
            (merge_east - 1, bottom_y + 8),
            (merge_east - 1, relay_y + 3),
            (relay_x + len(PHASE_RELAY[0]), relay_y + 3),
        ],
    )
    n_fill = _draw_pipe(
        g,
        [
            (fill_x + len(filler[0]) + 1, fill_y + 2),
            (relay_x - 2, fill_y + 2),
            (relay_x - 2, relay_y + 2),
            (relay_x - 1, relay_y + 2),
        ],
    )
    n_ret = _draw_pipe(
        g,
        [
            (relay_x + len(PHASE_RELAY[0]), relay_y + 1),
            (merge_east - 1, relay_y + 1),
            (merge_east - 1, wy + 10),
            (merge_east - 2, wy + 10),
            (merge_east - 2, bottom_y + 1),
            (merge_east - 3, bottom_y + 1),
            (merge_east - 3, wy + 9),
            (wx + iw + 1, wy + 9),
        ],
    )
    if n_fwd + n_ret < n + 1:
        raise Collision(f"tape holds {n_fwd + n_ret} slots, need >= {n + 1}")
    if n_fill < 2:
        raise Collision("filler pipe must have at least two cells")
    return [row.rstrip() for row in g.rows() if row.strip()]


def build_v3_one_shot_init(n: int) -> list[str]:
    """Build the one-shot filler plus persistent relay experiment."""
    return assemble_v3_one_shot_init(n)


def assemble_v3_debug(n: int) -> tuple[list[str], DebugMap]:
    """Build v3 together with the geometry used to generate it."""
    last: Collision | None = None
    fold = 0
    rows: list[str] | None = None
    for candidate in (0, 2, 4, 6, 8, 10):
        try:
            rows = assemble_v3(n, candidate)
            fold = candidate
            break
        except Collision as exc:
            last = exc
            if "slots" not in str(exc):
                raise
    if rows is None:
        raise Collision(f"no fold gives enough tape slots: {last}")

    wx, wy = 7, 7
    iw, ih = V3_IW, V3_IH
    worker = (wx, wy)
    dbg = DebugMap(f"memory tape v3 n={n}, fold={fold}")
    dbg.region(
        "worker",
        wx,
        wy,
        iw,
        ih,
        note="operation decode, paired tape passes, and target access",
        color="#38bdf8",
    )
    dbg.region_relative(
        "init",
        worker,
        0,
        0,
        24,
        7,
        note="fill the tape with n zero values",
        color="#64748b",
    )
    dbg.region_relative(
        "main-dispatch",
        worker,
        0,
        2,
        16,
        3,
        note="opcode 0 enters read setup; opcode 1 enters write setup",
        color="#60a5fa",
    )
    dbg.region_relative(
        "first-pass",
        worker,
        9,
        5,
        5,
        6,
        note="two values are rotated per lap; parity exits merge below",
        color="#22c55e",
    )
    dbg.region_relative(
        "target-dispatch",
        worker,
        14,
        9,
        3,
        3,
        note="read turns north; write turns south",
        color="#f59e0b",
    )
    dbg.region_relative(
        "read-target",
        worker,
        17,
        9,
        7,
        5,
        note="read target; S emits it and returns it to tape",
        color="#a78bfa",
    )
    dbg.region_relative(
        "write-target",
        worker,
        1,
        11,
        14,
        3,
        note="receive new input, append it, consume old target",
        color="#fb923c",
    )
    dbg.region_relative(
        "second-pass",
        worker,
        9,
        14,
        14,
        6,
        note="paired loop returns the remaining values and restores alignment",
        color="#14b8a6",
    )

    iy = wy + V3_IN_ROW
    ox = wx + V3_OUT_COL
    dbg.region(
        "input-room",
        wx - 6,
        iy - 1,
        3,
        3,
        note="operation stream",
        color="#22c55e",
    )
    dbg.region(
        "output-room",
        ox - 1,
        wy - 6,
        3,
        3,
        note="read results",
        color="#a78bfa",
    )

    bottom_y = wy + ih
    fy = wy + V3_FWD_ROW
    ret_col = wx + V3_RET_COL
    east = wx + iw + 2
    b_fwd = bottom_y + 6
    r_a, r_b, r_c = bottom_y + 4, bottom_y + 3, bottom_y + 2
    relay_y = bottom_y + 3
    adj = len(RELAY[0]) + 1
    fwd = [(wx + iw + 1, fy), (east, fy), (east, b_fwd), (adj, b_fwd)]
    ret = [
        (adj, r_a),
        (east - 1, r_a),
        (east - 1, r_b),
        (adj + fold, r_b),
        (adj + fold, r_c),
        (ret_col, r_c),
        (ret_col, bottom_y + 1),
    ]
    dbg.region(
        "relay",
        1,
        relay_y,
        len(RELAY[0]),
        len(RELAY),
        note="turnaround room for the value ring",
        color="#fb7185",
    )
    dbg.lane(
        "tape-forward-pipe",
        fwd,
        kind="pipe",
        expect="worker sends values toward relay",
        color="#34d399",
    )
    dbg.lane(
        "tape-return-pipe",
        ret,
        kind="pipe",
        expect="relay returns values into worker bottom wall",
        color="#10b981",
    )
    dbg.lane(
        "input-pipe",
        [(wx - 3, iy), (wx - 1, iy)],
        kind="pipe",
        expect="operations enter worker",
        color="#22c55e",
    )
    dbg.lane(
        "output-pipe",
        [(ox, wy - 2), (ox, wy - 5)],
        kind="pipe",
        expect="read values leave worker",
        color="#a78bfa",
    )

    dbg.lane_relative(
        "read-setup",
        worker,
        [(3, 2), (15, 2), (15, 4), (10, 4)],
        kind="expected",
        expect="op=0: prepare addr for the first paired pass",
        color="#60a5fa",
    )
    dbg.lane_relative(
        "write-setup",
        worker,
        [(3, 3), (15, 3), (15, 4), (10, 4)],
        kind="expected",
        expect="op=1: preserve write state while preparing addr",
        color="#fb923c",
    )
    dbg.lane_relative(
        "first-paired-loop",
        worker,
        [(11, 5), (12, 5), (12, 9), (11, 9), (11, 5)],
        kind="expected",
        expect="rotate exactly addr values, two per full lap",
        color="#22c55e",
    )
    dbg.lane_relative(
        "first-odd-exit",
        worker,
        [(10, 9), (9, 9), (9, 10), (13, 10)],
        kind="expected",
        expect="odd addr exits west and rejoins the even path",
        color="#84cc16",
    )
    dbg.lane_relative(
        "read-target-access",
        worker,
        [(16, 10), (16, 9), (22, 9)],
        kind="expected",
        expect="target read is sent to output and back to tape",
        color="#a78bfa",
    )
    dbg.lane_relative(
        "write-target-access",
        worker,
        [(16, 10), (16, 11), (1, 11), (1, 12), (13, 12)],
        kind="expected",
        expect="new value enters before old target is discarded",
        color="#fb923c",
    )
    dbg.lane_relative(
        "second-paired-loop",
        worker,
        [(11, 14), (12, 14), (12, 18), (11, 18), (11, 14)],
        kind="expected",
        expect="restore alignment, two values per full lap",
        color="#14b8a6",
    )
    dbg.lane_relative(
        "second-odd-exit",
        worker,
        [(10, 18), (9, 18), (9, 19), (22, 19), (22, 14)],
        kind="expected",
        expect="odd remainder exits west and rejoins the even return",
        color="#2dd4bf",
    )
    dbg.scenario(
        "write-read-7",
        "1 7 42 0 7",
        500,
        1600,
        watch=[
            "write-setup",
            "first-paired-loop",
            "write-target-access",
            "second-paired-loop",
            "tape-forward-pipe",
            "tape-return-pipe",
        ],
        note="write 42 at address 7, then read address 7",
    )

    first_row = wy - 6
    return rows, dbg.translated(0, -first_row)


def assemble_v2_debug(n: int) -> tuple[list[str], DebugMap]:
    """Build the compact tape machine plus named regions, pipes, and routes.

    The .man grid has no comment syntax.  This sidecar deliberately keeps the
    geometry in one place, including the fold picked for the requested capacity.
    """
    last: Collision | None = None
    fold = 0
    rows: list[str] | None = None
    for candidate in (0, 2, 4, 6, 8, 10):
        try:
            rows = assemble_v2(n, candidate)
            fold = candidate
            break
        except Collision as exc:
            last = exc
            if "slots" not in str(exc):
                raise
    if rows is None:
        raise Collision(f"no fold gives enough tape slots: {last}")

    wx, wy = 7, 7
    iw, ih = V2_IW, V2_IH
    dbg = DebugMap(f"memory tape v2 n={n}, fold={fold}")
    dbg.region("worker", wx, wy, iw, ih, note="operation decode, two tape passes, and target access", color="#38bdf8")
    dbg.region("init", wx, wy, 20, 6, note="fill the tape with n zero values", color="#64748b")
    dbg.region("main-dispatch", wx, wy + 2, 15, 3, note="opcode 0 enters read setup; opcode 1 enters write setup", color="#60a5fa")
    dbg.region("first-pass", wx + 10, wy + 4, 4, 7, note="rotate addr values from tape head to target", color="#22c55e")
    dbg.region("target-dispatch", wx + 13, wy + 8, 4, 4, note="sign of B selects read or write target", color="#f59e0b")
    dbg.region("read-target", wx + 15, wy + 10, 7, 3, note="read target; S emits it and returns it to tape", color="#a78bfa")
    dbg.region("write-target", wx + 1, wy + 8, 14, 6, note="receive new input, append it, consume old target", color="#fb923c")
    dbg.region("second-pass", wx + 10, wy + 13, 4, 5, note="return remaining n-1-addr values to their original alignment", color="#14b8a6")

    iy = wy + V2_IN_ROW
    ox = wx + V2_OUT_COL
    dbg.region("input-room", wx - 6, iy - 1, 3, 3, note="operation stream", color="#22c55e")
    dbg.region("output-room", ox - 1, wy - 6, 3, 3, note="read results", color="#a78bfa")

    bottom_y = wy + ih
    fy = wy + V2_FWD_ROW
    ret_col = wx + V2_RET_COL
    east = wx + iw + 2
    b_fwd = bottom_y + 6
    r_a, r_b, r_c = bottom_y + 4, bottom_y + 3, bottom_y + 2
    relay_y = bottom_y + 3
    adj = len(RELAY[0]) + 1
    fwd = [(wx + iw + 1, fy), (east, fy), (east, b_fwd), (adj, b_fwd)]
    ret = [
        (adj, r_a), (east - 1, r_a), (east - 1, r_b), (adj + fold, r_b),
        (adj + fold, r_c), (ret_col, r_c), (ret_col, bottom_y + 1),
    ]
    dbg.region("relay", 1, relay_y, len(RELAY[0]), len(RELAY), note="turnaround room for the value ring", color="#fb7185")
    dbg.lane("tape-forward-pipe", fwd, kind="pipe", expect="worker sends values toward relay", color="#34d399")
    dbg.lane("tape-return-pipe", ret, kind="pipe", expect="relay returns values into worker bottom wall", color="#10b981")
    dbg.lane("input-pipe", [(wx - 3, iy), (wx - 1, iy)], kind="pipe", expect="operations enter worker", color="#22c55e")
    dbg.lane("output-pipe", [(ox, wy - 2), (ox, wy - 5)], kind="pipe", expect="read values leave worker", color="#a78bfa")

    # These are semantic lanes, not additional tracks: they state the intended
    # runner route used by the focused tracer.
    dbg.lane("read-setup", [(wx + 3, wy + 2), (wx + 15, wy + 2), (wx + 15, wy + 4), (wx + 10, wy + 4)], kind="expected", expect="op=0: addr -> B=+(n-addr), BP=addr", color="#60a5fa")
    dbg.lane("write-setup", [(wx + 3, wy + 3), (wx + 15, wy + 3), (wx + 15, wy + 4), (wx + 10, wy + 4)], kind="expected", expect="op=1: addr -> B=-(n-addr), BP=addr", color="#fb923c")
    dbg.lane("first-tape-pass", [(wx + 11, wy + 5), (wx + 12, wy + 5), (wx + 12, wy + 8), (wx + 11, wy + 8), (wx + 11, wy + 5)], kind="expected", expect="pass exactly addr values through the ring", color="#22c55e")
    dbg.lane("read-target-access", [(wx + 15, wy + 10), (wx + 15, wy + 11), (wx + 20, wy + 11)], kind="expected", expect="target read is sent to output and tape", color="#a78bfa")
    dbg.lane("write-target-access", [(wx + 15, wy + 9), (wx + 2, wy + 9), (wx + 2, wy + 12), (wx + 12, wy + 12)], kind="expected", expect="new value enters before old target is discarded", color="#fb923c")
    dbg.lane("second-tape-pass", [(wx + 11, wy + 14), (wx + 12, wy + 14), (wx + 12, wy + 17), (wx + 11, wy + 17), (wx + 11, wy + 14)], kind="expected", expect="pass n-1-addr remaining values; alignment is restored", color="#14b8a6")
    dbg.scenario(
        "write-read-7",
        "1 7 42 0 7",
        700,
        2100,
        watch=["write-setup", "first-tape-pass", "write-target-access", "second-tape-pass", "tape-forward-pipe", "tape-return-pipe"],
        note="write 42 at address 7, then read address 7",
    )

    # assemble_v2 removes leading all-blank rows, so translate the sidecar too.
    first_row = wy - 6
    return rows, dbg.translated(0, -first_row)


def assemble_v3_external_init_debug(n: int) -> tuple[list[str], DebugMap]:
    """Build the independent initializer candidate with matching debug geometry."""
    rows = build_v3_external_init(n)
    _, dbg = assemble_v3_debug(n)
    dbg.title = f"memory tape v3 independent relay initialization n={n}"
    dbg.regions = [
        region for region in dbg.regions if region.name not in ("init", "relay")
    ]
    dbg.lanes = [
        lane
        for lane in dbg.lanes
        if lane.name not in ("tape-forward-pipe", "tape-return-pipe")
    ]
    dbg.region(
        "independent-fill-relay",
        0,
        30,
        14,
        7,
        note=(
            "own room: emit two zeroes per lap until all n cells exist, then "
            "fall directly into the steady receive/send relay"
        ),
        color="#facc15",
    )
    dbg.region(
        "cpu-direct-start",
        6,
        5,
        V3_IW + 2,
        4,
        note="CPU starts command decode immediately; tape receives provide synchronization",
        color="#60a5fa",
    )
    dbg.lane(
        "independent-zero-fill",
        [(8, 31), (9, 31), (9, 35), (8, 35), (8, 31)],
        kind="expected",
        expect="known-even n: two zero sends per lap, exactly n values total",
        color="#fde047",
    )
    dbg.lane(
        "fill-to-steady-handoff",
        [(10, 31), (11, 31), (12, 31), (12, 32), (11, 32)],
        kind="expected",
        expect="even exit enters r before s, preventing an extra stale zero",
        color="#fb923c",
    )
    dbg.lane(
        "tape-forward-pipe",
        [(32, 14), (33, 14), (33, 35), (14, 35)],
        kind="pipe",
        expect="worker sends rotated values to the independent relay",
        color="#34d399",
    )
    dbg.lane(
        "tape-return-pipe",
        [
            (14, 31),
            (32, 31),
            (32, 30),
            (14, 30),
            (14, 29),
            (32, 29),
            (32, 28),
            (19, 28),
            (19, 27),
        ],
        kind="pipe",
        expect="initializer and steady relay return values to the worker",
        color="#10b981",
    )
    return rows, dbg


def assemble_v3_upstream_init_debug(n: int) -> tuple[list[str], DebugMap]:
    """Build the split initializer/relay candidate with named dataflow."""
    rows = build_v3_upstream_init(n)
    _, dbg = assemble_v3_external_init_debug(n)
    dbg.title = f"memory tape v3 upstream initialization n={n}"
    dbg.regions = [
        region
        for region in dbg.regions
        if region.name != "independent-fill-relay"
    ]
    dbg.lanes = [
        lane
        for lane in dbg.lanes
        if lane.name
        not in (
            "tape-forward-pipe",
            "tape-return-pipe",
        )
    ]
    dbg.region(
        "upstream-initializer",
        0,
        30,
        14,
        7,
        note=(
            "emit exactly n zeroes, then permanently forward CPU values to "
            "the ordinary relay"
        ),
        color="#facc15",
    )
    dbg.region(
        "ordinary-relay",
        16,
        30,
        len(COMPACT_RELAY[0]),
        len(COMPACT_RELAY),
        note="steady receive/send stage; initialization is not part of this room",
        color="#a78bfa",
    )
    dbg.lane(
        "cpu-to-initializer",
        [(32, 14), (33, 14), (33, 35), (14, 35)],
        kind="pipe",
        expect="all worker tape output enters the initializer pass-through",
        color="#34d399",
    )
    dbg.lane(
        "initializer-to-relay",
        [(14, 30), (15, 30)],
        kind="pipe",
        expect="startup zeroes and later CPU values use the same short pipe",
        color="#22d3ee",
    )
    dbg.lane(
        "relay-to-cpu",
        [
            (22, 33),
            (32, 33),
            (32, 32),
            (23, 32),
            (23, 31),
            (32, 31),
            (32, 30),
            (22, 30),
            (22, 29),
            (32, 29),
            (32, 28),
            (19, 28),
            (19, 27),
        ],
        kind="pipe",
        expect="ordinary relay returns the circulating tape to the worker",
        color="#10b981",
    )
    return rows, dbg


def assemble_v3_one_shot_init_debug(n: int) -> tuple[list[str], DebugMap]:
    """Build the one-shot filler candidate with both pipe producers marked."""
    rows = build_v3_one_shot_init(n)
    _, dbg = assemble_v3_external_init_debug(n)
    dbg.title = f"memory tape v3 one-shot initialization n={n}"
    dbg.regions = [
        region
        for region in dbg.regions
        if region.name != "independent-fill-relay"
    ]
    dbg.lanes = [
        lane
        for lane in dbg.lanes
        if lane.name
        not in (
            "tape-forward-pipe",
            "tape-return-pipe",
            "independent-zero-fill",
            "fill-to-steady-handoff",
        )
    ]
    dbg.region(
        "one-shot-filler",
        0,
        29,
        12,
        6,
        note="emit n zeroes, append a positive sentinel, then halt permanently",
        color="#facc15",
    )
    dbg.region(
        "phase-relay",
        24,
        28,
        len(PHASE_RELAY[0]),
        len(PHASE_RELAY),
        note="drain filler until sentinel, then switch permanently to CPU tape input",
        color="#a78bfa",
    )
    dbg.region(
        "relay-startup-loop",
        24,
        28,
        8,
        3,
        note="only filler input is visible here; zeroes are forwarded to return",
        color="#fb923c",
    )
    dbg.region(
        "relay-steady-loop",
        27,
        31,
        5,
        3,
        note="six-tick CPU receive/send loop entered only after the sentinel",
        color="#c084fc",
    )
    dbg.lane(
        "cpu-forward-pipe",
        [
            (32, 14),
            (36, 14),
            (36, 35),
            (13, 35),
            (13, 34),
            (35, 34),
            (35, 31),
            (32, 31),
        ],
        kind="pipe",
        expect="persistent CPU tape input, ignored until the sentinel phase switch",
        color="#34d399",
    )
    dbg.lane(
        "filler-pipe",
        [(12, 32), (22, 32), (22, 30), (23, 30)],
        kind="pipe",
        expect="n zeroes followed by +1 sentinel; producer then halts",
        color="#fde047",
    )
    dbg.lane(
        "compact-zero-fill",
        [(8, 30), (9, 30), (9, 33), (8, 33), (8, 30)],
        kind="expected",
        expect="single-send counted loop; relay startup loop is the throughput limit",
        color="#facc15",
    )
    dbg.lane(
        "sentinel-and-halt",
        [(10, 30), (10, 33)],
        kind="expected",
        expect="emit +1 after all zeroes, then execute H",
        color="#fb923c",
    )
    dbg.lane(
        "relay-startup",
        [(27, 30), (30, 30), (30, 29), (26, 29), (26, 30)],
        kind="expected",
        expect="zero goes straight through X, is sent, and loops to filler receive",
        color="#fdba74",
    )
    dbg.lane(
        "sentinel-phase-switch",
        [(28, 30), (28, 31)],
        kind="expected",
        expect="+1 turns south at X and enters the steady CPU loop",
        color="#f472b6",
    )
    dbg.lane(
        "relay-steady",
        [(28, 31), (30, 31), (30, 32), (28, 32), (28, 31)],
        kind="expected",
        expect="persistent six-tick CPU receive/send loop",
        color="#c084fc",
    )
    dbg.lane(
        "relay-to-cpu",
        [
            (32, 29),
            (35, 29),
            (35, 16),
            (34, 16),
            (34, 27),
            (33, 27),
            (33, 15),
            (32, 15),
        ],
        kind="pipe",
        expect="phase relay returns filler zeroes, then circulating CPU tape values",
        color="#10b981",
    )
    return rows, dbg


def assemble_v2_compact_relay_debug(n: int) -> tuple[list[str], DebugMap]:
    """Build the relay experiment with sidecar geometry matching its pipes."""
    rows = build_v2_compact_relay(n)
    _, dbg = assemble_v2_debug(n)
    dbg.title = f"memory tape v2 compact relay n={n}"
    dbg.regions = [region for region in dbg.regions if region.name != "relay"]
    dbg.lanes = [
        lane
        for lane in dbg.lanes
        if lane.name not in ("tape-forward-pipe", "tape-return-pipe")
    ]

    wx, wy = 7, 7
    bottom_y = wy + V2_IH
    east = wx + V2_IW + 2
    adj = 1 + len(COMPACT_RELAY[0])
    first_row = wy - 6
    dbg.region(
        "compact-relay",
        1,
        bottom_y + 3 - first_row,
        len(COMPACT_RELAY[0]),
        len(COMPACT_RELAY),
        note="minimum six-tick receive/send loop with a one-time @ entry tail",
        color="#fb7185",
    )
    dbg.lane(
        "tape-forward-pipe",
        [
            (wx + V2_IW + 1, wy + V2_FWD_ROW - first_row),
            (east, wy + V2_FWD_ROW - first_row),
            (east, bottom_y + 5 - first_row),
            (adj, bottom_y + 5 - first_row),
        ],
        kind="pipe",
        expect="worker sends values toward compact relay",
        color="#34d399",
    )
    dbg.lane(
        "tape-return-pipe",
        [
            (adj, bottom_y + 4 - first_row),
            (east - 1, bottom_y + 4 - first_row),
            (east - 1, bottom_y + 3 - first_row),
            (adj, bottom_y + 3 - first_row),
            (adj, bottom_y + 2 - first_row),
            (wx + V2_RET_COL, bottom_y + 2 - first_row),
            (wx + V2_RET_COL, bottom_y + 1 - first_row),
        ],
        kind="pipe",
        expect="compact relay returns values into worker bottom wall",
        color="#10b981",
    )
    return rows, dbg


def assemble_v2_compact_relay_31_debug(n: int) -> tuple[list[str], DebugMap]:
    """Build the 31x31 relay variant and translate its generated sidecar."""
    untrimmed, dbg = assemble_v2_compact_relay_debug(n)
    left = min(
        i for row in untrimmed for i, ch in enumerate(row) if not ch.isspace()
    )
    rows = [row[left:].rstrip() for row in untrimmed]
    dbg.title = f"memory tape v2 compact relay 31x31 n={n}"
    return rows, dbg.translated(-left, 0)


def assemble_v2_constant_register_debug(n: int) -> tuple[list[str], DebugMap]:
    """Build the repeating-N experiment with its source and setup lanes marked."""
    rows = build_v2_constant_register(n)
    _, dbg = assemble_v2_compact_relay_31_debug(n)
    dbg = dbg.translated(0, 1)
    dbg.title = f"memory tape v2 dedicated constant register n={n}"
    dbg.regions = [
        region
        for region in dbg.regions
        if region.name
        not in (
            "worker",
            "input-room",
            "main-dispatch",
            "read-target",
            "write-target",
            "second-pass",
            "compact-relay",
        )
    ]
    dbg.lanes = [
        lane
        for lane in dbg.lanes
        if lane.name
        not in (
            "input-pipe",
            "read-setup",
            "write-setup",
            "read-target-access",
            "write-target-access",
            "second-tape-pass",
            "tape-forward-pipe",
            "tape-return-pipe",
        )
    ]
    dbg.region(
        "worker",
        6,
        7,
        V2_IW - 2,
        V2_IH - 1,
        note="narrow worker with shared decode and horizontal second pass",
        color="#38bdf8",
    )
    dbg.region(
        "constant-register",
        11,
        0,
        len(constant_source(n)[0]),
        len(constant_source(n)),
        note="walk N once per lap; initialization and every operation consume it",
        color="#facc15",
    )
    dbg.region(
        "input-room",
        0,
        13,
        3,
        3,
        note="lowered beside command decode and the write-value receive",
        color="#22c55e",
    )
    dbg.region(
        "shared-command-setup",
        6,
        9,
        12,
        4,
        note="receive op and addr once; BP keeps addr while op selects an arm",
        color="#60a5fa",
    )
    dbg.region(
        "folded-read-target",
        21,
        18,
        4,
        3,
        note="b,m on the top row; tape receive and fan-out run back to the left",
        color="#a78bfa",
    )
    dbg.region(
        "write-target",
        10,
        16,
        13,
        5,
        note="short path to lower input, then append new value and discard old",
        color="#fb923c",
    )
    dbg.region(
        "horizontal-second-pass",
        17,
        20,
        9,
        4,
        note="clockwise rotation of the same eight-tick counted rs loop",
        color="#14b8a6",
    )
    dbg.region(
        "compact-relay",
        0,
        27,
        len(COMPACT_RELAY[0]),
        len(COMPACT_RELAY),
        note="minimum six-tick receive/send turnaround",
        color="#fb7185",
    )
    dbg.lane(
        "constant-pipe",
        [(12, 4), (12, 5)],
        kind="pipe",
        expect="a ready N value enters the worker above both setup receives",
        color="#fde047",
    )
    dbg.lane(
        "input-pipe",
        [(3, 14), (5, 14)],
        kind="pipe",
        expect="operations enter near command decode and the write target",
        color="#22c55e",
    )
    dbg.lane(
        "shared-command-decode",
        [(7, 9), (9, 9), (9, 10), (6, 10), (6, 11), (9, 11)],
        kind="expected",
        expect="retain op in B, receive addr, put addr in BP, restore op, branch",
        color="#60a5fa",
    )
    dbg.lane(
        "read-setup",
        [(9, 11), (16, 11), (16, 12), (17, 12)],
        kind="expected",
        expect="op=0: fetch N, derive B=+(N-addr), enter first tape pass",
        color="#60a5fa",
    )
    dbg.lane(
        "write-setup",
        [(9, 11), (9, 12), (15, 12), (15, 11), (16, 11)],
        kind="expected",
        expect="op=1: fetch N, derive B=-(N-addr), enter first tape pass",
        color="#fb923c",
    )
    dbg.lane(
        "read-target-access",
        [
            (21, 17),
            (21, 18),
            (24, 18),
            (24, 19),
            (21, 19),
            (21, 20),
            (20, 20),
            (20, 21),
        ],
        kind="expected",
        expect="folded bm/down/left-rS reaches P2 without the old east corridor",
        color="#a78bfa",
    )
    dbg.lane(
        "write-target-access",
        [(21, 16), (10, 16), (10, 19), (17, 19), (17, 20), (20, 20)],
        kind="expected",
        expect="receive new value nearby, append it, then consume old target",
        color="#fb923c",
    )
    dbg.lane(
        "second-tape-pass",
        [(20, 20), (20, 22), (17, 22), (17, 21), (20, 21), (20, 22)],
        kind="expected",
        expect="horizontal eight-tick loop passes exactly n-1-addr values",
        color="#14b8a6",
    )
    dbg.lane(
        "tape-forward-pipe",
        [
            (27, 15),
            (28, 15),
            (28, 17),
            (27, 17),
            (27, 19),
            (28, 19),
            (28, 21),
            (27, 21),
            (27, 23),
            (28, 23),
            (28, 29),
            (6, 29),
        ],
        kind="pipe",
        expect="43-slot worker-to-relay lane with two inward capacity notches",
        color="#34d399",
    )
    dbg.lane(
        "tape-return-pipe",
        [(6, 28), (27, 28), (27, 27), (6, 27), (6, 26), (19, 26), (19, 25)],
        kind="pipe",
        expect="58-slot relay-to-worker lane; total ring capacity is 101",
        color="#10b981",
    )
    return rows, dbg


def assemble_v2_fast_init_debug(n: int) -> tuple[list[str], DebugMap]:
    """Build v2 with the paired initializer and mark its two-value loop."""
    rows = build_v2_fast_init(n)
    _, dbg = assemble_v2_debug(n)
    dbg.title = f"memory tape v2 paired zero initialization n={n}"
    for i, region in enumerate(dbg.regions):
        if region.name == "init":
            dbg.regions[i] = type(region)(
                region.name,
                region.x,
                region.y,
                region.w,
                region.h,
                "fill the tape with two zero values per loop lap",
                region.color,
                region.tags,
            )
            break
    first_row = 1
    dbg.lane(
        "paired-zero-fill",
        [
            (24, 12 - first_row),
            (25, 12 - first_row),
            (25, 16 - first_row),
            (24, 16 - first_row),
            (24, 12 - first_row),
        ],
        kind="expected",
        expect="N=100 is even: emit two zero values per full lap, then exit east",
        color="#facc15",
    )
    return rows, dbg


def assemble_v2_paced_init_debug(n: int) -> tuple[list[str], DebugMap]:
    """Build the relay-paced paired initializer with matching sidecar geometry."""
    rows = build_v2_paced_init(n)
    _, dbg = assemble_v2_fast_init_debug(n)
    dbg.title = f"memory tape v2 relay-paced zero initialization n={n}"
    for i, region in enumerate(dbg.regions):
        if region.name == "init":
            dbg.regions[i] = type(region)(
                region.name,
                region.x,
                region.y,
                region.w,
                region.h,
                "fill two zeros per lap, spaced at the relay's six-tick throughput",
                region.color,
                region.tags,
            )
            break
    dbg.lanes = [lane for lane in dbg.lanes if lane.name != "paired-zero-fill"]
    dbg.lane(
        "relay-paced-zero-fill",
        [(24, 11), (25, 11), (25, 16), (24, 16), (24, 11)],
        kind="expected",
        expect="emit two zeros per lap with six ticks between sends",
        color="#facc15",
    )
    return rows, dbg


if __name__ == "__main__" and any(
    arg
    in (
        "--v2",
        "--v3",
        "--v3-external-init",
        "--v3-upstream-init",
        "--v3-one-shot-init",
    )
    or arg.startswith("--debug-")
    for arg in sys.argv[1:]
):
    numeric_args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    n = int(numeric_args[0]) if numeric_args else 100
    if "--v3-one-shot-init" in sys.argv[1:]:
        rows, debug = assemble_v3_one_shot_init_debug(n)
    elif "--v3-upstream-init" in sys.argv[1:]:
        rows, debug = assemble_v3_upstream_init_debug(n)
    elif "--v3-external-init" in sys.argv[1:]:
        rows, debug = assemble_v3_external_init_debug(n)
    elif "--v3" in sys.argv[1:]:
        rows, debug = assemble_v3_debug(n)
    else:
        rows, debug = assemble_v2_debug(n)
    for arg in sys.argv[1:]:
        if arg.startswith("--debug-json="):
            debug.write_json(arg.split("=", 1)[1])
        elif arg.startswith("--debug-html="):
            debug.write_html(rows, arg.split("=", 1)[1])
    print("\n".join(rows))
