#!/usr/bin/env python3
"""``M`` columns of ``N`` cells: the same memory, ignited in parallel.

``memory_men_addr`` is one tall column of ``n`` cells, and measuring it turned up a
cost that no loop-shortening touches:

    n=100:  1 op  716 ticks    5 ops  781  ->  16 ticks/op + **700 fixed**

The 700 is the **igniter**. One man walks south handing every band its address —
seven ticks a band — and the router's ``S`` cannot fire until the hundredth
decoder exists, because ``S`` is all-or-nothing across every pipe and an unborn
cell's pipe fills after two words and never drains. Inverting the judge's 2,283
against ``700 + 16 * ops`` puts the average graded case at ~99 ops, so **the birth
walk is about a third of our score**.

Igniters in different rooms run **at the same time**. So cut the memory into ``M``
columns of ``N`` and the fixed cost falls from ``7n`` to ``7N``.

    [I] -> [ router strip                     ]   broadcasts addr, op, value
             |          |          |              one pipe down per column
          [rep][dec][cell][coll]  x M            rep fans the words into its column
             |          |          |              one pipe down per column
          [ collector strip                   ] -> [O]

**Nothing about a cell changes.** The column's decoders hold *global* addresses —
its igniter is handed a base and counts up from it — so the thing at the head of a
column has no decoding to do at all. It is a six-glyph ring, ``r S`` three times,
repeating the router's words into its own column. That is why ``M`` and ``N`` are
both free: no bit-alignment, no division, no power-of-two stride, and
``DECODER_TILE`` and ``CELL_TILE`` are the same text they were.

The base is a zero-padded literal read walking *south* (big literals work in any
direction). A column is its own room, so it can simply carry its number — no
igniter-of-igniters and no ignition pipes, and every column starts at tick 0
rather than waiting its turn.

Router strip on top, collector strip on the bottom, deliberately: broadcast pipes
then never have to cross answer pipes, and every column's answer travels exactly
the same distance — otherwise a later op's answer overtakes an earlier one and the
output order breaks.
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
from .memory_men_addr import (
    BAND,
    CELL_TILE,
    DECODER_TILE,
    ROUTER_ROWS,
    _init_height,
    band_room,
    build_addr,
    tile_x0,
)

__all__ = ["REPEATER", "Grid", "build_grid", "shapes_for"]

#: The head of a column: take one word, shout it at the whole column, three times.
#:
#: A ring again — the perimeter is the program. It decodes **nothing**: the
#: column's decoders hold global addresses, so this only has to repeat. Its one
#: incoming pipe comes down from the router strip, so ``r`` is unambiguous, and its
#: outgoing pipes are the ``N`` stubs into its own decoder room, so ``S`` reaches
#: exactly that column. Lap 10 against the router's 16, so it never paces.
REPEATER: tuple[str, ...] = (
    "v<",
    "Sr",
    "rS",
    "Sr",
    ">^",
    "@^",
)

#: Interior width of each room in a column, in order.
_REP_W = max(len(r) for r in REPEATER)
_DEC_W = tile_x0(True) + max(len(r) for r in DECODER_TILE)
_CELL_W = tile_x0(False) + max(len(r) for r in CELL_TILE)
_COLL_W = 4

#: Interior columns between two rooms: wall, two cells of pipe, wall.
_GAP = 4
#: Blank columns between one column of memory and the next.
_COL_GAP = 2


def shapes_for(n: int) -> list[tuple[int, int]]:
    """Every ``(cols, rows)`` that covers ``n`` addresses without a gap.

    ``rows`` divides the address space into contiguous runs, so a column's
    decoders are ``base .. base+rows-1`` and the last column may be short. Any
    ``rows`` works — the head does no arithmetic — so this is just the divisors
    worth measuring.
    """
    return [(-(-n // rows), rows) for rows in range(1, n + 1) if rows <= n]


@dataclass(frozen=True)
class Grid:
    """A complete ``memory`` program as ``cols`` columns of ``rows`` cells."""

    cols: int
    rows: int
    grid_rows: tuple[str, ...]
    width: int
    height: int
    debug: DebugMap | None = field(default=None, compare=False, repr=False)

    @property
    def n(self) -> int:
        return self.cols * self.rows

    @property
    def footprint(self) -> int:
        return max(self.width, self.height) ** 2

    def source(self) -> str:
        return "\n".join(self.grid_rows)


def build_grid(cols: int, rows: int) -> Grid:
    """``cols`` columns of ``rows`` cells each, addresses running column by column.

    Column ``j`` owns ``[j*rows, (j+1)*rows)`` and its igniter is handed ``j*rows``
    as a literal, so all ``cols`` igniters walk at once and the fixed cost of the
    whole memory is one column's walk.
    """
    if cols < 1 or rows < 1:
        raise ValueError("a grid needs at least one column of at least one cell")
    if cols == 1:
        # One column needs none of this: the repeater would be a pass-through
        # costing a pipeline stage and two pipes, and a strip spanning one column
        # is just a router. `memory_men_addr` *is* the one-column grid.
        one = build_addr(rows)
        return Grid(
            cols=1,
            rows=rows,
            grid_rows=one.rows,
            width=one.width,
            height=one.height,
            debug=one.debug,
        )

    init_h = _init_height(0)  # every column carries a base literal, zero included
    col_h = init_h + BAND * rows
    col_w = _REP_W + _GAP + _DEC_W + _GAP + _CELL_W + _GAP + _COLL_W

    # ── vertical plan ─────────────────────────────────────────────────────────
    # I room, its pipe, the router strip, its pipes, the columns, their pipes,
    # the collector strip, its pipe, the O room. Two-cell pipes throughout.
    router_y = 6
    router_h = len(ROUTER_ROWS)
    col_y = router_y + router_h + 1 + _GAP - 1  # south wall, 2 pipe cells, north wall
    coll_y = col_y + col_h + _GAP
    out_y = coll_y + 2 + _GAP

    x0 = 1
    total_w = x0 + cols * col_w + (cols - 1) * _COL_GAP + 1
    grid = Circuit(total_w + 2, out_y + 4)

    def column_x(j: int) -> dict[str, int]:
        base = x0 + j * (col_w + _COL_GAP)
        rep = base
        dec = rep + _REP_W + _GAP
        cell = dec + _DEC_W + _GAP
        coll = cell + _CELL_W + _GAP
        return {"rep": rep, "dec": dec, "cell": cell, "coll": coll, "end": coll + _COLL_W}

    def spanned(body: Sequence[str], iw: int, h: int) -> list[str]:
        # Every room in a column is as tall as the column: a pipe's first cell has
        # to sit against its source room's wall and its last against the
        # destination's, so a short room leaves the lower bands unreachable.
        out = [r.ljust(iw) for r in body]
        return out + [" " * iw] * (h - len(out))

    # ── the router strip ──────────────────────────────────────────────────────
    strip_w = column_x(cols - 1)["end"] - x0
    router_body = [r.ljust(strip_w) for r in ROUTER_ROWS]
    _room(grid, x0, router_y, router_body)
    _io_room(grid, x0 + 1, router_y - 5, "I")
    draw_pipe(grid, [(x0 + 1, router_y - 3), (x0 + 1, router_y - 2), (x0 + 1, router_y - 1)])

    # ── the collector strip ───────────────────────────────────────────────────
    coll_body = [r.ljust(strip_w) for r in collector_rows(1)[0]]
    _room(grid, x0, coll_y, coll_body)
    out_x = x0 + 2
    draw_pipe(grid, [(out_x, coll_y + 3), (out_x, coll_y + 4), (out_x, coll_y + 5)])
    _io_room(grid, out_x, out_y, "O")

    # ── the columns ───────────────────────────────────────────────────────────
    dbg = DebugMap(f"grid man-memory {cols}x{rows}: {cols} igniters walking at once")
    dbg.region(
        "router / broadcaster",
        x0,
        router_y,
        strip_w,
        router_h,
        note=(
            "one op in, three words out to every column at once. The strip spans the "
            "whole width so each column's pipe is a two-cell stub off its south wall, "
            "and it owns no other outgoing pipe — `S` would broadcast into that too."
        ),
        color=C_MID,
    )
    dbg.region(
        "collector strip",
        x0,
        coll_y,
        strip_w,
        2,
        note=(
            "`R` takes from any incoming pipe, so answers need no addressing. It is on "
            "the *bottom* on purpose: answer pipes never cross broadcast pipes, and "
            "every column's answer travels the same distance, so a later op's answer "
            "cannot overtake an earlier one."
        ),
        color=C_COLL,
    )
    dbg.region("input", x0, router_y - 6, 3, 3, note="`0 addr` / `1 addr value`", color=C_IO)
    dbg.region("output", out_x - 1, out_y - 1, 3, 3, note="one word per READ", color=C_IO)

    for j in range(cols):
        cx = column_x(j)
        base = j * rows
        dec_body, mains = band_room(rows, DECODER_TILE, increment=True, base=base)
        cell_body, cell_mains = band_room(rows, CELL_TILE, increment=False, init_h=init_h)
        assert mains == cell_mains, "both rooms of a column must band identically"

        _room(grid, cx["rep"], col_y, spanned(REPEATER, _REP_W, col_h))
        _room(grid, cx["dec"], col_y, spanned(dec_body, _DEC_W, col_h))
        _room(grid, cx["cell"], col_y, spanned(cell_body, _CELL_W, col_h))
        _room(grid, cx["coll"], col_y, spanned(collector_rows(1)[0], _COLL_W, col_h))

        # router strip -> this column's repeater, down two cells into its north wall
        feed_x = cx["rep"] + 2
        draw_pipe(grid, [(feed_x, y) for y in range(router_y + router_h + 1, col_y)])
        # this column's collector -> the collector strip, likewise
        ans_x = cx["coll"] + 1
        draw_pipe(grid, [(ans_x, y) for y in range(col_y + col_h + 1, coll_y)])

        for main in mains:
            y = col_y + main
            draw_pipe(grid, [(x, y) for x in range(cx["rep"] + _REP_W + 1, cx["dec"])])
            draw_pipe(grid, [(x, y) for x in range(cx["dec"] + _DEC_W + 1, cx["cell"])])
            draw_pipe(grid, [(x, y) for x in range(cx["cell"] + _CELL_W + 1, cx["coll"])])

        dbg.region(
            f"column {j}: addr {base}-{base + rows - 1}",
            cx["rep"],
            col_y,
            cx["end"] - cx["rep"],
            col_h,
            note=(
                f"its own igniter, its own `@`, walking at tick 0 like every other "
                f"column's. It is handed {base} as a literal and counts up, so these "
                "decoders hold global addresses and the head above them decodes nothing."
            ),
            color=C_STORE if j % 2 else C_MID,
        )
        dbg.region(
            f"repeater {j}",
            cx["rep"],
            col_y,
            _REP_W,
            len(REPEATER),
            note="`r S` three times: take a word off the strip, shout it at this column",
            color=C_CMD,
        )
        dbg.circle(
            f"base {base}",
            cx["dec"] + 1,
            col_y + 3,
            1,
            note=f"read walking south, `M` into B, then `+1` a band: {base}..{base + rows - 1}",
            color=C_MID,
        )
        dbg.lane(
            f"broadcast -> column {j}",
            [(feed_x, router_y + router_h + 1), (feed_x, col_y - 1)],
            kind="pipe",
            expect="addr, op, value",
            color=C_CMD,
        )
        dbg.lane(
            f"answer <- column {j}",
            [(ans_x, col_y + col_h + 1), (ans_x, coll_y - 1)],
            kind="pipe",
            expect="the stored value, on a selected READ",
            note="same length in every column, which is what keeps answers in order",
            color=C_ANS,
        )

    out = [row.rstrip() for row in grid.rows()]
    while out and not out[-1]:
        out.pop()
    return Grid(
        cols=cols,
        rows=rows,
        grid_rows=tuple(out),
        width=max(len(r) for r in out),
        height=len(out),
        debug=dbg,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """``--cols M --rows N``, written to ``--man`` / ``--html`` / ``--json``."""
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--cols", type=int, default=13, metavar="M")
    ap.add_argument("--rows", type=int, default=8, metavar="N")
    ap.add_argument("--man", type=Path, help="write the grid here")
    ap.add_argument("--html", type=Path, help="write the labelled debug overlay here")
    ap.add_argument("--json", type=Path, help="write the debug region sidecar here")
    args = ap.parse_args(argv)

    built = build_grid(args.cols, args.rows)
    assert built.debug is not None
    if args.man:
        args.man.write_text(built.source() + "\n", encoding="utf-8")
    if args.html:
        built.debug.write_html(list(built.grid_rows), args.html)
    if args.json:
        built.debug.write_json(args.json)
    if not (args.man or args.html or args.json):
        print(built.source())
    else:
        print(
            f"{built.cols}x{built.rows} = {built.n} cells, "
            f"{built.width} x {built.height}, footprint {built.footprint}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
