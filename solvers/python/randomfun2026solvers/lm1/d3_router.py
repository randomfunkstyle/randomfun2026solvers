#!/usr/bin/env python3
"""The DOOM command router — one CPU lane in, four DOOM units out.

``deadman-3d`` drives one 64x48 LM-75 through one :mod:`d3_unit` block hanging
off a single CPU stream lane.  The hi-res variant (``deadman-3d_hires``) wants
**128x96**, and the panel's hard ceiling is a 64x64 interior (``SPEC.md`` § The
LM-75 display), so the frame has to be tiled: four 64x48 panels in a 2x2 grid,
each one exactly the geometry the existing unit already paints.

Four panels means four units, and a unit is reached through a pipe.  Which pipe
an ``s`` talks to is a *static* property of where the glyph sits (``ARCH.md``
§7.1), so the CPU cannot pick a tile from an operand — the same wall ``DSP p``
hits, and the same answer :mod:`dsprelay` gives: move the choice **behind the
seam**, into a small room whose four ``s`` glyphs each sit statically beside
their own outlet.

Why a router rather than four CPU lanes
---------------------------------------

Four lanes would mean four opcodes, and the CPU's lane band is
``2 * (1 << k) - 1`` rows over ``k = (len(used) - 1).bit_length()`` — three more
opcodes is exactly the kind of pressure that pushes ``k`` up and doubles the
band.  It would also mean four new ``Sem``/ISA entries and four new bands in
:mod:`machine`, i.e. touching code every other slug shares.  The router costs
**one room** and leaves the CPU, the ISA and every other machine byte-identical:
``deadman-3d``'s own ``SND`` lane is unchanged, only the *word* it carries is.

The word
--------

One word per command, exactly as the unmodified unit's protocol::

    router word = 8 * (unit word) + sel = 8 * (8 * arg + code) + sel

``sel`` picks the destination the same way the unit's ``code`` picks an arm, and
the router recovers both halves with the arms' own literal-free ``M8W/`` — A
keeps the payload, B the selector, so a leaf is a bare ``s`` with **no second
receive**.  Floored ``/`` carries a negative payload through unharmed, which a
top-of-screen COL word needs (see :func:`d3_unit.word`).

:data:`SEL` names the five destinations.  Four are the tiles, west to east and
north to south::

    tile = (x >= 64) + 2 * (y >= 48)      in-tile = (x % 64, y % 48)

and the fifth is **ALL**, a broadcast leaf whose glyph is ``S`` rather than
``s``: *"send A into every outgoing pipe at once; blocks unless all have a free
source cell — never writes to only some"* (``SPEC.md``).

Tearing: what ``S`` guarantees, and what it does not
----------------------------------------------------

Each panel SWAPs on its own pipe, so it is worth being exact about which
property the composed image depends on.

**What ``S`` guarantees, exactly: frame-index correspondence.**  One glyph puts
the identical COMMIT word into all four command pipes, all or nothing, so every
panel receives precisely the same COMMIT sequence.  Tile frame *N* is therefore
always a piece of logical frame *N*, whatever tick it lands on, and
:func:`display.tiled_frames_from_writes` — which composes by index — can never
assemble a frame that is half-old.  Sending four separate COMMITs on four tile
selectors would give up exactly this: one dropped or reordered commit and every
later frame is stitched from mismatched halves.  The composition refuses to run
when the four commit counts disagree, which is that invariant made checkable.

**What it does not guarantee: a common tick.**  Two things pull the four SWAPs
apart, and they are not the same size.

The small one is pipe length: the four fan-out legs are 91, 322, 224 and 455
cells (:attr:`Wall.legs`), a pipe's length is its latency, so the COMMIT words
*arrive* up to 364 ticks apart.  Exact alignment is not reachable by padding — a
monotone leg's length is its Manhattan distance, the four distances here differ
in parity, and the fields that could absorb a detour are crossed by the other
legs — so it is measured rather than argued.

The large one is **backlog**, and it belongs to the driver, not the wall: a unit
only acts on a COMMIT once it has drained the paint commands queued ahead of it.
Feed the tiles one after another and the last one is still painting a whole
tile's worth of work when the first has long finished.  Measured on the demo's
title frame, that gap was 221,808 ticks with the tiles fed in turn and 85,340
with the same words round-robined — three orders of magnitude above the pipe
term, and fixed in software (:func:`deadman3d_hires.interleave`), not here.

None of it changes what the composed frame looks like, because that is indexed,
not timed.  It would only matter to something watching the four raw panels live,
and nothing does: the engine renders a display's contents nowhere in the grid.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..circuit import Circuit

__all__ = [
    "BROADCAST",
    "DEST_LEAF",
    "IH",
    "IW",
    "LEAF0",
    "LEAF_PITCH",
    "R_COLLECT",
    "R_SEND",
    "SEL",
    "TILES",
    "TILE_FLOOR_ROW",
    "Router",
    "RouterError",
    "Wall",
    "build_probe",
    "build_wall",
    "leaf_codes",
    "outlet_cols",
    "router_interior",
    "sel_codes",
    "word",
]


class RouterError(RuntimeError):
    """The router's geometry or binding did not close."""


