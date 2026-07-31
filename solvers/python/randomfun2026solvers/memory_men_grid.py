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

    [I] -> [ router strip               ]   broadcasts addr, op, value
             |        |        |             one pipe down per column
          [rep][dec][cell]  x M              rep fans the words into its column
             |        |        |             ONE answer pipe out of each column
          [ collector strip            ] -> [O]

**Nothing about a cell changes.** The column's decoders hold *global* addresses —
its igniter is handed a base and counts up from it — so the thing at the head of a
column has no decoding to do at all. It is a six-glyph ring, ``r S`` three times,
repeating the router's words into its own column. That is why ``M`` and ``N`` are
both free: no bit-alignment, no division, no power-of-two stride, and
``DECODER_TILE`` and ``CELL_TILE`` are the same text they were.

The base is a zero-padded literal. A column is its own room, so it can simply
carry its number — no igniter-of-igniters and no ignition pipes, and every column
starts at tick 0 rather than waiting its turn.

That literal is written along row 0 of the decoder room, and for two digits
``@ 1M`dd` v`` is *exactly* the room's eight columns — so a grid whose last column
started at 100 or more used to raise ``IndexError`` out of ``band_room``, which
capped the whole family near 110 cells (``10x11`` built, ``6x20`` did not). Three
digits now buy exactly one more column, so a grid past a hundred cells is
``28 * M`` wide instead of ``27 * M`` and everything below a hundred is
byte-identical — the shipped 4x25 ``memory`` solution included.

**A column needs no collector of its own.** Exactly one decoder speaks per
operation, so at most one cell ever reaches its ``s``: the whole room sends
through a single pipe out of its south wall, and the strip's ``R`` takes it from
there.

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
    _BASE_DIGITS,
    BAND,
    CELL_TILE,
    DECODER_TILE,
    ROUTER_ROWS,
    _init_height,
    band_room,
    build_addr,
    preamble_width,
    tile_x0,
)

__all__ = ["REPEATER", "ROUTER_FLAT", "Grid", "build_grid"]

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

#: The router, laid flat for the strip. Same program as ``ROUTER_ROWS`` and the
#: same laps — READ 16, WRITE 18 — but 9x3 instead of 3x8, because a strip spans
#: the width anyway and spends only rows.
#:
#: It works only with the input pipe on the **west** wall: ``U`` turns the man away
#: from the pipe's side, so a west feed faces him *east*, which is the direction
#: the code runs. That is the same trick the tall one plays with a north feed, and
#: it is why the router cannot simply be rotated without moving its input.
ROUTER_FLAT: tuple[str, ...] = (
    "@v     S<",
    " UMrSWSX^",
    "       U^",
)

#: Interior width of each room in a column, in order.
_REP_W = max(len(r) for r in REPEATER)
_DEC_W = tile_x0(True) + max(len(r) for r in DECODER_TILE)
_CELL_W = tile_x0(False) + max(len(r) for r in CELL_TILE)

#: Interior columns between two rooms: wall, two cells of pipe, wall.
_GAP = 4
#: Blank columns between one column of memory and the next.
_COL_GAP = 2

#: The strips stand ``_RISER_Y`` rows below the block's top edge, and in the
#: **standalone** form those rows are the ``I`` room's. The block form draws no
#: ``I`` room, so in a block they hold nothing but the answer riser's exit stub
#: walking through them — see ``riser_lift`` in :func:`build_grid`.
_RISER_Y = 6

#: The bound on ``riser_lift``, and it is where the win saturates rather than a
#: guess. At 4 the riser's north wall reaches the block's top edge, the block's
#: own stub is gone, and what is left is the caller's own two cells of pipe — the
#: engine's minimum. Measured on ``deadman-3d_hires`` men-v3, 3-round screen,
#: read latency by ``OpcodeTags(hist_pipe=...)``: lift 0/1/2/3/4 give
#: **49/48/47/46/45** ticks, one for one; **lift 5 also gives 45** (it only walks
#: teleport L up a row behind the riser) and lift 6 refuses outright. So 4 is the
#: last value that buys anything, and nothing is left on the table by stopping.
MAX_RISER_LIFT = _RISER_Y - 2


