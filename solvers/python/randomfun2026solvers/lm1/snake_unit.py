#!/usr/bin/env python3
"""The SNAKE unit — the snake's body as a pipe ring, and the LM-75 panel it owns.

``snake`` is display-judged, round-based, and its whole state is a FIFO of at most
50 display-cell indices (``cell = y*16 + x``, so ``0..255``). Nothing in it is
random-access: a tick reads the body once, in order, and writes one cell at each
end. That is a *ring*, not a tape — ``ARCH.md`` §2.1 — and a ring costs what a
pipe costs, one tick per cell, against the tape's ``105 + 8.3N`` per access.

So this block is a servant that owns three things the CPU then does not:

* the **body**, as values circulating forever in one ring (two pipes plus a relay
  room, because ``SPEC.md`` silently drops a pipe that loops back to its own room);
* the **16x16 panel**, with ADDR/DATA/SWAP on its top/left/bottom walls;
* the **game's per-tick decisions** that need the body — self-collision, and which
  cell the tail vacates.

The CPU therefore only ever *sends*. That is not a stylistic choice: an incoming
response pipe would be a rival for **every** ``r`` in the CPU (§7.1: an ``r``
competes with incoming pipes only), including the jump slab's ROM read, and the
CPU cannot place a machine with jumps against such a pipe. Outgoing ports are
free — ``snake_cpu.man`` already runs three of them.

Four commands, one word each, ``8 * arg + code`` exactly like ``stream.py``::

    GROW  cell           append cell; paint it green; commit.
    STEP  n*256 + cell   rotate n, comparing all but the first against cell;
                         no match -> drop+blacken the tail, append+green cell, commit;
                         match     -> paint all n red, commit, halt.
    FRUIT cell           paint cell red; commit.
    RED   n              paint all n body cells red; commit.

``GROW`` serves both the opening frame and every fruit-eating tick (the tail is
simply not dropped). ``RED`` is the wall death, which the CPU detects
arithmetically and the unit cannot see. Exactly one SWAP per command, never two:
``SWAP = 1`` commits ``next`` into ``current`` and *keeps* both, so the panel is a
persistent framebuffer and the unit only ever paints the pixels that changed.

Why ``STEP`` is the only hard arm
---------------------------------

Two facts drive its shape:

1. **The tail moves before the head** (``snake_sim.Game.tick``), so the first
   value of the lap — the tail — must *not* be compared: stepping into the cell
   the tail has just vacated is legal.
2. **The ring must come back to its exact rotation** on the no-match path, or
   every later tick reads the wrong cell. Rotating exactly ``n`` values, each read
   sent straight back, is the only discipline that guarantees it.

The comparison is ``-`` against ``B``: ``B`` holds the candidate for the whole lap
and ``-`` does not disturb it, which is why the count goes in the *high* part of
the argument (``/`` leaves the quotient in ``A`` and the remainder in ``B``).
``X`` then turns on the sign of ``A``: heading south, ``A>0`` turns west, ``A<0``
east, ``A==0`` goes straight — so **equal is the straight path**, and it is the
body's own continuation: three more glyphs (``1 N M``) that put ``-1`` in ``B`` as
a sentinel and fall into the loop's own bottom corner. Both turn lanes rejoin the
decrement leg. The loop therefore *always* runs to completion — there is no early
exit to desynchronise the ring, and no drain pass — and after it a single ``W``
brings the sentinel into ``A`` where one more ``X`` picks the outcome.

The count survives the lap as a **header word in the ring itself**. ``BP`` is the
loop counter and cannot be read back, ``A`` is clobbered by every ``r`` and ``B``
holds the candidate, so ``n`` is sent into the ring *before* the lap and read back
*after* it: the peel re-sends the tail behind the header, and after ``n-1`` more
rotations the ring is exactly ``[n, v1..vn]``. One read recovers ``n`` (which the
match path needs for its red lap) and leaves the body aligned.

The peel is free. ``counted_loop``'s decrement sits at ``(x, y+1)``, directly
above the loop's return leg, so the peel walks *up into it*: read the tail, send
it back, climb through ``m``, and arrive at ``>`` with ``BP = n-1`` and the loop
untouched.

Geometry: two rules, both asserted
----------------------------------

The unit has six pipes and every ``r``/``s`` in it is decided by position alone.

1. **Every outgoing pipe attaches to the east wall, on the row of the ``s`` that
   uses it** (``ring``, ``addr``, ``data``, ``swap``). All four share the same
   ``IW + 1 - x`` term, so the *row distance alone* decides: an ``s`` sitting
   exactly on its band's row is 0 away and every rival is at least 1, whatever
   column its arm is in. That is what lets ``ADDR`` and ``ring`` be adjacent rows.
2. **The incoming pipes are ``cmd`` on the north wall beside ``MAIN`` and the ring
   return on the east wall.** ``MAIN``'s ``r`` is one cell from ``cmd`` and 56 from
   the return; every arm's ``r`` is deep in the unit, where ``cmd``'s ``y`` term
   makes it lose by at least 6. Only two incoming pipes exist, so an arm ``r`` does
   not have to sit on a particular row — asserted anyway, with the margins.

Arms are trie leaves at fixed columns and their bodies run *down* their own
column, so "which row a glyph is on" is a free layout variable: pad a body with
blanks and the next send lands on its own band's row. ``RED``'s whole lap is one
``Circuit.counted_loop`` body, ``"rss  9s"`` — read, re-send, ADDR, pad, ``9``,
DATA — and the commit sits after the loop.

Panel ports, and why relative pipe *length* is load-bearing
-----------------------------------------------------------

The panel processes ADDR, then DATA, then SWAP within one tick, and each port is
a separate pipe with its own transit time. A DATA that overtakes its ADDR paints
the wrong pixel, so the three lengths are not free:

* ``len(addr) == len(data)`` — asserted. The sends are one or four ticks apart in
  the arm's column, and equal transit keeps them in order at the panel.
* ``len(swap) >= len(data)`` — asserted. The commit must not overtake the pixels
  it commits.
* the skew ``len(swap) - len(addr)`` must stay under the gap between two commands
  (~40 ticks), or one command's commit would land inside the next one's paints.
  ``RED``'s loop paints every ~11 ticks with no commit inside it, so only the
  ADDR/DATA pair matters there.

The ring runs *north* of the unit and the panel *south* of it, which is what keeps
eleven pipes planar: the three panel pipes leave the east wall below row 21 and
descend east of the unit, while the ring's two legs never go below row 28.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from ..circuit import Circuit, S
from ..man_debug import DebugMap
from ..snake_sim import DIRECTIONS, FRUIT, TICK, Game, on_grid
from .stream import RELAY_IH, RELAY_IW, relay_cells

__all__ = [
    "ARMS",
    "BANDS",
    "PANEL",
    "SnakeBlock",
    "SnakeUnitError",
    "UNIT_IH",
    "UNIT_IW",
    "Unit",
    "arm_codes",
    "build_probe",
    "build_snake",
    "commands_for_rounds",
    "unit_interior",
    "word",
]


class SnakeUnitError(RuntimeError):
    """The block's geometry did not close, with the constraint that failed."""