# ── the room's row map ───────────────────────────────────────────────────────
R_MAIN = 1  # the read, the unpack and the walk east to the trie
R_TRIE = 2  # rows 2..4 (TRIE_BITS levels), fanning sideways
R_SEND = 6  # every leaf's `s` sits on this row: south-wall binding is by column
R_COLLECT = 8  # every leaf rejoins here and walks back west to MAIN

#: Trie geometry, :mod:`d3_unit`'s verbatim: eight leaves at
#: ``LEAF0 + LEAF_PITCH * i``, the entry column midway.  The pitch only has to
#: exceed twice the leaf row's distance to the south wall for the outlets to
#: bind unambiguously; 12 leaves a margin of 12 against a required 1.
LEAF0 = 3
LEAF_PITCH = 12
TRIE_BITS = 3
TRIE_COL = LEAF0 + LEAF_PITCH * ((1 << TRIE_BITS) - 1) // 2

#: Which leaf each destination hangs from, **west to east**.  Not free, and not
#: tile order: :func:`build_wall` stacks the four blocks in a 2x2 and every leg
#: runs *east* out of its outlet, so a leg reaching further east must turn on a
#: shallower row than one it crosses — which fixes the outlets' west-to-east
#: order as **bottom-left, bottom-right, top-left, top-right** (the two legs that
#: dive past the top row of blocks must start west of the two that do not).  The
#: fifth leaf is the broadcast.
DEST_LEAF: dict[str, int] = {"T2": 0, "T3": 1, "T0": 2, "T1": 3, "ALL": 4}

#: The broadcast destination — an ``S``, not an ``s``.
BROADCAST = "ALL"

#: The four tiles, in outlet order.
TILES: tuple[str, ...] = ("T0", "T1", "T2", "T3")

IW = LEAF0 + LEAF_PITCH * ((1 << TRIE_BITS) - 1) + 3
IH = R_COLLECT

#: The command port's column on the north wall.  The room has exactly one
#: incoming pipe, so the single ``r`` binds it wherever it sits; the column is
#: chosen to sit above the read.
CMD_COL = 3


def _bit_of(level: int) -> int:
    return 1 << (level - 1)


def leaf_codes() -> list[int]:
    """The selector value that reaches each leaf, west to east.

    Read off the trie rather than assigned, exactly as :func:`d3_unit.arm_codes`
    does: ``x`` turns clockwise when BP's low bit is 1 and a man heading south
    turns clockwise to the **west**, so a west branch means that bit is set.
    """
    codes: dict[int, int] = {}

    def walk(level: int, col: int, code: int) -> None:
        step = LEAF_PITCH * (1 << (TRIE_BITS - level)) // 2
        for sign, bit in ((-1, 1), (+1, 0)):
            nxt = col + sign * step
            acc = code | (bit * _bit_of(level))
            if level < TRIE_BITS:
                walk(level + 1, nxt, acc)
            else:
                codes[nxt] = acc

    walk(1, TRIE_COL, 0)
    return [codes[c] for c in sorted(codes)]


def sel_codes() -> dict[str, int]:
    """Destination -> selector value."""
    codes = leaf_codes()
    return {dest: codes[leaf] for dest, leaf in DEST_LEAF.items()}


#: Destination -> selector value, resolved once (the asm's ``.equ R_*``).
SEL: dict[str, int] = sel_codes()


def outlet_cols() -> list[int]:
    """South-wall column of each tile's outlet, indexed by **tile number**.

    ``S`` broadcasts to *every* outgoing pipe, so the order the four are drawn in
    is what the engine's pipe list will hold; indexing by tile keeps the callers
    honest about which panel an outlet feeds (it is not the column order — see
    :data:`DEST_LEAF`).
    """
    return [LEAF0 + LEAF_PITCH * DEST_LEAF[t] for t in TILES]


def word(unit_word: int, sel: int) -> int:
    """One router word.  Floored ``/`` recovers a negative payload too."""
    return 8 * unit_word + sel


