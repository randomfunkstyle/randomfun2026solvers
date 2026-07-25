#!/usr/bin/env python3
"""A line of ``n`` man-cells in ONE room, every resident born by ``Y``.

``memory_men.build_line`` gives each value its own 6x6 room: 36 cells and four
walls per stored value, and a grid ``6n + 13`` wide. The walls are the waste. A
room exists to be a pipe boundary, and a stored value does not need one — so this
puts all ``n`` cells in a single room and creates their men with ``Y``.

The cell logic is unchanged from the verified one (``memory_men.CELL``): ``r`` the
command word, ``X`` on it, READ walks ``W s M`` (the ``M`` restoring ``B``), WRITE
walks ``r W``. What changes is only that the man arrives by birth rather than by
``@``, and that fifteen of his neighbours share his room.

**How the spawner works, against the split rules** (``reference/split.txt``):

* ``Y`` replaces the entering man with two copies on the cells left and right of
  his incoming heading, each facing away. Entering east, that is one north and one
  south, so the spawner corridor runs east with the tiles hanging south of it.
* The copies execute their birth cells and move on the tick *after* birth. So the
  south copy's birth cell is a blank it falls through, landing on the tile's ``>``
  and turning into the loop.
* The north copy runs a return lane and comes back down to the corridor to trigger
  the next site's ``Y``. So the sixteen residents are born in address order, one
  per split, and the last copy halts.
* A birth into a wall is fatal, which is why the corridor is row 1 of the interior
  and never row 0.
* A birth onto another live man kills both. It cannot happen here: every resident
  is parked on its own ``r`` down in the tiles, rows away from the two birth cells.

Registers cost nothing to set up: a copy retains ``A``, ``B`` and ``BP``, and the
spawner never loads anything, so every resident starts with ``B = 0`` — the
problem's "every cell starts at 0" for free, with **no initialisation pass**.
"""

from __future__ import annotations

from dataclasses import dataclass

from .circuit import Circuit
from .memory_men import LANE_PITCH, _io_room, _room, collector_rows, draw_pipe, router_rows

__all__ = ["FieldLine", "field_rows", "build_field_line"]

#: Columns per cell. Four is what the tile is wide, and it is also
#: ``memory_men.LANE_PITCH``, so the router's lanes sit directly above their cells
#: and every command pipe is two cells long.
CELL_PITCH = LANE_PITCH

#: Interior rows of the field room: return lane, spawner corridor, the south
#: copy's birth row, then four rows of tile.
FIELD_ROWS = 7
_TILE_TOP = 3


