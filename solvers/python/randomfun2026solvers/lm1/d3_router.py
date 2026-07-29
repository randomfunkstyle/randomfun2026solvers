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
from collections.abc import Callable, Sequence
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
    "build_packed_wall",
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


# ── the packed wall: one 2x2 cluster, four logic blocks around it ────────────
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
#: column is then a free north-south tunnel.  Running NE's SWAP down it — to the
#: band's first row, then east — is what turns the ports' forced cyclic order
#: into four contiguous ``(addr, data, swap)`` runs.  See :func:`build_packed_wall`.
GUTTER_X, GUTTER_Y = 2, 3


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
#: Columns: the router's own margin (:data:`BLOCK_X0`) | the west logic column |
#: three descent columns and DATA's arrival column | the cluster | the east
#: logic column | three margin columns.
#: Rows: router | NW and NE | their fan | the cluster | SW and SE | SE's fan.
PACK_CH_W = 4  # free columns between the west logic column and the cluster
PACK_CH_E = 2  # free columns between the cluster and the east logic column
PACK_MARGIN_E = 3  # the east margin's turn-round columns

#: The two block rows' *command* rows — the cell the router's leg ends on, one
#: above a block's north wall.  Both blocks in a row share one, exactly as
#: :func:`build_wall` has them, and that is not cosmetic: the leg fan is only
#: planar when the two legs of a row can take two lanes of the same corridor.
PACK_ROW_N = 18
PACK_ROW_S = 217

#: The cluster's north-west wall corner row.  It has to sit between the two
#: block rows with its band straddling the one row the T3 leg crosses on
#: (:attr:`Cluster.lane`); see :func:`build_packed_wall`.
PACK_CLUSTER_Y = 110

#: How many cells SWAP must lead DATA by.  ``build_doom`` only refuses a tie
#: ("a commit could overtake the pixels it commits"), which is enough when the
#: three pipes are 15 to 35 cells long.  Packed they are 300-odd, and the
#: interesting race is the *other* one: a COMMIT still in flight when the next
#: frame's first ADDR lands commits that pixel into the wrong buffer.  The
#: shipped block's own margin is ``35 - 15 = 20``, and the unit cannot emit a
#: paint sooner than a trie walk plus a collector walk after a COMMIT, so 20 is
#: kept as the floor rather than re-derived.
SWAP_LEAD = 20


def _excursion(x: int, y: int, w: int) -> list[tuple[int, int]]:
    """Corners that take a northbound climb ``w`` cells east and back again.

    A monotone route's length is its Manhattan distance, so a pipe that has to
    be *longer* than the straight way between its two ends has to double back;
    this is the smallest shape that does, and it costs two rows and ``w``
    columns of otherwise empty grid for ``2 * w`` cells of pipe.
    """
    return [(x, y), (x + w, y), (x + w, y - 1), (x, y - 1)]


def _plen(points: list[tuple[int, int]]) -> int:
    """A rectilinear polyline's cell count, without drawing it."""
    return 1 + sum(abs(x1 - x0) + abs(y1 - y0)
                   for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False))


