"""Program scoring — the footprint (area) term.

`task_docs/scoring.md`: a program's score (lower is better) is either

    footprint-tick:  max(width, height)**2 * average_ticks
    footprint:       max(width, height)**2

where width/height are the bounding box of the *entire* rendered program grid.

This module implements the **footprint (area) term only** — the part that does not
depend on execution. The tick term (`average_ticks`) is intentionally out of scope
here, as is any minimizer: this is measurement, so a generated grid can report its
own `max(w, h)**2` and we can lock a baseline before optimizing layout.

Producer-agnostic: it operates on the final `.man` string, so it works for both grid
producers — `layout.emit_grid`/`compile.compile_source` (M1 straight-line) and
`router.render` (R0/R1). `router.Canvas.render` already right-trims each row before
padding; we `rstrip` here too so trailing pad never inflates the width, matching the
canonical `emu.parse._pad` derivation.
"""

from __future__ import annotations


def bounding_box(grid: str) -> tuple[int, int]:
    """`(width, height)` of the grid's bounding box.

    Rows are split on newlines; the single trailing empty row from the final `\\n`
    is dropped. Width is the longest row's *trimmed* length (trailing whitespace
    does not count toward the footprint).
    """
    rows = grid.split("\n")
    if rows and rows[-1] == "":
        rows = rows[:-1]
    height = len(rows)
    width = max((len(r.rstrip()) for r in rows), default=0)
    return width, height


def footprint(grid: str) -> int:
    """The footprint score term: `max(width, height) ** 2` (lower is better)."""
    width, height = bounding_box(grid)
    return max(width, height) ** 2