# ── the room's interior ──────────────────────────────────────────────────────
@dataclass
class Router:
    """The router's interior, plus where each of its pipes must attach."""

    cells: dict[tuple[int, int], str]
    width: int = IW
    height: int = IH
    #: south-wall columns, west to east, one per tile
    south: list[int] = field(default_factory=list)
    #: north-wall column of the single incoming command pipe
    north: int = CMD_COL
    #: every pipe glyph: ``(x, y, glyph, dest)`` in interior coordinates
    glyphs: list[tuple[int, int, str, str]] = field(default_factory=list)
    sel: dict[str, int] = field(default_factory=dict)


def router_interior() -> Router:
    """Lay the room: the read, the unpack, the trie, five leaves, the collector."""
    c = Circuit(IW + 2, IH + 2)
    glyphs: list[tuple[int, int, str, str]] = []
    outlets = outlet_cols()

    # ── MAIN: the word arrives from the north; split it into payload | sel ───
    # `r`   A = word
    # `M8W` B = word, A = 8, then A = word, B = 8
    # `/`   A = word / 8 (the payload), B = word mod 8 (the selector)
    # `WbW` A = sel -> BP, then A = payload again
    c.set(1, R_MAIN, ">")
    c.set(2, R_MAIN, "@")
    c.set(3, R_MAIN, "r")
    glyphs.append((3, R_MAIN, "r", "cmd"))
    c.run(4, R_MAIN, "M8W/WbW")
    c.horizontal(R_MAIN, 10, TRIE_COL)
    c.set(TRIE_COL, R_MAIN, "v")

    # ── the decode trie, fanning sideways: leaves are columns ────────────────
    def trie(level: int, x: int) -> None:
        row = R_TRIE + level - 1
        step = LEAF_PITCH * (1 << (TRIE_BITS - level)) // 2
        c.set(x, row, "x")
        for sign in (-1, +1):
            for d in range(1, step + 1):
                c.set(x + sign * d, row, "v" if d == step else ("]" if d == 1 else " "))
            if level < TRIE_BITS:
                trie(level + 1, x + sign * step)

    trie(1, TRIE_COL)
    leaf_row = R_TRIE + TRIE_BITS - 1

    # ── the leaves: descend to R_SEND, send, carry on to the collector ───────
    east = 2
    for dest, leaf in DEST_LEAF.items():
        x = LEAF0 + LEAF_PITCH * leaf
        c.vertical(x, leaf_row, R_SEND)
        glyph = "S" if dest == BROADCAST else "s"
        c.set(x, R_SEND, glyph)
        glyphs.append((x, R_SEND, glyph, dest))
        c.vertical(x, R_SEND, R_COLLECT)
        east = max(east, x)

    # ── the collector: every leaf arrives southbound and turns west ──────────
    for xx in range(2, east + 1):
        c.set(xx, R_COLLECT, "<")
    c.set(1, R_COLLECT, "^")
    c.vertical(1, R_COLLECT, R_MAIN)

    r = Router(
        cells={k: v for k, v in c.cell.items() if v != " "},
        south=outlets,
        glyphs=glyphs,
        sel=sel_codes(),
    )
    _check_router(r)
    return r


def _check_router(r: Router) -> None:
    """Every tile ``s`` must be strictly nearest its own outlet."""
    for gx, gy, glyph, dest in r.glyphs:
        if glyph != "s":
            continue  # `r` has one rival and `S` binds nothing
        mine = TILES.index(dest)
        dists = [abs(gx - col) + (IH + 1 - gy) for col in r.south]
        own = dists[mine]
        margin = min(d for i, d in enumerate(dists) if i != mine) - own
        if margin < 1:
            raise RouterError(
                f"the leaf `s` at {(gx, gy)} is only {margin} nearer its own outlet "
                "than the runner-up: a margin of 0 is a reading-order tie"
            )


# ── the placed wall: the router plus four DOOM blocks ────────────────────────
#: Where the router room's north-west corner sits in the wall's own coordinates.
#: Row 1, not 0, so the incoming command pipe has a cell above the north wall.
RX, RY = 0, 1

#: The block grid.  ``GAP_X``/``GAP_Y`` are the corridors the fan-out legs turn
#: in; two rows of ``GAP_Y`` are lanes and the rest is slack.
GAP_X = 8
GAP_Y = 8

