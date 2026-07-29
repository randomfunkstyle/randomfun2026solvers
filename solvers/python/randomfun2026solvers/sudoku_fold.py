#!/usr/bin/env python3
"""A folded, phase-tracking `sudoku-validity` machine.

Same state design as :mod:`sudoku_ring` -- nine 27-bit words indexed by the
*value*, so a round touches exactly one word -- but two things change.

**One rotation loop instead of two.**  ``sudoku_ring`` rotates ``v-1`` slots to
reach ``WORD[v]``, accesses it, then rotates ``9-v`` back to phase 0: a fixed
nine pops a round *and a second loop block*.  Here the phase is tracked instead.
The ring is left wherever the access left it and the next round rotates
``(v' - 1 - v) mod 9`` -- uniform on 0..8, so five pops a round on average, and
ROT2 disappears from the grid entirely.  Carrying the phase costs one word in
the scratch FIFO, which is free: ``rq M L8 - M`` at the head of the round turns
last round's ``v`` into ``K = 8 - v`` in **B**, where it survives the three
``ri``/``sq`` pairs untouched.

**The grid is folded, not banded.**  ``sudoku_ring``'s room is 25x18 because its
pipe columns are spread over the whole width, so every IO/FILE alternation costs
a row.  Here the six pipe columns are chosen so the receive bands and the send
bands *overlap*: with incoming at ``7/5/11`` and outgoing at ``3/1/13``, ``ri``
lives at 7-9, ``rq`` at 0-6 and ``sq`` at 3-8, so the round's ``rq``/``sq``
chatter never leaves the middle of the room.  The ring ops are pushed to the
east (``rr``/``sr`` at 10-14), which frees the whole east column strip for the
rotation loop, its exit corridor and ACCESS -- none of which then costs a row.

Rows, top to bottom:

    0        ROUND's last row (walked east) *and* the rotation loop's entry lane
    1-2      the counted ring, columns 10-14
    3..R-1   ROUND, columns 0-14, with column 14 kept clear as the loop's exit
    R        ACCESS walked west from column 14, then OK on the same row
    R+1      BAD off ACCESS's `a` (west) and INIT's ring fill (east)
    R+2      INIT's return, west to column 0 and north into ROUND

ROUND is written *upside down* -- generated entering at the top and then
mirrored -- so that its entry lands at the bottom-left where OK's return and
INIT's return both arrive, and its exit lands at the top-right next to the
rotation loop.  That is what removes the return corridor: it is one cell.

**This machine is walking-bound with no stall left.**  Sampled every tick over
the 81-round case (``littleman/tools/heatmap.mjs``), the worker is *0% stalled*
and its occupancy is flat across 172 cells; the two relay men stall 55% and 88%
of the time, which is free because they are waiting on the worker rather than
the other way round.  So neither ring capacity nor pipe latency is on the
critical path, and the only remaining lever is cells walked a round: ~146, of
which 96 are glyphs.  See :data:`REJECTED` for the two redesigns that were
priced against that and lost.
"""

from __future__ import annotations

from randomfun2026solvers.circuit import Circuit, Collision
from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

__all__ = ["INC", "OG", "build", "build_grid", "worker"]

# ── the room ─────────────────────────────────────────────────────────────────
IW = 15                       # interior width
RES = 10                      # first column of the ring strip (5 wide: 10..14)

#: Interior columns of the three incoming pipes: input, file, ring.
INC = (7, 5, 11)
#: Interior columns of the three outgoing pipes: file, output, ring.
OG = (3, 1, 13)