def build_packed_wall(loop_row: int | None = None) -> Wall:
    """The router, four **panel-less** DOOM blocks, and one 2x2 panel cluster.

    :func:`build_wall` places four whole 235x101 blocks, each with its panel
    embedded and its logic filling the 169 columns west of it, so the four
    panels end up 177 columns and 59 rows apart — four monitors scattered across
    the grid rather than one screen.  This places the same four blocks with their
    panels taken off (:func:`d3_unit.build_logic`) and the panels packed into a
    single 134x102 cluster with a two-cell gutter, which is what
    ``deadman-3d_hires`` is a picture *of*: a 128x96 frame.

    Why the four blocks sit where they do
    -------------------------------------

    Not aesthetics — the ports' cyclic order, which is forced twice over.

    A block emits ADDR, DATA and SWAP on its east wall at
    :data:`d3_unit.BAND_ROWS`' own rows, in that order, north to south; nothing
    downstream can reorder them.  And the cluster's twelve terminals sit at fixed
    places on its boundary: ADDR on a top wall, DATA on a left wall, SWAP on a
    bottom wall, with the band's two rows and the corridor's two columns as the
    only ways in to the four interior ones.  So the twelve have a **cyclic order
    around the cluster**, and a planar routing exists only if that order breaks
    into four contiguous ``(addr, data, swap)`` runs — one per block, in the same
    rotational sense as the blocks themselves.

    Read counter-clockwise from the cluster's west face, with NE's SWAP taken
    down the corridor's free tunnel to the band's first row, the order is::

        NW(a, d, s)  SW(a, d, s)  SE(a, d, s)  NE(a, d, s)

    — four contiguous triples, all in the emitted order.  So the blocks must
    appear counter-clockwise as NW, SW, SE, NE, and that is exactly
    ``[NW][cluster][NE]`` over ``[SW][cluster][SE]``: down the west side, along
    the south, up the east side.  Send NE's SWAP into the band's *east* end
    instead and the north face reads ``NW.ADDR, NE.DATA, NE.ADDR`` — NE's triple
    is ``(d, a, s)``, which no block emits, and the routing deadlocks against
    itself whatever the column assignment.

    Why the two fans have to be kept apart
    --------------------------------------

    The router's four legs run **east**, from outlets on its south wall to a
    command port on each block's north wall.  The east blocks' twelve-pipe fan
    runs **west**, from their east walls to a cluster that is west of them.  In
    the strip immediately north of an east block the two interleave — the leg
    ends *inside* the block's column span while every panel pipe crosses it — so
    they cross unconditionally, and no choice of lane rows helps.

    The fix is that the east blocks' pipes never enter that strip: they leave the
    east wall, run out to the east margin, and turn **away** from the command
    lane — NE's down and back west underneath itself into the east channel, SE's
    up and back west over itself — reaching the cluster from the channel between
    it and the blocks.  The router's legs then have the whole northern strip to
    themselves and are the unmodified fan :func:`build_wall` already draws.

    Lengths, which are the other half of the geometry
    -------------------------------------------------

    ``len(addr) == len(data)`` and ``len(swap) > len(data)`` are
    :func:`d3_unit.build_doom`'s invariants and they do not become optional
    because the pipes got twenty times longer: a DATA that overtakes its ADDR
    paints the wrong pixel, and a COMMIT that arrives after the next frame's
    first paint commits it into the wrong buffer.  Every route below therefore
    has one free coordinate — the column an ADDR turns down into its top wall,
    the row a DATA arrives at on its left wall, the column a SWAP rises in — and
    they are solved for rather than chosen.  Monotone legs cannot be padded (a
    staircase's length is its Manhattan distance), which is why the free
    coordinate is a *terminal* one.
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

    # ── the frame: two logic columns, the cluster between them ──────────────
    lw = lg.width
    wx = RX + BLOCK_X0  # the west logic column
    c1, c2, c3 = (wx + lw + i for i in range(3))  # the west channel's descents
    cx = wx + lw + PACK_CH_W  # the cluster's north-west wall corner
    cl = cluster_at(cx, PACK_CLUSTER_Y)
    ex = cx + cl.width + PACK_CH_E  # the east logic column
    m1, m2, m3 = (ex + lw + 1 + i for i in range(PACK_MARGIN_E))  # the east margin

    anchors = {0: (wx, PACK_ROW_N), 1: (ex, PACK_ROW_N),
               2: (wx, PACK_ROW_S), 3: (ex, PACK_ROW_S)}
    for tile, (bx, by) in anchors.items():
        g.blit(bx, by, logic[tile].cells)
    for cnr in cl.corners:
        g.room(cnr[0], cnr[1], cnr[0] + d3_unit.PANEL_W + 1, cnr[1] + d3_unit.PANEL_H + 1,
               h="=", v=":")

    def port(tile: int, band: str) -> tuple[int, int]:
        bx, by = anchors[tile]
        px, py = logic[tile].ports[band]
        return bx + px, by + py

    lengths: dict[tuple[int, str], int] = {}

    def pipe(tile: int, band: str, points: list[tuple[int, int]], end: str = "") -> None:
        """Draw one panel pipe.  ``end`` overrides the terminal arrowhead, which
        ``draw_pipe`` reads off the direction the run arrived from — right for a
        run into a wall, wrong for a terminal that is itself the bend (a DATA
        that turns east in the very cell it ends on, an ADDR that turns south)."""
        lengths[tile, band] = g.draw_pipe(points)
        if end:
            g.c[points[-1]] = end

    def solve(route: Callable[[int], list[tuple[int, int]]], span: range,
              want: Callable[[int], bool], what: str) -> list[tuple[int, int]]:
        """The one free coordinate of a route, solved rather than chosen.

        Every route here is monotone in its own free terminal, so its length is
        affine in it and a scan of the legal span is both exact and honest about
        failing: if no value in the panel's own column or row range gives the
        length the invariant wants, the geometry is wrong and saying so beats
        shipping a pipe that paints at the wrong cursor.
        """
        for v in span:
            if want(_plen(route(v))):
                return route(v)
        raise RouterError(
            f"no {what} in {span.start}..{span.stop - 1} gives the pipe length the "
            "ADDR/DATA/SWAP invariants need; the cluster or a block has moved"
        )

    px0, py0 = cl.corners[0]
    px3, py3 = cl.corners[3]
    cols_w = range(px0 + 1, px0 + d3_unit.PANEL_W + 1)  # the west panels' interior
    cols_e = range(px3 + 1, px3 + d3_unit.PANEL_W + 1)  # the east panels' interior
    rows_n = range(py0 + 1, py0 + d3_unit.PANEL_H + 1)  # the north panels' interior
    rows_s = range(py3 + 1, py3 + d3_unit.PANEL_H + 1)  # the south panels' interior

    # ── T0, the north-west panel.  Its block is due west of the cluster, so
    #    ADDR runs straight along the row above the top wall, DATA straight
    #    into the left wall, and SWAP drops one column into the band ─────────
    a0, d0, s0 = (port(0, b) for b in ("addr", "data", "swap"))
    pipe(0, "addr", [a0, (c3, a0[1]), (c3, cl.north), (px0 + 3, cl.north)], end="v")
    la = lengths[0, "addr"]
    pipe(0, "data", solve(lambda r: [d0, (c2, d0[1]), (c2, r), (cl.west_data, r)],
                          rows_n, lambda n: n == la, "DATA row"))
    pipe(0, "swap", solve(lambda c: [s0, (c1, s0[1]), (c1, cl.band[0]), (c, cl.band[0])],
                          cols_w, lambda n: n >= la + SWAP_LEAD, "SWAP column"), end="^")

    # ── T2, the south-west panel.  Its block is due west too, one whole block
    #    row below, so all three *climb* the same channel T0's three descend —
    #    and the two never meet, because every T0 target is at or above the
    #    band's first row and every T2 target at or below its last ───────────
    a2, d2, s2 = (port(2, b) for b in ("addr", "data", "swap"))
    pipe(2, "addr", [a2, (c1, a2[1]), (c1, cl.band[1]), (px0 + 1, cl.band[1])], end="v")
    la = lengths[2, "addr"]
    pipe(2, "data", solve(lambda r: [d2, (c2, d2[1]), (c2, r), (cl.west_data, r)],
                          rows_s, lambda n: n == la, "DATA row"))
    pipe(2, "swap", solve(lambda c: [s2, (c3, s2[1]), (c3, cl.south + 1), (c, cl.south + 1),
                                     (c, cl.south)],
                          cols_w, lambda n: n >= la + SWAP_LEAD, "SWAP column"))

    # ── T1, the north-east panel.  Its ports face *away* from the cluster, so
    #    all three run out to the east margin, turn **south** past their own
    #    block and come back west underneath it, into the band between the top
    #    block row and the cluster.  South rather than north is the whole
    #    point: north of the block is the router's command lane, and the two
    #    fans cross there unconditionally.  The three margin columns are forced
    #    — ADDR leaves the highest east-wall row and descends deepest, so every
    #    other run would cross its descent unless ADDR turns last (``m3``).
    #    SWAP takes the corridor's free tunnel, which is what puts the twelve
    #    ports in an order four blocks can actually emit ─────────────────────
    a1, d1, s1 = (port(1, b) for b in ("addr", "data", "swap"))
    pipe(1, "addr", [a1, (m3, a1[1]), (m3, cl.north), (px3 + 5, cl.north)], end="v")
    la = lengths[1, "addr"]
    pipe(1, "data", solve(
        lambda r: [d1, (m2, d1[1]), (m2, cl.north - 1),
                   (cl.corridor, cl.north - 1), (cl.corridor, r)],
        rows_n, lambda n: n == la, "DATA row"), end=">")
    pipe(1, "swap", solve(
        lambda c: [s1, (m1, s1[1]), (m1, cl.north - 2), (cl.tunnel, cl.north - 2),
                   (cl.tunnel, cl.band[0]), (c, cl.band[0])],
        cols_e, lambda n: n >= la + SWAP_LEAD, "SWAP column"), end="^")

    # ── T3, the south-east panel: the same detour, past the *bottom* of its
    #    own block this time, and back west underneath it into the empty band
    #    below the wall.  ADDR takes the tunnel from the south, mirroring NE's
    #    SWAP from the north ─────────────────────────────────────────────────
    a3, d3, s3 = (port(3, b) for b in ("addr", "data", "swap"))
    p1, p2, p3 = (PACK_ROW_S + lg.height + i for i in range(3))  # under the block
    # ADDR is the longest of the three by construction — it alone crosses the
    # whole cluster to the band's last row, and its margin column and its
    # westward row are both forced outermost by the other two — so on this tile
    # the invariants have to be met by *padding the other two up* rather than
    # by choosing a terminal.  A staircase's length is its Manhattan distance,
    # so padding means a reversal: an excursion sideways and back, one row
    # apart, inside the empty band between the cluster's south wall and the
    # block.  ``_excursion`` is the whole of it; the widths are solved.
    pipe(3, "addr", [a3, (m3, a3[1]), (m3, p3), (cl.tunnel, p3),
                     (cl.tunnel, cl.band[1]), (px3 + 1, cl.band[1])], end="v")
    la = lengths[3, "addr"]
    sx = px3 + 25  # SWAP's climb, east of DATA's excursion and west of its own
    pipe(3, "data", solve(
        lambda w: [d3, (m2, d3[1]), (m2, p2), (cl.corridor, p2),
                   *_excursion(cl.corridor, 250, w), (cl.corridor, rows_s.start)],
        range(1, sx - cl.corridor - 1), lambda n: n == la, "DATA excursion"), end=">")
    pipe(3, "swap", solve(
        lambda w: [s3, (m1, s3[1]), (m1, p1), (sx, p1),
                   *_excursion(sx, 240, w), *_excursion(sx, 230, w),
                   *_excursion(sx, 220, w), (sx, cl.south)],
        range(1, cols_e.stop - sx), lambda n: n >= la + SWAP_LEAD, "SWAP excursion"))

    _check_lengths(lengths)

    # ── the router's four legs: the unmodified fan, east out of the outlets ──
    outlets = outlet_cols()
    #: The lane each leg turns east on.  Three of the four are ``build_wall``'s
    #: own: within a block row the leg that reaches further east turns two rows
    #: above the one it crosses.  T3's is the exception and it is the reason the
    #: gutter is three rows rather than two — see below.
    lanes = {0: PACK_ROW_N - 2, 1: PACK_ROW_N - 3, 2: PACK_ROW_S - 2, 3: cl.lane}
    legs: list[int] = []
    for tile, (bx, by) in anchors.items():
        ox = RX + outlets[tile]
        cmd_x, cmd_y = bx + lg.cmd_cell[0], by + lg.cmd_cell[1]
        if ox >= cmd_x:
            raise RouterError(
                f"tile {tile}'s outlet at column {ox} is not west of its command "
                f"port at {cmd_x}: the leg would run west and cross its neighbours"
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