# ── the unit's row map ───────────────────────────────────────────────────────
#: Interior rows. ``MAIN`` reads a command, a depth-2 trie fans it to four
#: columns, every arm recovers its argument with the same four glyphs, and the
#: rows below are the *band rows*: an ``s`` sitting on one of these binds that
#: pipe (module docstring, rule 1).
R_MAIN = 1
R_TRIE = 2  # rows 2..3
R_ARG = 4  # rows 4..7: `M 8 W /`
R_ARG2 = 8  # STEP only, rows 8..16: `M ` 2 5 6 ` W / b`
R_LOOP = 18  # every counted loop is entered here, so its body lands on the bands
R_RING = 20  # east wall, out: one value into the ring
R_ADDR = 21  # east wall, out: panel ADDR
R_RET = 19  # east wall, in: the ring's return
R_DATA = 25  # east wall, out: panel DATA
R_SWAP = 27  # east wall, out: panel SWAP
R_COLLECT = 30  # every arm rejoins here and walks back to MAIN

UNIT_IW = 40
UNIT_IH = R_COLLECT

#: band -> the wall it attaches to and the row/column on it.
BANDS: dict[str, tuple[str, int]] = {
    "cmd": ("north", 3),
    "ring_ret": ("east", R_RET),
    "ring": ("east", R_RING),
    "addr": ("east", R_ADDR),
    "data": ("east", R_DATA),
    "swap": ("east", R_SWAP),
}

