#!/usr/bin/env python3
"""The grid man-memory with **both ports on one wall** — a tape-shaped slot.

``memory_men_grid.build_grid`` puts the router strip on top and the collector
strip on the bottom, and says why: broadcast pipes then never cross answer pipes,
and every column's answer travels exactly the same distance, so a later op's
answer cannot overtake an earlier one.  Nothing here changes that.  The internals
are ``build_grid``'s internals, cell for cell.

What changes is only where the answer *comes out*.  A client like the LM-1 CPU
(``lm1.machine._assemble``) drops a STORE block into a slot and wires two pipes to
it, and with the request arriving at the top and the answer leaving at the bottom
it has to route one of them around the whole block.  So:

    request in  -->  [ router strip      ]        <- top
    answer out  <-+  [ rep | dec | cell ] x M
                  |  [ rep | dec | cell ]
      one pipe up |  [  collector strip  ]        <- bottom
      the side    +----- [ relay ]  >@rv  <-------+
                                    ^.s<

**Why a room and not just a longer pipe.**  A pipe connects two *rooms*; it cannot
feed another pipe.  The collector strip's answer pipe therefore has to *terminate*
somewhere, and that somewhere is the relay: an eight-cell cycle with exactly one
``r`` and exactly one ``s`` (``lllm_layout.RELAY``).  It needs no ``R``/``S`` and
no addressing, because the collector has already merged the columns — "exactly one
decoder speaks per operation ... the strip's ``R`` takes it from there".  The relay
re-sends into a *fresh* pipe, and that pipe is one straight climb up the west
margin to wherever the caller wants it: :func:`grid_side_block` takes ``out_row``.
One long pipe, not a chain of relays — a value moves one cell a tick either way,
and every extra relay would add a room and its man's cycle on top.

**The ordering assumption, stated once.**  ``R`` takes from whichever pipe is
ready, so the collector only keeps operation order while answers cannot overtake
each other.  ``build_grid`` guarantees that geometrically (equal answer paths).
This variant inherits that *inside* the block and then adds one shared tail — the
relay and the climb — which every answer traverses, so it cannot reorder anything
either.  It is nonetheless intended for a **one-request-in-flight client**: the
LM-1 CPU blocks on every memory read, so at most one answer exists at a time.  It
is not a drop-in for the streaming ``memory`` problem at high offered load unless
you re-check the pipeline; the standalone build below does pass ``memory``-style
random streams, because the router's lap is longer than the tail.

**Planarity, and why the answer exits *below* the request.**  The climb runs up the
west margin, so it separates the west wall from the rooms at every row it crosses.
The request enters the west wall and runs east.  They therefore cannot cross: the
climb has to stop *before* the request's row, which means the answer port sits
below the request port, never above it.  ``out_row`` is validated for that.

Both ports are on the **west wall**: the request enters at ``in_cell`` heading
east, the answer leaves at ``out_cell`` heading **west** (``build_grid``'s answer
leaves heading south, and ``memory_men_grid_store``'s heading north up the *east*
side).  A caller that wants a north-facing stub can extend the last cell itself;
what it can never get is an answer above the request, for the reason above.

Measured on the engine, one read judged against ten (see :func:`side_grid_ticks`
and the test module).  Against ``memory_men_grid_store``'s east-climbing block
under an identical wrapper, the turnaround costs a flat **ten ticks** at every
shape — and three columns *less* width, because the climb reuses the west margin
the request's corner already needed:

    3x14 =  42 cells    83 x  73    164 first read, 15 marginal   (east: 164 - 10)
    4x25 = 100          110 x 106   252                           (east: 242)
    6x20 = 120          170 x  91   214                           (east: 204)
    7x61 = 427          198 x 214   542                           (east: 532)

The 427-slot tape this replaces costs ``8.0 * N`` = 3,416 ticks a read.
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
    _corners,
    _io_room,
    _room,
    collector_rows,
    draw_pipe,
)
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

__all__ = [
    "RELAY",
    "SideGrid",
    "build_side_grid",
    "grid_side_block",
    "side_grid_ticks",
]

#: The turnaround room: one ``r``, one ``s``, an eight-cell cycle.  Verbatim from
#: ``lllm_layout.RELAY`` (its ``.`` filler is a blank here, which is what the
#: engine wants).  The spawn may not sit in a corner: ``@`` is a nop, so a man who
#: entered a corner heading north would walk straight out through the wall.
RELAY: tuple[str, ...] = (
    ">@rv",
    "^ s<",
)
_RELAY_W = max(len(r) for r in RELAY)

#: Rows above the router strip: the request's corner turns in them.
_STUB_ROWS = 4
#: Columns west of the rooms: the answer's climb (col 0) and its stub row.
_STUB_COLS = 3
#: The request's row on the west wall, i.e. ``in_cell``'s row.
_IN_ROW = 1
#: Rows from the collector strip's first interior row to the relay's.
#: Two cells of pipe plus both walls — the minimum a pipe can span.
_RELAY_DROP = 6


@dataclass(frozen=True)
class _Plan:
    """Every coordinate the block is made of, in block-local cells."""

    cols: int
    rows: int
    base: int
    digits: int
    dec_w: int
    col_w: int
    col_h: int
    init_h: int
    strip_w: int
    x0: int
    router_y: int
    col_y: int
    coll_y: int
    relay_x: int
    relay_y: int
    out_row: int
    width: int
    height: int

    @property
    def feed_x(self) -> int:
        """Column the request dives down, over the router strip's ``U``."""
        return self.x0 + 1

    def column_x(self, j: int) -> dict[str, int]:
        b = self.x0 + j * (self.col_w + _COL_GAP)
        rep = b
        dec = rep + _REP_W + _GAP
        cell = dec + self.dec_w + _GAP
        return {"rep": rep, "dec": dec, "cell": cell, "end": cell + _CELL_W}


