#!/usr/bin/env python3
"""The man-memory *grid* packaged as an LM-1 ``STORE`` block — the hot tier.

``memory_men_grid.build_grid`` answers the ``memory`` problem: it comes with its
own ``I`` and ``O`` rooms, so it is a program, not a part.  ``lm1.machine`` wants
a *part*: a dict of cells, a request stub entered heading east, and a response
stub leaving heading north (``lm1.machine._Tape``).  This is that packaging, and
it is the same trade ``memory_men_store`` made for the line variant.

Two things are different from ``build_grid`` and both are what make it a **second
tier** rather than a replacement store:

* **The base is a parameter.**  A column's decoders hold *global* addresses — the
  igniter is handed a literal and counts up from it — so a grid built with
  ``base = T`` answers exactly the CPU's own slot numbers ``T .. T + cols*rows-1``
  with **no address translation anywhere**.  That is the whole reason a second
  tier is cheap: the adapter only has to decide *which pipe* to send the already
  correct request down.
* **The request enters from the west and the answer leaves to the north**, which
  is what the machine's corridor geometry wants.  The router strip is fed on its
  *north* wall (``U`` turns the man away from the pipe, and this router's code
  runs south), so the stub turns the corner inside the block: two cells east, then
  south into the strip.  The answer climbs a column of its own east of every room,
  exactly as ``memory_men_store.men_block`` does.

Cost, measured by ``judge`` at one read against nine (``LLM-DESIGN.md``):

    3x14 = 42 cells   118 ticks first read, 15 marginal   81 x 74
    4x25 = 100        173                                108 x 107
    7x61 = 427        355                                189 x 215

A CPU read *blocks*, so what a machine pays is the first-read latency — ~118
ticks against a 427-slot tape's 3,416.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .circuit import Circuit
from .memory_men import _room, collector_rows, draw_pipe
from .memory_men_addr import (
    _BASE_DIGITS,
    BAND,
    CELL_TILE,
    DECODER_TILE,
    ROUTER_ROWS,
    _init_height,
    band_room,
    preamble_width,
)
from .memory_men_grid import _CELL_W, _COL_GAP, _DEC_W, _GAP, _REP_W, REPEATER

__all__ = ["GridStore", "grid_block", "grid_ticks"]

#: Rows of margin above the router strip, for the request stub's corner.
_STUB_ROWS = 4
#: Columns of margin west of the rooms, for the request stub's approach.
_STUB_COLS = 3
#: Columns between the last room's east wall and the answer's climbing column.
_CLIMB_GAP = 3


@dataclass(frozen=True)
class GridStore:
    """Same shape as ``lm1.machine._Tape``, so the machine builder cannot tell."""

    cells: dict[tuple[int, int], str]
    width: int
    height: int
    in_cell: tuple[int, int]  # request arrives here heading east
    out_cell: tuple[int, int]  # response leaves here heading north
    slots: int  # cells, i.e. addresses ``base .. base + slots - 1``
    pipes: int  # every pipe the block itself draws (for _check_pipe_count)
    cols: int
    rows: int
    base: int

    @property
    def low(self) -> int:
        return self.base

    @property
    def high(self) -> int:
        """One past the last address this tier answers."""
        return self.base + self.slots


def grid_ticks(cols: int, rows: int) -> int:
    """Measured first-read latency: the igniter walk plus the router's lap.

    ``judge`` on the standalone grids: 3x14 -> 118, 4x25 -> 173, 7x61 -> 355.
    A least-squares fit through those three is ``22 + 6.9 * rows`` with the column
    count falling out entirely, which is exactly the claim the geometry makes —
    every column's igniter walks at the same time, so only its *length* is paid.
    """
    return 22 + 7 * rows


def grid_block(cols: int, rows: int, *, base: int = 0) -> GridStore:
    """``cols`` columns of ``rows`` man-cells, answering ``base ..`` , as a STORE.

    ``cols`` must be at least 2: a one-column grid is ``memory_men_addr`` and needs
    none of the strips, and the placement here assumes the strips exist.
    """
    if cols < 2 or rows < 1:
        raise ValueError(f"a grid STORE wants >= 2 columns of >= 1 cell, not {cols}x{rows}")
    if base < 0:
        raise ValueError(f"a tier base must be non-negative, not {base}")

    init_h = _init_height(0)
    col_h = init_h + BAND * rows
    top = base + cols * rows - 1
    digits = max(_BASE_DIGITS, len(str(top)))
    dec_w = max(_DEC_W, preamble_width(digits))
    col_w = _REP_W + _GAP + dec_w + _GAP + _CELL_W

    # ── vertical plan: stub margin, router strip, columns, collector strip ────
    router_y = _STUB_ROWS
    router_h = len(ROUTER_ROWS)
    col_y = router_y + router_h + 1 + _GAP - 1
    coll_y = col_y + col_h + _GAP

    x0 = _STUB_COLS
    strip_w = cols * col_w + (cols - 1) * _COL_GAP
    resp_x = x0 + strip_w + _CLIMB_GAP
    grid = Circuit(resp_x + 2, coll_y + 4)

    def column_x(j: int) -> dict[str, int]:
        b = x0 + j * (col_w + _COL_GAP)
        rep = b
        dec = rep + _REP_W + _GAP
        cell = dec + dec_w + _GAP
        return {"rep": rep, "dec": dec, "cell": cell, "end": cell + _CELL_W}

    def spanned(body: Sequence[str], iw: int, h: int) -> list[str]:
        out = [r.ljust(iw) for r in body]
        return out + [" " * iw] * (h - len(out))

    _room(grid, x0, router_y, [r.ljust(strip_w) for r in ROUTER_ROWS])
    _room(grid, x0, coll_y, [r.ljust(strip_w) for r in collector_rows(1)[0]])

    npipes = 0
    # ── the request stub: east along its own row, then south into the strip ──
    # The strip's ``U`` sits at interior column 1 and turns *away* from the wall it
    # read through, so the feed has to arrive on the north wall — hence the corner.
    feed_x = x0 + 1
    stub_y = 1
    npipes += 1
    draw_pipe(
        grid,
        [(x, stub_y) for x in range(0, feed_x)] + [(feed_x, y) for y in range(stub_y, router_y)],
    )

    # ── the answer stub: east out of the collector, then north up a clear column ──
    npipes += 1
    draw_pipe(
        grid,
        [(x, coll_y + 1) for x in range(x0 + strip_w + 1, resp_x)]
        + [(resp_x, y) for y in range(coll_y + 1, -1, -1)],
    )
    grid.set(resp_x, 0, "^")

    # ── the columns ──────────────────────────────────────────────────────────
    for j in range(cols):
        cx = column_x(j)
        cbase = base + j * rows
        dec_body, mains = band_room(
            rows, DECODER_TILE, increment=True, base=cbase, base_digits=digits
        )
        cell_body, cell_mains = band_room(rows, CELL_TILE, increment=False, init_h=init_h)
        assert mains == cell_mains, "both rooms of a column must band identically"

        _room(grid, cx["rep"], col_y, spanned(REPEATER, _REP_W, col_h))
        _room(grid, cx["dec"], col_y, spanned(dec_body, dec_w, col_h))
        _room(grid, cx["cell"], col_y, spanned(cell_body, _CELL_W, col_h))

        fx = cx["rep"] + 2
        npipes += 1
        draw_pipe(grid, [(fx, y) for y in range(router_y + router_h + 1, col_y)])
        ax = cx["cell"] + 1
        npipes += 1
        draw_pipe(grid, [(ax, y) for y in range(col_y + col_h + 1, coll_y)])
        for main in mains:
            y = col_y + main
            npipes += 2
            draw_pipe(grid, [(x, y) for x in range(cx["rep"] + _REP_W + 1, cx["dec"])])
            draw_pipe(grid, [(x, y) for x in range(cx["dec"] + dec_w + 1, cx["cell"])])

    cells = {k: v for k, v in grid.cell.items() if v != " "}
    return GridStore(
        cells=cells,
        width=max(x for x, _ in cells) + 1,
        height=max(y for _, y in cells) + 1,
        in_cell=(0, stub_y),
        out_cell=(resp_x, 0),
        slots=cols * rows,
        pipes=npipes,
        cols=cols,
        rows=rows,
        base=base,
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--rows", type=int, default=14)
    ap.add_argument("--base", type=int, default=0)
    ap.add_argument("--man", type=Path)
    args = ap.parse_args(argv)
    blk = grid_block(args.cols, args.rows, base=args.base)
    text = "\n".join(
        "".join(blk.cells.get((x, y), " ") for x in range(blk.width)).rstrip()
        for y in range(blk.height)
    )
    if args.man:
        args.man.write_text(text + "\n", encoding="utf-8")
    print(
        f"{blk.cols}x{blk.rows} = {blk.slots} cells at {blk.base}..{blk.high - 1}, "
        f"{blk.width} x {blk.height}, {blk.pipes} pipes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