#: Trie geometry: four leaves at ``LEAF0 + LEAF_PITCH*i``, entry column midway.
LEAF0 = 3
LEAF_PITCH = 6
TRIE_BITS = 2
TRIE_COL = LEAF0 + LEAF_PITCH * ((1 << TRIE_BITS) - 1) // 2  # 12

#: The four arms, **west to east**. ``STEP`` is last because it is the only arm
#: that needs more than its own six columns: nothing sits east of it, so it may
#: spill as far as it likes.
ARMS: tuple[str, ...] = ("GROW", "FRUIT", "RED", "STEP")

#: ``RED``'s lap, and ``STEP``'s red lap: read, re-send, ADDR, pad, ``9``, DATA.
#: Entered at :data:`R_LOOP`, so ``body[i]`` lands on row ``R_LOOP + 1 + i`` and
#: every send is on its own band (asserted by :func:`_check_body`).
RED_BODY = "rss  9s"
#: ``STEP``'s comparison lap. ``1 N M`` is the *equal* lane: it continues straight
#: out of ``X`` and falls into the loop's own bottom corner, so the match costs no
#: extra turn cells and cannot skip the decrement.
STEP_BODY = "rs-X1NM"

#: The panel: 16x16, ``cell = row*16 + col`` (``SPEC.md``), colours green/red/black.
PANEL = 16
GREEN, RED, BLACK = 10, 9, 0


def _bit_of(level: int) -> int:
    return 1 << (level - 1)


def arm_codes() -> dict[str, int]:
    """Command code per arm, *read off* the trie rather than assigned.

    ``x`` turns clockwise on BP's low bit and a man heading south turns clockwise
    to the **west**, so a west branch means that bit is 1. Move a leaf and these
    numbers move with it — which is why the emulator/CPU side must take them from
    here instead of hard-coding them (``ARCH.md`` §7.1: opcode numbering is a
    layout variable).
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
    leaves = sorted(codes)
    if len(leaves) != len(ARMS):
        raise SnakeUnitError(f"trie has {len(leaves)} leaves for {len(ARMS)} arms")
    return {arm: codes[col] for arm, col in zip(ARMS, leaves, strict=True)}


def arm_columns() -> dict[str, int]:
    """Interior column of each arm's leaf, west to east."""
    return {arm: LEAF0 + LEAF_PITCH * i for i, arm in enumerate(ARMS)}


def word(code: int, arg: int) -> int:
    """One command word. Floored ``/`` recovers a negative ``arg`` too."""
    return 8 * arg + code


# ── the unit's interior ──────────────────────────────────────────────────────
@dataclass
class Unit:
    """The unit's interior, plus where each of its pipes must attach."""

    cells: dict[tuple[int, int], str]
    width: int = UNIT_IW
    height: int = UNIT_IH
    #: band -> interior row on the east wall
    east: dict[str, int] = field(default_factory=dict)
    #: band -> interior column on the north wall
    north: dict[str, int] = field(default_factory=dict)
    #: every pipe glyph: ``(x, y, glyph, band)`` in interior coordinates
    glyphs: list[tuple[int, int, str, str]] = field(default_factory=list)
    codes: dict[str, int] = field(default_factory=dict)


#: Which band an ``s`` on a given row belongs to. The row *is* the pipe (rule 1),
#: so this is the single place that mapping lives.
_SEND_BAND: dict[int, str] = {R_RING: "ring", R_ADDR: "addr", R_DATA: "data", R_SWAP: "swap"}


