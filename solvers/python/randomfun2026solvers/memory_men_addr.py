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
    "TILE_X0",
    "DECODER_TILE",
    "CELL_TILE",
    "ROUTER_ROWS",
    "band_room",
    "build_addr",
]

#: Interior rows one cell occupies: return corridor, main line, branch lane.
#: Three, not five, because the only branch left in a tile is two-way.
BAND = 3

#: Interior column the tiles start at. Columns 0-2 belong to the spawner: the
#: ``Y`` sits in column 2 so its east child lands on the tile's first cell, and
#: the west child needs columns 0-1 to carry the increment back down.
TILE_X0 = 3

#: One decoder, and every decoder is this. ``B`` holds his address forever.
#:
#: ``r`` takes the broadcast address, ``~`` XORs it against ``B`` — zero for
#: exactly one man — and ``X`` turns on the result: straight is mine, clockwise
#: is everyone else. Mine forwards ``op`` and ``value`` to its cell; not-mine
#: swallows both and climbs home east of the mine lane, because the two paths may
#: not cross.
DECODER_TILE: tuple[str, ...] = (
    "v       <<",
    ">r~Xrsrs^ ",
    "   >rr   ^",
)

#: One cell, and every cell is this. ``B`` holds the value; ``A`` starts at 0,
#: which is the problem's "every cell starts at 0" for free.
#:
#: ``r`` takes the op word and ``X`` turns on it: READ is 0 (straight), WRITE is 1
#: (clockwise). READ still has to swallow the dummy value word, then ``W M s``
#: sends the value and puts it back in ``B``. WRITE is ``r`` then ``M``.
CELL_TILE: tuple[str, ...] = (
    "v      <<",
    ">rXrWMs^ ",
    "  >rM   ^",
)

#: The router: one op off the input stream becomes three broadcast words.
#:
#: ``r`` the op and ``X`` on it. The READ lane sends ``addr``, then ``0`` twice —
#: the op word and the dummy value. The WRITE lane sends ``addr``, ``1``, and the
#: value it reads next. The room must own **no other outgoing pipe**: ``S`` would
#: broadcast into that one too, which is why answers go to a separate collector.
ROUTER_ROWS: tuple[str, ...] = (
    ">rXrS0SS   v",
    "  >rS1SrS v ",
    "            ",
    "^    @    <<",
)

#: Where the input pipe must arrive (interior row of the ``r`` that reads ``op``).
ROUTER_IN_ROW = 0


def band_room(n: int, tile: Sequence[str], *, increment: bool) -> tuple[list[str], list[int]]:
    """``n`` copies of ``tile`` in one room, one per three-row band.

    Returns the interior rows and each band's *main* row — the row a tile reads
    and sends on, and therefore the row both of its pipes bind to.

    The spawner walks south down columns 0-2. Entering ``Y`` heading south puts
    the order-preserving child one cell **west** (facing west) and the newest one
    east (facing east), so the east child lands on his tile's ``>`` and starts
    looping while the west child walks the increment and drops back into column 2
    in time for the next band's ``Y``.

    With ``increment`` that westward walk is ``+`` ``M`` ``1``: the spawner keeps
    ``A = 1``, so ``+`` makes ``A = 1 + B``, ``M`` writes it back to ``B`` and
    ``1`` restores ``A``. Cell ``j`` is therefore born holding ``j``.
    """
    if n < 1:
        raise ValueError("a memory needs at least one cell")
    tile_w = max(len(r) for r in tile)
    iw = TILE_X0 + tile_w
    # one extra row on top: a man spawns facing east and has to be turned south
    # before he can enter the first `Y` heading south.
    ih = BAND * n + 1
    rows = [[" "] * iw for _ in range(ih)]

    def put(x: int, y: int, glyph: str) -> None:
        if rows[y][x] not in (" ", glyph):
            raise ValueError(f"collision at ({x},{y}): {rows[y][x]!r} vs {glyph!r}")
        rows[y][x] = glyph

    put(0, 0, "@")
    if increment:
        put(1, 0, "1")  # A = 1 for the whole walk; every `+` is an increment
    put(2, 0, "v")

    main_rows: list[int] = []
    for j in range(n):
        top = BAND * j + 1
        r_ret, r_main, r_alt = top, top + 1, top + 2
        main_rows.append(r_main)

        put(2, r_main, "Y")
        for dy, line in enumerate(tile):
            for dx, glyph in enumerate(line):
                if glyph != " ":
                    put(TILE_X0 + dx, r_ret + dy, glyph)

        if j + 1 == n:
            put(1, r_main, "H")  # no band left to seed, and no room to walk into
            continue
        # the west child: increment, round the corner, back to column 2 heading
        # south. Blank cells are walked over, so the no-increment room is the
        # same path with nothing on it.
        if increment:
            put(1, r_main, "+")
            put(0, r_alt, "M")
            put(1, r_ret + BAND, "1")
        put(0, r_main, "v")
        put(0, r_ret + BAND, ">")
        put(2, r_ret + BAND, "v")
    return ["".join(r) for r in rows], main_rows


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
    grid = Circuit(cox + coll_iw + 2, roy + ih + 2)

    def spanned(body: Sequence[str], iw: int) -> list[str]:
        # A pipe's first cell must sit against its source room's wall, and its
        # last against the destination's — so every room in the chain has to be
        # as tall as the field, empty rows and all.
        out = [r.ljust(iw) for r in body]
        return out + [" " * iw] * (ih - len(out))

    _room(grid, rox, roy, spanned(ROUTER_ROWS, router_iw))
    _room(grid, dx, roy, dec_body)
    _room(grid, cx, roy, cell_body)
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
            "`r` the op and `X` on it, then broadcast exactly three words: addr, op, "
            "value. A READ has no value, so it sends a dummy 0 — fixed width means an "
            "unselected decoder needs no branch to know how much to swallow. This room "
            "owns no other outgoing pipe on purpose: `S` would broadcast into it too."
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
            "A, so the next `Y` hands out the next address. This is the only difference "
            "between the two rooms' spawners."
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
            dx + TILE_X0,
            roy + main - 1,
            len(DECODER_TILE[1]),
            BAND,
            note=(
                f"B = {j} for life. `r` the broadcast address, `~` it against B: 0 only "
                "here. `X` straight = mine (forward op and value down), clockwise = not "
                "mine (swallow both, climb home east of the mine lane)."
            ),
            color=C_MID,
        )
        dbg.region(
            f"cell addr {j}",
            cx + TILE_X0,
            roy + main - 1,
            len(CELL_TILE[1]),
            BAND,
            note=(
                "value in B. READ: swallow the dummy, `W M s` sends it and puts it back. "
                "WRITE: `r` the value, `M` stores it. Never blocks anyone else — its "
                "decoder only speaks when this address is called."
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