#: The blocks' west margin — how far east of the wall's own edge column 0 the
#: 2x2 starts.  It used to be ``IW + 4`` (94), which put the whole router room
#: *beside* block 0 and charged its 92 columns to the wall's width for nothing:
#: the router is 10 rows tall and the blocks start at row 18, so it fits
#: perfectly well **above** block 0's left-hand columns.
#:
#: What the margin actually has to cover is the two **lower** tiles' legs.  A
#: leg leaves the router's south wall, drops to its lane row and runs east, and
#: the bottom row's lanes are ~114 rows below the router — so those two descents
#: have to pass the whole height of the top row of blocks, which means their
#: outlet columns must stay west of ``bx0``.  :data:`DEST_LEAF` already puts the
#: bottom tiles on the two westernmost leaves (``T2`` at 3, ``T3`` at 15), so
#: clearing column 15 is the whole requirement and 17 is the first value that
#: does it.  The top tiles' legs turn two rows above the blocks and never
#: descend past them, so their outlets (27, 39) may sit over block 0 freely.
#:
#: 572 -> 495 columns, which is a quarter of the wall and — with the ROM folded
#: (``machine.ROM_ROWS``) — a quarter off the machine's binding side.
BLOCK_X0 = 17

#: The last panel row COL's floor run fills, per tile row.  At 128x96 the 3D
#: viewport is logical rows 0..79 and the HUD is 80..95, so a **top** tile is
#: viewport all the way down (floor to its own row 47) and a **bottom** tile stops
#: at row 31, where the HUD strip begins.  The single-panel unit bakes 39 for the
#: same reason (:data:`d3_unit.FLOOR_ROW`); it is the one geometric constant in the
#: COL arm that a tiled framebuffer cannot share.
TILE_FLOOR_ROW: tuple[int, int, int, int] = (47, 47, 31, 31)


@dataclass
class Wall:
    """Four DOOM blocks, a router, and the one pipe the CPU has to reach."""

    cells: dict[tuple[int, int], str]
    width: int
    height: int
    #: the cell a command pipe from the CPU must end on, pointing south
    cmd_cell: tuple[int, int]
    pipes: int
    #: per tile, the panel's north-west *wall* corner in wall coordinates
    panels: list[tuple[int, int]] = field(default_factory=list)
    #: per tile, the length of the router -> block command pipe
    legs: list[int] = field(default_factory=list)
    #: per tile, the last panel row COL's floor run fills
    floor_rows: tuple[int, ...] = TILE_FLOOR_ROW
    sel: dict[str, int] = field(default_factory=dict)
    codes: dict[str, int] = field(default_factory=dict)
    regions: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)