def _check_body(body: str, entry: int, wanted: dict[int, str]) -> None:
    """A counted-loop body's sends must land on exactly the wanted bands."""
    got = {entry + 1 + i: ch for i, ch in enumerate(body) if ch in "rs"}
    for row, ch in got.items():
        if ch == "s" and _SEND_BAND.get(row) != wanted.get(row):
            raise SnakeUnitError(
                f"body {body!r} sends on row {row}, which is band "
                f"{_SEND_BAND.get(row)!r}, not {wanted.get(row)!r}"
            )
    if {r for r, ch in got.items() if ch == "s"} != set(wanted):
        raise SnakeUnitError(f"body {body!r} sends on {sorted(got)}, wanted {sorted(wanted)}")


def unit_interior() -> Unit:
    """Lay the unit: MAIN, the decode trie, four arms, the collector."""
    _check_body(RED_BODY, R_LOOP, {R_RING: "ring", R_ADDR: "addr", R_DATA: "data"})
    _check_body(STEP_BODY, R_LOOP, {R_RING: "ring"})

    c = Circuit(UNIT_IW + 2, UNIT_IH + 2)
    glyphs: list[tuple[int, int, str, str]] = []
    col = arm_columns()

    def pipe(x: int, y: int, glyph: str, band: str) -> None:
        c.set(x, y, glyph)
        glyphs.append((x, y, glyph, band))

    def loop_glyphs(x: int, y0: int, body: str) -> None:
        """Register a counted loop's own ``r``/``s`` against their bands."""
        for i, ch in enumerate(body):
            if ch == "r":
                glyphs.append((x + 1, y0 + 1 + i, ch, "ring_ret"))
            elif ch == "s":
                glyphs.append((x + 1, y0 + 1 + i, ch, _SEND_BAND[y0 + 1 + i]))

    # ── MAIN: the command arrives from the north, BP decodes it ───────────────
    c.set(1, R_MAIN, ">")
    c.set(2, R_MAIN, "@")
    pipe(3, R_MAIN, "r", "cmd")
    c.set(4, R_MAIN, "b")
    c.horizontal(R_MAIN, 4, TRIE_COL)
    c.set(TRIE_COL, R_MAIN, "v")

    # ── the decode trie, fanning *sideways*: leaves are columns, not rows ─────
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

    # ── GROW: append, paint green, commit — one column, no loop ───────────────
    x = col["GROW"]
    c.run(x, R_ARG, "M8W/", d=S)  # A = cell
    c.vertical(x, R_ARG + 3, R_RING)
    pipe(x, R_RING, "s", "ring")  # the new head joins the back of the FIFO
    pipe(x, R_ADDR, "s", "addr")
    c.run(x, R_ADDR + 1, "5M+", d=S)  # A = 10 (green); `10` would need a literal
    pipe(x, R_DATA, "s", "data")
    c.set(x, R_DATA + 1, "1")
    pipe(x, R_SWAP, "s", "swap")  # SWAP = 1: commit, keep `next` and the cursor
    c.vertical(x, R_SWAP, R_COLLECT)

    # ── FRUIT: paint red, commit ─────────────────────────────────────────────
    x = col["FRUIT"]
    c.run(x, R_ARG, "M8W/", d=S)
    c.vertical(x, R_ARG + 3, R_ADDR)
    pipe(x, R_ADDR, "s", "addr")
    c.vertical(x, R_ADDR, R_DATA - 1)
    c.set(x, R_DATA - 1, "9")
    pipe(x, R_DATA, "s", "data")
    c.set(x, R_DATA + 1, "1")
    pipe(x, R_SWAP, "s", "swap")
    c.vertical(x, R_SWAP, R_COLLECT)

    # ── RED: n x { read, re-send, paint red }, then commit ───────────────────
    x = col["RED"]
    c.run(x, R_ARG, "M8W/b", d=S)  # BP = n
    c.vertical(x, R_ARG + 4, R_LOOP)
    c.counted_loop(x, R_LOOP, RED_BODY)
    loop_glyphs(x, R_LOOP, RED_BODY)
    ex = x + 2
    c.set(ex, R_LOOP, "v")
    c.vertical(ex, R_LOOP, R_DATA + 1)
    c.set(ex, R_DATA + 1, "1")
    pipe(ex, R_SWAP, "s", "swap")
    c.vertical(ex, R_SWAP, R_COLLECT)

    # ── STEP ─────────────────────────────────────────────────────────────────
    x = col["STEP"]
    c.run(x, R_ARG, "M8W/", d=S)  # A = n*256 + cell
    c.set(x, R_ARG2, "M")
    c.run(x, R_ARG2 + 1, "`256`", d=S)  # a vertical literal: A = 256
    c.run(x, R_ARG2 + 6, "W/b", d=S)  # A = n, B = cell, BP = n
    c.vertical(x, R_ARG2 + 8, R_RING)
    c.set(x, R_RING, ">")  # turn east *onto* the ring band

    # the header and the peel, all on the ring row, then a climb through `m`
    pipe(x + 1, R_RING, "s", "ring")  # header = n, so the lap can give it back
    pipe(x + 2, R_RING, "r", "ring_ret")  # the tail: never compared (it vacates)
    pipe(x + 3, R_RING, "s", "ring")  # straight back, behind the header
    leg = x + 4  # the loop's return leg
    c.set(leg, R_RING, "^")  # climbing it runs `m` once: BP = n-1
    c.counted_loop(leg, R_LOOP, STEP_BODY)
    loop_glyphs(leg, R_LOOP, STEP_BODY)
    body = leg + 1
    row_x = R_LOOP + 1 + STEP_BODY.index("X")
    c.set(leg, row_x, "^")  # A > 0 (west): rejoin the decrement leg
    c.set(body + 1, row_x, "v")  # A < 0 (east): dive under and rejoin
    c.set(body + 1, row_x + 1, "<")
    c.set(leg, row_x + 1, "^")

    # the outcome: `W` brings the sentinel into A, `X` picks the lane
    test = leg + 2
    c.set(test, R_LOOP, "W")
    c.set(test + 1, R_LOOP, "X")

    # ── STEP, no match: drop+blacken the tail, append+green the head, commit ─
    c.set(test + 1, R_LOOP + 1, ">")  # A > 0 (cell > 0): south, then east
    c.set(test + 2, R_LOOP, "v")  # A == 0 (cell == 0): straight, then south
    c.set(test + 2, R_LOOP + 1, ">")  # both lanes merge heading east
    c.set(test + 3, R_LOOP + 1, "M")  # B = cell, so it survives the two reads
    pipe(test + 4, R_LOOP + 1, "r", "ring_ret")  # the header (n, not needed here)
    pipe(test + 5, R_LOOP + 1, "r", "ring_ret")  # the tail — dropped, not re-sent
    black = test + 6
    c.set(black, R_LOOP + 1, "v")
    c.vertical(black, R_LOOP + 1, R_ADDR)
    pipe(black, R_ADDR, "s", "addr")  # black *first*: the head may be here
    c.set(black, R_ADDR + 1, "0")
    c.vertical(black, R_ADDR + 1, R_DATA)
    pipe(black, R_DATA, "s", "data")
    c.set(black, R_DATA + 1, "W")  # A = cell again
    c.set(black, R_DATA + 2, ">")
    climb = black + 1  # the bands only run one way, so come back up
    c.set(climb, R_DATA + 2, "^")
    c.vertical(climb, R_DATA + 2, R_LOOP)
    c.set(climb, R_LOOP, ">")
    green = climb + 1
    c.set(green, R_LOOP, "v")
    c.vertical(green, R_LOOP, R_RING)
    pipe(green, R_RING, "s", "ring")
    pipe(green, R_ADDR, "s", "addr")
    c.run(green, R_ADDR + 1, "5M+", d=S)
    pipe(green, R_DATA, "s", "data")
    c.set(green, R_DATA + 1, "1")
    pipe(green, R_SWAP, "s", "swap")
    c.vertical(green, R_SWAP, R_COLLECT)

    # ── STEP, match: the player has lost — paint the whole body red and halt ──
    c.set(test + 1, R_LOOP - 1, ">")  # A < 0 (the sentinel): north, then east
    pipe(test + 2, R_LOOP - 1, "r", "ring_ret")  # the header: A = n
    c.set(test + 3, R_LOOP - 1, "b")
    red = green + 1
    c.horizontal(R_LOOP - 1, test + 3, red)
    c.set(red, R_LOOP - 1, "v")
    c.counted_loop(red, R_LOOP, RED_BODY)
    loop_glyphs(red, R_LOOP, RED_BODY)
    red_ex = red + 2
    c.set(red_ex, R_LOOP, "v")
    c.vertical(red_ex, R_LOOP, R_DATA + 1)
    c.set(red_ex, R_DATA + 1, "1")
    pipe(red_ex, R_SWAP, "s", "swap")
    c.set(red_ex, R_SWAP + 1, "H")  # the case is over; pipes still drain (SPEC)

    # ── the collector: every arm arrives southbound and turns west ───────────
    for xx in range(2, red_ex + 1):
        c.set(xx, R_COLLECT, "<")
    c.set(1, R_COLLECT, "^")
    c.vertical(1, R_COLLECT, R_MAIN)

    if red_ex >= UNIT_IW:
        raise SnakeUnitError(f"the arms reach column {red_ex}, past the {UNIT_IW}-wide interior")
    cells = {k: v for k, v in c.cell.items() if v != " "}
    return Unit(
        cells=cells,
        east={b: r for b, (wall, r) in BANDS.items() if wall == "east"},
        north={b: r for b, (wall, r) in BANDS.items() if wall == "north"},
        glyphs=glyphs,
        codes=arm_codes(),
    )


