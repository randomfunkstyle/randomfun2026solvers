#!/usr/bin/env python3
"""Man-memory v3: the same cells, a router that does not walk home.

``memory_men_addr`` (v2) answers an access in a flat ~16.8 ticks whatever the
address is, and measuring where those ticks go settles the design question this
module exists to answer:

    pad the router's return corridor by two rows -> 16.79 -> 20.73 ticks/op
    pad it by four                               -> 24.67

**One tick added to the router's loop is one tick added to every access.** The
decoders, the cells and the collector all have slack; the router has none, because
it is the one man every operation has to pass through. v2 spends 17 ticks a lap on
8 glyphs of work -- the other 9 are the walk back to the top of the loop.

So v3 changes the *router*, not the storage. The op loop is **unrolled into a
boustrophedon**: one block of program text per operation, laid end to end, so a
block's last cell is the next block's first and nothing is ever walked twice. Only
the final block walks home, and that cost is amortised over the whole stream.

One block, ten columns, running east::

    r M r S W X S S . >        READ  = 10 ticks
              > S r S ^        WRITE = 11 ticks

``M`` parks the op in ``B`` and ``W`` brings it back, so ``X`` branches on the op
with the address already broadcast. A READ falls through the two ``S`` that send
``op = 0`` and a ``0`` dummy; a WRITE drops one row, sends ``op``, takes the value
and sends that. Both lanes rejoin on the block's last cell.

**The bus is v2's, byte for byte** -- ``addr``, ``op``, ``value|dummy``, three
words, fixed width -- so :data:`~memory_men_addr.DECODER_TILE` and
:data:`~memory_men_addr.CELL_TILE` drop in unchanged and this is a drop-in
replacement at the wire.

Unrolling is what "area is free" buys. It is the *only* place in this design where
it buys anything: the decoder's not-mine lap is 8 ticks of which 5 are work, and no
amount of space makes a ring smaller than the glyphs it has to hold.
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
    C_IO,
    C_MID,
    C_STORE,
    _io_room,
    _room,
    collector_rows,
    draw_pipe,
)
from .memory_men_addr import CELL_TILE, DECODER_TILE, band_room

__all__ = ["BLOCK", "MAIN", "router_rows", "MemoryV3", "build_v3", "build_v3_grid", "main"]

#: Columns one unrolled operation occupies.
BLOCK = 10

#: The block's main row. ``r``(op) ``M`` ``r``(addr) ``S``(addr) ``W`` ``X``
#: ``S``(op) ``S``(dummy); column 8 is a pass-through and column 9 the rejoin.
MAIN = "rMrSWXSS"


def router_rows(ops: int, per_row: int = 12) -> list[str]:
    """``ops`` unrolled blocks snaking east/west, closing back onto block 0.

    The chain is a *loop*, not a line: the last block walks home to the first, so a
    stream longer than ``ops`` operations is still answered correctly and only pays
    one walk. ``ops`` is therefore a speed knob, never a correctness one.

    Rows are pitched three apart: the main row, the write lane one row *right of
    travel* (south going east, north going west -- that is where ``X`` turns), and
    one spare so two bands' write lanes cannot meet.
    """
    if ops < 1:
        raise ValueError("a router needs at least one unrolled operation")
    grid: dict[tuple[int, int], str] = {}

    def put(x: int, y: int, glyph: str) -> None:
        if grid.get((x, y), glyph) != glyph:
            raise ValueError(f"collision at ({x},{y}): {grid[(x, y)]!r} vs {glyph!r}")
        grid[(x, y)] = glyph

    per_row = max(1, per_row)
    bands = max(1, (ops + per_row - 1) // per_row)
    span = per_row * BLOCK
    x0 = 2  # column 0 is the walk home, column 1 its turn and the westward U-turn

    for band in range(bands):
        east = band % 2 == 0
        ry = 1 + band * 3
        step = 1 if east else -1
        wy = ry + step  # `X` turns right of travel
        for slot in range(per_row):
            base = x0 + slot * BLOCK if east else x0 + span - 1 - slot * BLOCK
            for k, glyph in enumerate(MAIN):
                put(base + step * k, ry, glyph)
            put(base + step * 9, ry, ">" if east else "<")
            lane = (">" if east else "<", "S", "r", "S", "^" if east else "v")
            for k, glyph in zip(range(5, 10), lane, strict=True):
                put(base + step * k, wy, glyph)
        end = x0 + span if east else x0 - 1
        if band + 1 < bands:
            # three rows down, then reverse into the next band's first block
            for dy in range(3):
                put(end, ry + dy, "v")
            put(end, ry + 3, "<" if east else ">")
        else:
            floor = ry + 3
            for y in range(ry, floor):
                put(end, y, "v")
            put(end, floor, "<")
            for x in range(1, end):
                put(x, floor, "<")
            put(0, floor, "^")
            for y in range(2, floor):
                put(0, y, "^")
            put(0, 1, ">")

    put(0, 0, "@")
    put(1, 0, "v")
    put(1, 1, ">")

    w = max(x for x, _ in grid) + 1
    h = max(y for _, y in grid) + 1
    return ["".join(grid.get((x, y), " ") for x in range(w)) for y in range(h)]


@dataclass(frozen=True)
class MemoryV3:
    """A complete ``memory`` program: unrolled router over v2's storage tiles."""

    n: int
    ops: int
    rows: tuple[str, ...]
    width: int
    height: int
    debug: DebugMap | None = field(default=None, compare=False, repr=False)
    in_cell: tuple[int, int] | None = field(default=None, compare=False)
    out_cell: tuple[int, int] | None = field(default=None, compare=False)

    @property
    def footprint(self) -> int:
        return max(self.width, self.height) ** 2

    def source(self) -> str:
        return "\n".join(self.rows)


