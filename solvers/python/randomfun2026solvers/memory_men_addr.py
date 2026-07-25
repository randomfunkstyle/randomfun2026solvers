#!/usr/bin/env python3
"""Broadcast man-memory whose cells carry their address as **data**, not as code.

``memory_men_bcast`` shouts ``1 << addr`` at every cell and each one tests bit
``j`` with ``b`` ``]``*j ``x``. That works, but the address lives in the *code*:
cell ``j`` is ``j`` glyphs wider than cell ``j-1``, the field is O(n^2) glyphs and
no two tiles are alike, so nothing can be packed and the last cell paces every
access. Here every tile is the **same text** and the address is a number in a
man's hand.

**Why it takes two men, and therefore two rooms.** A cell needs three live
integers — its value, its address and the incoming word — and a man has two
readable hands (``r`` always lands in ``A``; ``BP`` can only be turned on). So the
address moves into a *second* man in a *second* room:

* a **decoder** man holds his address in ``B`` and nothing else, so ``~`` is the
  whole comparator: ``A = addr ^ myaddr`` is ``0`` for exactly one man and
  positive for all the others, which is a clean two-way ``X``;
* a **cell** man holds his value in ``B`` and never sees an address at all. He is
  addressed by *which pipe* speaks to him, and only his own decoder does.

**Where the addresses come from.** ``Y`` is the only way to put many men in one
room, and children inherit ``A``, ``B`` and ``BP`` — so a spawner walking south
down the decoder room, incrementing ``B`` between splits, hands cell ``j`` the
number ``j`` at birth. It carries ``A = 1`` for its whole life so the increment is
``+`` ``M`` ``1``: three glyphs on the west child's way back to the next ``Y``.

**The wire protocol is fixed-width on purpose.** The router broadcasts exactly
three words per operation — ``addr``, ``op``, ``value`` (a dummy ``0`` on a READ)
— so an unselected decoder needs no branch to know how much to swallow: it reads
two more words and goes home. Every decoder consumes every word, which is what
keeps ``S`` (all-or-nothing by definition) from wedging.

    addr op value          op [value]
  ──────────────>  decoder ────────> cell ──> collector
     S, all n                pipe j            R, any

Cost is the same for every address, because every tile is the same tile.
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

__all__ = [
    "BAND",
    "tile_x0",
    "DECODER_TILE",
    "CELL_TILE",
    "ROUTER_ROWS",
    "band_room",
    "build_addr",
]

#: Interior rows one cell occupies. Three, not five: a tile is a ring whose
#: perimeter is the program, and the three interior cells are its one branch.
BAND = 3


def tile_x0(increment: bool) -> int:
    """Interior column the tiles start at; the columns west of it are the spawner's.

    The ``Y`` sits immediately west of the tiles so its east child lands on the
    first cell. Behind it the west child needs somewhere to turn around: one
    column if he only has to walk (the cell room), two if he also has to carry
    ``+`` ``M`` ``1`` (the decoder room).
    """
    return 3 if increment else 2


#: One decoder, and every decoder is this. ``B`` holds his address forever.
#:
#: A **ring**, not a lane with a return corridor: the man walks the perimeter of a
#: 3x5 box, so every cell he steps on is a cell that does something and a lap is 12
#: ticks instead of 20. The three interior cells are the not-mine lane, which is
#: the only branch a decoder has.
#:
#: ``r`` takes the broadcast address and ``~`` XORs it against ``B`` — zero for
#: exactly one man. ``X`` turns on that: straight round the ring is mine (``r`` the
#: op, ``s`` it on, ``r`` the value, ``s`` it on), clockwise is everyone else, who
#: cuts across the middle swallowing both words in 8 ticks. Both paths rejoin at
#: the west side, which is also where the ``Y`` puts the man — born facing east
#: onto a ``^``, he turns straight into the ring.
DECODER_TILE: tuple[str, ...] = (
    ">r~Xv",
    "^rr<r",
    "^srs<",
)

#: One cell, and every cell is this. ``B`` holds the value; ``A`` starts at 0,
#: which is the problem's "every cell starts at 0" for free.
#:
#: The same 3x5 ring. Both ops read exactly the same two words — op, then value
#: (a dummy on a READ) — so the *reads* are shared and only the tail differs; the
#: branch is deferred to ``d`` at the south-east corner by parking the op in the
#: backpack with ``b``. That is what lets one ring serve both:
#:
#:     READ   `r b r`, straight on: `W M s` — send the value, put it back in B
#:     WRITE  `r b r`, `d` turns west: `M` — the value read is the value stored
CELL_TILE: tuple[str, ...] = (
    ">rbrv",
    "^  Md",
    "^sMW<",
)

#: The router: one op off the input stream becomes three broadcast words.
#:
#: Written **downwards**, three columns wide. The room has to span the field
#: anyway — every band needs a pipe off its east wall — so its rows are already
#: paid for and its columns are the only thing it can spend. Horizontally the same
#: code was 12x4 and its lap was 28 ticks, which *was* the per-op cost of the whole
#: memory. This is 3x8 and 17.
#:
#: ``U`` the op, ``M`` it into ``B``, ``r`` the address and ``S`` it, then ``W`` to
#: bring the op back and ``S`` that. ``X`` turns on the op: READ walks straight out
#: to the return column, WRITE turns west onto a second ``U``. Both then rise
#: through the **same** final ``S``.
#:
#: Two things make it this short:
#:
#: * **``U`` is a receive and a turn in one cell.** The input pipe is the room's
#:   only incoming one and it lands on the *north* wall, so every ``U`` here faces
#:   the man south whatever he was doing — which is exactly the turn both receives
#:   needed. ``v r`` twice becomes ``U`` twice.
#: * **The dummy value is free.** After ``W`` a READ's ``A`` is still the ``0`` it
#:   broadcast as its op, so the shared final ``S`` sends a correct third word with
#:   no literal, no second lane and no extra row.
#:
#: The room must own **no other outgoing pipe**: ``S`` would broadcast into that
#: one too, which is why answers go to a separate collector.
ROUTER_ROWS: tuple[str, ...] = (
    "@U<",
    " M ",
    " r ",
    " S ",
    " W ",
    " S ",
    "UXS",
    ">>^",
)


#: Digits in a column's zero-padded base literal. Fixed width so that every
#: column of a grid puts its bands on the same rows whatever number it starts at.
_BASE_DIGITS = 3


def _init_height(base: int | None) -> int:
    """Rows the igniter's preamble needs above the first band."""
    return 1 if base is None else 2


