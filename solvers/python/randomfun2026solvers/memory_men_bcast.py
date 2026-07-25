#!/usr/bin/env python3
"""Broadcast-addressed man-memory: no walk at all, ``S`` out and ``R`` back.

Every earlier variant paid for *routing*: a man walked to lane ``addr`` at 10-14
ticks a lane, and the storage itself was nearly free. The two teleport glyphs
delete that walk:

* ``S`` writes ``A`` into **every** outgoing pipe at once — one-to-many, one glyph;
* ``R`` reads from **any** incoming pipe — many-to-one, one glyph, no addressing.

So a request is *shouted* at all ``n`` cells and each decides for itself, and the
answer teleports back to a collector. Cost stops depending on the address.

**Why one-hot.** A cell's value lives in ``B``, and every comparison glyph (``-``,
``~``, ``%``, ``{``, ...) reads ``B``. A cell holding a value therefore cannot
compare a broadcast address against its own index — there is no spare hand. But the
router can build ``1 << addr`` in a single glyph (``{``), and then cell ``j`` tests
bit ``j`` with ``b`` ``]``*j ``x``: all three touch only ``BP``, so ``B`` is never
disturbed. ``x`` always turns, so it is a clean two-way branch.

**The cell, and why it needs only one test per lane.** ``BP`` still holds
``onehot >> j`` after the op word arrives, because ``r``/``X``/``W``/``M``/``s``
leave ``BP`` alone. So the cell does not branch on "mine" first — it reads the op,
branches on *that*, and each lane re-tests ``x`` to decide whether to act:

    READ   x -> mine: `W M s` (send the value, restore B) | not mine: nothing
    WRITE  r(value), then x -> mine: `M` (store) | not mine: nothing

Every cell consumes every broadcast word, which is what keeps ``S`` from wedging:
it blocks until all sixteen pipes have a free source cell, so the slowest cell sets
the pace — order ``n`` ticks per access, against a walk's ``10 * addr``.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .circuit import Circuit
from .man_debug import DebugMap
from .memory_men import (
    C_ANS,
    C_CMD,
    C_COLL,
    C_IO,
    C_MID,
    C_STORE,
    _io_room,
    _room,
    collector_rows,
    draw_pipe,
)

__all__ = ["TILE_ROWS", "BAND", "tile_cols", "field_rows", "ROUTER_ROWS"]

#: Interior rows one cell occupies: return corridor, main line, READ tail, WRITE
#: lane, WRITE store tail.
TILE_ROWS = 5

#: Rows between one cell's main line and the next. Equal to ``TILE_ROWS``: the
#: bands touch, and the only cell that could stray between them is the spawner,
#: which keeps to columns 0-1.
BAND = TILE_ROWS

#: Interior column the tiles start at. Columns 0 and 1 belong to the spawner: a
#: copy born west of a ``Y`` entered heading south lands in column 0, and a birth
#: into a wall is fatal, so the ``Y`` may not sit in column 0.
TILE_X0 = 2


def tile_cols(j: int) -> dict[str, int]:
    """Column landmarks for cell ``j``'s tile, all relative to the room interior."""
    x0 = TILE_X0
    xx = x0 + 4 + j  # the `X` that branches on the op word
    return {"x0": x0, "shift0": x0 + 3, "op": x0 + 3 + j, "X": xx, "width": xx + 9}


#: The router: turn the problem's stream into two or three broadcast words.
#:
#: ``r`` the op and branch on it, then in each lane ``r`` the address, build
#: ``1 << addr`` with ``M 1 {``, and ``S`` it. A READ then broadcasts ``0``; a WRITE
#: broadcasts ``1`` and the value. The router must own **no other outgoing pipe** —
#: ``S`` would broadcast into it too — which is why the answer path goes through a
#: separate collector room rather than back through here.
ROUTER_ROWS: tuple[str, ...] = (
    ">rXrM1{S0S   v",
    "  >rM1{S1SrSv ",
    "              ",
    "^    @      <<",
)

#: Where the input pipe must arrive (interior row of the ``r`` that reads ``op``).
ROUTER_IN_ROW = 0