def _auto_per_row(n: int, ops: int) -> int:
    """The widest snake that does not make the router the longer side.

    Footprint is ``max(w, h) ** 2`` and the decoder room's ``3n`` rows already set
    the height, so router *width* is free right up to the point where it overtakes
    them — and a wider snake is a faster one, because the four-tick turn-around
    between bands is amortised over ``per_row`` operations. Measured at n=100:
    ``per_row`` 8/12/20/25 gives 11.38/11.22/11.08/11.04 ticks per op at an
    identical footprint, and 30 buys 0.02 ticks for 22% more area.
    """
    best, best_key = 1, None
    for per_row in range(1, 65):
        built = build_v3(n, ops=ops, per_row=per_row, io=True, per_row_auto=False)
        key = (built.footprint, -per_row)
        if best_key is None or key < best_key:
            best, best_key = per_row, key
    return best


def build_v3(
    n: int,
    *,
    ops: int = 500,
    per_row: int | None = None,
    io: bool = True,
    per_row_auto: bool = True,
) -> MemoryV3:
    """Router, decoder room, cell room, collector -- v2's four rooms, v3's router.

    ``ops`` is how many operations the router unrolls. The problem admits at most
    1000 input tokens, so 500 covers every stream that can be all-READ; past that
    the loop simply comes round again.

    ``per_row`` is the snake's width in blocks; left at ``None`` it is chosen by
    :func:`_auto_per_row` — the widest snake the footprint does not notice.
    """
    if per_row is None:
        per_row = _auto_per_row(n, ops) if per_row_auto else 12
    router = router_rows(ops, per_row=per_row)
    dec_body, main_rows = band_room(n, DECODER_TILE, increment=True)
    cell_body, cell_mains = band_room(n, CELL_TILE, increment=False)
    assert main_rows == cell_mains, "both rooms must band identically"

    router_iw = max(len(r) for r in router)
    dec_iw = max(len(r) for r in dec_body)
    cell_iw = max(len(r) for r in cell_body)
    coll_rows, _ = collector_rows(1)
    coll_iw = max(len(r) for r in coll_rows)
    span_h = max(len(dec_body), len(router))

    rox, roy = 6, 6
    dx = rox + router_iw + 4
    cx = dx + dec_iw + 4
    cox = cx + cell_iw + 4
    grid = Circuit(cox + coll_iw + 6, roy + span_h + 8)

    def spanned(body: Sequence[str], iw: int) -> list[str]:
        out = [r.ljust(iw) for r in body]
        return out + [" " * iw] * (span_h - len(out))

    _room(grid, rox, roy, spanned(router, router_iw))
    _room(grid, dx, roy, spanned(dec_body, dec_iw))
    _room(grid, cx, roy, spanned(cell_body, cell_iw))
    _room(grid, cox, roy, spanned(coll_rows, coll_iw))

    # The stream arrives on the router's **west** wall. The router owns exactly one
    # incoming pipe, so every `r` in it binds there wherever the man is standing --
    # which is the property that lets the blocks be laid out purely for speed.
    in_y, out_x = roy, cox + 2
    if io:
        _io_room(grid, rox - 5, in_y, "I")
        draw_pipe(grid, [(x, in_y) for x in range(rox - 3, rox)])
        _io_room(grid, out_x, roy - 5, "O")
        draw_pipe(grid, [(out_x, roy - 2), (out_x, roy - 3), (out_x, roy - 4)])
        stubs: tuple[tuple[int, int] | None, tuple[int, int] | None] = (None, None)
    else:
        draw_pipe(grid, [(x, in_y) for x in range(rox - 3, rox)])
        draw_pipe(grid, [(out_x, y) for y in range(roy - 2, -1, -1)])
        stubs = ((rox - 3, in_y), (out_x, 0))

    for main in main_rows:
        y = roy + main
        draw_pipe(grid, [(x, y) for x in range(rox + router_iw + 1, dx)])
        draw_pipe(grid, [(x, y) for x in range(dx + dec_iw + 1, cx)])
        draw_pipe(grid, [(x, y) for x in range(cx + cell_iw + 1, cox)])

    dbg = DebugMap(f"man-memory v3, n={n}: v2 storage, router unrolled {ops} deep")
    dbg.region(
        "input",
        rox - 6,
        in_y - 1,
        3,
        3,
        note="the problem's own stream: `0 addr` (READ) / `1 addr value` (WRITE)",
        color=C_IO,
    )
    dbg.region(
        "unrolled router",
        rox,
        roy,
        router_iw,
        len(router),
        note=(
            f"{ops} copies of one ten-column block, snaking. v2 ran the same eight "
            "glyphs round a 17-cell loop and spent 9 of those ticks walking back to "
            "the top; here a block's last cell is the next block's first, so the walk "
            "home is paid once for the whole stream instead of once per operation. "
            "The bus it drives is v2's, unchanged: addr, op, value-or-dummy."
        ),
        color=C_MID,
    )
    dbg.region(
        "decoder room",
        dx,
        roy,
        dec_iw,
        len(dec_body),
        note=(
            f"unchanged from v2: {n} decoders, three rows each, `B` the address for "
            "life. Its not-mine lap is 8 ticks of which 5 are work -- there is no "
            "slack worth spending area on, which is why v3 leaves it alone."
        ),
        color=C_MID,
    )
    dbg.region(
        "cell room",
        cx,
        roy,
        cell_iw,
        len(cell_body),
        note=f"unchanged from v2: {n} values, each in a man's off hand.",
        color=C_STORE,
    )
    dbg.region(
        "collector",
        cox,
        roy,
        coll_iw,
        len(coll_rows),
        note="`R` takes from any incoming pipe, so an answer needs no addressing.",
        color=C_ANS,
    )
    for j, main in enumerate(main_rows):
        dbg.lane(
            f"broadcast -> decoder {j}",
            [(rox + router_iw + 1, roy + main), (dx - 1, roy + main)],
            kind="pipe",
            expect="addr, op, value -- always three, always all n pipes",
            color=C_CMD,
        )

    out = [row.rstrip() for row in grid.rows()]
    while out and not out[-1]:
        out.pop()
    return MemoryV3(
        n=n,
        ops=ops,
        rows=tuple(out),
        width=max(len(r) for r in out),
        height=len(out),
        debug=dbg,
        in_cell=stubs[0],
        out_cell=stubs[1],
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--cells", type=int, default=100, metavar="N")
    ap.add_argument("--ops", type=int, default=500, help="operations to unroll")
    ap.add_argument("--per-row", type=int, default=12, help="blocks per snake row")
    ap.add_argument("--man", type=Path)
    ap.add_argument("--html", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)

    built = build_v3(args.cells, ops=args.ops, per_row=args.per_row)
    assert built.debug is not None
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


def build_v3_grid(cols: int, rows: int, *, ops: int = 500, per_row: int | None = None):
    """The ``cols x rows`` grid driven by the unrolled router.

    The grid is the shape the graded solution actually used, and its igniters walk
    in parallel, so its fixed cost is one column's walk instead of ``n``. Its
    *marginal* cost was the same 16.79 ticks as the tall column, for the same
    reason -- one router man, one loop -- so it is the shape v3 helps most.

    ``per_row`` defaults to the widest snake the strip can hold: the strip spans
    the columns and a router wider than that has nowhere to go.
    """
    from .memory_men_grid import build_grid

    if cols == 1:
        # `build_grid` delegates one column to `build_addr`, which has no strip to
        # hold a router. `build_v3` *is* that memory with the unrolled one.
        return build_v3(rows, ops=ops, per_row=per_row)
    if per_row is not None:
        return build_grid(cols, rows, router=router_rows(ops, per_row=per_row))
    for candidate in range(64, 0, -1):
        try:
            return build_grid(cols, rows, router=router_rows(ops, per_row=candidate))
        except Exception:  # noqa: BLE001 - the strip is simply too narrow; try again
            continue
    raise ValueError(f"no snake width fits a {cols}x{rows} strip")


# ── the machine-facing block (lm1.machine STORE tier "men-v3") ───────────────
@dataclass(frozen=True)
class V3Store:
    """Same shape as ``memory_men_store.MenStore`` plus a pipe inventory, so
    ``lm1.machine`` can place it as a STORE tier and still verify the grid's
    total pipe count (its banded bus is ``3 * len(main_rows)`` internal pipes;
    the in/out stubs merge with the machine's own request and response runs)."""

    cells: dict[tuple[int, int], str]
    width: int
    height: int
    in_cell: tuple[int, int]
    out_cell: tuple[int, int]
    pipes: int


def v3_store_block(n: int, *, ops: int = 500) -> V3Store:
    """:func:`build_v3` with I/O stubs instead of rooms, as a placeable block.

    The wire is the ``memory`` problem's protocol verbatim (``0 addr`` /
    ``1 addr value`` in, one word out per READ), which is exactly what the
    machine's standard adapter emits — so this is a drop-in for the other
    tiers at ~11 ticks an access against the grid store's ~31, paid for in
    area (the router unrolls ``ops`` blocks and simply wraps for a longer
    stream, so ``ops`` is a speed knob, not a capacity).
    """
    v3 = build_v3(n, ops=ops, io=False)
    assert v3.in_cell is not None and v3.out_cell is not None
    cells = {(x, y): ch for y, row in enumerate(v3.rows) for x, ch in enumerate(row) if ch != " "}
    _dec, main_rows = band_room(n, DECODER_TILE, increment=True)
    # ``build_v3``'s stub draw stops one short of the row it names, so the
    # answer pipe's real topmost cell is one row below ``out_cell``; a caller
    # extending the pipe from the named cell would leave a one-cell gap the
    # engine reads as two dangling pipes.
    ox, oy = v3.out_cell
    while (ox, oy) not in cells and oy < v3.height:
        oy += 1
    return V3Store(
        cells=cells,
        width=v3.width,
        height=v3.height,
        in_cell=v3.in_cell,
        out_cell=(ox, oy),
        pipes=3 * len(main_rows),
    )


def v3_store_grid_block(
    cols: int, rows: int, *, ops: int = 500, request_west: bool = False
) -> V3Store:
    """:func:`build_v3_grid` as a placeable block: the multi-column store.

    The wire and the addressing are :func:`v3_store_block`'s exactly — column
    ``j`` owns global addresses ``[j*rows, (j+1)*rows)``, so the shape is pure
    geometry: ``cols x rows`` trades the one-column block's height for width at a
    near-identical ticks-per-op (the sweep behind 709c62e measured grid shapes at
    ~11.2-11.6 t/op against the strip's ~11.3). The outlet is normalised onto the
    answer riser's real topmost pipe cell, same as the one-column block.

    ``ops`` is honoured exactly: the router unrolls ``ops`` blocks (``ops=1`` is
    the single looping block — v2's footprint, the walk home paid every lap) and
    the snake is folded to whatever width the strip can hold.

    ``request_west`` moves the request's touch point from the roof stub to the
    router strip's south-west corner; see :func:`memory_men_grid.build_grid`. It is
    a touch point, not a shape: the block's cells are otherwise identical bar the
    two stub cells the caller no longer has to reach over.
    """
    if cols == 1:
        if request_west:
            raise ValueError(
                "request_west is the grid form's touch point; the one-column block is "
                "v3_store_block, whose router is entered on its own west wall already"
            )
        return v3_store_block(rows, ops=ops)
    from .memory_men_grid import build_grid

    g = None
    for per_row in range(min(max(ops, 1), 64), 0, -1):
        try:
            g = build_grid(
                cols,
                rows,
                router=router_rows(ops, per_row=per_row),
                io=False,
                request_west=request_west,
            )
            break
        except Exception:  # noqa: BLE001 - the strip is simply too narrow; fold deeper
            continue
    if g is None:
        raise ValueError(f"no snake width fits a {cols}x{rows} strip")
    assert g.in_cell is not None and g.out_cell is not None
    cells = {(x, y): ch for y, row in enumerate(g.grid_rows) for x, ch in enumerate(row) if ch != " "}
    # Same off-by-one normalisation as the one-column block: the stub draw stops
    # one short of the row it names, so the riser's real topmost pipe cell is one
    # row below the named ``out_cell``.
    ox, oy = g.out_cell
    while (ox, oy) not in cells and oy < g.height:
        oy += 1
    # Per column: two bus pipes per band (repeater->decoder, decoder->cell) plus
    # its feed (router strip -> repeater) and its one answer pipe; plus the
    # collector -> riser stub. The in/out stubs merge with the machine's runs.
    return V3Store(
        cells=cells,
        width=g.width,
        height=g.height,
        in_cell=g.in_cell,
        out_cell=(ox, oy),
        pipes=cols * (2 * rows + 2) + 1,
    )
