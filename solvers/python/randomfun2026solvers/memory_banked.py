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

import argparse
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, E
from randomfun2026solvers.man_debug import DebugMap

__all__ = ["build_bucket_probe", "build_bucket_probe_debug"]


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


def build_bucket_probe_debug() -> tuple[list[str], DebugMap]:
    """Build the selector together with its non-drifting visual explanation."""
    rows = build_bucket_probe()
    debug = DebugMap("Y-banked memory selector — low addr / high addr−50")
    debug.region(
        "input-and-split",
        6,
        4,
        5,
        3,
        note="The parent reads addr, copies it with M, then Y births one worker on each side.",
        color="#a855f7",
    )
    debug.region(
        "high-bank-selector",
        10,
        2,
        12,
        4,
        note="Compute addr−50. Negative halts; zero/positive merge and emit local address.",
        color="#3b82f6",
    )
    debug.region(
        "low-bank-selector",
        10,
        5,
        11,
        4,
        note="Compute addr−50. Negative adds 50 back and emits; zero/positive halt.",
        color="#22c55e",
    )
    debug.lane(
        "parent",
        [(7, 5), (10, 5)],
        kind="control",
        expect="rM leaves A=B=addr; Y duplicates both registers",
        color="#a855f7",
    )
    debug.lane(
        "high-child",
        [(10, 4), (17, 4), (17, 5), (19, 5), (19, 4), (21, 4)],
        kind="control",
        expect="active exactly for addr ≥ 50; output addr−50",
        color="#3b82f6",
    )
    debug.lane(
        "low-child",
        [(10, 6), (10, 7), (17, 7), (17, 6), (20, 6)],
        kind="control",
        expect="active exactly for addr < 50; output original addr",
        color="#22c55e",
    )
    debug.scenario(
        "low bucket",
        "18",
        0,
        30,
        watch=["parent", "low-child"],
        note="The low child emits 18; the high child sees −32 and halts.",
    )
    debug.scenario(
        "high bucket",
        "81",
        0,
        30,
        watch=["parent", "high-child"],
        note="The high child emits 31; the low child rejects address 81.",
    )
    return rows, debug


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--man", type=Path, required=True)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()

    rows, debug = build_bucket_probe_debug()
    args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    debug.write_html(rows, args.html)
    debug.write_json(args.json)


if __name__ == "__main__":
    main()