# ── binding margins, computed rather than argued ─────────────────────────────
def binding_margins(unit: Unit | None = None) -> dict[tuple[int, int], int]:
    """Per pipe glyph, how much nearer its own pipe is than the runner-up.

    Distance is Manhattan to the pipe's segment *attached to this room* — the
    source end for an ``s``, the destination end for an ``r`` (``SPEC.md``) — with
    the east wall at ``UNIT_IW + 1`` and the north wall at row 0. A margin of 0
    would be a reading-order tie, i.e. a coin flip; the assertion is ``>= 1``.
    """
    unit = unit or unit_interior()
    out: dict[tuple[int, int], int] = {}
    for gx, gy, glyph, band in unit.glyphs:
        rivals = {
            b: (UNIT_IW + 1 - gx) + abs(gy - r) if w == "east" else abs(gx - r) + gy
            for b, (w, r) in BANDS.items()
            if (b in ("ring_ret", "cmd")) == (glyph == "r")
        }
        mine = rivals.pop(band)
        out[(gx, gy)] = min(rivals.values()) - mine
    return out


# ── the placed block ────────────────────────────────────────────────────────
@dataclass
class SnakeBlock:
    """A placed SNAKE block: cells, the one anchor the CPU needs, capacities."""

    cells: dict[tuple[int, int], str]
    width: int
    height: int
    #: the cell a command pipe from the CPU must end on, pointing south
    cmd_cell: tuple[int, int]
    ring: int  # ring capacity in values
    pipes: int  # pipes the block draws (the engine must find exactly these + 1)
    panel: tuple[int, int]  # the panel's north-west wall corner
    lengths: dict[str, int] = field(default_factory=dict)
    glyphs: list[tuple[int, int, str, str]] = field(default_factory=list)
    codes: dict[str, int] = field(default_factory=dict)
    regions: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)


