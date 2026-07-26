#!/usr/bin/env python3
"""The PATH unit — the ``pathfinder`` board's LM-75 panel and the robot's cell.

This is :mod:`snake_unit` with the value ring taken out. ``pathfinder`` keeps its
whole state — four 64-bit board words and a BFS — in the CPU's tape, so the only
thing the coprocessor owns is

* the **16x16 panel**, with ADDR/DATA/SWAP on its top/left/bottom walls, and
* **one value**: the cell the robot is standing on, which is the only thing a
  ``MOVE`` needs that the previous frame already knows.

One value is still a value the unit cannot keep in a register (``A`` is clobbered
by every ``r``, ``B`` by every ``M``, and ``BP`` cannot be read back — ARCH §5.1),
so it lives where snake's body lived: in a pipe. Two two-legged pipes and a relay
room make a one-value ring, which ``SPEC.md`` forces anyway — a pipe that loops
back to its own room is silently dropped.

The CPU only ever *sends*. That is not a style choice: §7.1 makes an incoming pipe
a rival for **every** ``r`` in the CPU, including the jump slab's ROM read, so a
replying unit cannot be placed on a machine that has jumps at all (§8.0, measured
on ``snake``: all 4,800 placement combinations fail on exactly that binding).
Outgoing ports are free, and a unit that owns the display costs the CPU its three
panel lanes and their pipes.

Four commands, one word each, ``8 * arg + code`` exactly like :mod:`stream`::

    CELL  cell    paint the board cell (wall);            no commit.
    ROBOT cell    paint the robot, *become* that cell;    commit.
    FLAG  cell    paint the flag;                         no commit.
    MOVE  cell    erase the cell the robot leaves, paint
                  it at ``cell``, become it;              commit.

The codes are **read off the trie** (:func:`arm_codes`), and the arm order was
chosen so they come out as the CPU side wants them: ``CELL 0, ROBOT 1, FLAG 2,
MOVE 3``. ``x`` turns clockwise on BP's low bit and a man heading south turns
clockwise to the *west*, so a west branch means that bit is 1 — which is why
``MOVE`` (``11``) is the **westernmost** leaf and ``CELL`` (``00``) the easternmost.

Which arms are real, and which are stubs
----------------------------------------

This module is PART 2 of the build: the room, the panel, the pipes and the
assertions that keep them legal. What sits in each arm's column now is the
*geometric* skeleton — which row every ``s`` stands on, and which column the arm
climbs when it needs to paint twice — and it happens to be a working
micro-program. **PART 3 owns the behaviour**: the colours, which arms commit, and
what a command does to the register are all decided inside :data:`ARM_STUBS`'
columns and may be rewritten freely, as long as a send stays on its band's row.

Geometry: three rules, all asserted at build time
-------------------------------------------------

1. **Every outgoing pipe attaches to the east wall, on the row of the ``s`` that
   uses it** (``reg``, ``addr``, ``data``, ``swap``). All four share the same
   ``IW + 1 - x`` term, so the *row distance alone* decides binding: an ``s`` on
   its band's row is 0 away and every rival at least 1, whatever column its arm
   is in. Arms therefore run *down* their own column and "which row a glyph is
   on" is a free layout variable — pad with blanks until the next send lands on
   its band.
2. **The two incoming pipes are ``cmd`` on the north wall and the register's
   return on the east wall**, and ``cmd``'s column is deliberately *not*
   ``MAIN``'s: it sits at :data:`CMD_COL`, ten columns east. ``MOVE``'s ``r``
   stands in the westernmost arm's column, nine rows down, where a ``cmd`` port
   above ``MAIN`` would have been nearer than the register — the one binding this
   block cannot get by argument, and the reason the port moved east.
3. **The panel's three pipe lengths are related, not routed freely** (below).

Panel ports, and why relative pipe *length* is load-bearing
-----------------------------------------------------------

The panel processes ADDR, then DATA, then SWAP within a tick, and each port is a
separate pipe with its own transit time, so a DATA that overtakes its ADDR paints
the wrong pixel:

* ``len(addr) == len(data)`` — asserted. The two sends are four ticks apart in the
  arm's column and equal transit keeps them in that order at the panel.
* ``len(swap) > len(data)`` — asserted, **stricter than snake's** ``>=``. A commit
  must not overtake the pixels it commits; whether the equal case is safe is being
  measured on the engine by a sibling agent, and until it reports this builder
  refuses the tie.
* the skew ``len(swap) - len(addr)`` must stay under the gap between two commands,
  or one command's commit lands inside the next one's paints. The gap here is the
  collector walk back to ``MAIN`` plus the next arm's decode, ~34 ticks; snake ships
  a skew of 14 and this block has :data:`MAX_SKEW` to spare.

Layout, and why the panel hangs south-*east*
--------------------------------------------

ARCH §4.4 derives the three lanes' column order for a CPU that drops all three
pipes out of its south wall (DATA < ADDR < SWAP). This block drops them out of its
*east* wall and hangs the panel below-right, so the same three facts come out
mirrored, as descent columns west of the panel:

* ADDR leaves on the topmost band row and drops **straight** into the top wall, so
  the panel must **span ADDR's column** — asserted;
* DATA turns west into the left wall, so its descent column is west of the panel;
* SWAP leaves on the *lowest* band row and has to get *under* the panel, so its
  descent column is west of DATA's — otherwise SWAP's eastward leg, being lower,
  would cross DATA's descent.

The register's ring runs *north* of that: its relay sits above the panel's rows and
its two legs turn north in columns the three panel pipes only ever pass through
horizontally, which is what keeps all five pipes planar in a 40x33 box.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from ..circuit import Circuit, S
from ..man_debug import DebugMap
from .stream import RELAY_IH, RELAY_IW, relay_cells

__all__ = [
    "ARMS",
    "ARM_STUBS",
    "BANDS",
    "MAX_SKEW",
    "PANEL",
    "PathBlock",
    "PathUnitError",
    "UNIT_IH",
    "UNIT_IW",
    "Unit",
    "arm_codes",
    "arm_columns",
    "binding_margins",
    "build_path",
    "build_probe",
    "unit_interior",
    "word",
]


class PathUnitError(RuntimeError):
    """The block's geometry did not close, with the constraint that failed."""


