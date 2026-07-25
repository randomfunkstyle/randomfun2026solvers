#!/usr/bin/env python3
"""The man-memory packaged as an LM-1 ``STORE`` block — a drop-in for the tape.

``lm1.machine.tape_block`` hands the machine builder four things: a dict of cells,
a size, a request stub that must be entered heading east, and a response stub that
leaves heading north. Anything with that shape can be the machine's memory, and
``lm1.store`` already documents the wire protocol as the ``memory`` problem's own
``0 addr`` / ``1 addr value``, which is exactly what the man-memory speaks.

So this reuses the *line* variant, not a tree, and that is a measured choice:

* the line is the only variant that never waits. Its answers go straight out of the
  collector, so its cost is one router cycle — a measured ``22 + 14 * addr`` — while
  both tree variants serialise READs at the head to keep answers in order, which at
  ``n = 8`` costs 177-272 ticks against the line's 22-120;
* against the tape's ``105 + 8.3n`` per access, a line wins on ticks for every
  small ``n``: at ``n = 5`` it is 50 ticks average against 147, and at 43x21 it is
  *smaller in area* than the 33x32 tape block it replaces;
* it grows the wrong way — ``6n + 13`` columns — so it is only the right memory
  while ``n`` is small. That is the whole reason the block asserts a bound.

One free bonus over the tape: ``tape_n`` is a slot count whose slot 0 is
sign-ambiguous and unusable, and a man-cell has no such hole. Address 0 works.
"""

from __future__ import annotations

from dataclasses import dataclass

from .circuit import Circuit
from .memory_men import (
    CELL_H,
    CELL_IN_COL,
    CELL_OUT_COL,
    PITCH,
    _room,
    cell_at,
    collector_rows,
    draw_pipe,
    router_rows,
)

__all__ = ["MenStore", "men_block", "men_ticks"]

#: Beyond this the line's ``6n + 13`` width stops being worth its tick win against
#: a 33-column tape; the caller should be reaching for a tree (and a placer) there.
MAX_LINE_N = 24


@dataclass(frozen=True)
class MenStore:
    """Same shape as ``lm1.machine._Tape``, so the machine builder cannot tell."""

    cells: dict[tuple[int, int], str]
    width: int
    height: int
    in_cell: tuple[int, int]
    out_cell: tuple[int, int]


def men_ticks(addr: int) -> int:
    """Measured cost of one access at ``addr``: the router's whole cycle."""
    return 22 + 14 * addr


def men_block(n: int) -> MenStore:
    """A man-memory of ``n`` cells wired as a STORE block.

    Geometry, north to south: the router, its ``n`` cells, the collector. The
    response stub then has to climb *north* past all of that to meet the machine's
    corridor, so it runs up a dedicated column two east of the router's east wall,
    where the block itself never reaches.
    """
    if not 1 <= n <= MAX_LINE_N:
        raise ValueError(f"a line STORE block wants 1..{MAX_LINE_N} cells, not {n}")
    leaf_rows, leaf_ports = router_rows(n, pitch=PITCH, merge_head=True)
    leaf_iw = max(len(r) for r in leaf_rows)
    coll_rows, _ = collector_rows(1)

    # Two columns of margin west for the request stub, and the response column two
    # east of everything.
    bx, by = 4, 4
    resp_x = bx + leaf_iw + 3
    grid = Circuit(resp_x + 4, by + len(leaf_rows) + 4 + CELL_H + 4 + 6)

    _room(grid, bx, by, leaf_rows)
    # request stub: two cells pointing east into the router's west wall
    grid.set(bx - 3, by, ">")
    grid.set(bx - 2, by, ">")

    cell_y = by + len(leaf_rows) + 4
    for i in range(n):
        cell_x = bx + leaf_ports[i] - 1
        cell_at(grid, cell_x, cell_y)
        draw_pipe(grid, [(cell_x, cell_y - 3), (cell_x, cell_y - 2), (cell_x, cell_y - 1)])
        out_x = cell_x + CELL_OUT_COL - CELL_IN_COL
        draw_pipe(grid, [(out_x, cell_y + 5), (out_x, cell_y + 6), (out_x, cell_y + 7)])

    cy = cell_y + CELL_H + 2
    _room(grid, bx, cy, [r.ljust(leaf_iw) for r in coll_rows])

    # response stub: out of the collector's east wall, then north up the clear
    # column to the block's top row, where the machine's corridor picks it up.
    draw_pipe(
        grid,
        [(x, cy + 1) for x in range(bx + leaf_iw + 1, resp_x + 1)]
        + [(resp_x, y) for y in range(cy, 0, -1)]
        + [(resp_x, 0)],
    )

    cells = {k: v for k, v in grid.cell.items() if v != " "}
    return MenStore(
        cells=cells,
        width=max(x for x, _ in cells) + 1,
        height=max(y for _, y in cells) + 1,
        in_cell=(bx - 3, by),
        out_cell=(resp_x, 1),
    )