# Placement. Every constant below exists to keep eleven pipes planar, and two
# rules do all the work:
#
# **The ring runs north of the unit, the panel south of it.** The ring's legs
# occupy rows 6..28 in columns 52..56; the three panel pipes leave the east wall
# at rows 29, 33 and 35 and descend at columns 60, 56 and 55 — all *below* the
# ring's lowest cell, so no leg crosses another.
# **The panel sits east of the unit's shadow**, which is what makes DATA cheap:
# it may descend *west* of the panel and step straight east into the left wall,
# instead of detouring round the room to reach its far side.
UX, UY = 10, 8  # the unit room's north-west wall corner
EAST = UX + UNIT_IW + 2  # first free column east of its east wall
RELAY = (54, 1)  # the ring's turnaround room, north-east of the unit
RING_FWD_TURN, RING_RET_COL = 56, 55
IO_IN = (1, 0)  # the probe's I room
FEED = (1, 5)  # the probe's command relay: I -> cmd
ADDR_COL, DATA_COL, SWAP_COL = 60, 56, 55
PANEL_AT = (58, UY + 33)
DATA_ROW = 6  # which interior row of the panel DATA enters, tuned so len == addr


def build_snake() -> SnakeBlock:
    """Place the unit, its ring and relay, the panel, and the three panel pipes."""
    from .machine import MachineError, _Grid

    unit = unit_interior()
    g = _Grid()
    g.room(UX, UY, UX + UNIT_IW + 1, UY + UNIT_IH + 1)
    g.blit(UX, UY, unit.cells)

    npipes = 0

    def pipe(points: list[tuple[int, int]]) -> int:
        nonlocal npipes
        npipes += 1
        return g.draw_pipe(points)

    # ── the ring: east wall -> north -> relay -> south -> east wall ───────────
    rx, ry = RELAY
    g.room(rx, ry, rx + RELAY_IW + 1, ry + RELAY_IH + 1)
    g.blit(rx, ry, relay_cells())
    fwd = pipe(
        [
            (EAST, UY + R_RING),
            (RING_FWD_TURN, UY + R_RING),
            (RING_FWD_TURN, ry + RELAY_IH + 2),
        ]
    )
    ret = pipe(
        [
            (RING_RET_COL, ry + RELAY_IH + 2),
            (RING_RET_COL, UY + R_RET),
            (EAST, UY + R_RET),
        ]
    )

    # ── the panel and its three ports ────────────────────────────────────────
    px, py = PANEL_AT
    g.room(px, py, px + PANEL + 1, py + PANEL + 1, h="=", v=":")
    lengths = {
        # ADDR lands on the top wall, so it simply descends into it.
        "addr": pipe([(EAST, UY + R_ADDR), (ADDR_COL, UY + R_ADDR), (ADDR_COL, py - 1)]),
        # DATA lands on the left wall: descend *west* of the panel, then one step
        # east. `DATA_ROW` is the free variable that makes this exactly as long as
        # ADDR — see the module docstring.
        "data": pipe(
            [
                (EAST, UY + R_DATA),
                (DATA_COL, UY + R_DATA),
                (DATA_COL, py + DATA_ROW),
                (px - 1, py + DATA_ROW),
            ]
        ),
        # SWAP lands on the bottom wall, so it goes round the panel's south-west
        # corner and turns north. The northward leg is two cells, not one: the
        # terminal arrowhead has to *point* north, and an arrowhead's glyph comes
        # from the leg it arrives on.
        "swap": pipe(
            [
                (EAST, UY + R_SWAP),
                (SWAP_COL, UY + R_SWAP),
                (SWAP_COL, py + PANEL + 3),
                (ADDR_COL - 1, py + PANEL + 3),
                (ADDR_COL - 1, py + PANEL + 2),
            ]
        ),
    }
    if lengths["addr"] != lengths["data"]:
        raise MachineError(
            f"ADDR is {lengths['addr']} cells and DATA {lengths['data']}: a pixel would "
            "be painted at the wrong cursor; retune DATA_ROW"
        )
    if lengths["swap"] < lengths["data"]:
        raise MachineError("SWAP is shorter than DATA: a commit could overtake its pixels")

    rows = g.rows()
    regions = {
        "unit": (UX, UY, UNIT_IW + 2, UNIT_IH + 2),
        "unit:main": (UX + 1, UY + R_MAIN, TRIE_COL, 1),
        "unit:trie": (UX + LEAF0, UY + R_TRIE, LEAF_PITCH * 3 + 1, TRIE_BITS),
        **{
            f"unit:{arm}": (UX + x - 1, UY + R_ARG, LEAF_PITCH, R_COLLECT - R_ARG + 1)
            for arm, x in arm_columns().items()
        },
        "relay": (rx, ry, RELAY_IW + 2, RELAY_IH + 2),
        "panel": (px, py, PANEL + 2, PANEL + 2),
        "ring": (EAST, ry, RING_FWD_TURN - EAST + 1, UY + R_RING - ry + 1),
    }
    return SnakeBlock(
        cells=g.c,
        width=max(len(r) for r in rows),
        height=len(rows),
        cmd_cell=(UX + unit.north["cmd"], UY - 1),
        ring=fwd + ret,
        pipes=npipes,
        panel=PANEL_AT,
        lengths=lengths,
        regions=regions,
        glyphs=[(UX + x, UY + y, gl, band) for x, y, gl, band in unit.glyphs],
        codes=unit.codes,
    )