def field_rows(n: int) -> tuple[list[str], list[int]]:
    """The one room holding all ``n`` cells; returns rows and each cell's main row.

    Cell ``j`` gets a five-row band. Its shift chain is ``j`` long, so bands are
    ragged in width and the room is as wide as cell ``n-1`` needs.

    The spawner walks **south** down columns 0-1. Entering ``Y`` heading south puts
    one copy east (facing east) and one west (facing west), so the east copy lands
    on its tile's ``>`` and turns straight into the loop, while the west copy drops
    into column 0, turns south, and comes back to column 1 in time for the next
    band's ``Y``. That is why the ``Y`` sits in column 1 and not column 0: a birth
    into a wall is fatal.
    """
    if n < 1:
        raise ValueError("a field needs at least one cell")
    # one extra row on top: the spawner has to enter the first `Y` heading *south*,
    # and a man spawns facing east, so he needs a cell to turn in first.
    ih = BAND * n + 1
    iw = tile_cols(n - 1)["width"] + 1
    rows = [[" "] * iw for _ in range(ih)]

    def put(x: int, y: int, glyph: str) -> None:
        if rows[y][x] not in (" ", glyph):
            raise ValueError(f"field collision at ({x},{y}): {rows[y][x]!r} vs {glyph!r}")
        rows[y][x] = glyph

    rows[0][0] = "@"
    rows[0][1] = "v"
    main_rows: list[int] = []
    for j in range(n):
        top = 1 + BAND * j
        r_ret, r_main, r_read, r_write, r_store = (top, top + 1, top + 2, top + 3, top + 4)
        main_rows.append(r_main)
        c = tile_cols(j)
        x0, xx = c["x0"], c["X"]

        # the spawner: `Y` on this band's main row, then south again for the next
        put(1, r_main, "Y")
        put(0, r_main, "v")  # the west copy turns south...
        if j + 1 < n:
            put(0, r_store, ">")  # ...and comes back east...
            put(1, r_store, "v")  # ...heading south into the next band's `Y`
        else:
            put(0, r_store, "H")  # the last copy has no band left to seed

        # main line: read the one-hot, move it to BP, shift j, read the op, branch
        put(x0, r_main, ">")
        put(x0 + 1, r_main, "r")
        put(x0 + 2, r_main, "b")
        for i in range(j):
            put(x0 + 3 + i, r_main, "]")
        put(c["op"], r_main, "r")
        put(xx, r_main, "X")
        put(xx + 1, r_main, "x")  # READ walks straight on into its own `x`

        # READ, selected: bring the value to A, restore B, send it
        put(xx + 1, r_read, ">")
        put(xx + 2, r_read, "W")
        put(xx + 3, r_read, "M")
        put(xx + 4, r_read, "s")
        put(xx + 5, r_read, "^")

        # WRITE: `X` turned clockwise, so drop to the write lane and take the value
        put(xx, r_read, "v")
        put(xx, r_write, ">")
        put(xx + 1, r_write, "r")
        put(xx + 6, r_write, "x")

        # WRITE, selected: `M` stores the new value into B
        put(xx + 6, r_store, ">")
        put(xx + 7, r_store, "M")
        put(xx + 8, r_store, "^")

        # the return corridor: every path climbs into it and runs west
        put(x0, r_ret, "v")
        for col in (xx + 1, xx + 5, xx + 6, xx + 8):
            put(col, r_ret, "<")
    return ["".join(r) for r in rows], main_rows


@dataclass(frozen=True)
class Bcast:
    """A complete broadcast-addressed ``memory`` program."""

    n: int
    rows: tuple[str, ...]
    width: int
    height: int
    #: names for a grid that cannot carry comments, built from the same coordinates
    debug: DebugMap | None = field(default=None, compare=False, repr=False)

    @property
    def footprint(self) -> int:
        return max(self.width, self.height) ** 2

    def source(self) -> str:
        return "\n".join(self.rows)


