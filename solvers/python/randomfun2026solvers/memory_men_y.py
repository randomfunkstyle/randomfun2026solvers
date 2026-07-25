#!/usr/bin/env python3
"""The two-layer man-memory with a ``Y``-split bank selector for its top level.

``memory_men.router_rows`` selects a child by *walking*: ``d`` bypasses a lane
while ``BP > 0``, which costs 14 ticks per lane skipped. ``Y`` replaces the walk
with a **test**: the parent copies ``addr`` into both hands, splits, and each
child computes ``addr - bank_size`` and decides for itself whether the request is
in its range. Exactly one child survives; the other halts.

That is the gadget ``memory_banked.build_bucket_probe`` proved on the engine
(``ARCH.md`` §8.2 — "memory: high; bank selector implemented"), wired up for real
here: instead of both children sending an address to one output, each child owns
its own bank's pipe, relays ``local op [value]`` into it, and **returns to the top
of the loop** rather than halting, so the room keeps exactly one man per request
forever.

Three things about ``Y`` shape this layout, all of them from SPEC:

* children are born one cell to the parent's **right and left** relative to its
  entry heading, facing away — so a ``Y`` entered heading east throws one child
  north and one south, and a birth cell on a wall is a fatal ``wall``;
* they carry identical ``A``, ``B`` and ``BP``, so the *predicate is the same in
  both children* and only the code they stand on differs. Each child therefore
  recomputes the delta rather than trusting the parent to have done it;
* there is no join. The two lanes never meet: the survivor walks home alone, and
  the loser reaches an ``H``.

The one real subtlety is ``X``'s three-way turn against a two-way question. "Is
``addr - bank`` non-negative" merges *zero* and *positive*, which leave ``X`` on
different headings, so the high lane needs a turn glyph where those two paths
rejoin — that is what the ``>`` at the end of the test row is for.
"""

from __future__ import annotations

from dataclasses import dataclass

from .circuit import Circuit
from .memory_men import (
    CELL_H,
    CELL_IN_COL,
    CELL_OUT_COL,
    HEAD_ROWS,
    PITCH,
    _io_room,
    _room,
    cell_at,
    collector_rows,
    draw_pipe,
    router_rows,
)

__all__ = ["YMenStore", "YSelector", "y_men_block", "y_selector_rows", "build_tree_y"]


@dataclass(frozen=True)
class YSelector:
    """A rendered ``Y`` bank selector: interior rows plus its two bank ports."""

    rows: tuple[str, ...]
    #: interior column of the north-wall port (high bank) and south-wall port (low)
    high_col: int
    low_col: int
    #: interior row where the parent's pipe should arrive on the west wall
    in_row: int


@dataclass(frozen=True)
class YMenStore:
    """A two-bank ``Y`` memory packaged for the LM-1 STORE seam."""

    cells: dict[tuple[int, int], str]
    width: int
    height: int
    in_cell: tuple[int, int]
    out_cell: tuple[int, int]
    pipes: int
    capacity: int