def build_probe() -> tuple[list[str], DebugMap, SnakeBlock]:
    """The block plus the smallest possible CPU: ``I -> relay -> cmd``.

    With no response pipe the whole protocol is write-only, so the driver is one
    relay room forwarding the program's input into the command pipe. Commands are
    therefore just ``--input``, and one grid proves every scenario.
    """
    from .machine import _Grid

    blk = build_snake()
    g = _Grid()
    for (x, y), ch in blk.cells.items():
        g.put(x, y, ch)

    ix, iy = IO_IN
    g.room(ix, iy, ix + 2, iy + 2)
    g.put(ix + 1, iy + 1, "I")
    fx, fy = FEED
    g.room(fx, fy, fx + RELAY_IW + 1, fy + RELAY_IH + 1)
    g.blit(fx, fy, relay_cells())
    g.draw_pipe([(ix + 1, iy + 3), (ix + 1, fy - 1)])
    cx, cy = blk.cmd_cell
    g.draw_pipe([(fx + RELAY_IW + 2, fy + 1), (cx, fy + 1), (cx, cy)])

    rows = g.rows()
    dbg = DebugMap("snake unit probe")
    for name, (x, y, w, h) in blk.regions.items():
        dbg.region(name, x, y, w, h, note=name)
    dbg.region("io:I", ix, iy, 3, 3, note="commands in")
    dbg.region("driver", fx, fy, RELAY_IW + 2, RELAY_IH + 2, note="I -> cmd relay")
    return rows, dbg, blk


