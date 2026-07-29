#!/usr/bin/env python3
"""The DOOM command router — one CPU lane in, four DOOM units out.

``deadman-3d`` drives one 64x48 LM-75 through one :mod:`d3_unit` block hanging
off a single CPU stream lane.  The hi-res variant (``deadman-3d_hires``) wants
**128x96**, and the panel's hard ceiling is a 64x64 interior (``SPEC.md`` § The
LM-75 display), so the frame has to be tiled: four 64x48 panels in a 2x2 grid,
each one exactly the geometry the existing unit already paints.

The four panels are one **cluster**, two columns and three rows apart
(:func:`build_packed_wall`), so the thing on the grid is a contiguous 128x96
screen rather than four monitors scattered across it — which took taking the
panel off the block (:func:`d3_unit.build_logic`) and re-routing all twelve
port pipes around a shared cluster.  :func:`build_wall`, where each panel
still sits inside its own block, is kept for the probe and for the tests that
pin the router's own geometry.

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

The small one is pipe length: the four fan-out legs are 14, 308, 237 and 531
cells (:attr:`Wall.legs`), a pipe's length is its latency, so the COMMIT words
*arrive* up to 517 ticks apart.  Exact alignment is not reachable by padding — a
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ..circuit import Circuit

__all__ = [
    "BROADCAST",
    "DEST_LEAF",
    "GUTTER_X",
    "GUTTER_Y",
    "IH",
    "IW",
    "LEAF0",
    "LEAF_PITCH",
    "R_COLLECT",
    "R_SEND",
    "SEL",
    "SWAP_LEAD",
    "TILES",
    "TILE_FLOOR_ROW",
    "Cluster",
    "Router",
    "RouterError",
    "Wall",
    "build_packed_wall",
    "build_probe",
    "build_wall",
    "cluster_at",
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


def build_wall(loop_row: int | None = None) -> Wall:
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

    ``loop_row`` is :func:`d3_unit.build_doom`'s, handed to all four blocks at
    once — and this is the one arrangement where it pays **twice**, because the
    2x2 stacks two block heights and the wall's own height is what adds to the
    machine's.  ``None`` keeps ``d3_unit.R_LOOP``, so the standalone probe and
    any wall built without an opt-in stay byte-identical.  ``machine.
    DOOM_LOOP_ROW`` is where a tier opts in; the wall does not choose for
    itself, because the row is only worth anything against a *particular*
    machine's fold (see ``ROM_ROWS``).
    """
    from . import d3_unit
    from .machine import _Grid

    if loop_row is None:
        loop_row = d3_unit.R_LOOP
    blocks = [d3_unit.build_doom(row, loop_row) for row in TILE_FLOOR_ROW]
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


# ── the packed wall: one 2x2 cluster, four logic blocks west of it ───────────
#: Free cells between the four panels' facing walls — ``scratch/deadman3d-opt/
#: panel_pack.py``'s sweep, which drove bare panels against the real engine:
#: side by side gap 0 is undrawable (the east panel's DATA arrowhead has to sit
#: immediately west of its own left wall, and at gap 0 that cell *is* the west
#: panel's wall); stacked, gap 0 leaves no row for either the upper SWAP's
#: arrowhead or the lower ADDR's; and the 2x2 needs ``gy >= 2`` because the band
#: between the panel rows carries four arrowheads — two SWAPs pointing north in
#: its first row, two ADDRs pointing south in its last — and a band row can only
#: be entered from its west or east end, so one row carries at most two.
#:
#: ``gx`` is 2 rather than 1 for a different reason, and it is the whole reason
#: the twelve pipes can be routed at all: the corridor's **east** column is
#: spoken for (both east panels' DATA arrowheads must sit in it), and the west
#: column is then a free north-south tunnel — the passage NE's SWAP comes down
#: to the band's first row and SE's SWAP comes down to the south edge.
#:
#: ``gy`` is **6**, and every one of the six is spoken for.  The cluster is fed
#: entirely from its west side (:func:`build_packed_wall` says why), so the band
#: is not a corridor the wall crosses — it is where four of the twelve pipes
#: arrive.  Its first row carries the two north panels' SWAP arrowheads and its
#: last the two south panels' ADDR arrowheads, exactly as the sweep says; the
#: four rows between are the approach lanes for SE's ADDR, SE's DATA, SE's SWAP
#: and SW's ADDR, which must be four *different* rows because each turns south
#: at a different column and a lane crossing another's descent is a collision.
#: They are ordered east-target-first, which is the same rule the four rows
#: above the cluster's north edge follow.
GUTTER_X, GUTTER_Y = 2, 6