# ── the unit's row map ───────────────────────────────────────────────────────
#: Interior rows. ``MAIN`` reads a command, a depth-2 trie fans it to four
#: columns, every arm recovers its argument with the same four glyphs, and the
#: rows below are the *band rows*: an ``s`` sitting on one of these binds that
#: pipe (module docstring, rule 1).
R_MAIN = 1
R_TRIE = 2  # rows 2..3
R_ARG = 4  # rows 4..7: `M 8 W /` — A = arg, B = code
R_REG = 8  # east wall, out: the robot's cell into the register
R_REG_RET = 9  # east wall, in: the register's return
R_ADDR = 10  # east wall, out: panel ADDR
R_DATA = 14  # east wall, out: panel DATA — four rows down, which is what `5M+`
#              (A = 10, the robot's colour) needs and a one-glyph colour tolerates
R_SWAP = 16  # east wall, out: panel SWAP — two rows down, room for the `1`
R_COLLECT = 18  # every arm rejoins here and walks back to MAIN

UNIT_IW = 16
UNIT_IH = R_COLLECT

#: The command port's column on the north wall. **Not** ``MAIN``'s ``r`` column:
#: see rule 2 in the module docstring — ``MOVE``'s ``r`` is nine rows below the
#: westernmost arm's leaf and would otherwise be nearer ``cmd`` than the register.
CMD_COL = 16

#: band -> the wall it attaches to and the row/column on it.
BANDS: dict[str, tuple[str, int]] = {
    "cmd": ("north", CMD_COL),
    "reg_ret": ("east", R_REG_RET),
    "reg": ("east", R_REG),
    "addr": ("east", R_ADDR),
    "data": ("east", R_DATA),
    "swap": ("east", R_SWAP),
}

#: Trie geometry: four leaves at ``LEAF0 + LEAF_PITCH*i``, entry column midway.
#: The pitch is 4, not snake's 6: no arm here runs a counted loop, so a simple arm
#: needs exactly one column and ``MOVE`` exactly three.
LEAF0 = 3
LEAF_PITCH = 4
TRIE_BITS = 2
TRIE_COL = LEAF0 + LEAF_PITCH * ((1 << TRIE_BITS) - 1) // 2  # 9