def _plan(cols: int, rows: int, base: int, out_row: int | None) -> _Plan:
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

    router_y = _STUB_ROWS
    col_y = router_y + len(ROUTER_ROWS) + 1 + _GAP - 1
    coll_y = col_y + col_h + _GAP
    relay_y = coll_y + _RELAY_DROP

    x0 = _STUB_COLS
    strip_w = cols * col_w + (cols - 1) * _COL_GAP
    # The answer's exit.  Below the request (the climb would otherwise have to
    # cross it) and above the relay (the climb needs at least one cell).
    lo, hi = _IN_ROW + 2, relay_y
    if out_row is None:
        out_row = lo
    if not lo <= out_row <= hi:
        raise ValueError(
            f"out_row must be in {lo}..{hi} for a {cols}x{rows} block: the answer "
            f"climbs the west margin, so it cannot pass the request's row {_IN_ROW}"
        )
    return _Plan(
        cols=cols,
        rows=rows,
        base=base,
        digits=digits,
        dec_w=dec_w,
        col_w=col_w,
        col_h=col_h,
        init_h=init_h,
        strip_w=strip_w,
        x0=x0,
        router_y=router_y,
        col_y=col_y,
        coll_y=coll_y,
        relay_x=x0,
        relay_y=relay_y,
        out_row=out_row,
        width=x0 + strip_w + 2,
        height=relay_y + 3,
    )


def _spanned(body: Sequence[str], iw: int, h: int) -> list[str]:
    out = [r.ljust(iw) for r in body]
    return out + [" " * iw] * (h - len(out))