@dataclass
class Cluster:
    """The 2x2 of panels, and every cell a pipe is allowed to terminate on."""

    #: per tile, the panel's north-west *wall* corner
    corners: list[tuple[int, int]]
    width: int
    height: int
    #: the corridor's free column (north-south through the whole cluster)
    tunnel: int
    #: the corridor's east column — where both east panels' DATA arrowheads sit
    corridor: int
    #: the band's first row (north SWAPs) and last row (south ADDRs)
    band: tuple[int, int]
    #: the band's middle row — free all the way across, and the only east-west
    #: highway through the cluster there is
    lane: int
    #: the column immediately west of the west panels' left walls
    west_data: int
    #: the row immediately below the cluster (south SWAP arrowheads)
    south: int
    #: the row immediately above the cluster (north ADDR arrowheads)
    north: int


def cluster_at(cx: int, cy: int, gx: int = GUTTER_X, gy: int = GUTTER_Y) -> Cluster:
    """The cluster's whole geometry from its north-west wall corner."""
    from . import d3_unit

    pw, ph = d3_unit.PANEL_W + 2, d3_unit.PANEL_H + 2  # with walls
    x1, y1 = cx + pw + gx, cy + ph + gy
    return Cluster(
        corners=[(cx, cy), (x1, cy), (cx, y1), (x1, y1)],
        width=pw * 2 + gx,
        height=ph * 2 + gy,
        tunnel=cx + pw,
        corridor=x1 - 1,
        band=(cy + ph, y1 - 1),
        lane=cy + ph + 1,
        west_data=cx - 1,
        south=y1 + ph,
        north=cy - 1,
    )


#: The packed wall's row and column map.  Every one of these is a *free* choice
#: constrained by the crossing arguments in :func:`build_packed_wall`; they are
#: named rather than inlined so the assertions there can be read against them.
#:
#: Columns: the router's margin (:data:`BLOCK_X0`) | the west logic column |
#: :data:`PACK_CH_W` | the middle logic column | :data:`PACK_CH_E` | the cluster.
#: Rows: router and its lane | the north blocks | the gutter | the south blocks
#: | the return band.
PACK_CH_W = 3  # the west channel: three descent columns, one per port band
PACK_CH_E = 13  # twelve one-net columns plus the column DATA arrives on

#: The north blocks' *command* row — the cell the router's leg ends on, one above
#: a block's north wall.  Both north blocks share it, exactly as
#: :func:`build_wall` has them, and that is not cosmetic: the leg fan is only
#: planar when the two legs of a row can take two lanes of the same corridor.
#:
#: 13 rather than 12 because a leg has to leave the router *southward*: a pipe
#: starts at the arrowhead whose backward cell is on the source room's border
#: (``SPEC.md`` § Pipes), so a leg that turns east in the very cell below the
#: router's south wall has no source at all — the engine loads it, binds it to
#: nothing, and the router silently drives two tiles instead of four.  One free
#: row between the router's wall and the command row is what buys the turn.
PACK_ROW_N = 13

#: Free rows between the two block rows.  Three carry the west blocks' upper
#: port pipes east across the middle column, and the remaining two are the two
#: south legs' lanes — a leg has to arrive at a command cell from the *north*,
#: so it needs a free row above the south blocks' command row, and the two legs
#: cannot share one because their eastward runs overlap.
PACK_GAP = 5

#: The cluster's north-west wall corner row, searched rather than chosen: it is
#: the one free variable the twelve pipes' length invariants all see at once
#: (:func:`_pack_cluster_row`).
PACK_CLUSTER_Y: int | None = None