F = None
#: ``(glyph, band)`` for the round.  ``i`` = ri, ``q`` = rq, ``Q`` = sq.
ROUND_OPS: list[tuple[str, str | None]] = [
    ("r", "q"), ("M", F), ("8", F), ("-", F), ("M", F),      # B = K = 8 - v_prev
    ("r", "i"), ("s", "Q"),                                  # A=r  F=[r]
    ("r", "i"), ("s", "Q"),                                  # A=c  F=[r,c]
    ("r", "i"), ("s", "Q"),                                  # A=v  F=[r,c,v]
    ("+", F), ("M", F), ("9", F), ("W", F), ("%", F),         # A = (K+v) % 9
    ("b", F),                                                # BP = rotation count
    ("r", "q"), ("M", F), ("3", F), ("W", F), ("s", "Q"), ("/", F),
    ("M", F), ("6", F), ("+", F), ("M", F), ("3", F), ("*", F), ("s", "Q"),
    ("r", "q"), ("M", F), ("3", F), ("W", F), ("s", "Q"), ("/", F), ("M", F),
    ("r", "q"), ("s", "Q"),                                  # recycle v
    ("r", "q"), ("s", "Q"),                                  # recycle r
    ("r", "q"), ("+", F), ("M", F), ("1", F), ("{", F), ("s", "Q"),   # BB
    ("r", "q"), ("M", F), ("9", F), ("+", F), ("M", F), ("1", F), ("{", F),
    ("s", "Q"),                                              # BC
    ("r", "q"), ("s", "Q"),                                  # recycle v
    ("r", "q"), ("M", F), ("1", F), ("{", F), ("M", F),      # BR
    ("r", "q"), ("+", F), ("M", F),
    ("r", "q"), ("+", F), ("M", F),                          # B = P
]


def _nearest(cols: tuple[int, ...], x: int) -> int:
    """Index of the pipe an op at interior column ``x`` binds to."""
    return min(range(len(cols)), key=lambda k: (abs(x - cols[k]), cols[k]))


def bands() -> dict[str, set[int]]:
    """Column sets each pipe op may stand in, from :data:`INC` / :data:`OG`."""
    b = {}
    for k, t in enumerate("iqr"):
        b[t] = {x for x in range(IW) if _nearest(INC, x) == k}
    for k, t in enumerate("QoR"):
        b[t] = {x for x in range(IW) if _nearest(OG, x) == k}
    return b


def _serpentine(ops, widths, band, x0=1):
    """Lay ``ops`` on a serpentine whose row ``y`` may use columns 0..widths[y]-1."""
    cell: dict[tuple[int, int], str] = {}
    x, y, d = x0, 0, 1
    H = len(widths)

    def turn():
        nonlocal x, y, d
        if y + 1 >= H:
            raise Collision("serpentine ran out of rows")
        cell[(x, y)] = "v"
        y += 1
        d = -d
        if x >= widths[y]:
            raise Collision("serpentine turned into a narrower row")
        cell[(x, y)] = ">" if d > 0 else "<"
        x += d

    for g, tag in ops:
        for _ in range(400):
            w = widths[y]
            ok = band[tag] if tag else set(range(w))
            if 0 <= x < w and x in ok and 0 <= x + d < w:
                break
            ahead = [xx for xx in (range(x, w - 1) if d > 0 else range(1, x + 1))
                     if xx in ok and xx < w]
            if ahead and 0 <= x < w:
                cell.setdefault((x, y), " ")
                x += d
                continue
            turn()
        else:  # pragma: no cover - the guard above always fires first
            raise Collision("serpentine did not settle")
        cell[(x, y)] = g
        x += d
    return cell, x, y, d


def round_rows() -> tuple[dict[tuple[int, int], str], int, int]:
    """Place ROUND; return its cells (already mirrored), its height and exit column."""
    b = bands()
    for R in range(6, 14):
        widths = [IW - 1] * (R - 3) + [RES] * 3
        try:
            cell, ex, ey, ed = _serpentine(ROUND_OPS, widths, b)
        except Collision:
            continue
        if ey != R - 1 or ed != 1:
            continue
        flip = {}
        for (x, y), g in cell.items():
            flip[(x, R - 1 - y)] = {"v": "^", "^": "v"}.get(g, g)
        return flip, R, ex
    raise Collision("no ROUND placement found")