#: The four arms, **west to east**. The order is not free: it is what makes
#: :func:`arm_codes` return ``CELL 0, ROBOT 1, FLAG 2, MOVE 3``, and ``MOVE`` needs
#: to be the arm with three columns, which the westernmost leaf has (its neighbour
#: is a whole pitch away and nothing else lives between them).
ARMS: tuple[str, ...] = ("MOVE", "ROBOT", "FLAG", "CELL")

#: What each arm's column holds today, and what PART 3 may change about it. The
#: *rows* are geometry and are asserted; everything else here is policy.
ARM_STUBS: dict[str, str] = {
    "CELL": "ADDR=cell, DATA=7 (wall). No commit: round 0 shows one frame.",
    "ROBOT": "reg=cell, ADDR=cell, DATA=10, SWAP=1 — round 0's only commit.",
    "FLAG": "ADDR=cell, DATA=9. No commit: the flag appears in the next move's frame.",
    "MOVE": (
        "column 1: B=cell, r=old, ADDR=old, DATA=0 (erase); column 2 climbs back "
        "to R_ARG+3; column 3: reg=cell, ADDR=cell, DATA=10, SWAP=1."
    ),
}

#: The panel: 16x16, ``cell = row*16 + col`` (``SPEC.md``).
PANEL = 16
#: Colours (``tasks/problems/pathfinder.json``): path 0, wall 7, flag 9, robot 10.
PATH, WALL, FLAG, ROBOT = 0, 7, 9, 10
#: ``len(swap) - len(addr)`` must stay below the gap between two commands: the
#: collector walk back to ``MAIN`` (~25 ticks) plus the next arm's decode (~9).
MAX_SKEW = 34


def _bit_of(level: int) -> int:
    return 1 << (level - 1)


