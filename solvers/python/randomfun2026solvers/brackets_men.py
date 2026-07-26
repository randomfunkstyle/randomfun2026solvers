#!/usr/bin/env python3
"""`brackets` as three hand-folded rooms, with only the five pipes routed.

:mod:`brackets_stack` is the machine — a one-register base-3 stack, verified at
the op level against every string through length seven.  :mod:`brackets_place`
is the same machine laid out by :mod:`blockplace`, and it **loses**: the placer
routes every CFG edge as a corridor back to the successor's entry column, which
costs a room width per block transition.  Measured, that is 48x48 at 599 ticks
against the shipped 25x25's 1,096 — 1.38e6 against 6.85e5.

So the rooms are folded by hand and the router keeps only the job it is good at,
which is the five pipes.  Folding means the successor's glyphs simply follow the
branch: `X`'s **straight** lane is the next cell east, so `QPOP -> QZERO`,
`QDIV -> QQUOT` and `QEOS -> QBAL` cost nothing at all, and the branch glyph's
two turning lanes are the cells directly above and below it.

Three facts shape every room here:

* **`x` and `X` always turn, `d` only sometimes.**  A branch glyph therefore
  needs its two perpendicular neighbours free, and they *are* the lanes — so a
  branch may never sit against a wall on the side a lane leaves from.
* **A one-glyph block is cheaper to copy than to jump to.**  `PSEND` is a single
  `s` and `QFAIL` four glyphs; every lane that would have merged into them gets
  its own copy instead, which removes the merge problem the pop path had.
* **A turn glyph is not an op.**  A man may walk over `<` on his way between two
  ops, so two lanes may share a corridor as long as they both want the heading
  that corridor's arrowheads impose.

## The rooms

`CLASS` is 7x6, `WORK` 15x8 and `COUNT` 12x7, and the whole machine is 23x25 —
side 25, the same as the hand-built single-room parser it replaces, at 310
average reference ticks against 1,096: 1.94e5 against 6.85e5.

Only `COUNT` has a pipe-binding question to answer, because it alone sends on
two pipes; its `sw` and `so` attach cells sit at the two ends of the same wall,
so the split is the column midpoint and every `s` is checked against it.

## The bug that shipped, and the check that now catches it

The first version of this grid was wrong on any input that mixed bracket types
past four characters, and neither an exhaustive `FastLittleman` sweep nor a clean
`score_program` saw it.  The cause was not in the rooms: two cells of the routed
`tok` corridor turned south directly under `CLASS`'s south wall, and **an
arrowhead with a room wall behind it is the mouth of a pipe out of that room**.
The runtime therefore built seven pipes where `analyze` reported five, `CLASS`'s
four `s` glyphs bound three different queues by nearest column, and the worker
read the tokens out of order.

:func:`check_no_phantom_pipes` counts mouths the way the runtime does and refuses
any grid with more than the five that were drawn; :func:`route_pipe` will not lay
a corridor cell that would become one.  `tests/test_pipe_mouths.py` pins the
underlying engine divergence in nine lines.

## What stops it going smaller

The three rooms need 63 + 170 + 126 = 359 cells of walled rectangle between
them, so a side of 18 (324 cells) cannot hold the rooms at all, never mind the
band, and side 15 is out by more than a factor of two.  The register pressure
that forces three men is therefore also what fixes the footprint: 68 glyph cells
is a small machine, but three sets of walls and five pipe attachments are not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from randomfun2026solvers.brackets_place import DIRS, route_pipe
from randomfun2026solvers.circuit import Circuit, Collision
from randomfun2026solvers.plotter_block import pipe as draw_pipe
from randomfun2026solvers.value_ring import stamp, walls

if TYPE_CHECKING:  # pragma: no cover
    from randomfun2026solvers.man_debug import DebugMap

__all__ = ["CLASS", "COUNT", "WORK", "build", "build_grid", "debug_map"]


def _room(w: int, h: int, cells: dict[tuple[int, int], str]) -> Circuit:
    c = Circuit(w, h)
    for (x, y), glyph in cells.items():
        c.set(x, y, glyph)
    return c


# ── CLASS: the character in A, its bits in BP, the token out ──────────────────
#
#   PINIT  r s 5 M              B = 5 once, so the loop is only `r b } x`
#   PLOOP  r b } x              bit0 = 0 is `(`; everything else needs bit1
#   P1     ] x                  bit1 = 1 is `[`/`{`, bit1 = 0 is a closer
#   PNEG   N                    a closer sends -type
#   PSEND  s                    copied into all three lanes; it is one glyph
#
# The three lanes come back to the same `>` at (0,2), which is legal because all
# three want to leave it heading east.  `(` and the openers cost 12 ticks a lap,
# the closers 18 — they walk the long way round column 5.
CLASS_W, CLASS_H = 7, 6
CLASS = {
    (0, 0): "v", (4, 0): "<", (5, 0): "<", (6, 0): "<",
    (4, 1): "s",
    (0, 2): ">", (1, 2): "r", (2, 2): "b", (3, 2): "}", (4, 2): "x", (5, 2): "s",
    (4, 3): "]", (5, 3): "N",
    (0, 4): "^", (3, 4): "s", (4, 4): "x", (5, 4): "^",
    (0, 5): "@", (1, 5): "r", (2, 5): "s", (3, 5): "5", (4, 5): "M", (6, 5): "^",
}

# ── WORK: A takes the token, B is the stack, BP is never touched ──────────────
#
#   QLOOP  R X                  pos -> push (south), neg -> pop (north),
#                               zero -> the end sentinel, straight ahead
#   QPUSH  + + + s M            v = 3v + d, and the ack is the new stack word
#   QPOP   + X                  w = v - d; neg is a mismatch, zero empties it
#   QDIV   M 3 W / W X          w % 3 must be zero, and the quotient is v'
#   QQUOT  W s M                falls straight through from QDIV's `X`
#   QEOS   W X                  falls straight through from QLOOP's `X`
#
# The push lap is a ten-cell ring — `R X` on row 4, two `+` down column 2, the
# third `+` on row 7 after the corner, then `s M` back up column 0.  Putting the
# third `+` on the horizontal leg rather than the vertical one is what makes it
# ten cells and not twelve, and it is why the room is eight rows and not nine.
# The pop path is linear — eleven ops — so it alone pays a return along row 0.
WORK_W, WORK_H = 15, 8
WORK = {
    (0, 0): "v", (6, 0): "<", (14, 0): "<",
    (3, 1): ">", (4, 1): "1", (5, 1): "N", (7, 1): "s", (8, 1): "H",
    (9, 1): "@", (10, 1): "R", (11, 1): "s", (12, 1): "0", (13, 1): "M",
    (14, 1): "^",
    (2, 2): ">", (3, 2): "X", (4, 2): "s", (5, 2): "M", (6, 2): "^",
    (9, 2): ">", (10, 2): "1", (11, 2): "N", (12, 2): "s", (13, 2): "H",
    (2, 3): "+", (3, 3): ">", (4, 3): "M", (5, 3): "3", (6, 3): "W", (7, 3): "/",
    (8, 3): "W", (9, 3): "X", (10, 3): "W", (11, 3): "s", (12, 3): "M",
    (14, 3): "^",
    (0, 4): ">", (1, 4): "R", (2, 4): "X", (3, 4): "v",
    (9, 4): ">", (10, 4): "1", (11, 4): "N", (12, 4): "s", (13, 4): "H",
    (0, 5): "M", (2, 5): "+", (5, 5): ">", (6, 5): "1", (7, 5): "N",
    (8, 5): "s", (9, 5): "H",
    (0, 6): "s", (2, 6): "+", (3, 6): ">", (4, 6): "W", (5, 6): "X", (6, 6): "2",
    (7, 6): "N", (8, 6): "s", (9, 6): "H",
    (0, 7): "^", (1, 7): "+", (2, 7): "<", (5, 7): ">", (6, 7): "1", (7, 7): "N",
    (8, 7): "s", (9, 7): "H",
}

# ── COUNT: `remaining` in BP, the 1-based position in B ───────────────────────
#
#   CINIT  r b 0 M d            BP = n, B = 0, then the same test CTEST makes
#   CLOOP  r X                  a positive or zero ack counts, a negative ends
#   CINC   1 + M m              B = B + 1, BP = BP - 1
#   CTEST  d                    BP > 0 keeps counting, BP == 0 sends the sentinel
#   CEND   0 sw                 the sentinel, on the *west* pipe
#   CTERM  b x                  -1 -> count + 1, -2 -> 0
#
# `X`'s pos and zero lanes both count, so they are merged two cells later at the
# `>` on row 4, where both are already heading east.  The `sw` glyph stands in
# column 1 and the two `so` glyphs in column 6, either side of the midpoint of
# the two attach cells — see :func:`check_bindings`.
COUNT_W, COUNT_H = 12, 7
COUNT = {
    (3, 0): ">", (4, 0): "0", (6, 0): "s", (7, 0): "H",
    (2, 1): ">", (3, 1): "x",
    (2, 2): "b", (3, 2): ">", (4, 2): "1", (5, 2): "+", (6, 2): "s", (7, 2): "H",
    (0, 3): ">", (1, 3): "r", (2, 3): "X", (3, 3): "v",
    (5, 3): "@", (6, 3): "r", (7, 3): "b", (8, 3): "0", (9, 3): "M",
    (10, 3): "d", (11, 3): "v",
    (2, 4): ">", (3, 4): ">", (4, 4): "1", (5, 4): "+", (6, 4): "M", (7, 4): "m",
    (8, 4): "d", (9, 4): "v", (10, 4): "v",
    (0, 5): "^", (8, 5): "<", (10, 5): "<",
    (0, 6): "^", (1, 6): "s", (2, 6): "0", (9, 6): "<", (11, 6): "<",
}

ROOMS = {"CLASS": (CLASS_W, CLASS_H, CLASS),
         "WORK": (WORK_W, WORK_H, WORK),
         "COUNT": (COUNT_W, COUNT_H, COUNT)}

#: Where each pipe meets each room, in that room's interior coordinates:
#: (column, row, outward direction).  A negative row means the north wall, a row
#: of `h` the south wall; likewise -1 and `w` for the west and east walls.
ATTACH = {
    ("in", "CLASS"): (0, -1, "N"),
    ("tok", "CLASS"): (CLASS_W - 1, CLASS_H, "S"),
    ("tok", "WORK"): (0, -1, "N"),
    ("ack", "WORK"): (WORK_W - 1, -1, "N"),
    ("term", "WORK"): (WORK_W // 2, -1, "N"),
    # An *incoming* attach never enters the nearest-pipe split, which compares
    # outgoing attach cells only, so `ack` may share the south wall with them —
    # and it must, because the gap column between the two top rooms is one lane
    # wide and `term` already crosses it.
    ("ack", "COUNT"): (COUNT_W // 2, COUNT_H, "S"),
    # COUNT sends on two pipes, so these two must share a wall: the nearest-pipe
    # split is then the column midpoint and nothing else can move it.
    ("term", "COUNT"): (0, COUNT_H, "S"),
    ("out", "COUNT"): (COUNT_W - 1, COUNT_H, "S"),
}
WIRES = (("in", "I", "CLASS"), ("tok", "CLASS", "WORK"), ("ack", "WORK", "COUNT"),
         ("term", "COUNT", "WORK"), ("out", "COUNT", "O"))


def check_bindings() -> None:
    """Every `s` in `COUNT` must reach the pipe its block meant.

    Both attach cells stand on the north wall, so the Manhattan distance from a
    glyph at `(x, y)` is `x + y + 1` to `term` and `(w - 1 - x) + y + 1` to
    `out`: the `y` term is common and the split is the column midpoint.  Ties go
    to the attach cell that reads first, which is `term`, so a glyph exactly on
    the midpoint would bind `term` — the check therefore refuses it.
    """
    want = {(1, 6): "term", (6, 2): "out", (6, 0): "out"}
    for (x, y), glyph in COUNT.items():
        if glyph != "s":
            continue
        if (x, y) not in want:
            raise Collision(f"COUNT has an unclassified `s` at ({x},{y})")
        near = "term" if x <= (COUNT_W - 1) - x else "out"
        if near != want[(x, y)]:
            raise Collision(f"COUNT's `s` at ({x},{y}) binds {near}, "
                            f"wanted {want[(x, y)]}")
    if {c for c, g in COUNT.items() if g == "s"} != set(want):
        raise Collision("COUNT's `s` glyphs moved; re-derive the binding split")


ARROW = {">": (1, 0), "<": (-1, 0), "^": (0, -1), "v": (0, 1)}


def pipe_mouths(rows: list[str], inside: set) -> list[tuple[tuple[int, int], str]]:
    """Every cell the engine will read as the **start** of a pipe.

    A pipe is found by looking behind an arrowhead: if the cell a `v` points away
    from is a room border, that `v` is the mouth of a pipe leaving that room.
    Nothing says the author meant it — a corridor that turns south against the
    underside of a wall mints a pipe just as surely as one drawn on purpose, the
    grid loads, and `lm.mjs analyze` still reports the pipes that were drawn.

    That is the bug that shipped: two corridor cells sat under `CLASS`'s south
    wall, so the room had **three** outgoing pipes, its four `s` glyphs split
    across them by nearest column, and the worker read the tokens out of order.
    `analyze` said five pipes; the runtime made seven.
    """
    grid = {(x, y): ch for y, r in enumerate(rows) for x, ch in enumerate(r)}
    walls, cells = inside
    return [(c, ch) for c, ch in grid.items()
            if ch in ARROW and c not in cells
            and (c[0] - ARROW[ch][0], c[1] - ARROW[ch][1]) in walls]


def wall_cells(boxes) -> tuple[set, set]:
    """(every room's border, every room's interior), from the boxes themselves.

    Derived from the floor plan rather than from the glyphs, because `|` and `-`
    are a *pipe's* body as well as a room's wall and there is no telling them
    apart in the finished grid — which is exactly the confusion that lets a
    phantom pipe hide.
    """
    borders, cells = set(), set()
    for bx, by, bw, bh in boxes:
        borders |= {(x, by) for x in range(bx, bx + bw)}
        borders |= {(x, by + bh - 1) for x in range(bx, bx + bw)}
        borders |= {(bx, y) for y in range(by, by + bh)}
        borders |= {(bx + bw - 1, y) for y in range(by, by + bh)}
        cells |= {(x, y) for x in range(bx + 1, bx + bw - 1)
                  for y in range(by + 1, by + bh - 1)}
    return borders, cells


def check_no_phantom_pipes(rows: list[str], boxes, expect: int = 5) -> None:
    mouths = pipe_mouths(rows, wall_cells(boxes))
    if len(mouths) != expect:
        raise Collision(
            f"{len(mouths)} pipe mouths, wanted {expect}: "
            + ", ".join(f"{ch!r}@{c}" for c, ch in sorted(mouths)))


# ── the floor plan ────────────────────────────────────────────────────────────
#
#     +---+  +--CLASS--+  +--COUNT--+          rows 0..TOP: the input room
#            |         |  |         |          and whatever the router needs
#            +---------+  +---------+          <- south walls aligned at SB
#     ............ the band, MID rows ......   all five pipes and the output
#     +---------------WORK---------------+     <- north wall below the band
#
# Both stacks put every attach cell on a wall that faces a band, which is the
# whole of the plan: a pipe that has to reach round a room is a pipe that does
# not fit.  `CLASS` and `COUNT` are **bottom**-aligned so the band below them is
# one rectangle, and the taller of the two decides where the band starts.
TOP, MID, MARGIN, GAPX, IX = 5, 2, 0, 0, 0


def _origins():
    """Interior origins, and the grid the plan needs."""
    sb = TOP + max(CLASS_H, COUNT_H)          # the shared south wall row
    xc = MARGIN + 1
    xn = xc + CLASS_W + 1 + GAPX + 1
    at = {"CLASS": (xc, sb - CLASS_H), "COUNT": (xn, sb - COUNT_H)}
    top_w = xn + COUNT_W + 1 + MARGIN
    at["WORK"] = (MARGIN + 1, sb + MID + 2)
    width = max(top_w, MARGIN + 1 + WORK_W + 1 + MARGIN)
    height = at["WORK"][1] + WORK_H + 1 + MARGIN
    return at, width, height, sb


def build_grid(seed: int = 0):
    """The whole machine: three folded rooms, five routed pipes, I and O."""
    from randomfun2026solvers.man_debug import DebugMap

    check_bindings()
    at, width, height, sb = _origins()
    # The output room stands east of `WORK`, entered from the north, which is
    # the only free rectangle big enough that no pipe has to reach round a room.
    # The input room stands over `CLASS`'s east end, not over the column its pipe
    # attaches to: the pipe then runs west along the one free row above the room
    # and turns south into the wall, which costs a row of band instead of the
    # three a room standing directly over the attach column would need.
    io = {"I": (at["CLASS"][0] + IX, 1), "O": (width - 2, at["WORK"][1] + 2)}
    boxes = {n: (at[n][0] - 1, at[n][1] - 1, ROOMS[n][0] + 2, ROOMS[n][1] + 2)
             for n in ROOMS}
    boxes.update({n: (x - 1, y - 1, 3, 3) for n, (x, y) in io.items()})
    blocked = {(x, y) for bx, by, bw, bh in boxes.values()
               for x in range(bx, bx + bw) for y in range(by, by + bh)}
    # Every wall cell of every room.  A pipe cell whose arrowhead has one of
    # these behind it is read as a *new* pipe leaving that room, which is how a
    # corridor that merely turns under a wall silently splits a room's sends
    # across two queues.
    borders, _cells = wall_cells(boxes.values())

    ends = {}
    for (key, man), (c, r, face) in ATTACH.items():
        ox, oy = at[man]
        ends[(key, man)] = ((ox + c, oy + r), DIRS[face])
    ends[("in", "I")] = ((io["I"][0], io["I"][1] + 1), DIRS["S"])
    ends[("out", "O")] = ((io["O"][0], io["O"][1] - 1), DIRS["N"])

    import random
    rng = random.Random(seed)
    order = list(WIRES)
    for _ in range(64):
        taken, paths = set(blocked), {}
        for key, src, dst in order:
            (sa, sd), (da, dd) = ends[(key, src)], ends[(key, dst)]
            path = route_pipe(taken, width, height, sa, sd, da, (-dd[0], -dd[1]),
                              borders)
            if path is None:
                break
            paths[key] = (path, da)
            taken |= set(path)
        else:
            break
        rng.shuffle(order)
    else:
        raise Collision("the five pipes do not fit this floor plan")

    g = Circuit(width, height)
    for name, (w, h, cells) in ROOMS.items():
        ox, oy = at[name]
        walls(g, ox, oy, w, h)
        for (x, y), glyph in cells.items():
            g.set(ox + x, oy + y, glyph)
    for name, (x, y) in io.items():
        stamp(g, x - 1, y - 1, ["+-+", f"|{name}|", "+-+"])
    caps = {k: draw_pipe(g, p, into=into) for k, (p, into) in paths.items()}

    rows = [r.rstrip() for r in g.rows()]
    while rows and not rows[-1]:
        rows.pop()
    check_no_phantom_pipes(rows, boxes.values())

    dbg = DebugMap("brackets — three men over a one-register base-3 stack")
    notes = {
        "CLASS": "c >> 5 is the type; closer iff (bit0, bit1) = (1, 0)",
        "WORK": "B is the stack, v = 3v + d; pop is one sign test and one remainder",
        "COUNT": "remaining in BP, position in B; the countdown sends the sentinel",
    }
    for name in ROOMS:
        bx, by, bw, bh = boxes[name]
        dbg.region(f"room:{name}", bx, by, bw, bh, color="#f59e0b",
                   note=notes[name], tags=["compute"])
    for key, (path, _into) in paths.items():
        dbg.lane(f"pipe:{key}", path, kind="pipe",
                 note=f"{caps[key]} cells of capacity")
    for name in io:
        bx, by, bw, bh = boxes[name]
        dbg.region(name, bx, by, bw, bh, color="#64748b")

    info = {"grid": (max(len(r) for r in rows), len(rows)),
            "rooms": {n: (ROOMS[n][0], ROOMS[n][1]) for n in ROOMS},
            "pipe_capacity": caps, "origins": at}
    return rows, dbg, info


def build() -> list[str]:
    return build_grid()[0]


def debug_map() -> DebugMap:
    return build_grid()[1]


if __name__ == "__main__":  # pragma: no cover - the generator's CLI
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", "--out", dest="man", type=Path)
    ap.add_argument("--html", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    grid, dbg, meta = build_grid()
    for path in (args.man, args.html, args.json):
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
    if args.man:
        args.man.write_text("\n".join(grid) + "\n", encoding="utf-8")
    if args.html:
        dbg.write_html(grid, args.html)
    if args.json:
        dbg.write_json(args.json)
    if not (args.man or args.html or args.json):
        print("\n".join(grid))
    else:
        print(meta)