def y_selector_rows(bank: int) -> YSelector:
    """A one-``Y`` selector over two banks of ``bank`` addresses each.

    Receives ``addr op [value]`` from the parent and relays ``local op [value]``
    into the bank that owns ``addr``, where ``local = addr`` for the low bank and
    ``addr - bank`` for the high one.

    Both children's ``r`` glyphs are unambiguous because the room's only incoming
    pipe is the parent's — the same property that makes the walking router work,
    and the reason a WRITE's value can be fetched deep inside a lane.
    """
    if bank < 1:
        raise ValueError("bank size must be positive")
    lit = str(bank) if bank < 10 else f"`{bank}`"
    #: column of the test's `X`. Everything else is placed relative to it, so a
    #: multi-digit bank size (a backticked literal) just slides the lanes east.
    xt = 4 + len(lit) + 2
    iw, ih = xt + 9, 11
    rows = [[" "] * iw for _ in range(ih)]

    def put(x: int, y: int, glyph: str) -> None:
        if rows[y][x] not in (" ", glyph):
            raise ValueError(f"selector collision at ({x},{y}): {rows[y][x]!r} vs {glyph!r}")
        rows[y][x] = glyph

    def run(x: int, y: int, text: str, dx: int = 1, dy: int = 0) -> None:
        for glyph in text:
            put(x, y, glyph)
            x, y = x + dx, y + dy

    # ── the parent: A = B = addr, then split ─────────────────────────────────
    run(0, 4, ">rMY")  # Y at column 3: children at (3,3) north and (3,5) south
    put(1, 0, "@")  # spawn walks the high return corridor in, touching nothing

    # ── high bank: born at (3,3) facing north, birth cell a nop ──────────────
    put(3, 2, ">")
    run(4, 2, f"{lit}W-X")  # A = addr - bank, B = bank
    put(xt, 1, "H")  # A < 0 -> counter-clockwise: not mine
    put(xt, 3, ">")  # A > 0 -> clockwise, south, then east...
    put(xt + 2, 2, "v")  # ...and A == 0 straight on, then south...
    put(xt + 2, 3, ">")  # ...rejoin here: one turn glyph unifies both headings
    # Climb over the test row to row 1: the relay has to run somewhere its WRITE
    # tail can turn south into free cells, and rows 2-3 are the test's.
    put(xt + 3, 3, "^")
    put(xt + 3, 1, ">")
    run(xt + 4, 1, "srsX")  # local, op, branch on op
    put(xt + 7, 2, "r")  # WRITE: value off the parent pipe...
    put(xt + 7, 3, "s")  # ...and on into the high bank
    put(xt + 7, 4, ">")
    put(xt + 8, 4, "^")
    put(xt + 8, 1, "^")  # READ arrives heading east, WRITE heading north: both climb
    put(xt + 8, 0, "<")
    put(0, 0, "v")  # home: down the return column into the `>` on row 4

    # ── low bank: born at (3,5) facing south ─────────────────────────────────
    put(3, 6, ">")
    run(4, 6, f"{lit}W-X")
    put(xt, 5, ">")  # A < 0 -> counter-clockwise: mine
    put(xt + 1, 5, "+")  # local = addr again; `-` left B holding bank
    put(xt + 1, 6, "H")  # A == 0 -> not mine
    put(xt, 7, "H")  # A > 0 -> not mine
    put(xt + 2, 5, "v")
    put(xt + 2, 7, ">")
    run(xt + 3, 7, "srsX")  # running EAST, so a WRITE turns south, away from high
    put(xt + 6, 8, "r")
    put(xt + 6, 9, "s")
    put(xt + 6, 10, "<")
    put(xt + 7, 7, "v")
    put(xt + 7, 10, "<")
    put(0, 10, "^")

    return YSelector(
        rows=tuple("".join(r) for r in rows),
        high_col=xt + 5,
        low_col=xt + 3,
        in_row=4,
    )