def band_room(
    n: int,
    tile: Sequence[str],
    *,
    increment: bool,
    base: int | None = None,
    init_h: int = 1,
) -> tuple[list[str], list[int]]:
    """``n`` copies of ``tile`` in one room, one per three-row band.

    Returns the interior rows and each band's *main* row — the row a tile reads
    and sends on, and therefore the row both of its pipes bind to.

    The spawner walks south down the columns west of the tiles. Entering ``Y``
    heading south puts the order-preserving child one cell **west** (facing west)
    and the newest one east (facing east), so the east child lands on his tile's
    ``^`` and turns straight into the ring while the west child loops back into
    the ``Y`` column in time for the next band.

    With ``increment`` that westward walk is ``+`` ``M`` ``1``: the spawner keeps
    ``A = 1``, so ``+`` makes ``A = 1 + B``, ``M`` writes it back to ``B`` and
    ``1`` restores ``A``. Cell ``j`` is therefore born holding ``j``. Those three
    glyphs are the only reason the decoder room is a column wider than the cell
    room: without them the child's turn-around fits in two columns.
    """
    if n < 1:
        raise ValueError("a memory needs at least one cell")
    x0 = tile_x0(increment)
    ycol = x0 - 1  # the `Y`, so that its east child lands on the tile
    tile_w = max(len(r) for r in tile)
    iw = x0 + tile_w
    # at least one row on top: a man spawns facing east and has to be turned south
    # before he can enter the first `Y` heading south. `base` and `init_h` buy more
    # rows for a starting address and for keeping two rooms' bands level.
    init_h = max(init_h, _init_height(base))
    ih = init_h + BAND * n
    rows = [[" "] * iw for _ in range(ih)]

    def put(x: int, y: int, glyph: str) -> None:
        if rows[y][x] not in (" ", glyph):
            raise ValueError(f"collision at ({x},{y}): {rows[y][x]!r} vs {glyph!r}")
        rows[y][x] = glyph

    put(0, 0, "@")
    if base is not None:
        # A whole column of memory starts at `base`, so the igniter is handed one
        # number and counts up from it. Read the literal walking *east* and snake
        # back west for the `1` — a room this wide has the columns to spare and
        # rows are what a column costs. Zero-padded to a fixed width so every
        # column's bands sit on the same rows whatever its base.
        literal = f"`{base:0{_BASE_DIGITS}d}`M"
        for dx, glyph in enumerate(literal):
            put(1 + dx, 0, glyph)
        turn = 1 + len(literal)
        put(turn, 0, "v")
        put(turn, 1, "<")
        put(turn - 1, 1, "1")  # A = 1 again, picked up on the way back
        put(ycol, 1, "v")
    else:
        if increment:
            put(1, 0, "1")  # A = 1 for the whole walk; every `+` is an increment
        put(ycol, 0, "v")

    main_rows: list[int] = []
    for j in range(n):
        top = init_h + BAND * j
        r_ret, r_main, r_alt = top, top + 1, top + 2
        main_rows.append(r_main)

        put(ycol, r_main, "Y")
        for dy, line in enumerate(tile):
            for dx, glyph in enumerate(line):
                if glyph != " ":
                    put(x0 + dx, r_ret + dy, glyph)

        if j + 1 == n:
            # no band left to seed, and no room to walk into
            put(ycol - 1, r_main, "H")
            continue
        # the west child, turning around: down the west column, back east under
        # the tile, and south into the next `Y`.
        if increment:
            put(1, r_main, "+")  # born on it, facing west
            put(0, r_alt, "M")
            put(1, r_ret + BAND, "1")
        put(0, r_main, "v")
        put(0, r_ret + BAND, ">")
        put(ycol, r_ret + BAND, "v")
    return ["".join(r) for r in rows], main_rows