def arm_codes() -> dict[str, int]:
    """Command code per arm, *read off* the trie rather than assigned.

    ``x`` turns clockwise on BP's low bit and a man heading south turns clockwise
    to the **west**, so a west branch means that bit is 1. Move a leaf and these
    numbers move with it — which is why the CPU side must take them from here
    instead of hard-coding them (ARCH §7.1: opcode numbering is a layout variable).
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
        raise PathUnitError(f"trie has {len(leaves)} leaves for {len(ARMS)} arms")
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
_SEND_BAND: dict[int, str] = {
    R_REG: "reg",
    R_ADDR: "addr",
    R_DATA: "data",
    R_SWAP: "swap",
}
#: Which band an ``r`` on a given row belongs to, when it is on one at all.
_RECV_BAND: dict[int, str] = {R_MAIN: "cmd", R_REG_RET: "reg_ret"}


def unit_interior() -> Unit:
    """Lay the unit: MAIN, the decode trie, four arms, the collector."""
    c = Circuit(UNIT_IW + 2, UNIT_IH + 2)
    glyphs: list[tuple[int, int, str, str]] = []
    col = arm_columns()

    def pipe(x: int, y: int, glyph: str, band: str) -> None:
        c.set(x, y, glyph)
        glyphs.append((x, y, glyph, band))

    # ── MAIN: the command arrives from the north, BP decodes it ───────────────
    # `@` is a nop when walked over (verified on the engine), so the collector's
    # return leg may run straight through the spawn cell.
    c.set(1, R_MAIN, ">")
    c.set(2, R_MAIN, "@")
    pipe(3, R_MAIN, "r", "cmd")
    c.set(4, R_MAIN, "b")  # BP = the whole word; the trie reads its two low bits
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

    # Every arm starts with the same four glyphs: A = arg, B = code.
    for x in col.values():
        c.run(x, R_ARG, "M8W/", d=S)

    # ── CELL: paint one board cell. PART 3 owns the colour and the commit ─────
    x = col["CELL"]
    c.vertical(x, R_ARG + 3, R_ADDR)
    pipe(x, R_ADDR, "s", "addr")
    c.vertical(x, R_ADDR, R_DATA - 1)
    c.set(x, R_DATA - 1, str(WALL))
    pipe(x, R_DATA, "s", "data")
    c.vertical(x, R_DATA, R_COLLECT)

    # ── FLAG: paint the flag, no commit ──────────────────────────────────────
    x = col["FLAG"]
    c.vertical(x, R_ARG + 3, R_ADDR)
    pipe(x, R_ADDR, "s", "addr")
    c.vertical(x, R_ADDR, R_DATA - 1)
    c.set(x, R_DATA - 1, str(FLAG))
    pipe(x, R_DATA, "s", "data")
    c.vertical(x, R_DATA, R_COLLECT)

    # ── ROBOT: become this cell, paint it, commit ─────────────────────────────
    x = col["ROBOT"]
    pipe(x, R_REG, "s", "reg")  # the register's only writer besides MOVE
    c.vertical(x, R_REG, R_ADDR)
    pipe(x, R_ADDR, "s", "addr")
    c.run(x, R_ADDR + 1, "5M+", d=S)  # A = 10; `10` would need a literal pair
    pipe(x, R_DATA, "s", "data")
    c.set(x, R_DATA + 1, "1")
    pipe(x, R_SWAP, "s", "swap")  # SWAP = 1: commit and *keep* next (ARCH §4.4)
    c.vertical(x, R_SWAP, R_COLLECT)

    # ── MOVE: erase where the robot was, paint where it is, commit ───────────
    # Three columns, because a band only runs one way: the arm has to paint twice
    # and both ADDR sends must stand on R_ADDR, so it descends, climbs back above
    # the bands in its own gutter, and descends again.
    x = col["MOVE"]
    c.set(x, R_REG, "M")  # B = the new cell: it must survive the `r`
    pipe(x, R_REG_RET, "r", "reg_ret")  # A = the cell the robot is leaving
    pipe(x, R_ADDR, "s", "addr")
    c.set(x, R_ADDR + 1, str(PATH))
    c.vertical(x, R_ADDR + 1, R_DATA)
    pipe(x, R_DATA, "s", "data")  # erase first: the new cell may be this one
    c.set(x, R_DATA + 1, "W")  # A = the new cell again
    c.set(x, R_SWAP, ">")
    climb = x + 1
    c.set(climb, R_SWAP, "^")
    c.vertical(climb, R_SWAP, R_ARG + 3)
    c.set(climb, R_ARG + 3, ">")
    head = x + 2
    c.set(head, R_ARG + 3, "v")
    pipe(head, R_REG, "s", "reg")  # the register becomes the new cell
    c.vertical(head, R_REG, R_ADDR)
    pipe(head, R_ADDR, "s", "addr")
    c.run(head, R_ADDR + 1, "5M+", d=S)
    pipe(head, R_DATA, "s", "data")
    c.set(head, R_DATA + 1, "1")
    pipe(head, R_SWAP, "s", "swap")  # one frame per move
    c.vertical(head, R_SWAP, R_COLLECT)

    # ── the collector: every arm arrives southbound and turns west ───────────
    east_most = max(col.values())
    for xx in range(2, east_most + 1):
        c.set(xx, R_COLLECT, "<")
    c.set(1, R_COLLECT, "^")
    c.vertical(1, R_COLLECT, R_MAIN)

    if east_most >= UNIT_IW:
        raise PathUnitError(f"the arms reach column {east_most}, past the {UNIT_IW}-wide interior")

    unit = Unit(
        cells={k: v for k, v in c.cell.items() if v != " "},
        east={b: r for b, (wall, r) in BANDS.items() if wall == "east"},
        north={b: r for b, (wall, r) in BANDS.items() if wall == "north"},
        glyphs=glyphs,
        codes=arm_codes(),
    )
    _check_bands(unit)
    return unit


def _check_bands(unit: Unit) -> None:
    """Rule 1, checked: every ``s`` stands on its own band's row, on one wall."""
    walls = {BANDS[b][0] for _x, _y, g, b in unit.glyphs if g == "s"}
    if walls != {"east"}:
        raise PathUnitError(f"outgoing pipes leave by {sorted(walls)}, not the east wall alone")
    for x, y, glyph, band in unit.glyphs:
        want = _SEND_BAND.get(y) if glyph == "s" else _RECV_BAND.get(y)
        if want != band:
            raise PathUnitError(
                f"{glyph!r}@{(x, y)} claims band {band!r}, but row {y} is band {want!r}"
            )
    margins = binding_margins(unit)
    worst = min(margins.items(), key=lambda kv: kv[1])
    if worst[1] < 1:
        raise PathUnitError(
            f"the glyph at {worst[0]} is only {worst[1]} nearer its own pipe than the "
            "runner-up: a margin of 0 is a reading-order tie, i.e. a coin flip"
        )