def _stamp(
    grid: Circuit,
    p: _Plan,
    ox: int,
    oy: int,
    *,
    dbg: DebugMap | None = None,
) -> int:
    """Draw the whole block into ``grid`` with its (0,0) at ``(ox, oy)``.

    Returns the number of pipes drawn, which is what ``analyze`` must agree with:
    a pipe whose first cell does not point away from its room does not fail, it
    simply is not there.
    """
    npipes = 0

    def X(x: int) -> int:
        return ox + x

    def Y(y: int) -> int:
        return oy + y

    router_h = len(ROUTER_ROWS)
    _room(grid, X(p.x0), Y(p.router_y), [r.ljust(p.strip_w) for r in ROUTER_ROWS])
    _room(grid, X(p.x0), Y(p.coll_y), [r.ljust(p.strip_w) for r in collector_rows(1)[0]])
    _room(grid, X(p.relay_x), Y(p.relay_y), list(RELAY))

    # ── the request: east along its own row, then south into the strip ───────
    # The strip's `U` sits at interior column 1 and turns *away* from the wall it
    # read through, so the feed has to arrive on the north wall — hence the corner.
    req = [(X(x), Y(_IN_ROW)) for x in range(0, p.feed_x)] + [
        (X(p.feed_x), Y(y)) for y in range(_IN_ROW, p.router_y)
    ]
    draw_pipe(grid, req)
    npipes += 1

    # ── the collector's answer -> the relay, straight down ───────────────────
    drop_x = p.x0 + 2  # under the collector's `s`, over the relay's `r`
    drop = [(X(drop_x), Y(y)) for y in range(p.coll_y + 3, p.relay_y)]
    draw_pipe(grid, drop)
    npipes += 1

    # ── the relay's answer -> the west wall, one pipe up the margin ──────────
    # Out of the relay's west wall, one cell further west, then straight north to
    # `out_row` and out.  Every bend has a clear cell behind it: a bend that hugs
    # a wall is read as a *new* pipe starting at that room, and the pipe it was
    # part of silently disappears.
    climb = [(X(p.relay_x - 2), Y(p.relay_y + 1)), (X(0), Y(p.relay_y + 1))] + [
        (X(0), Y(y)) for y in range(p.relay_y, p.out_row - 1, -1)
    ]
    draw_pipe(grid, climb)
    grid.set(X(0), Y(p.out_row), "<")  # the stub the client extends
    npipes += 1

    # ── the columns, exactly `build_grid`'s ──────────────────────────────────
    for j in range(p.cols):
        cx = p.column_x(j)
        cbase = p.base + j * p.rows
        dec_body, mains = band_room(
            p.rows, DECODER_TILE, increment=True, base=cbase, base_digits=p.digits
        )
        cell_body, cell_mains = band_room(p.rows, CELL_TILE, increment=False, init_h=p.init_h)
        assert mains == cell_mains, "both rooms of a column must band identically"

        _room(grid, X(cx["rep"]), Y(p.col_y), _spanned(REPEATER, _REP_W, p.col_h))
        _room(grid, X(cx["dec"]), Y(p.col_y), _spanned(dec_body, p.dec_w, p.col_h))
        _room(grid, X(cx["cell"]), Y(p.col_y), _spanned(cell_body, _CELL_W, p.col_h))

        fx = cx["rep"] + 2
        draw_pipe(grid, [(X(fx), Y(y)) for y in range(p.router_y + router_h + 1, p.col_y)])
        npipes += 1
        ax = cx["cell"] + 1
        draw_pipe(grid, [(X(ax), Y(y)) for y in range(p.col_y + p.col_h + 1, p.coll_y)])
        npipes += 1
        for main in mains:
            y = Y(p.col_y + main)
            draw_pipe(grid, [(X(x), y) for x in range(cx["rep"] + _REP_W + 1, cx["dec"])])
            draw_pipe(grid, [(X(x), y) for x in range(cx["dec"] + p.dec_w + 1, cx["cell"])])
            npipes += 2

        if dbg is not None:
            _annotate_column(dbg, p, j, cx, ox, oy, fx, ax)

    if dbg is not None:
        _annotate(dbg, p, ox, oy, req, drop, climb)
    return npipes