def build_router_probe() -> str:
    """The router alone: input in, one pipe out, every broadcast word printed.

    ``S`` writes to every outgoing pipe, so a router with exactly one of them is
    the same program the field sees — and a sink that forwards each word to the
    output makes the wire protocol readable directly:

        ``0 5``     -> ``5 0 0``   (addr, op, dummy)
        ``1 7 42``  -> ``7 1 42``  (addr, op, value)

    Isolated on purpose: the router's lap *is* the memory's per-op cost, so it is
    the one room worth shortening on its own, without 16 bands in the way.
    """
    grid = Circuit(28, 20)
    rx, ry = 4, 6
    iw = max(len(r) for r in ROUTER_ROWS)
    _room(grid, rx, ry, list(ROUTER_ROWS))
    _io_room(grid, rx + 1, ry - 5, "I")
    draw_pipe(grid, [(rx + 1, ry - 3), (rx + 1, ry - 2), (rx + 1, ry - 1)])
    sink_x = rx + iw + 4
    draw_pipe(grid, [(x, ry + 1) for x in range(rx + iw + 1, sink_x)])
    sink, _ = collector_rows(1)
    _room(grid, sink_x, ry, list(sink))
    ex = sink_x + max(len(r) for r in sink)
    draw_pipe(grid, [(ex + 1, ry + 1), (ex + 2, ry + 1), (ex + 3, ry + 1)])
    _io_room(grid, ex + 4, ry + 1, "O")
    rows = [row.rstrip() for row in grid.rows()]
    while rows and not rows[-1]:
        rows.pop()
    return "\n".join(rows)


@dataclass(frozen=True)
class Addr:
    """A complete ``memory`` program with uniform, address-carrying tiles."""

    n: int
    rows: tuple[str, ...]
    width: int
    height: int
    #: names for a grid that cannot carry comments, built from the same coordinates
    debug: DebugMap | None = field(default=None, compare=False, repr=False)

    @property
    def footprint(self) -> int:
        return max(self.width, self.height) ** 2

    def source(self) -> str:
        return "\n".join(self.rows)