def build_tree_y(bank: int) -> tuple[tuple[str, ...], int, int]:
    """A complete ``memory`` program: head, ``Y`` selector, two banks of cells.

    Returns the grid rows plus its width and height, with ``n = 2 * bank``.

    Each bank's command pipe leaves the selector on the wall its child's ``s`` is
    nearest — north for high, south for low — and then takes the long way round to
    its leaf router's *east* wall. Long is free: a router only ever writes into
    that pipe, so pipe length costs latency for one request, never throughput, and
    entering from the east keeps the pipe clear of the collector chain.
    """
    sel = y_selector_rows(bank)
    leaf_rows, leaf_ports = router_rows(bank, pitch=PITCH)
    leaf_iw = max(len(r) for r in leaf_rows)
    coll_rows, _ = collector_rows(1)
    sel_iw = max(len(r) for r in sel.rows)

    bx, hy, sy = 4, 6, 13
    sel_h = len(sel.rows)
    sel_x = bx + 2
    east = bx + max(leaf_iw, sel_iw) + 3
    grid = Circuit(east + 10, sy + sel_h + 2 * 26 + 16)

    _io_room(grid, bx + 1, 1, "I")
    _room(grid, bx, hy, list(HEAD_ROWS))
    _io_room(grid, bx + 15, hy + 1, "O")
    draw_pipe(grid, [(bx + 1, 3), (bx + 1, 4), (bx + 1, hy - 1)])
    draw_pipe(grid, [(bx + 12, hy + 1), (bx + 13, hy + 1), (bx + 14, hy + 1)])
    _room(grid, sel_x, sy, list(sel.rows))

    # head -> selector, round the west side so it never enters the selector's box
    turn = sy - 2
    draw_pipe(
        grid,
        [(bx + 5, y) for y in range(hy + 4, turn + 1)]
        + [(x, turn) for x in range(bx + 4, sel_x - 4, -1)]
        + [(sel_x - 3, y) for y in range(turn + 1, sy + sel.in_row + 1)]
        + [(sel_x - 2, sy + sel.in_row), (sel_x - 1, sy + sel.in_row)],
    )

    prev_coll: tuple[int, int] | None = None
    block_y = sy + sel_h + 5
    # The two bank pipes leave the selector on opposite walls and must reach two
    # blocks stacked below it. On one side of the grid they always cross, so they
    # take opposite sides: high goes round the east, low round the west, and the
    # collector chain keeps to the east beyond everything the high pipe touches.
    for bank_id in (1, 0):
        by = block_y
        _room(grid, bx, by, leaf_rows)
        if bank_id == 1:
            px, start, ex = sel_x + sel.high_col, sy - 3, east + 2
            path = (
                # two cells straight out of the wall first: the opening arrowhead
                # has to point away from the source room, not along the turn.
                [(px, start + 1), (px, start)]
                + [(x, start) for x in range(px + 1, ex + 1)]
                + [(ex, y) for y in range(start + 1, by + 1)]
                # ...ending ON the router's east wall: a pipe's last arrowhead
                # must have the destination border as its forward cell.
                + [(x, by) for x in range(ex - 1, bx + leaf_iw - 1, -1)]
            )
        else:
            px, start, wx = sel_x + sel.low_col, sy + sel_h + 2, bx - 3
            path = (
                [(px, start - 1), (px, start)]
                + [(x, start) for x in range(px - 1, wx - 1, -1)]
                + [(wx, y) for y in range(start + 1, by + 1)]
                + [(x, by) for x in range(wx + 1, bx)]
            )
        draw_pipe(grid, path)
        for i in range(bank):
            cell_x = bx + leaf_ports[i] - 1
            cell_y = by + len(leaf_rows) + 4
            cell_at(grid, cell_x, cell_y)
            draw_pipe(grid, [(cell_x, cell_y - 3), (cell_x, cell_y - 2), (cell_x, cell_y - 1)])
            out_x = cell_x + CELL_OUT_COL - CELL_IN_COL
            draw_pipe(grid, [(out_x, cell_y + 5), (out_x, cell_y + 6), (out_x, cell_y + 7)])
        cy = by + len(leaf_rows) + 4 + CELL_H + 2
        _room(grid, bx, cy, [r.ljust(leaf_iw) for r in coll_rows])
        if prev_coll is not None:
            ppx, ppy = prev_coll
            cex = east + 4
            draw_pipe(
                grid,
                [(x, ppy) for x in range(ppx, cex + 1)]
                + [(cex, y) for y in range(ppy + 1, cy)]
                + [(x, cy) for x in range(cex, bx + leaf_iw - 1, -1)],
            )
        prev_coll = (bx + leaf_iw + 1, cy + 1)
        block_y = cy + 6

    ppx, ppy = prev_coll
    cex = east + 6
    draw_pipe(
        grid,
        [(x, ppy) for x in range(ppx, cex + 1)]
        + [(cex, y) for y in range(ppy - 1, 3, -1)]
        + [(x, 4) for x in range(cex - 1, bx + 8 - 1, -1)]
        + [(bx + 8, hy - 1)],
    )

    rows = [row.rstrip() for row in grid.rows()]
    while rows and not rows[-1]:
        rows.pop()
    return tuple(rows), max(len(r) for r in rows), len(rows)