def _annotate_column(
    dbg: DebugMap,
    p: _Plan,
    j: int,
    cx: dict[str, int],
    ox: int,
    oy: int,
    fx: int,
    ax: int,
) -> None:
    base = p.base + j * p.rows
    dbg.region(
        f"column {j}: addr {base}-{base + p.rows - 1}",
        ox + cx["rep"],
        oy + p.col_y,
        cx["end"] - cx["rep"],
        p.col_h,
        note=(
            f"repeater, decoders, cells — `build_grid`'s column, untouched. Its igniter "
            f"is handed {base} as a literal and counts up, so these decoders hold global "
            "addresses and the head above them decodes nothing."
        ),
        color=C_STORE if j % 2 else C_MID,
    )
    dbg.lane(
        f"broadcast -> column {j}",
        [
            (ox + fx, oy + p.router_y + len(ROUTER_ROWS) + 1),
            (ox + fx, oy + p.col_y - 1),
        ],
        kind="pipe",
        expect="addr, op, value",
        color=C_CMD,
    )
    dbg.lane(
        f"answer <- column {j}",
        [(ox + ax, oy + p.col_y + p.col_h + 1), (ox + ax, oy + p.coll_y - 1)],
        kind="pipe",
        expect="the stored value, on a selected READ",
        note="same length in every column, which is what keeps answers in order",
        color=C_ANS,
    )


def _annotate(
    dbg: DebugMap,
    p: _Plan,
    ox: int,
    oy: int,
    req: list[tuple[int, int]],
    drop: list[tuple[int, int]],
    climb: list[tuple[int, int]],
) -> None:
    dbg.region(
        "router / broadcaster",
        ox + p.x0,
        oy + p.router_y,
        p.strip_w,
        len(ROUTER_ROWS),
        note=(
            "one op in, three words out to every column at once. `S` is all-or-nothing "
            "across every outgoing pipe, so this room owns the column feeds and nothing "
            "else. Fed on its north wall: `U` turns the man away from the pipe he read "
            "through, and this router's code runs south."
        ),
        color=C_MID,
    )
    dbg.region(
        "collector strip",
        ox + p.x0,
        oy + p.coll_y,
        p.strip_w,
        2,
        note=(
            "`R` takes from ANY incoming pipe — the many-to-one teleport — so the columns "
            "need no addressing on the way back. Still on the bottom, so answer pipes "
            "never cross broadcast pipes and every column's answer is the same length."
        ),
        color=C_COLL,
    )
    dbg.region(
        "relay (turnaround)",
        ox + p.relay_x,
        oy + p.relay_y,
        _RELAY_W,
        len(RELAY),
        note=(
            "one `r`, one `s`, an eight-cell cycle. A pipe joins two *rooms* and cannot "
            "feed another pipe, so the answer has to land in a room before it can climb "
            "back up the side. No `R`/`S` and no addressing: the collector already merged "
            "the columns, so this moves one value from one room to one room."
        ),
        color=C_COLL,
    )
    dbg.lane(
        "request (west wall, heading east)",
        _corners(req),
        kind="pipe",
        expect="`0 addr` / `1 addr value`, one operation at a time",
        color=C_CMD,
    )
    dbg.lane(
        "collector -> relay",
        _corners(drop),
        kind="pipe",
        expect="the merged answer, one word per READ",
        color=C_ANS,
    )
    dbg.lane(
        "the climb: relay -> west wall",
        _corners(climb),
        kind="pipe",
        expect="the answer, arriving `height` ticks later — one tick a cell",
        note=(
            "ONE pipe, not a chain of relays: a value moves one cell a tick either way, "
            "and every extra room would add its man's cycle on top."
        ),
        color=C_ANS,
    )