# ── the protocol, as a CPU would drive it ────────────────────────────────────
def commands_for_rounds(rounds: list[list[int]]) -> list[int]:
    """The command words a CPU would send for ``snake_sim``'s rounds.

    This is the *interface spec*, executable: the unit owns the body and the
    panel, so the CPU only has to know which of four words a round becomes. It
    never has to look at the body — except for the wall death, which is pure
    arithmetic on the head, and which is exactly why ``RED`` exists.
    """
    codes = arm_codes()
    game = Game()
    out: list[int] = []

    def cell(xy: tuple[int, int]) -> int:
        x, y = xy
        return y * PANEL + x

    for values in rounds:
        if not game.started:
            out.append(word(codes["GROW"], cell((values[0], values[1]))))
        elif values[0] == FRUIT:
            out.append(word(codes["FRUIT"], cell((values[1], values[2]))))
        elif values[0] == TICK:
            hx, hy = game.body[0]
            dx, dy = game.direction
            nxt = (hx + dx, hy + dy)
            if nxt == game.fruit:
                out.append(word(codes["GROW"], cell(nxt)))
            elif not on_grid(nxt):
                out.append(word(codes["RED"], len(game.body)))
            else:
                out.append(word(codes["STEP"], len(game.body) * 256 + cell(nxt)))
        elif values[0] not in DIRECTIONS:
            raise SnakeUnitError(f"unknown round {values!r}")
        game.play_round(values)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", type=Path)
    ap.add_argument("--html", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--ruler", action="store_true", help="print the unit interior with a ruler")
    args = ap.parse_args(argv)

    if args.ruler:
        c = Circuit(UNIT_IW + 2, UNIT_IH + 2)
        for (x, y), ch in unit_interior().cells.items():
            c.set(x, y, ch)
        print(c.ruler())
        return 0

    rows, dbg, blk = build_probe()
    if args.man:
        args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    if args.html:
        dbg.write_html(rows, args.html)
    if args.json:
        dbg.write_json(args.json)
    if not (args.man or args.html or args.json):
        print("\n".join(rows))
    print(
        f"# {blk.width}x{blk.height}, ring={blk.ring} values, pipes={blk.pipes}, "
        f"codes={blk.codes}, panel pipes={blk.lengths}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