@dataclass(frozen=True)
class Grid:
    """A complete ``memory`` program as ``cols`` columns of ``rows`` cells."""

    cols: int
    rows: int
    grid_rows: tuple[str, ...]
    width: int
    height: int
    debug: DebugMap | None = field(default=None, compare=False, repr=False)
    #: With ``io=False`` (the machine-facing block form): the first cell of the
    #: request stub and the named top of the answer stub. ``None`` in the
    #: standalone form, whose ends are its own I/O rooms.
    in_cell: tuple[int, int] | None = field(default=None, compare=False)
    out_cell: tuple[int, int] | None = field(default=None, compare=False)

    @property
    def n(self) -> int:
        return self.cols * self.rows

    @property
    def footprint(self) -> int:
        return max(self.width, self.height) ** 2

    def source(self) -> str:
        return "\n".join(self.grid_rows)


def build_grid(
    cols: int,
    rows: int,
    *,
    router: Sequence[str] | None = None,
    io: bool = True,
    request_west: bool = False,
    riser_lift: int = 0,
) -> Grid:
    """``cols`` columns of ``rows`` cells each, addresses running column by column.

    Column ``j`` owns ``[j*rows, (j+1)*rows)`` and its igniter is handed ``j*rows``
    as a literal, so all ``cols`` igniters walk at once and the fixed cost of the
    whole memory is one column's walk.

    ``router`` swaps the strip's program for another one driving the same bus --
    :func:`memory_men_v3.router_rows` is the reason the hook exists. The strip only
    ever needed the router to own one incoming pipe and no outgoing pipe but the
    column stubs, so any program with those two properties drops in. Measured on
    ``4x25``: the default's 16.79 ticks per operation against v3's 11.28, at the
    cost of a taller strip (the v3 router trades area for the walk home, and this
    grid is the one shape where area is what the score is made of).

    ``io=False`` is the machine-facing **block** form: no I/O rooms. The request
    keeps its two-cell stub into the router strip's north wall (``in_cell`` names
    its first cell), and the answer — which the standalone form drops out of the
    *south* — is carried back to the block's **top** by a vertical teleport room
    down the east side (``memory_men.teleport_v``: ``R`` has no distance term, so
    the tall room crosses its whole height in one instruction). That keeps the
    block's outlet where ``lm1.machine``'s STORE teleport expects every man-memory
    outlet to be, at the cost of one man and two short stubs.

    ``request_west`` drops that stub and names the strip's **south-west corner**
    as ``in_cell`` instead, so a caller standing level with the corner delivers
    the request in one straight leg rather than climbing over the block's roof.
    Nothing inside the strip moves: the room owns exactly one incoming pipe, so
    every ``r`` in it binds to that pipe wherever it attaches, and the *place* it
    attaches is free (``SPEC.md`` §Nearest — "nearest" only picks which pipe, and
    the walk to it costs nothing). ``ARCH.md`` §7.4b: a plain room may be attached
    at its corner; only displays forbid it. Block form only — the standalone grid
    has an I room to feed the stub.

    ``riser_lift`` raises the answer riser's **north wall** by that many rows, and
    shortens its exit stub by exactly as many pipe cells.  The stub is five cells
    long at ``0`` only because ``router_y`` reserves five rows above the strip for
    the standalone form's ``I`` room — a room the block form never draws, so in
    the block those rows hold nothing but the stub walking through them.  Every
    pipe cell is a tick of *read latency* (``ARCH.md`` §7.4b), and the riser sits
    on the answer path, so this is worth ``riser_lift`` ticks off every read.
    Left at ``0`` so every existing block stays byte-identical; opt in per caller.
    """
    if cols < 1 or rows < 1:
        raise ValueError("a grid needs at least one column of at least one cell")
    if not io and cols == 1:
        raise ValueError("the one-column block form is memory_men_v3.v3_store_block")
    if request_west and io:
        raise ValueError("request_west is the block form's touch point; io=True has an I room")
    if riser_lift and io:
        raise ValueError("riser_lift raises the block form's answer riser; io=True has no riser")
    if not 0 <= riser_lift <= MAX_RISER_LIFT:
        raise ValueError(
            f"riser_lift {riser_lift} is outside 0..{MAX_RISER_LIFT}: it comes straight "
            f"off the riser's exit stub, and a pipe is two cells or it is not a pipe"
        )
    if cols == 1:
        # One column needs none of this: the repeater would be a pass-through
        # costing a pipeline stage and two pipes, and a strip spanning one column
        # is just a router. `memory_men_addr` *is* the one-column grid.
        #
        # Which is also why it cannot honour `router`: there is no strip to put one
        # in. Say so rather than returning a grid that quietly ignores the argument
        # — a one-column build measured identical to the default is a bug, not a
        # negative result, and `memory_men_v3.build_v3` is the shape being asked for.
        if router is not None:
            raise ValueError(
                "a one-column grid is `build_addr`, which owns no router strip; "
                "use memory_men_v3.build_v3(rows) for the unrolled one-column memory"
            )
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
    # A column's base literal is as wide as the *last* column's number, and every
    # column carries the same width so their bands stay level.  Past 99 that makes
    # the decoder room a column wider, which is the whole cost of allowing a grid
    # bigger than ~110 cells.
    digits = max(_BASE_DIGITS, len(str((cols - 1) * rows)))
    dec_w = max(_DEC_W, preamble_width(digits))
    col_w = _REP_W + _GAP + dec_w + _GAP + _CELL_W

    # ── vertical plan ─────────────────────────────────────────────────────────
    # I room, its pipe, the router strip, its pipes, the columns, their pipes,
    # the collector strip, its pipe, the O room. Two-cell pipes throughout.
    router_y = _RISER_Y
    router_prog = tuple(router) if router is not None else ROUTER_ROWS
    router_h = len(router_prog)
    col_y = router_y + router_h + 1 + _GAP - 1  # south wall, 2 pipe cells, north wall
    # A collector only has to reach its column's *last band row*, not the column's
    # last row — the band below it is a tile's third row, which owns no pipe. So
    # its room stops early and the strip comes up behind it, as far as the two
    # cells of pipe and the neighbouring rooms' walls allow.
    coll_y = col_y + col_h + _GAP
    out_y = coll_y + 2 + _GAP

    x0 = 1
    total_w = x0 + cols * col_w + (cols - 1) * _COL_GAP + 1
    # The block form appends the riser room east of the strips: wall, two stub
    # cells, wall, four interior columns, wall.
    grid = Circuit(total_w + (2 if io else 10), out_y + 4)

    def column_x(j: int) -> dict[str, int]:
        base = x0 + j * (col_w + _COL_GAP)
        rep = base
        dec = rep + _REP_W + _GAP
        cell = dec + dec_w + _GAP
        return {"rep": rep, "dec": dec, "cell": cell, "end": cell + _CELL_W}

    def spanned(body: Sequence[str], iw: int, h: int) -> list[str]:
        # Every room in a column is as tall as the column: a pipe's first cell has
        # to sit against its source room's wall and its last against the
        # destination's, so a short room leaves the lower bands unreachable.
        out = [r.ljust(iw) for r in body]
        return out + [" " * iw] * (h - len(out))

    # ── the router strip ──────────────────────────────────────────────────────
    strip_w = column_x(cols - 1)["end"] - x0
    router_body = [r.ljust(strip_w) for r in router_prog]
    _room(grid, x0, router_y, router_body)
    if io:
        _io_room(grid, x0 + 1, router_y - 5, "I")
    if request_west:
        # No stub at all: the caller's own leg ends one cell west of the strip's
        # south-west corner and that is the whole attachment. Drawing a stub here
        # as well would leave the block owning a *second* incoming pipe, which is
        # the one property the strip's `r` glyphs rely on not being true.
        in_cell = (x0 - 1, router_y + router_h)
    else:
        draw_pipe(
            grid, [(x0 + 1, router_y - 3), (x0 + 1, router_y - 2), (x0 + 1, router_y - 1)]
        )
        in_cell = None if io else (x0 + 1, router_y - 3)

    # ── the collector strip ───────────────────────────────────────────────────
    coll_body = [r.ljust(strip_w) for r in collector_rows(1)[0]]
    _room(grid, x0, coll_y, coll_body)
    out_x = x0 + 2
    out_cell = None
    if io:
        draw_pipe(grid, [(out_x, coll_y + 3), (out_x, coll_y + 4), (out_x, coll_y + 5)])
        _io_room(grid, out_x, out_y, "O")
    else:
        # The answer riser: a vertical teleport room east of everything carries
        # the collector's output back to the block's top, where the machine's
        # STORE teleport expects a man-memory outlet. Two short stubs; the tall
        # room itself is crossed in one instruction (`R` has no distance term).
        from .memory_men import teleport_v

        east = x0 + strip_w  # the strips' shared east wall column
        rx = east + 4
        # The riser's interior starts `riser_lift` rows higher, and is that much
        # taller, so its south end still reaches the collector's attachment row.
        # `router_y - 2` is the stub's first cell, so the lift comes straight off
        # the stub — and the guard is that a pipe is two cells or it is not a pipe.
        top = router_y - riser_lift
        _room(grid, rx, top, teleport_v(coll_y + 2 - top)[0])
        # collector east wall -> the riser's west wall, two cells of pipe
        draw_pipe(grid, [(east + 1, coll_y), (east + 2, coll_y), (east + 3, coll_y)])
        # the riser's north wall -> the block's top edge; the caller extends it
        draw_pipe(grid, [(rx + 1, y) for y in range(top - 2, -1, -1)])
        out_cell = (rx + 1, 0)

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
    if io:
        dbg.region("input", x0, router_y - 6, 3, 3, note="`0 addr` / `1 addr value`", color=C_IO)
        dbg.region("output", out_x - 1, out_y - 1, 3, 3, note="one word per READ", color=C_IO)

    for j in range(cols):
        cx = column_x(j)
        base = j * rows
        dec_body, mains = band_room(
            rows, DECODER_TILE, increment=True, base=base, base_digits=digits
        )
        cell_body, cell_mains = band_room(rows, CELL_TILE, increment=False, init_h=init_h)
        assert mains == cell_mains, "both rooms of a column must band identically"

        _room(grid, cx["rep"], col_y, spanned(REPEATER, _REP_W, col_h))
        _room(grid, cx["dec"], col_y, spanned(dec_body, dec_w, col_h))
        _room(grid, cx["cell"], col_y, spanned(cell_body, _CELL_W, col_h))

        # router strip -> this column's repeater, down two cells into its north wall
        feed_x = cx["rep"] + 2
        draw_pipe(grid, [(feed_x, y) for y in range(router_y + router_h + 1, col_y)])
        # this column's ONE answer pipe, straight down out of the cell room. At
        # most one cell sends per operation — a cell only reaches its `s` if its
        # decoder spoke to it, and exactly one decoder does — so the whole room
        # needs a single outgoing pipe, and the strip's `R` takes it from there.
        ans_x = cx["cell"] + 1
        draw_pipe(grid, [(ans_x, y) for y in range(col_y + col_h + 1, coll_y)])

        for main in mains:
            y = col_y + main
            draw_pipe(grid, [(x, y) for x in range(cx["rep"] + _REP_W + 1, cx["dec"])])
            draw_pipe(grid, [(x, y) for x in range(cx["dec"] + dec_w + 1, cx["cell"])])

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
        in_cell=in_cell,
        out_cell=out_cell,
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