#: How many cells SWAP must lead DATA by.  ``build_doom`` only refuses a tie
#: ("a commit could overtake the pixels it commits"), which is enough when the
#: three pipes are 15 to 35 cells long.  Packed they are 300-odd, and the
#: interesting race is the *other* one: a COMMIT still in flight when the next
#: frame's first ADDR lands commits that pixel into the wrong buffer.  The
#: shipped block's own margin is ``35 - 15 = 20``, and the unit cannot emit a
#: paint sooner than a trie walk plus a collector walk after a COMMIT, so 20 is
#: kept as the floor rather than re-derived.
SWAP_LEAD = 20


def _plen(points: list[tuple[int, int]]) -> int:
    """A rectilinear polyline's cell count, without drawing it."""
    return 1 + sum(abs(x1 - x0) + abs(y1 - y0)
                   for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False))


#: The twelve nets of the cluster channel.  Only the *order* of their columns is
#: searched; which column a net turns in never changes its length, because a
#: route that runs east to ``c``, then vertical, then east again is
#: ``(target - source) + |exit - entry|`` whatever ``c`` is.
PACK_NETS = tuple((t, b) for t in (1, 0, 3, 2) for b in ("addr", "data", "swap"))


def _pack_rows(cl: "Cluster", port, gap_rows, bot_rows) -> dict:
    """Each net's ``(entry row, exit row)`` in the cluster channel.

    The entry row is where the pipe arrives from the west — a middle block's own
    port row, or the gutter/return row a west block crossed the middle column on.
    The exit row is where it leaves eastward, which is fixed for eleven of the
    twelve and *is* the free terminal for the two west DATA pipes.
    """
    n = cl.north
    b0 = cl.band[0]
    rows = {}
    for i, band in enumerate(("addr", "data", "swap")):
        rows[1, band] = (port(1, band)[1], n - 4 + i)
    rows[0, "addr"] = (gap_rows[0], n - 1)
    rows[0, "data"] = (gap_rows[1], None)
    rows[0, "swap"] = (gap_rows[2], b0)
    for i, band in enumerate(("addr", "data", "swap")):
        rows[3, band] = (port(3, band)[1], b0 + 1 + i)
    rows[2, "addr"] = (bot_rows[0], b0 + 4)
    rows[2, "data"] = (bot_rows[1], None)
    rows[2, "swap"] = (bot_rows[2], cl.south)
    return rows