def worker() -> tuple[Circuit, int]:
    """The single worker room, and the number of rows ROUND occupies."""
    cells, R, ex = round_rows()
    ih = R + 3
    c = Circuit(IW, ih)
    for (x, y), g in cells.items():
        c.set(x, y, g)
    c.set(0, R - 1, ">")                     # ROUND's entry, walked north into

    # ── the rotation loop: entry lane on row 0, the ring on rows 1-2 ─────────
    c.horizontal(0, ex, RES)                 # ROUND's exit runs east into the lane
    exits = c.counted_ring_horizontal(RES, 1, "rs")
    assert exits == [(RES + 4, 3), (RES, 0)], exits
    c.set(RES, 0, ">")                       # the odd-tail lane, back to the entry
    c.horizontal(0, RES, RES + 4)
    c.set(RES + 4, 0, "v")
    c.vertical(RES + 4, 2, R)                # the loop's exit corridor, down to ACCESS

    b = bands()
    for x, y in ((RES + 1, 1), (RES + 3, 2)):
        if x not in b["r"]:
            raise Collision(f"ring receive at {x} binds elsewhere")
    for x, y in ((RES + 2, 1), (RES + 2, 2)):
        if x not in b["R"]:
            raise Collision(f"ring send at {x} binds elsewhere")

    # ── ACCESS (row R, walked west) then OK on the same row ─────────────────
    c.set(IW - 1, R, "<")
    for i, g in enumerate("r+s&-Nba"):
        c.set(IW - 2 - i, R, g)
    if (IW - 2) not in b["r"] or (IW - 4) not in b["R"]:
        raise Collision("ACCESS pipe ops bind elsewhere")
    br = IW - 9                              # the `a` column
    c.set(br - 1, R, "1")
    c.horizontal(R, br - 1, 2)
    c.set(2, R, "s")                         # emit 1
    if 2 not in b["o"]:
        raise Collision("OK's send binds elsewhere")
    c.set(1, R, " ")
    c.set(0, R, "^")                         # up into ROUND's entry

    # ── BAD (row R+1): `a` turns counter-clockwise, i.e. south, on a dupe ────
    c.set(br, R + 1, "<")
    c.horizontal(R + 1, br, 4)
    c.set(4, R + 1, "0")
    c.set(3, R + 1, " ")
    c.set(2, R + 1, "s")
    c.set(1, R + 1, "H")

    # ── INIT: nine empty words, then the phase seed ─────────────────────────
    # A stays 0 the whole way, so the nine `sr` and the seed `sq` share it, and
    # the order between them does not matter -- which is what lets INIT start on
    # BAD's own row, east of BAD's westbound lane, and cost no row of its own.
    # The walk ends heading north up column 0 and merges into OK's return.
    c.set(br + 1, R + 1, "@")
    c.set(br + 2, R + 1, "0")
    sends = 0
    for x in range(9, IW - 1):
        c.set(x, R + 1, "s")
        sends += 1
    c.set(IW - 1, R + 1, "v")
    c.set(IW - 1, R + 2, "<")
    for x in range(IW - 2, IW - 2 - (9 - sends), -1):
        c.set(x, R + 2, "s")
    left = IW - 2 - (9 - sends)
    c.horizontal(R + 2, left, 3)
    c.set(3, R + 2, "s")                     # F = [0]  ->  K = 8 next round
    if 3 not in b["Q"]:
        raise Collision("INIT's seed send binds elsewhere")
    c.horizontal(R + 2, 3, 0)
    c.set(0, R + 2, "^")
    for x in list(range(9, IW - 1)) + list(range(IW - 2, IW - 2 - (9 - sends), -1)):
        if x not in b["R"]:
            raise Collision(f"INIT send at {x} binds elsewhere")
    return c, R


# ── the whole machine ────────────────────────────────────────────────────────
BAND = 6                      # rows above the worker: rooms (0-3) and pipes (4-5)
GW = 20                       # grid width; the band, not the worker, sets it
RING_RELAY_W = 6              # ring turnaround interior; 8 wide outer
RELAY_W, RELAY_H = 4, 2       # relay interiors; 6x4 outer, one word a lap