# ── binding margins, computed rather than argued ─────────────────────────────
def binding_margins(unit: Unit | None = None) -> dict[tuple[int, int], int]:
    """Per pipe glyph, how much nearer its own pipe is than the runner-up.

    Distance is Manhattan to the pipe's segment *attached to this room* — the
    source end for an ``s``, the destination end for an ``r`` (``SPEC.md``) — with
    the east wall at ``UNIT_IW + 1`` and the north wall at row 0. Both walls are
    one cell short of the real segment, and identically so, which leaves every
    comparison intact.
    """
    unit = unit or unit_interior()
    out: dict[tuple[int, int], int] = {}
    for gx, gy, glyph, band in unit.glyphs:
        rivals = {
            b: (UNIT_IW + 1 - gx) + abs(gy - r) if w == "east" else abs(gx - r) + gy
            for b, (w, r) in BANDS.items()
            if (b in ("reg_ret", "cmd")) == (glyph == "r")
        }
        mine = rivals.pop(band)
        out[(gx, gy)] = min(rivals.values()) - mine
    return out


# ── the placed block ────────────────────────────────────────────────────────
@dataclass
class PathBlock:
    """A placed PATH block: cells, the one anchor the CPU needs, its pipes.

    ``lengths`` is not snake's ring bookkeeping: it is *every* pipe the builder
    drew, keyed by band, and it is what makes ``pipes == len(lengths)`` checkable.
    ``machine._stream`` needs ``pipes`` to stay an ``int``, so the count and the
    inventory are both carried and asserted against each other.
    """

    cells: dict[tuple[int, int], str]
    width: int
    height: int
    #: the cell a command pipe from the CPU must end on, pointing south
    cmd_cell: tuple[int, int]
    pipes: int  # pipes the block draws (the engine must find exactly these + 1)
    panel: tuple[int, int]  # the panel's north-west wall corner
    lengths: dict[str, int] = field(default_factory=dict)
    glyphs: list[tuple[int, int, str, str]] = field(default_factory=list)
    codes: dict[str, int] = field(default_factory=dict)
    regions: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)


# Placement. Every constant below exists to keep five pipes planar in a 40x33 box,
# and the module docstring's last section is the argument. In short: the register's
# relay sits north-east, above every row the panel pipes descend through, and the
# panel hangs south-east with its three descent columns in the order SWAP < DATA <
# panel <= ADDR.
UX, UY = 0, 1  # the unit room's north-west wall corner
EAST = UX + UNIT_IW + 2  # first free column east of its east wall
REG_RELAY = (19, 0)  # the register's turnaround room, north-east of the unit
REG_FWD_TURN, REG_RET_COL = 20, 21
SWAP_COL, DATA_COL, ADDR_COL = 19, 20, 23
PANEL_AT = (22, 13)
DATA_ROW = 5  # which interior row of the panel DATA enters, tuned so len == addr
SWAP_UP_COL = ADDR_COL + 1  # where SWAP comes back north into the bottom wall
#: The probe's own two anchors (see :func:`build_probe`).
FEED_AT = (0, 1)
BLOCK_AT = (5, 4)