def _pack_routes(cl: "Cluster", col: dict, port, gap_rows, bot_rows, c1) -> dict:
    """Every pipe's corner list as a function of its one free terminal.

    Twelve routes, three per tile, and each is monotone in its own free
    coordinate — an ADDR's terminal column, a DATA's terminal row, a SWAP's
    terminal column — so its length is affine in it and :func:`_pack_solve` can
    scan rather than search.  ``col`` gives each net its channel column, which
    :func:`_pack_order` works out; see :func:`build_packed_wall`.
    """
    n = cl.north            # the row ADDR arrowheads sit on, above the top walls
    b0, b1 = cl.band        # the band's first row (SWAPs) and last row (ADDRs)
    tun, cor, wd = cl.tunnel, cl.corridor, cl.west_data
    ent = _pack_rows(cl, port, gap_rows, bot_rows)

    def k(tile: int, band: str) -> tuple[int, int, int]:
        """The net's channel column and its two channel rows."""
        return (col[tile, band], *ent[tile, band][:1], ent[tile, band][1])

    r = {}
    # -- tile 1, the north-east panel: the middle block on the north row.  Its
    #    three ports go straight east into the channel and turn; ADDR descends to
    #    the north edge, DATA down the corridor, SWAP down the tunnel to the
    #    band's first row and then east ---------------------------------------
    a, d, s = (port(1, x) for x in ("addr", "data", "swap"))
    r[1, "addr"] = lambda c, a=a, k=col[1, "addr"], y=ent[1, "addr"][1]: [
        a, (k, a[1]), (k, y), (c, y), (c, n)]
    r[1, "data"] = lambda y, d=d, k=col[1, "data"], q=ent[1, "data"][1]: [
        d, (k, d[1]), (k, q), (cor, q), (cor, y)]
    r[1, "swap"] = lambda c, s=s, k=col[1, "swap"], y=ent[1, "swap"][1]: [
        s, (k, s[1]), (k, y), (tun, y), (tun, b0), (c, b0)]

    # -- tile 0, the north-west panel: the *west* block on the north row.  Its
    #    ports cannot see the channel — the middle block is in the way — so they
    #    take the west channel to the gutter rows and cross the middle column
    #    along them.  Everything after that is the same shape -----------------
    a, d, s = (port(0, x) for x in ("addr", "data", "swap"))
    r[0, "addr"] = lambda c, a=a, k=col[0, "addr"], y=ent[0, "addr"][1]: [
        a, (c1[2], a[1]), (c1[2], gap_rows[0]), (k, gap_rows[0]), (k, y),
        (c, y), (c, n)]
    r[0, "data"] = lambda y, d=d, k=col[0, "data"]: [
        d, (c1[1], d[1]), (c1[1], gap_rows[1]), (k, gap_rows[1]), (k, y), (wd, y)]
    r[0, "swap"] = lambda c, s=s, k=col[0, "swap"]: [
        s, (c1[0], s[1]), (c1[0], gap_rows[2]), (k, gap_rows[2]), (k, b0), (c, b0)]

    # -- tile 3, the south-east panel: the middle block on the south row, and
    #    the band's four inner rows are its (and SW's ADDR's) approach lanes ---
    a, d, s = (port(3, x) for x in ("addr", "data", "swap"))
    r[3, "addr"] = lambda c, a=a, k=col[3, "addr"], y=ent[3, "addr"][1]: [
        a, (k, a[1]), (k, y), (c, y), (c, b1)]
    r[3, "data"] = lambda y, d=d, k=col[3, "data"], q=ent[3, "data"][1]: [
        d, (k, d[1]), (k, q), (cor, q), (cor, y)]
    r[3, "swap"] = lambda c, s=s, k=col[3, "swap"], y=ent[3, "swap"][1]: [
        s, (k, s[1]), (k, y), (tun, y), (tun, cl.south), (c, cl.south)]

    # -- tile 2, the south-west panel: the west block on the south row, crossing
    #    the middle column along the band below the whole wall ----------------
    a, d, s = (port(2, x) for x in ("addr", "data", "swap"))
    r[2, "addr"] = lambda c, a=a, k=col[2, "addr"], y=ent[2, "addr"][1]: [
        a, (c1[2], a[1]), (c1[2], bot_rows[0]), (k, bot_rows[0]), (k, y),
        (c, y), (c, b1)]
    r[2, "data"] = lambda y, d=d, k=col[2, "data"]: [
        d, (c1[1], d[1]), (c1[1], bot_rows[1]), (k, bot_rows[1]), (k, y), (wd, y)]
    r[2, "swap"] = lambda c, s=s, k=col[2, "swap"]: [
        s, (c1[0], s[1]), (c1[0], bot_rows[2]), (k, bot_rows[2]), (k, cl.south),
        (c, cl.south)]
    return r


def _pack_order(spans: dict) -> list:
    """West-to-east order for the twelve channel columns, or a failure.

    A pipe holds its column from the row it enters the channel on to the row it
    leaves on, and a pipe turning east at column ``c`` crosses every channel
    column east of ``c``.  So ``P`` may sit west of ``Q`` only if ``Q``'s entry
    row misses ``P``'s span and ``P``'s exit row misses ``Q``'s.  Some pairs
    allow one direction, some both; a pair that allows neither — or a cycle in
    the forced ones — means this cluster row has no planar channel at all, which
    is a real answer about the geometry and not a bug.
    """
    def span(p):
        e, a = spans[p]
        return e, a, min(e, a), max(e, a)

    def ok(p, q) -> bool:
        _pe, pa, plo, phi = span(p)
        qe, _qa, qlo, qhi = span(q)
        return not (plo <= qe <= phi) and not (qlo <= pa <= qhi)

    nets = list(spans)
    after = {p: set() for p in nets}      # p must come before everything in after[p]
    for i, p in enumerate(nets):
        for q in nets[i + 1:]:
            fwd, bwd = ok(p, q), ok(q, p)
            if not fwd and not bwd:
                raise RouterError(
                    f"{p} and {q} cannot share the cluster channel in either order: "
                    "each one's turn crosses the other's column"
                )
            if fwd and not bwd:
                after[p].add(q)
            elif bwd and not fwd:
                after[q].add(p)

    order: list = []
    left = list(nets)
    while left:
        free = [p for p in left if not (after[p] & set(left))]
        if not free:
            raise RouterError(
                "the cluster channel's west-of relation has a cycle: no column "
                "order routes all twelve pipes without a crossing"
            )
        order.append(free[0])
        left.remove(free[0])
    order.reverse()
    return order