@dataclass(frozen=True)
class SideGrid:
    """``lm1.machine._Tape``'s shape, plus what a placer needs to know.

    ``cells`` / ``width`` / ``height`` / ``in_cell`` / ``out_cell`` / ``slots`` are
    exactly ``_Tape``'s fields, so a machine builder can take this as it stands.
    ``in_cell`` points **east**, as ``_Tape`` requires.  ``out_cell`` points
    **west**, on the same wall ``out_row - in_row`` rows below — where ``_Tape``'s
    tape leaves *north*, because its answer climbs out of the block's top.  A
    client that wants a north-facing stub bends it in the cell outside the wall
    (``out_dir`` says which way it leaves so it need not guess); what no layout can
    give it is an answer *above* the request, for the planarity reason above.
    """

    cells: dict[tuple[int, int], str]
    width: int
    height: int
    in_cell: tuple[int, int]  # request arrives here heading east
    out_cell: tuple[int, int]  # answer leaves here heading west
    slots: int  # cells, i.e. addresses `base .. base + slots - 1`
    pipes: int  # every pipe the block draws (check with `analyze`)
    cols: int
    rows: int
    base: int
    out_row: int
    #: The direction the answer leaves ``out_cell`` in: west, always.
    out_dir: tuple[int, int] = (-1, 0)
    debug: DebugMap | None = field(default=None, compare=False, repr=False)

    @property
    def low(self) -> int:
        return self.base

    @property
    def high(self) -> int:
        """One past the last address this block answers."""
        return self.base + self.slots

    @property
    def footprint(self) -> int:
        return max(self.width, self.height) ** 2

    def source(self) -> str:
        return "\n".join(
            "".join(self.cells.get((x, y), " ") for x in range(self.width)).rstrip()
            for y in range(self.height)
        )


def side_grid_ticks(cols: int, rows: int) -> int:  # noqa: ARG001 - cols does not enter
    """Measured first-read latency, ``judge`` at one read against ten.

        shape   cells   box        first read   marginal
        3x14       42    83 x  73        164         15
        4x25      100   110 x 106        252         15
        6x20      120   170 x  91        214         15
        7x61      427   198 x 214        542         15

    ``53 + 8 * rows`` fits all four to a tick, and the column count drops out —
    every column's igniter walks at the same time, so only its *length* is paid,
    and the answer's climb is the block's height, which is ``3 * rows`` plus a
    constant.  Wrapped identically, ``memory_men_grid_store``'s east-climbing
    block measures ``43 + 8 * rows`` (154 / 242 / 204 / 532), so the turnaround
    costs a flat **ten ticks** at every size: the relay's eight-cell cycle plus
    its two pipe stubs.  The tape it replaces costs ``8.0 * N`` — 3,416 ticks at
    N=427 against 542 here.
    """
    return 53 + 8 * rows


def grid_side_block(
    cols: int,
    rows: int,
    *,
    base: int = 0,
    out_row: int | None = None,
) -> SideGrid:
    """``cols`` columns of ``rows`` man-cells with both ports on the west wall.

    ``base`` shifts every column's literal, so the block answers the client's own
    slot numbers with no address translation anywhere.  ``out_row`` places the
    answer stub; it defaults to two rows under the request stub and may be pushed
    as far down as the relay (see :func:`_plan` for why it can never go above).
    """
    p = _plan(cols, rows, base, out_row)
    grid = Circuit(p.width + 2, p.height + 2)
    npipes = _stamp(grid, p, 0, 0)
    cells = {k: v for k, v in grid.cell.items() if v != " "}
    return SideGrid(
        cells=cells,
        width=max(x for x, _ in cells) + 1,
        height=max(y for _, y in cells) + 1,
        in_cell=(0, _IN_ROW),
        out_cell=(0, p.out_row),
        slots=cols * rows,
        pipes=npipes,
        cols=cols,
        rows=rows,
        base=base,
        out_row=p.out_row,
    )