def field_rows(n: int) -> tuple[list[str], list[int], list[int]]:
    """The shared room holding ``n`` cells; returns rows, command cols, answer cols.

    Per cell, four columns::

        >  v     return lane: the north copy runs east and drops back to the corridor
        Y  >     spawner: split here, then carry on to the next site
                 the south copy is born here and falls through
        >rXv     the cell, identical to the verified walled one...
          rW
        ^W<s     ...with `@` replaced by a blank: the man arrives by birth
        ^ M<
    """
    if n < 1:
        raise ValueError("a line needs at least one cell")
    iw = CELL_PITCH * n + 2
    rows = [[" "] * iw for _ in range(FIELD_ROWS)]

    def put(x: int, y: int, glyph: str) -> None:
        if rows[y][x] not in (" ", glyph):
            raise ValueError(f"field collision at ({x},{y}): {rows[y][x]!r} vs {glyph!r}")
        rows[y][x] = glyph

    cmd_cols: list[int] = []
    ans_cols: list[int] = []
    for j in range(n):
        # sites start at column 1: the spawner needs a cell of its own west of
        # site 0, and putting `@` on site 0's `Y` would leave cell 0 with no man
        # at all — which passes every test that never reads address 0.
        x = 1 + CELL_PITCH * j
        # the north copy's return lane, and the corridor it drops back onto
        put(x, 0, ">")
        put(x + 3, 0, "v")
        put(x, 1, "Y")
        put(x + 3, 1, ">")
        # the tile: memory_men.CELL, shifted, with the spawn glyph gone
        put(x, _TILE_TOP, ">")
        put(x + 1, _TILE_TOP, "r")
        put(x + 2, _TILE_TOP, "X")
        put(x + 3, _TILE_TOP, "v")
        put(x + 2, _TILE_TOP + 1, "r")
        put(x + 3, _TILE_TOP + 1, "W")
        put(x, _TILE_TOP + 2, "^")
        put(x + 1, _TILE_TOP + 2, "W")
        put(x + 2, _TILE_TOP + 2, "<")
        put(x + 3, _TILE_TOP + 2, "s")
        put(x, _TILE_TOP + 3, "^")
        put(x + 2, _TILE_TOP + 3, "M")
        put(x + 3, _TILE_TOP + 3, "<")
        # The command pipe lands above the first `r` and the answer pipe leaves
        # under the `s`, which makes both strictly nearest their own cell.
        cmd_cols.append(x + 1)
        ans_cols.append(x + 3)
    rows[1][0] = "@"  # the spawner starts on the corridor, west of site 0's `Y`
    put(1 + CELL_PITCH * (n - 1) + 4, 1, "H")  # ...and the last copy stops here
    return ["".join(r) for r in rows], cmd_cols, ans_cols


@dataclass(frozen=True)
class FieldLine:
    """A complete ``memory`` program: router, one shared cell room, collector."""

    n: int
    rows: tuple[str, ...]
    width: int
    height: int

    @property
    def footprint(self) -> int:
        return max(self.width, self.height) ** 2

    def source(self) -> str:
        return "\n".join(self.rows)


def build_field_line(n: int) -> FieldLine:
    """Router over ``n`` ``Y``-born cells sharing one room, wired I/O to I/O.

    Speaks the problem's stream directly (``0 addr`` / ``1 addr value``), so it can
    be verified against ``tasks/problems/memory.json`` at any ``n``.
    """
    body, cmd_cols, ans_cols = field_rows(n)
    router, ports = router_rows(n, pitch=CELL_PITCH, merge_head=True)
    router_iw = max(len(r) for r in router)
    field_iw = max(len(r) for r in body)
    coll_rows, _ = collector_rows(1)

    # Align the router's lane ports over the field's command columns so every
    # command pipe is a straight two-cell drop.
    rx, ry = 8, 4
    fx = rx + ports[0] - cmd_cols[0]
    # +4, not +3: between two rooms a pipe needs two cells of its own plus the
    # destination wall to point into.
    fy = ry + len(router) + 4
    cy = fy + FIELD_ROWS + 4
    grid = Circuit(max(rx + router_iw, fx + field_iw) + 16, cy + 6)

    _room(grid, rx, ry, router)
    _io_room(grid, rx - 5, ry, "I")
    draw_pipe(grid, [(rx - 3, ry), (rx - 2, ry), (rx - 1, ry)])

    _room(grid, fx, fy, body)
    for j in range(n):
        cx = fx + cmd_cols[j]
        draw_pipe(grid, [(cx, y) for y in range(ry + len(router) + 1, fy)])
        ax = fx + ans_cols[j]
        draw_pipe(grid, [(ax, y) for y in range(fy + FIELD_ROWS + 1, cy)])

    _room(grid, fx, cy, [r.ljust(field_iw) for r in coll_rows])
    draw_pipe(grid, [(fx - 2, cy + 1), (fx - 3, cy + 1), (fx - 4, cy + 1)])
    _io_room(grid, fx - 5, cy + 1, "O")

    rows = [row.rstrip() for row in grid.rows()]
    while rows and not rows[-1]:
        rows.pop()
    return FieldLine(n=n, rows=tuple(rows), width=max(len(r) for r in rows), height=len(rows))