#: The arrowhead each terminal needs, which is *not* the one ``draw_pipe`` would
#: infer: an ADDR that turns south in the very cell it ends on, a DATA that turns
#: east in its, a SWAP that turns north in its.
PACK_END = {"addr": "v", "data": ">", "swap": "^"}


def _pack_solve(cl: "Cluster", routes: dict) -> dict[tuple[int, str], int]:
    """Pick the twelve free terminals so every tile meets its two invariants.

    ``len(addr) == len(data)`` and ``len(swap) >= len(data) + SWAP_LEAD`` are
    :func:`d3_unit.build_doom`'s, and here they are solved rather than chosen:
    ADDR's terminal column is scanned outward from the panel's west edge, then
    DATA's row is the one that matches its length and SWAP's column the first
    that clears it.  Every route is monotone in its own terminal, so each scan is
    exact and failing is honest — a wall that cannot meet the invariants paints
    at the wrong cursor rather than not at all.
    """
    from . import d3_unit

    cx, cy = cl.corners[0]
    pw, ph = d3_unit.PANEL_W, d3_unit.PANEL_H
    ex, ey = cl.corners[3]
    cols = {0: range(cx + 1, cx + pw + 1), 2: range(cx + 1, cx + pw + 1),
            1: range(ex + 1, ex + pw + 1), 3: range(ex + 1, ex + pw + 1)}
    rows = {0: range(cy + 1, cy + ph + 1), 1: range(cy + 1, cy + ph + 1),
            2: range(ey + 1, ey + ph + 1), 3: range(ey + 1, ey + ph + 1)}

    out: dict[tuple[int, str], int] = {}
    for tile in range(4):
        for col_a in cols[tile]:
            la = _plen(routes[tile, "addr"](col_a))
            row_d = next((y for y in rows[tile]
                          if _plen(routes[tile, "data"](y)) == la), None)
            if row_d is None:
                continue
            col_s = next((c for c in cols[tile]
                          if _plen(routes[tile, "swap"](c)) >= la + SWAP_LEAD), None)
            if col_s is None:
                continue
            out[tile, "addr"], out[tile, "data"], out[tile, "swap"] = col_a, row_d, col_s
            break
        else:
            raise RouterError(
                f"tile {tile}: no ADDR column in {cols[tile].start}.."
                f"{cols[tile].stop - 1} admits a DATA row of the same length and a "
                "SWAP column twenty cells clear of it - the cluster row is wrong"
            )
    return out


def _pack_plan(cl: "Cluster", ch, port, gap_rows, bot_rows, c1) -> tuple[dict, dict]:
    """Solve the twelve terminals, then order the twelve channel columns.

    The two halves are independent, and in this order: a net's length does not
    depend on which channel column it turns in (the route is *east, vertical,
    east*, whose length telescopes to ``target - source + |exit - entry|``), so
    the terminals can be solved against any assignment and the ordering then
    reads the solved rows off them.  Returns the terminals and the columns.
    """
    nominal = {net: ch + i for i, net in enumerate(PACK_NETS)}
    term = _pack_solve(cl, _pack_routes(cl, nominal, port, gap_rows, bot_rows, c1))
    spans = {net: (e, term[net] if a is None else a)
             for net, (e, a) in _pack_rows(cl, port, gap_rows, bot_rows).items()}
    return term, {net: ch + i for i, net in enumerate(_pack_order(spans))}