def build_side_grid(
    cols: int,
    rows: int,
    *,
    base: int = 0,
    out_row: int | None = None,
) -> SideGrid:
    """The same block with ``I`` and ``O`` rooms bolted on: a runnable program.

    A littleman program may have at most one of each and a client owns them both,
    so :func:`grid_side_block` has neither.  This is the testable form: the ``I``
    room feeds the request stub from the west and the ``O`` room takes the answer
    stub, which is the client's job in place.  Both hang off the same wall, which
    is the whole claim being made.
    """
    p = _plan(cols, rows, base, out_row)
    ox, oy = 10, 2
    grid = Circuit(ox + p.width + 4, oy + p.height + 4)
    dbg = DebugMap(
        f"same-side grid man-memory {cols}x{rows} at {base}..{base + cols * rows - 1}: "
        "request and answer on one wall"
    )
    npipes = _stamp(grid, p, ox, oy, dbg=dbg)

    # `I` west of the request stub, `O` west of the answer stub.  The two rooms
    # are staggered *horizontally*, not stacked: the ports are two rows apart —
    # that is the claim — and two 3x3 rooms two rows apart would share a wall.
    in_y, out_y = oy + _IN_ROW, oy + p.out_row
    _io_room(grid, ox - 4, in_y, "I")
    draw_pipe(grid, [(x, in_y) for x in range(ox - 2, ox + 1)])
    _io_room(grid, ox - 8, out_y, "O")
    draw_pipe(grid, [(x, out_y) for x in range(ox - 1, ox - 8, -1)])
    dbg.region("input", ox - 5, in_y - 1, 3, 3, note="`0 addr` / `1 addr value`", color=C_IO)
    dbg.region(
        "output",
        ox - 9,
        out_y - 1,
        3,
        3,
        note="one word per READ, two rows under the request — the whole point",
        color=C_IO,
    )

    cells = {k: v for k, v in grid.cell.items() if v != " "}
    return SideGrid(
        cells=cells,
        width=max(x for x, _ in cells) + 1,
        height=max(y for _, y in cells) + 1,
        in_cell=(ox, in_y),
        out_cell=(ox, out_y),
        slots=cols * rows,
        pipes=npipes,  # the I/O stubs extend the block's own two, they are not new
        cols=cols,
        rows=rows,
        base=base,
        out_row=p.out_row,
        debug=dbg,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """``--cols M --rows N``, written to ``--man`` / ``--html`` / ``--json``."""
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--cols", type=int, default=3, metavar="M")
    ap.add_argument("--rows", type=int, default=14, metavar="N")
    ap.add_argument("--base", type=int, default=0, help="first address answered")
    ap.add_argument("--out-row", type=int, default=None, help="row of the answer stub")
    ap.add_argument(
        "--block",
        action="store_true",
        help="emit the placeable block (no I/O rooms) instead of the standalone program",
    )
    ap.add_argument("--man", type=Path, help="write the grid here")
    ap.add_argument("--html", type=Path, help="write the labelled debug overlay here")
    ap.add_argument("--json", type=Path, help="write the debug region sidecar here")
    args = ap.parse_args(argv)

    if args.block:
        built = grid_side_block(args.cols, args.rows, base=args.base, out_row=args.out_row)
    else:
        built = build_side_grid(args.cols, args.rows, base=args.base, out_row=args.out_row)
    if args.man:
        args.man.write_text(built.source() + "\n", encoding="utf-8")
    if built.debug is not None:
        if args.html:
            built.debug.write_html(built.source().split("\n"), args.html)
        if args.json:
            built.debug.write_json(args.json)
    elif args.html or args.json:
        raise SystemExit("the placeable block carries no debug map; drop --block")
    if not (args.man or args.html or args.json):
        print(built.source())
    else:
        print(
            f"{built.cols}x{built.rows} = {built.slots} cells at "
            f"{built.low}..{built.high - 1}, {built.width} x {built.height}, "
            f"{built.pipes} pipes, in {built.in_cell} out {built.out_cell}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
