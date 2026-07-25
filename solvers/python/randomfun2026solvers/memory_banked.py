#!/usr/bin/env python3
"""Executable control-flow probes for a ``Y``-banked memory tape.

The full machine will have two independent 50-value rings.  Before drawing
those rings, :func:`build_bucket_probe` pins the risky part against the actual
Littleman semantics:

* one runner reads the address and copies it into both hands;
* ``Y`` creates the low and high workers;
* both compute ``addr - 50``;
* the low worker accepts only a negative result and adds 50 back;
* the high worker accepts zero or a positive result;
* exactly one worker sends its local address, and both halt.

Thus input 0..99 produces 0..49 twice.  The same selection predicate will guard
the target side effect in the complete machine.  In particular, only the active
WRITE child will consume the value token after the split.
"""

from __future__ import annotations

from randomfun2026solvers.circuit import Circuit, E

__all__ = ["build_bucket_probe"]


def _walls(grid: Circuit, ox: int, oy: int, iw: int, ih: int) -> None:
    for x in range(-1, iw + 1):
        grid.set(ox + x, oy - 1, "+" if x in (-1, iw) else "-")
        grid.set(ox + x, oy + ih, "+" if x in (-1, iw) else "-")
    for y in range(ih):
        grid.set(ox - 1, oy + y, "|")
        grid.set(ox + iw, oy + y, "|")


def build_bucket_probe() -> list[str]:
    """Return a small complete program mapping ``addr`` to ``addr % 50``.

    This is intentionally a one-operation probe rather than a partial memory
    solution.  It exercises child birth directions, the zero boundary at
    address 50, mutually exclusive side effects, and collision-free termination.
    """
    iw, ih = 17, 9
    room = Circuit(iw, ih)

    # Parent: after rM, both A and B contain addr.  Entering Y east creates the
    # high child immediately north and the low child immediately south.
    room.run(1, 4, "@rMY")

    # High child: delta = addr - 50.  Negative is inactive; zero continues
    # straight and positive turns south, with both active paths merging at `s`.
    room.set(4, 3, ">")
    room.run(5, 3, "`50`W-X")
    room.set(11, 2, "H")
    room.set(11, 4, ">")
    room.set(13, 4, "^")
    room.set(13, 3, ">")
    room.run(14, 3, "sH")

    # Low child computes the identical delta.  Here negative is active and
    # turns north; `+` restores the original 0..49 address.  Zero and positive
    # halt without touching output.
    room.set(4, 5, "v")
    room.set(4, 6, ">")
    room.run(5, 6, "`50`W-X")
    room.set(11, 5, ">")
    room.run(12, 5, "+sH")
    room.set(12, 6, "H")
    room.set(11, 7, "H")

    grid = Circuit(29, 11)
    ox, oy = 6, 1
    for (x, y), glyph in room.cell.items():
        grid.set(ox + x, oy + y, glyph)
    _walls(grid, ox, oy, iw, ih)

    # Input and output share the parent's row.  They are the room's only
    # incoming/outgoing pipes, so the probe has no positional binding ambiguity.
    for i, row in enumerate(("+-+", "|I|", "+-+")):
        for j, glyph in enumerate(row):
            grid.set(j, oy + 3 + i, glyph)
    grid.run(3, oy + 4, ">>", d=E)

    out_x = ox + iw + 3
    for i, row in enumerate(("+-+", "|O|", "+-+")):
        for j, glyph in enumerate(row):
            grid.set(out_x + j, oy + 3 + i, glyph)
    grid.run(ox + iw + 1, oy + 4, ">>", d=E)

    return [row.rstrip() for row in grid.rows() if row.strip()]