def build_path() -> PathBlock:
    """Place the unit, its register ring and relay, the panel, and three ports."""
    from .machine import _Grid

    unit = unit_interior()
    g = _Grid()
    g.room(UX, UY, UX + UNIT_IW + 1, UY + UNIT_IH + 1)
    g.blit(UX, UY, unit.cells)

    lengths: dict[str, int] = {}

    def pipe(band: str, points: list[tuple[int, int]]) -> None:
        lengths[band] = g.draw_pipe(points)

    # ── the register: east wall -> north -> relay -> south -> east wall ───────
    rx, ry = REG_RELAY
    g.room(rx, ry, rx + RELAY_IW + 1, ry + RELAY_IH + 1)
    g.blit(rx, ry, relay_cells())
    attach = ry + RELAY_IH + 2  # one cell below the relay's south wall
    pipe("reg", [(EAST, UY + R_REG), (REG_FWD_TURN, UY + R_REG), (REG_FWD_TURN, attach)])
    # The return turns north *east* of the forward leg's own turn column, because
    # its row is the lower of the two: the other way round the two legs cross.
    pipe("reg_ret", [(REG_RET_COL, attach), (REG_RET_COL, UY + R_REG_RET), (EAST, UY + R_REG_RET)])
    if REG_RET_COL <= REG_FWD_TURN:
        raise PathUnitError("the register's return turns west of its forward leg: they cross")

    # ── the panel and its three ports ────────────────────────────────────────
    px, py = PANEL_AT
    g.room(px, py, px + PANEL + 1, py + PANEL + 1, h="=", v=":")
    # ADDR lands on the top wall, so it simply descends into it — and that is why
    # the panel has to span its column (ARCH §4.4).
    pipe("addr", [(EAST, UY + R_ADDR), (ADDR_COL, UY + R_ADDR), (ADDR_COL, py - 1)])
    # DATA lands on the left wall: descend *west* of the panel, then one step east.
    # `DATA_ROW` is the free variable that makes this exactly as long as ADDR.
    pipe(
        "data",
        [
            (EAST, UY + R_DATA),
            (DATA_COL, UY + R_DATA),
            (DATA_COL, py + DATA_ROW),
            (px - 1, py + DATA_ROW),
        ],
    )
    # SWAP lands on the bottom wall, so it goes round the panel's south-west corner
    # and turns north. The northward leg is two cells, not one: the terminal
    # arrowhead has to *point* north, and an arrowhead's glyph comes from the leg it
    # arrives on.
    pipe(
        "swap",
        [
            (EAST, UY + R_SWAP),
            (SWAP_COL, UY + R_SWAP),
            (SWAP_COL, py + PANEL + 3),
            (SWAP_UP_COL, py + PANEL + 3),
            (SWAP_UP_COL, py + PANEL + 2),
        ],
    )

    # ── the assertions the placement exists to satisfy ───────────────────────
    if lengths["addr"] != lengths["data"]:
        raise PathUnitError(
            f"ADDR is {lengths['addr']} cells and DATA {lengths['data']}: a pixel would "
            "be painted at the wrong cursor; retune DATA_ROW"
        )
    # Stricter than snake's `swap >= data` on purpose: whether the tie is safe is
    # being measured on the engine right now, and until that lands the tie is a bug.
    if lengths["swap"] <= lengths["data"]:
        raise PathUnitError(
            f"SWAP is {lengths['swap']} cells against DATA's {lengths['data']}: a commit "
            "could overtake the pixels it commits"
        )
    if lengths["swap"] - lengths["addr"] >= MAX_SKEW:
        raise PathUnitError(
            f"the ADDR/SWAP skew is {lengths['swap'] - lengths['addr']} ticks, which is not "
            f"under the {MAX_SKEW}-tick gap between two commands: a commit would land "
            "inside the next command's paints"
        )
    if not px < ADDR_COL < px + PANEL + 1:
        raise PathUnitError(
            f"the panel spans columns {px + 1}..{px + PANEL} and cannot take ADDR's "
            f"descent at {ADDR_COL}: ADDR has no corridor row to turn on (ARCH §4.4)"
        )
    if not SWAP_COL < DATA_COL < px:
        raise PathUnitError(
            f"the descent columns are SWAP {SWAP_COL}, DATA {DATA_COL}, panel {px}: DATA "
            "must turn west into the left wall and SWAP must pass under the panel from "
            "further west still, or the two legs cross"
        )

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
    }
    blk = PathBlock(
        cells=g.c,
        width=max(len(r) for r in rows),
        height=len(rows),
        cmd_cell=(UX + unit.north["cmd"], UY - 1),
        pipes=len(lengths),
        panel=PANEL_AT,
        lengths=lengths,
        regions=regions,
        glyphs=[(UX + x, UY + y, gl, band) for x, y, gl, band in unit.glyphs],
        codes=unit.codes,
    )
    if blk.pipes != len(blk.lengths):
        raise PathUnitError(f"the builder drew {len(blk.lengths)} pipes but reports {blk.pipes}")
    return blk