def _pack_cluster_row(cl_at, ch, port, gap_rows, bot_rows, c1, span: range) -> int:
    """The cluster's row, searched — and the whole search this wall needs.

    It is the only coordinate the twelve length invariants and the channel's
    ordering all see at once, and it is cheap to test: :func:`_pack_plan` is two
    scans and a topological sort, with no grid drawn.  The span is walked from
    the top, so the row that wins is the one that makes the wall shortest — the
    cluster hangs below the block rows and every row it drops adds a row to the
    wall.  Searched rather than baked because it moves whenever a block's port
    rows or a channel's width move, and a baked row that silently stops closing
    is a worse failure than a slow build.
    """
    for cy in span:
        try:
            _pack_plan(cl_at(cy), ch, port, gap_rows, bot_rows, c1)
        except RouterError:
            continue
        return cy
    raise RouterError(
        f"no cluster row in {span.start}..{span.stop - 1} closes all twelve pipe "
        "length invariants with a planar channel"
    )


def build_packed_wall(loop_row: int | None = None) -> Wall:
    """The router, four **panel-less** DOOM blocks, and one 2x2 panel cluster.

    :func:`build_wall` places four whole 235x101 blocks, each with its panel
    embedded and its logic filling the 169 columns west of it, so the four
    panels end up 177 columns and 59 rows apart — four monitors scattered across
    the grid rather than one screen.  This places the same four blocks with their
    panels taken off (:func:`d3_unit.build_logic`) and the panels packed into a
    single 134x106 cluster, which is what ``deadman-3d_hires`` is a picture *of*:
    one 128x96 frame.

    The four blocks go in a 2x2 whose positions mirror the tiles they drive, and
    the cluster sits **east of all four** — not between them::

        router
        [ NW logic ]  [ NE logic ] |            |
              the gutter rows      |  cluster   |
        [ SW logic ]  [ SE logic ] |            |
              the return rows

    Why the cluster is east of everything, and not in the middle
    ------------------------------------------------------------

    Because of ``len(swap) >= len(data) + 20``, which is a statement about
    *where the terminals are*, not about how a pipe is drawn.  Rank the three
    terminals of a panel by how deep into the cluster they sit when you come at
    it from the north-west: ADDR is on the row above the top wall, DATA on the
    left wall, SWAP below the bottom wall — shallowest to deepest, in exactly the
    order the invariants want.  Come at the same panel along the band and the
    order inverts: a north panel's SWAP arrowhead is the first thing a pipe
    entering the band meets and its ADDR arrowhead the last (ADDR has to climb
    the tunnel to the north edge), so ADDR ends up hundreds of cells longer than
    SWAP and no choice of terminal can pay that back — the free columns are only
    64 wide.

    So every one of the twelve pipes has to reach the cluster from the west, and
    a block *east* of the cluster cannot: over the top is the router's own leg to
    that block, which must arrive at its command cell from the north and so owns
    that column and every row above it, and around the band inverts the order.
    The previous arrangement put two blocks either side and paid for it in rows:
    the cluster's 103 rows sat *between* the two block rows and added to the
    wall's height in full.  Moving it east makes those rows overlap the blocks
    instead — 305 rows becomes 190 — at the same width, because the width was
    always ``2 * 166 + 134`` plus channels either way.

    The two channels
    ----------------

    The middle blocks' ports look straight into the **cluster channel**, which is
    thirteen columns: one per pipe, plus the column the two west DATA arrowheads
    sit on.  Twelve columns rather than a shared few because a pipe holds its
    column from the row it enters on to the row it leaves on, and with entries
    spread over 130 rows and exits bunched around the cluster no two of the
    twelve have disjoint spans in a way that survives the crossing rule (a pipe
    turning east at column ``c`` crosses every channel column east of ``c``, so
    for ``P`` west of ``Q``: ``Q``'s entry row must miss ``P``'s span and ``P``'s
    exit row must miss ``Q``'s).  Ordering the twelve by that rule gives one
    total order — NE, NW, SE, SW, each ``(addr, data, swap)`` — and no two of
    them can share.

    The west blocks cannot see that channel at all, so their six pipes take the
    **west channel** (three columns, shared between the two block rows because
    their spans are disjoint) to a free row and cross the middle column along it:
    the north block along three of the gutter rows, the south block along three
    rows below the whole wall.  Both bands are free the full width, which is the
    whole reason the blocks are a 2x2 and not a 1x4.

    Why the gutter is five rows
    ---------------------------

    Three of them carry the north-west block's port pipes east.  The other two
    are the south legs' lanes: a leg has to enter its command cell from the north
    (the arrowhead points *into* the block's top wall), so it needs a free row
    above the south blocks' command row, and the two legs cannot share one
    because their eastward runs overlap in the router's own margin.

    Why the leg fan is planar here and was not before
    -------------------------------------------------

    Nothing but the legs is ever north of the blocks.  Every port pipe leaves its
    block heading east and then goes *south* or stays put; none climbs into the
    strip the legs run in, so the two fans never meet.  The old arrangement could
    not have that: its east blocks' pipes had to double back underneath
    themselves, and the strip north of a block was contested.
    """
    from . import d3_unit
    from .machine import _Grid

    if loop_row is None:
        loop_row = d3_unit.R_LOOP
    logic = [d3_unit.build_logic(row, loop_row) for row in TILE_FLOOR_ROW]
    lg = logic[0]
    if {(b.width, b.height, b.cmd_cell, tuple(sorted(b.ports.items()))) for b in logic} != {
        (lg.width, lg.height, lg.cmd_cell, tuple(sorted(lg.ports.items())))
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

    # -- the frame --------------------------------------------------------
    lw, lh = lg.width, lg.height
    wx = RX + BLOCK_X0            # the west logic column
    c1 = tuple(wx + lw + i for i in range(PACK_CH_W))  # SWAP, DATA, ADDR
    mx = wx + lw + PACK_CH_W      # the middle logic column
    ch = mx + lw                  # the cluster channel's first column
    cx = ch + PACK_CH_E           # the cluster's north-west wall corner

    row_n = PACK_ROW_N
    row_s = row_n + lh + PACK_GAP
    gap_rows = (row_n + lh, row_n + lh + 1, row_n + lh + 2)  # NW's three crossings
    lane_s = (row_s - 2, row_s - 1)                          # T3's lane, then T2's
    bot_rows = (row_s + lh, row_s + lh + 1, row_s + lh + 2)  # SW's three crossings

    # Tile positions and block positions are the same 2x2: west is left, north is
    # top.  Nothing forces that — the panels are composed in software — but it is
    # what makes every pipe's route the short way round.
    anchors = {0: (wx, row_n), 1: (mx, row_n), 2: (wx, row_s), 3: (mx, row_s)}
    for tile, (bx, by) in anchors.items():
        g.blit(bx, by, logic[tile].cells)

    def port(tile: int, band: str) -> tuple[int, int]:
        bx, by = anchors[tile]
        px, py = logic[tile].ports[band]
        return bx + px, by + py

    def cl_at(cy: int) -> Cluster:
        return cluster_at(cx, cy)

    # The cluster's row: the one coordinate all twelve invariants see.  It has to
    # keep its four approach rows clear of the router's own room and its south
    # edge inside the wall; the rest is solved.
    lo = (RY + IH + 2) + 4
    hi = bot_rows[-1] + 2 * (d3_unit.PANEL_H + 2) + GUTTER_Y
    cy = PACK_CLUSTER_Y or _pack_cluster_row(cl_at, ch, port, gap_rows, bot_rows,
                                             c1, range(lo, hi + 1))
    cl = cl_at(cy)
    for cnr in cl.corners:
        g.room(cnr[0], cnr[1], cnr[0] + d3_unit.PANEL_W + 1, cnr[1] + d3_unit.PANEL_H + 1,
               h="=", v=":")

    term, col = _pack_plan(cl, ch, port, gap_rows, bot_rows, c1)
    routes = _pack_routes(cl, col, port, gap_rows, bot_rows, c1)
    lengths: dict[tuple[int, str], int] = {}
    for net, value in term.items():
        points = routes[net](value)
        lengths[net] = g.draw_pipe(points)
        g.c[points[-1]] = PACK_END[net[1]]
    _check_lengths(lengths)

    # -- the router's four legs -------------------------------------------
    # The north pair share the one lane below the router: their eastward runs are
    # disjoint because T0's outlet is west of T1's and its command column is west
    # of T1's outlet.  The south pair each need their own gutter row.
    outlets = outlet_cols()
    lanes = {0: south_wall + 2, 1: south_wall + 2, 2: lane_s[1], 3: lane_s[0]}
    legs: list[int] = []
    for tile, (bx, by) in anchors.items():
        ox = RX + outlets[tile]
        cmd_x, cmd_y = bx + lg.cmd_cell[0], by + lg.cmd_cell[1]
        if ox >= cmd_x:
            raise RouterError(
                f"tile {tile}'s outlet at column {ox} is not west of its command "
                f"port at {cmd_x}: the leg would run west and cross its neighbours"
            )
        if tile in (2, 3) and ox >= wx:
            raise RouterError(
                f"tile {tile}'s outlet at column {ox} is not west of the west logic "
                f"column at {wx}: its leg has to descend past a whole block row"
            )
        legs.append(g.draw_pipe([(ox, south_wall + 1), (ox, lanes[tile]),
                                 (cmd_x, lanes[tile]), (cmd_x, cmd_y)]))

    rows = g.rows()
    regions: dict[str, tuple[int, int, int, int]] = {"router": (RX, RY, IW + 2, IH + 2)}
    for tile, (bx, by) in anchors.items():
        for name, (x, y, w, h) in logic[tile].regions.items():
            regions[f"t{tile}:{name}"] = (bx + x, by + y, w, h)
        px, py = cl.corners[tile]
        regions[f"t{tile}:panel"] = (px, py, d3_unit.PANEL_W + 2, d3_unit.PANEL_H + 2)
    regions["cluster"] = (cx, cl.corners[0][1], cl.width, cl.height)

    return Wall(
        cells=g.c,
        width=max(len(row) for row in rows),
        height=len(rows),
        cmd_cell=(RX + CMD_COL, RY - 1),
        pipes=4 + 4 * lg.pipes + 12,
        panels=list(cl.corners),
        legs=legs,
        floor_rows=TILE_FLOOR_ROW,
        sel=r.sel,
        codes=lg.codes,
        regions=regions,
    )


def _check_lengths(lengths: dict[tuple[int, str], int]) -> None:
    """``d3_unit``'s two pipe invariants, per tile, on the packed routes."""
    for tile in range(4):
        la, ld, ls = (lengths[tile, b] for b in ("addr", "data", "swap"))
        if la != ld:
            raise RouterError(
                f"tile {tile}: ADDR is {la} cells and DATA {ld}; a pixel would be "
                "painted at the wrong cursor. Retune the row DATA arrives on."
            )
        if ls < ld + SWAP_LEAD:
            raise RouterError(
                f"tile {tile}: SWAP is {ls} cells against DATA's {ld}; a COMMIT could "
                "be overtaken by the next frame's first paint. Raise SWAP's column."
            )


def build_probe(commands: Sequence[int], *, packed: bool = False
                ) -> tuple[list[str], Wall]:
    """The wall plus a room that recites ``commands`` into the router and halts.

    The whole protocol is write-only, so the grid is standalone: run it with no
    input at all and it paints all four panels.  ``packed`` swaps
    :func:`build_wall` for :func:`build_packed_wall`, which is how the packed
    cluster's twelve pipes are checked against the real engine without an IWAD
    or a CPU anywhere near them.
    """
    from .machine import _Grid

    wall = build_packed_wall() if packed else build_wall()
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
    ap.add_argument("--packed", action="store_true",
                    help="the packed cluster instead of four scattered panels")
    ap.add_argument("--commands", default="", help="probe command words")
    args = ap.parse_args(argv)
    if args.wall or args.commands:
        rows, wall = build_probe([int(v) for v in args.commands.split()],
                                 packed=args.packed)
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