def build_addr(n: int) -> Addr:
    """Router, decoder room, cell room, collector — four rooms, ``3n`` pipes.

    Every room spans the same rows, so all ``3n`` pipes are two-cell stubs on
    facing walls. That is what makes the extra decoder hop cheap: a pipe's cost is
    its length, and none of these is longer than the wall gap.
    """
    dec_body, main_rows = band_room(n, DECODER_TILE, increment=True)
    cell_body, cell_mains = band_room(n, CELL_TILE, increment=False)
    assert main_rows == cell_mains, "both rooms must band identically"
    dec_iw = max(len(r) for r in dec_body)
    cell_iw = max(len(r) for r in cell_body)
    ih = len(dec_body)
    router_iw = max(len(r) for r in ROUTER_ROWS)
    coll_rows, _ = collector_rows(1)
    coll_iw = max(len(r) for r in coll_rows)

    # room | wall | 2 cells of pipe | wall | room, with both IO rooms stacked
    # *above* the chain rather than flanking it: the four rooms are already as
    # tall as the field, so a column spent on IO is a column added to the longer
    # side, while a row is spent on the shorter one.
    rox, roy = 1, 6
    dx = rox + router_iw + 4
    cx = dx + dec_iw + 4
    cox = cx + cell_iw + 4
    # every room is as tall as the tallest, which for a small n is the router
    span_h = max(ih, len(ROUTER_ROWS))
    grid = Circuit(cox + coll_iw + 2, roy + span_h + 2)

    def spanned(body: Sequence[str], iw: int) -> list[str]:
        # A pipe's first cell must sit against its source room's wall, and its
        # last against the destination's — so every room in the chain has to be
        # as tall as the field, empty rows and all.
        out = [r.ljust(iw) for r in body]
        return out + [" " * iw] * (span_h - len(out))

    _room(grid, rox, roy, spanned(ROUTER_ROWS, router_iw))
    _room(grid, dx, roy, spanned(dec_body, dec_iw))
    _room(grid, cx, roy, spanned(cell_body, cell_iw))
    _room(grid, cox, roy, spanned(coll_rows, coll_iw))

    # input drops into the router's north wall; the answer climbs out of the
    # collector's. Both rooms own exactly one pipe that way, so neither needs a
    # binding argument — only `S` and `R` are ambiguous, and neither is here.
    in_x, out_x = rox + 1, cox + 2
    _io_room(grid, in_x, roy - 5, "I")
    draw_pipe(grid, [(in_x, roy - 3), (in_x, roy - 2), (in_x, roy - 1)])
    _io_room(grid, out_x, roy - 5, "O")
    draw_pipe(grid, [(out_x, roy - 2), (out_x, roy - 3), (out_x, roy - 4)])
    for main in main_rows:
        y = roy + main
        # Every hop binds by row: `r` competes only with incoming pipes and `s`
        # only with outgoing, and same-wall ports differ only in row, so a tile
        # reaches its own port and no other.
        draw_pipe(grid, [(x, y) for x in range(rox + router_iw + 1, dx)])
        draw_pipe(grid, [(x, y) for x in range(dx + dec_iw + 1, cx)])
        draw_pipe(grid, [(x, y) for x in range(cx + cell_iw + 1, cox)])

    # ── the sidecar ───────────────────────────────────────────────────────────
    # The tiles are identical, so the ASCII cannot say which band is address 5 —
    # only the spawner's increment does, and that is three glyphs in a corner.
    dbg = DebugMap(f"address-carrying man-memory n={n}: identical tiles, address in B")
    dbg.region(
        "input",
        in_x - 1,
        roy - 6,
        3,
        3,
        note="the problem's own stream: `0 addr` (READ) / `1 addr value` (WRITE)",
        color=C_IO,
    )
    dbg.region(
        "router / broadcaster",
        rox,
        roy,
        router_iw,
        len(ROUTER_ROWS),
        note=(
            "`U` the op (the input pipe is on the north wall, so `U` is a receive and "
            "a turn in one cell), `M` it aside, `r` the address and `S` it, `W` the op "
            "back and `S` that. Only then does `X` run, and all it decides is where the "
            "*third* word comes from: a READ falls through to the shared final `S` still "
            "holding the 0 it sent as its op, a WRITE turns west onto a second `U` for "
            "the value. Three words, fixed width, so an unselected decoder needs no "
            "branch to know how much to swallow. This room owns no other outgoing pipe "
            "on purpose: `S` would broadcast into it too."
        ),
        color=C_MID,
    )
    dbg.region(
        "decoder room",
        dx,
        roy,
        dec_iw,
        ih,
        note=(
            f"all {n} decoders share this room, three rows each, every tile the same "
            "text. A decoder's `B` is his address and holds nothing else, which is the "
            "whole reason he exists: `~` needs `B`, and a cell's `B` is busy."
        ),
        color=C_MID,
    )
    dbg.region(
        "cell room",
        cx,
        roy,
        cell_iw,
        ih,
        note=(
            f"all {n} values share this room. A cell holds its value in `B` and never "
            "sees an address — it is addressed by which pipe speaks to it, and only its "
            "own decoder ever does."
        ),
        color=C_STORE,
    )
    dbg.circle(
        "the increment",
        dx + 1,
        roy + main_rows[0],
        1,
        note=(
            "`+` with A=1 pinned: A = 1 + B. Then `M` puts it back in B and `1` restores "
            "A, so the next `Y` hands out the next address. The cell room walks the same "
            "path with these three cells blank — cells are addressed by which pipe speaks "
            "to them and never learn a number."
        ),
        color=C_MID,
    )
    for j, main in enumerate(main_rows):
        dbg.circle(
            f"split -> addr {j}",
            dx + 2,
            roy + main,
            1,
            note=(
                f"entered heading south, so the east child lands on the tile and starts "
                f"decoding with B = {j}; the west child walks the increment on"
            ),
            color=C_MID,
        )
        dbg.region(
            f"decoder addr {j}",
            dx + tile_x0(True),
            roy + main - 1,
            len(DECODER_TILE[1]),
            BAND,
            note=(
                f"B = {j} for life. `r` the broadcast address, `~` it against B: 0 only "
                "here. `X` straight = mine — round the ring, `r` the op and `s` it on, "
                "`r` the value and `s` it on. Clockwise = not mine: cut west across the "
                "middle swallowing both words, and rejoin at the ring's west column, "
                "which both paths share."
            ),
            color=C_MID,
        )
        dbg.region(
            f"cell addr {j}",
            cx + tile_x0(False),
            roy + main - 1,
            len(CELL_TILE[1]),
            BAND,
            note=(
                "value in B. Both ops read the same two words — `r` the op, `b` it into "
                "the backpack, `r` the value — so only the tail differs and `d` picks it "
                "at the south-east corner: READ carries on into `W M s`, WRITE turns west "
                "onto `M`. Never blocks anyone else — its decoder only speaks when this "
                "address is called."
            ),
            color=C_STORE,
        )
        dbg.lane(
            f"broadcast -> decoder {j}",
            [(rox + router_iw + 1, roy + main), (dx - 1, roy + main)],
            kind="pipe",
            expect="addr, op, value — always three, always all n pipes",
            color=C_CMD,
        )
        dbg.lane(
            f"decoder {j} -> cell {j}",
            [(dx + dec_iw + 1, roy + main), (cx - 1, roy + main)],
            kind="pipe",
            expect="op, value — only when this address is selected",
            color=C_CMD,
        )
        dbg.lane(
            f"answer addr {j}",
            [(cx + cell_iw + 1, roy + main), (cox - 1, roy + main)],
            kind="pipe",
            expect="the stored value, on a selected READ only",
            color=C_ANS,
        )
    dbg.region(
        "collector",
        cox,
        roy,
        coll_iw,
        ih,
        note=(
            "`R` reads from ANY incoming pipe, so the answer needs no addressing at all. "
            "Spanning the field is what makes every answer pipe a two-cell stub."
        ),
        color=C_COLL,
    )
    dbg.region("output", out_x - 1, roy - 6, 3, 3, note="one word per READ", color=C_IO)
    dbg.lane(
        "input-pipe",
        [(in_x, roy - 3), (in_x, roy - 1)],
        kind="pipe",
        expect="op addr [value]",
        color=C_CMD,
    )
    dbg.lane(
        "output-pipe",
        [(out_x, roy - 2), (out_x, roy - 4)],
        kind="pipe",
        expect="one word per READ",
        color=C_ANS,
    )

    out = [row.rstrip() for row in grid.rows()]
    while out and not out[-1]:
        out.pop()
    return Addr(
        n=n,
        rows=tuple(out),
        width=max(len(r) for r in out),
        height=len(out),
        debug=dbg,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """``--cells N``, written to ``--man`` / ``--html`` / ``--json`` in one go."""
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--cells", type=int, default=16, metavar="N", help="how many cells")
    ap.add_argument("--man", type=Path, help="write the grid here")
    ap.add_argument("--html", type=Path, help="write the labelled debug overlay here")
    ap.add_argument("--json", type=Path, help="write the debug region sidecar here")
    args = ap.parse_args(argv)

    built = build_addr(args.cells)
    assert built.debug is not None  # the builder always emits its own map
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