# ── the probe: the block plus the smallest possible driver ───────────────────
def _feeder_cells(commands: list[int]) -> tuple[dict[tuple[int, int], str], int, int]:
    """A driver room that sends ``commands`` and halts. Returns cells + interior size.

    One column of **vertical numeric literals**, each followed by an ``s``: the man
    walks south, loads a word when he reaches the literal's closing backtick, sends
    it (blocking naturally while the command pipe is full) and walks on. Backticks
    pair top-to-bottom within a column — 1st with 2nd, 3rd with 4th — so
    consecutive literals in one column pair exactly as written and the ``s`` between
    two of them sits *between* pairs, where it is not read as literal content
    (``SPEC.md``, "Fine print"). Keeping every backtick in one column is also what
    keeps them from pairing sideways with a neighbour's.
    """
    cells: dict[tuple[int, int], str] = {(1, 1): "@", (2, 1): "v"}
    y = 2
    for w in commands:
        if w < 0:
            raise PathUnitError(f"command {w} is negative: a vertical literal has no sign glyph")
        for ch in f"`{w}`s":
            cells[(2, y)] = ch
            y += 1
    cells[(2, y)] = "H"
    return cells, 2, y


def build_probe(commands: list[int]) -> tuple[list[str], DebugMap, PathBlock]:
    """The block plus the smallest possible CPU: a room that recites ``commands``.

    With no response pipe the whole protocol is write-only, so the driver is one
    room walking a ladder of literals into the command pipe — which makes the grid
    **standalone**: ``lm.mjs run`` it, or hand it to ``display-frames.mjs`` with
    ``"in": []``, and it plays the commands with no input at all.

    Returns ``(rows, debug map, block)``; the block's own coordinates are offset by
    :data:`BLOCK_AT` in the returned grid, and ``blk.width``/``blk.height`` are still
    the *block's* bounding box, which is what the score is paid on.
    """
    from .machine import _Grid

    blk = build_path()
    ox, oy = BLOCK_AT
    g = _Grid()
    for (x, y), ch in blk.cells.items():
        g.put(ox + x, oy + y, ch)

    fx, fy = FEED_AT
    cells, fw, fh = _feeder_cells(commands)
    g.room(fx, fy, fx + fw + 1, fy + fh + 1)
    g.blit(fx, fy, cells)

    cx, cy = ox + blk.cmd_cell[0], oy + blk.cmd_cell[1]
    lane = fy + 1  # the corridor row above the block, level with the feeder's `@`
    g.draw_pipe([(fx + fw + 2, lane), (cx, lane), (cx, cy)])

    rows = g.rows()
    dbg = DebugMap("path unit probe")
    for name, (x, y, w, h) in blk.regions.items():
        dbg.region(name, ox + x, oy + y, w, h, note=ARM_STUBS.get(name.removeprefix("unit:"), name))
    dbg.region("driver", fx, fy, fw + 2, fh + 2, note=f"{len(commands)} command words -> cmd")
    return rows, dbg, blk


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", type=Path)
    ap.add_argument("--html", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--ruler", action="store_true", help="print the unit interior with a ruler")
    ap.add_argument(
        "--commands",
        default="",
        help="command words for the probe, e.g. '9 1281' (see word()/arm_codes())",
    )
    args = ap.parse_args(argv)

    if args.ruler:
        c = Circuit(UNIT_IW + 2, UNIT_IH + 2)
        for (x, y), ch in unit_interior().cells.items():
            c.set(x, y, ch)
        print(c.ruler())
        return 0

    rows, dbg, blk = build_probe([int(v) for v in args.commands.split()])
    if args.man:
        args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    if args.html:
        dbg.write_html(rows, args.html)
    if args.json:
        dbg.write_json(args.json)
    if not (args.man or args.html or args.json):
        print("\n".join(rows))
    print(
        f"# block {blk.width}x{blk.height}, pipes={blk.pipes}, codes={blk.codes}, "
        f"panel pipes={blk.lengths}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