def build_bcast(n: int) -> Bcast:
    """Router, one room of ``n`` broadcast-addressed cells, collector.

    The router and the collector are tall thin rooms flanking the field, which
    makes every one of the ``2n`` pipes a two-cell stub instead of needing a
    fan-out corridor. That is free for the router because ``S`` writes to *every*
    outgoing pipe — it has no nearest-pipe constraint at all — and free for the
    collector because ``R`` reads from *any* incoming one.
    """
    body, main_rows = field_rows(n)
    field_iw = max(len(r) for r in body)
    field_ih = len(body)
    router = list(ROUTER_ROWS)
    router_iw = max(len(r) for r in router)
    coll_rows, _ = collector_rows(1)
    coll_iw = max(len(r) for r in coll_rows)

    # router | 2 cells of pipe | field | 2 cells of pipe | collector
    rox, roy = 6, 3
    fx = rox + router_iw + 4
    cox = fx + field_iw + 4
    fy = roy
    grid = Circuit(cox + coll_iw + 10, fy + field_ih + 8)

    # The router has to span the field's rows, not just its own four: a pipe leaving
    # for a lower band would otherwise start where the router has no wall, parse with
    # no source room, and leave those cells binding a *neighbour's* pipe — which
    # looks exactly like the cells stealing each other's tokens.
    router_body = [r.ljust(router_iw) for r in router]
    router_body += [" " * router_iw] * (field_ih - len(router_body))
    _room(grid, rox, roy, router_body)
    _io_room(grid, rox - 5, roy + ROUTER_IN_ROW, "I")
    draw_pipe(
        grid,
        [(rox - 3, roy + ROUTER_IN_ROW), (rox - 2, roy + ROUTER_IN_ROW), (rox - 1, roy)],
    )

    _room(grid, fx, fy, body)
    # the collector spans the field's rows so every answer pipe is a stub
    coll_body = [r.ljust(coll_iw) for r in coll_rows] + [" " * coll_iw] * (field_ih - 2)
    _room(grid, cox, fy, coll_body)

    for main in main_rows:
        # router -> cell j: S has no affinity, so this may land anywhere on the wall
        draw_pipe(grid, [(x, fy + main) for x in range(rox + router_iw + 1, fx)])
        # cell j -> collector: leaves level with its own `s`, which is what makes it
        # strictly nearer this cell than any other
        read_row = fy + main + 1
        draw_pipe(grid, [(x, read_row) for x in range(fx + field_iw + 1, cox)])

    # the collector's own `s` is its only outgoing, so the output room can hang
    # anywhere; east of its wall keeps it clear of the answer stubs
    ex = cox + coll_iw
    draw_pipe(grid, [(ex + 1, fy + 1), (ex + 2, fy + 1), (ex + 3, fy + 1)])
    _io_room(grid, ex + 4, fy + 1, "O")

    # ── the sidecar ───────────────────────────────────────────────────────────
    # Nothing in the ASCII says which band holds address 5, and nothing says why one
    # band's `]` chain is longer than its neighbour's. Both are the whole design.
    dbg = DebugMap(f"broadcast man-memory n={n}: S out, one-hot decode, R back")
    dbg.region(
        "input",
        rox - 6,
        roy + ROUTER_IN_ROW - 1,
        3,
        3,
        note="the problem's own stream: `0 addr` (READ) / `1 addr value` (WRITE)",
        color=C_IO,
    )
    dbg.region(
        "router / broadcaster",
        rox,
        roy,
        router_iw,
        len(ROUTER_ROWS),
        note=(
            "`r` the op and branch on it. Each lane then reads the address and builds "
            "`1 << addr` with `M 1 {`, and `S` writes that into EVERY outgoing pipe at "
            "once. A READ then broadcasts 0; a WRITE broadcasts 1 and the value. This "
            "room owns no other outgoing pipe on purpose — `S` would broadcast into it "
            "too, which is why answers go through the collector instead."
        ),
        color=C_MID,
    )
    dbg.circle(
        "one-hot in one glyph",
        rox + 6,
        roy,
        1,
        note="A=1, B=addr, so `{` (A << B) is the whole address decoder",
        color=C_MID,
    )
    dbg.region(
        "router spans the field",
        rox,
        roy + len(ROUTER_ROWS),
        router_iw,
        field_ih - len(ROUTER_ROWS),
        note=(
            "empty, and load-bearing: a pipe's first cell must sit against its source "
            "room's wall. With a four-row router the lower bands' pipes parsed with no "
            "source at all and those cells bound a neighbour's pipe instead."
        ),
        color=C_MID,
    )
    dbg.region(
        "cell field (ONE room)",
        fx,
        fy,
        field_iw,
        field_ih,
        note=(
            f"all {n} cells share this room. Five rows each: return corridor, main "
            "line, READ tail, WRITE lane, WRITE store. Columns 0-1 are the spawner's."
        ),
        color=C_STORE,
    )
    for j, main in enumerate(main_rows):
        c = tile_cols(j)
        dbg.circle(
            f"split -> cell {j}",
            fx + 1,
            fy + main,
            1,
            note=(
                f"entered heading south, so one copy lands east on cell {j}'s `>` and "
                "the other drops into column 0 to seed the next band"
            ),
            color=C_MID,
        )
        dbg.region(
            f"cell addr {j}",
            fx + c["x0"],
            fy + main - 1,
            c["width"] - c["x0"],
            TILE_ROWS,
            note=(
                f"holds address {j} in its man's B. `b` moves the broadcast word to BP, "
                f"then {j} x `]` shifts bit {j} down to bit 0 and `x` turns on it — all "
                "BP-only, so B is never touched. READ: `W M s`. WRITE: `r` then `M`."
            ),
            color=C_STORE,
        )
        dbg.lane(
            f"broadcast -> cell {j}",
            [(rox + router_iw + 1, fy + main), (fx - 1, fy + main)],
            kind="pipe",
            expect="one-hot, op, [value]",
            note="one of the n pipes `S` writes at once; every cell consumes every word",
            color=C_CMD,
        )
        dbg.lane(
            f"answer addr {j}",
            [(fx + field_iw + 1, fy + main + 1), (cox - 1, fy + main + 1)],
            kind="pipe",
            expect="the stored value, on a selected READ only",
            note=f"leaves level with cell {j}'s own `s`, which is what binds it here",
            color=C_ANS,
        )
    dbg.region(
        "collector",
        cox,
        fy,
        coll_iw,
        field_ih,
        note=(
            "`R` reads from ANY incoming pipe, so the answer needs no addressing at "
            "all — the many-to-one teleport. Spanning the field's rows is what makes "
            "every answer pipe a two-cell stub instead of a fan-out corridor."
        ),
        color=C_COLL,
    )
    dbg.region("output", ex + 3, fy, 3, 3, note="one word per READ", color=C_IO)
    dbg.lane(
        "input-pipe",
        [(rox - 3, roy + ROUTER_IN_ROW), (rox - 1, roy)],
        kind="pipe",
        expect="op addr [value]",
        color=C_CMD,
    )
    dbg.lane(
        "output-pipe",
        [(ex + 1, fy + 1), (ex + 3, fy + 1)],
        kind="pipe",
        expect="one word per READ",
        color=C_ANS,
    )

    out = [row.rstrip() for row in grid.rows()]
    while out and not out[-1]:
        out.pop()
    return Bcast(
        n=n,
        rows=tuple(out),
        width=max(len(r) for r in out),
        height=len(out),
        debug=dbg,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """``--cells N``, written to ``--man`` / ``--html`` / ``--json`` in one go."""
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--cells", type=int, default=16, metavar="N", help="how many cells")
    ap.add_argument("--man", type=Path, help="write the grid here")
    ap.add_argument("--html", type=Path, help="write the labelled debug overlay here")
    ap.add_argument("--json", type=Path, help="write the debug region sidecar here")
    args = ap.parse_args(argv)

    built = build_bcast(args.cells)
    assert built.debug is not None  # the builder always emits its own map
    if args.man:
        args.man.write_text(built.source() + "\n", encoding="utf-8")
    if args.html:
        built.debug.write_html(list(built.rows), args.html)
    if args.json:
        built.debug.write_json(args.json)
    if not (args.man or args.html or args.json):
        print(built.source())
    else:
        print(f"{built.width} x {built.height}, footprint {built.footprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