def build_wall() -> Wall:
    """Place the router and the four unmodified DOOM blocks, and wire the fan-out.

    The blocks go in a 2x2 — tile 0 top-left, tile 3 bottom-right.  *Nothing*
    about that is visual: the engine renders a display's contents nowhere in the
    grid, so the tiling is composed in software (the four panels are four
    separate frame streams) and the blocks could sit anywhere.  It is a footprint
    choice, and the honest version of it is that the wall alone is **not** where
    the 2x2 wins.  A 235x101 block gives, wall only,

        2x2 572x228 (max 572) | 1x4 1058x120 | 4x1 329x446 (max 446)

    so on its own the single stack is squarest.  What decides it is that the wall
    hangs *below* a whole machine: the totals are ``max(W, wall_w)`` by
    ``H + wall_h``, and the height is the one that adds.  Against the eventual
    hi-res raycaster's CPU-plus-store (``H`` ~380, as the 64x48 machine is today)
    the 2x2 lands at 608 and the stack at 826.  Against *this* demo's near-empty
    CPU the stack would in fact win, 486 to 572 — worth writing down, because it
    means the arrangement is a free variable to re-sweep once the raycaster lands
    and ``H`` is real.

    Every leg leaves the router's south wall, drops to its own lane row, runs
    **east** and drops into its block's command port.  A leg's length is then
    ``(by - south_wall) + (cmd_x - outlet)`` — the vertical legs telescope — so
    the lane rows are free and only the block corners and the outlet order
    decide it.  :data:`DEST_LEAF` explains why that order is what it is.
    """
    from . import d3_unit
    from .machine import _Grid

    blocks = [d3_unit.build_doom(row) for row in TILE_FLOOR_ROW]
    blk = blocks[0]  # every variant has the same footprint and the same ports
    if {(b.width, b.height, b.cmd_cell, b.panel, b.pipes) for b in blocks} != {
        (blk.width, blk.height, blk.cmd_cell, blk.panel, blk.pipes)
    }:
        raise RouterError(
            "the four tile variants do not share a footprint: floor_row was supposed "
            "to move nothing but one two-digit literal"
        )
    r = router_interior()
    g = _Grid()

    g.room(RX, RY, RX + IW + 1, RY + IH + 1)
    g.blit(RX, RY, r.cells)
    south_wall = RY + IH + 1

    # The blocks: a 2x2 whose west margin clears the two long leg descents (see
    # :data:`BLOCK_X0`); the router itself sits *above* block 0, not beside it.
    bx0 = RX + BLOCK_X0
    bx1 = bx0 + blk.width + GAP_X
    by0 = south_wall + 1 + GAP_Y - 1
    by1 = by0 + blk.height + GAP_Y
    corners = [(bx0, by0), (bx1, by0), (bx0, by1), (bx1, by1)]
    for block, (bx, by) in zip(blocks, corners, strict=True):
        g.blit(bx, by, block.cells)

    # The lanes.  Within each row of blocks the leg that reaches further east
    # must turn *above* the one it crosses, so the west block's lane is the
    # deeper of the pair.
    lanes = {0: by0 - 2, 1: by0 - 3, 2: by1 - 2, 3: by1 - 3}
    outlets = outlet_cols()
    legs: list[int] = []
    for tile, (bx, by) in enumerate(corners):
        ox = RX + outlets[tile]
        cmd_x, cmd_y = bx + blk.cmd_cell[0], by + blk.cmd_cell[1]
        lane = lanes[tile]
        if ox >= cmd_x:
            raise RouterError(
                f"tile {tile}'s outlet at column {ox} is not west of its command "
                f"port at {cmd_x}: the leg would run west and cross its neighbours"
            )
        legs.append(
            g.draw_pipe([(ox, south_wall + 1), (ox, lane), (cmd_x, lane), (cmd_x, cmd_y)])
        )

    rows = g.rows()
    regions: dict[str, tuple[int, int, int, int]] = {
        "router": (RX, RY, IW + 2, IH + 2),
    }
    panels: list[tuple[int, int]] = []
    for tile, (bx, by) in enumerate(corners):
        panels.append((bx + blk.panel[0], by + blk.panel[1]))
        for name, (x, y, w, h) in blk.regions.items():
            regions[f"t{tile}:{name}"] = (bx + x, by + y, w, h)

    return Wall(
        cells=g.c,
        width=max(len(row) for row in rows),
        height=len(rows),
        cmd_cell=(RX + CMD_COL, RY - 1),
        pipes=4 + 4 * blk.pipes,
        panels=panels,
        legs=legs,
        floor_rows=TILE_FLOOR_ROW,
        sel=r.sel,
        codes=blk.codes,
        regions=regions,
    )


# ── the probe: the wall plus the smallest possible driver ────────────────────
def build_probe(commands: Sequence[int]) -> tuple[list[str], Wall]:
    """The wall plus a room that recites ``commands`` into the router and halts.

    The whole protocol is write-only, so the grid is standalone: run it with no
    input at all and it paints all four panels.
    """
    from .machine import _Grid

    wall = build_wall()
    ox, oy = 8, 6
    g = _Grid()
    for (x, y), ch in wall.cells.items():
        g.put(ox + x, oy + y, ch)

    cells: dict[tuple[int, int], str] = {(1, 1): "@", (2, 1): "v"}
    y = 2
    for w in commands:
        for ch in f"`{abs(w)}`" + ("N" if w < 0 else "") + "s":
            cells[(2, y)] = ch
            y += 1
    cells[(2, y)] = "H"
    fw, fh = 2, y

    fx, fy = 0, 0
    g.room(fx, fy, fx + fw + 1, fy + fh + 1)
    g.blit(fx, fy, cells)
    cx, cy = ox + wall.cmd_cell[0], oy + wall.cmd_cell[1]
    g.draw_pipe([(fx + fw + 2, fy + 1), (cx, fy + 1), (cx, cy)])
    return g.rows(), wall


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--ruler", action="store_true")
    ap.add_argument("--wall", action="store_true", help="print the placed 2x2 wall")
    ap.add_argument("--commands", default="", help="probe command words")
    args = ap.parse_args(argv)
    if args.wall or args.commands:
        rows, wall = build_probe([int(v) for v in args.commands.split()])
        print("\n".join(rows))
        print(
            f"# wall {wall.width}x{wall.height}, pipes={wall.pipes}, sel={wall.sel}, "
            f"legs={wall.legs}, panels={wall.panels}"
        )
        return 0
    r = router_interior()
    c = Circuit(IW + 2, IH + 2)
    for (x, y), ch in r.cells.items():
        c.set(x, y, ch)
    print(c.ruler() if args.ruler else "\n".join(c.rows()))
    print(f"# router {IW}x{IH}, sel={r.sel}, outlets={r.south}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