def y_men_block(n: int) -> YMenStore:
    """Build a direct two-bank man-memory for the LM-1 STORE seam.

    Odd slot counts round up to two equal banks; the last cell is unreachable.
    The CPU adapter emits ``addr op [value]`` for this backend, so its selector
    needs neither the standalone memory program's I/O rooms nor its ordering
    head. Loads already block the CPU until their response arrives.
    """
    if n < 1:
        raise ValueError("a Y STORE block needs at least one cell")
    bank = (n + 1) // 2
    sel = y_selector_rows(bank)
    leaf_rows, leaf_ports = router_rows(bank, pitch=PITCH)
    leaf_iw = max(len(r) for r in leaf_rows)
    coll_rows, _ = collector_rows(1)
    sel_iw = max(len(r) for r in sel.rows)

    bx, sy = 8, 4
    sel_h = len(sel.rows)
    sel_x = bx + 2
    east = bx + max(leaf_iw, sel_iw) + 3
    response_x = east + 6
    grid = Circuit(response_x + 4, sy + sel_h + 2 * 26 + 16)

    _room(grid, sel_x, sy, list(sel.rows))

    # Adapter -> selector. The exposed stub is entered heading east.
    request_y = sy + sel.in_row
    request = [(x, request_y) for x in range(3, sel_x - 1)]
    draw_pipe(grid, request + [(sel_x - 1, request_y)])

    prev_coll: tuple[int, int] | None = None
    block_y = sy + sel_h + 5
    for bank_id in (1, 0):
        by = block_y
        _room(grid, bx, by, leaf_rows)
        if bank_id == 1:
            px, start, ex = sel_x + sel.high_col, sy - 3, east + 2
            path = (
                [(px, start + 1), (px, start)]
                + [(x, start) for x in range(px + 1, ex + 1)]
                + [(ex, y) for y in range(start + 1, by + 1)]
                + [(x, by) for x in range(ex - 1, bx + leaf_iw - 1, -1)]
            )
        else:
            px, start, wx = sel_x + sel.low_col, sy + sel_h + 2, bx - 3
            path = (
                [(px, start - 1), (px, start)]
                + [(x, start) for x in range(px - 1, wx - 1, -1)]
                + [(wx, y) for y in range(start + 1, by + 1)]
                + [(x, by) for x in range(wx + 1, bx)]
            )
        draw_pipe(grid, path)
        for i in range(bank):
            cell_x = bx + leaf_ports[i] - 1
            cell_y = by + len(leaf_rows) + 4
            cell_at(grid, cell_x, cell_y)
            draw_pipe(grid, [(cell_x, cell_y - 3), (cell_x, cell_y - 2), (cell_x, cell_y - 1)])
            out_x = cell_x + CELL_OUT_COL - CELL_IN_COL
            draw_pipe(grid, [(out_x, cell_y + 5), (out_x, cell_y + 6), (out_x, cell_y + 7)])
        cy = by + len(leaf_rows) + 4 + CELL_H + 2
        _room(grid, bx, cy, [r.ljust(leaf_iw) for r in coll_rows])
        if prev_coll is not None:
            ppx, ppy = prev_coll
            cex = east + 4
            draw_pipe(
                grid,
                [(x, ppy) for x in range(ppx, cex + 1)]
                + [(cex, y) for y in range(ppy + 1, cy)]
                + [(x, cy) for x in range(cex, bx + leaf_iw - 1, -1)],
            )
        prev_coll = (bx + leaf_iw + 1, cy + 1)
        block_y = cy + 6

    # Last collector -> CPU response stub, around the far east of every bank.
    ppx, ppy = prev_coll
    draw_pipe(
        grid,
        [(x, ppy) for x in range(ppx, response_x + 1)]
        + [(response_x, y) for y in range(ppy - 1, 0, -1)]
        + [(response_x, 0)],
    )

    cells = {point: glyph for point, glyph in grid.cell.items() if glyph != " "}
    return YMenStore(
        cells=cells,
        width=max(x for x, _ in cells) + 1,
        height=max(y for _, y in cells) + 1,
        in_cell=(request[0][0], request[0][1]),
        out_cell=(response_x, 1),
        # selector->banks (2), collector chain (1), plus one command and one
        # answer pipe for every physical cell.
        pipes=4 * bank + 3,
        capacity=2 * bank,
    )