def relay2(w: int) -> list[str]:
    """A ``w``x2-interior turnaround room: spawn outside a perimeter of 2(w-1)."""
    top = ["@"] + [">"] + ["r", "s"] * ((w - 2) // 2)
    top = top[:w - 1] + ["v"]
    bot = [" ", "^"] + ["s", "r"] * ((w - 2) // 2)
    bot = bot[:w - 1] + ["<"]
    return ["+" + "-" * w + "+", "|" + "".join(top) + "|",
            "|" + "".join(bot) + "|", "+" + "-" * w + "+"]


def build() -> list[str]:
    """Worker room, four aux rooms and six pipes.

    Row 4 carries every horizontal run that flows *up* into a room and row 5
    every run that flows *down* into the worker, so the two directions never
    have to cross.  The ring's forward pipe is deliberately walked six cells
    east along row 4: nine words have to be resident somewhere, and an
    under-capacity ring deadlocks silently.
    """
    w, R = worker()
    ih = R + 3
    g = Circuit(GW, BAND + ih + 2)
    stamp(g, 1, BAND + 1, w.rows())
    walls(g, 1, BAND + 1, IW, ih)
    top = BAND                                        # the worker's top wall row

    col = {"output": OG[1] + 1, "file_out": OG[0] + 1, "file_in": INC[1] + 1,
           "input": INC[0] + 1, "ring_in": INC[2] + 1, "ring_out": OG[2] + 1}

    stamp(g, 0, 0, ["+-+", "|O|", "+-+"])             # cols 0-2
    stamp(g, 3, 0, relay2(4))                         # file relay, cols 3-8
    stamp(g, 9, 0, ["+-+", "|I|", "+-+"])             # cols 9-11
    stamp(g, 12, 0, relay2(RING_RELAY_W))             # ring relay, cols 12-19

    # output: up column 2, west one, up into O's south wall at (1,2)
    draw_pipe(g, [(col["output"], top - 1), (col["output"], top - 2),
                  (1, top - 2), (1, 3)])
    # file ring: 2 + 2 cells, exactly FILE_WORDS + 1 -- latency is the cost here
    draw_pipe(g, [(col["file_out"], top - 1), (col["file_out"], top - 2)])
    draw_pipe(g, [(col["file_in"], top - 2), (col["file_in"], top - 1)])
    # input: I's south wall, down column 10, then west along row 5.  It bends on
    # row 5 rather than row 4 on purpose: an arrowhead on row 4 sits directly
    # under a relay's wall and *is* a second pipe mouth even when `analyze`
    # folds it into this pipe.
    draw_pipe(g, [(10, 3), (10, top - 1), (col["input"], top - 1)])
    g.cell[(col["input"], top - 1)] = "v"
    # ring: the forward pipe runs east along row 4 so the nine words have
    # somewhere to sit.  Its last cell is a bend *and* the terminal arrowhead --
    # `draw_pipe` writes the arrival direction there, so it is re-pointed north
    # into the relay's south wall by hand.
    e = 12 + RING_RELAY_W
    r_fwd = draw_pipe(g, [(col["ring_out"], top - 1), (col["ring_out"], top - 2),
                          (e, top - 2)])
    g.cell[(e, top - 2)] = "^"
    r_ret = draw_pipe(g, [(13, top - 2), (13, top - 1), (col["ring_in"], top - 1)])
    g.cell[(col["ring_in"], top - 1)] = "v"
    # Slots = forward cells + return cells + the one word the relay man carries.
    # Nine words need a tenth slot or the worker's `sr` can never find room.
    if r_fwd + r_ret + 1 < 10:
        raise Collision(f"ring holds {r_fwd + r_ret + 1}, needs 10")
    return [r.rstrip() for r in g.rows()]


def build_grid():
    rows = build()
    return rows, None, {"grid": (max(len(r) for r in rows), len(rows))}


# ═══════════════════════════════════════════════════════ priced and rejected ═
#
# Both alternatives below are *correct* -- the packed one was run at the op
# level over all six public cases and agreed on every verdict.  They are
# rejected on cost, and the cost is recorded here because both look like
# obvious wins from the outside and neither one is.
#
# A. TWO VALUES A WORD.  The store is 9 words of 27 bits = 243 bits and a word
#    is 64, so two values fit in one (halves at bits 0-26 and 32-58, with 27-31
#    a guard gap nothing ever writes, so a carry out of bit 26 dies in the gap
#    instead of reaching the other value's row field).  Five slots instead of
#    nine, and a delta-rotation over five averages two pops against four: 8 op
#    ticks a round saved.  It costs 21 glyphs -- `M L2 W /` to split v into
#    word and half, a mod-5 delta instead of mod-9, one more live value cycling
#    through the scratch FIFO, and the dance that lifts P into its half at the
#    end (park P, recycle the word index, pop the half, `M L5 W {`, pop P,
#    `{`).  Net **+13 op ticks a round**, and the same serpentine search that
#    sizes ROUND here puts the bigger round at **13 rows against 9** -- side 24
#    against 20.  Both axes move the wrong way.
#
#    It pays on `little-little-little-man` and not here because of where the
#    cost sits.  There, packing removed *lapping*: 184 word-moves a tick became
#    6.6.  Here phase tracking already cut the ring to ~17 of 96 op ticks a
#    round -- 18% -- while ROUND's arithmetic is 71%.  Packing buys down the
#    cheap term with the expensive one.
#
# B. ONE ROOM PER VALUE, systolic.  Nine PE rooms, each holding its own 27-bit
#    word, operands hopping between neighbours.  The smallest room whose
#    perimeter walk can carry an `r` and an `s` is 6x4 = 24 cells, so nine are
#    216 cells before a single compute glyph -- in a grid that is 400 cells
#    whole -- and each needs pipes on two sides, so they cannot abut.  The hops
#    do not pay for it either: routing a cell to PE `v` is data-dependent, so
#    it walks the chain to its own room, averaging 4.5 hops at ~4 ticks a hop
#    against the ~20 ticks the rotation it replaces already costs.  Dominated
#    on area, level on ticks.
REJECTED: dict[str, dict[str, object]] = {
    "A: two values a word, 5-slot ring": {
        "round_glyphs": 89,          # against 68
        "op_ticks_per_round": 108,   # against 95
        "round_rows": 13,            # against 9, same serpentine search
        "side": 24,                  # against 20
        "correct": True,
    },
    "B: one room per value, operands hopping": {
        "rooms": 9,
        "room_cells_each": 24,
        "cells_before_any_glyph": 216,
        "mean_hops_to_the_right_room": 4.5,
        "correct": True,
    },
    # The relays look like they are on the wrong loops: the scratch FIFO carries
    # 22 words a round through `relay2(4)` (6-cell lap, one r/s pair, 6.00
    # ticks/word) while the ring carries ~5 through `relay2(6)` (10-cell lap,
    # three pairs, 3.33).  Both halves of the trade fail.
    "C: widen the scratch relay to relay2(6)": {
        # The ring's return pipe must leave the relay's south wall *west* of the
        # column its forward pipe climbs (OG[2]+1 = 14), or the two cross on the
        # only two pipe rows.  So the ring relay's interior must reach column 13
        # and its outer edge starts at <= 12; O (3 cols), the scratch relay and I
        # (3 cols) all fit west of that, which caps the scratch relay at 6 outer
        # columns -- independent of GW, so widening the grid does not buy it.
        "scratch_relay_outer_columns_max": 6,
        "at_8_wide": "analyze reports both ring pipes src=-1; run dies no-pipe on tick 4",
        "correct": False,
    },
    "D: 4x3 scratch relay (8-cell lap, 2 pairs, 4.00 t/w) in the same 6 columns": {
        # Buys the throughput without the geometry, at one extra band row.  But
        # the worker is 0% stalled, so relay throughput is not on the critical
        # path at all: the whole 6.00 -> 4.00 move is worth ONE tick.
        "avg_ticks": 6936.83,        # against 6937.83
        "area2": 441,                # against 400, from BAND 6 -> 7
        "score": 3059143,            # against 2775133 -- 10% worse
        "correct": True,
    },
}


if __name__ == "__main__":  # pragma: no cover
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", type=Path)
    args = ap.parse_args()
    rows = build()
    if args.man:
        args.man.write_text("\n".join(rows) + "\n")
    print("\n".join(rows))
    print(len(rows), max(len(r) for r in rows))
