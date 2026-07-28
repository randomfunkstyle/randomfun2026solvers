#!/usr/bin/env python3
"""The DOOM unit — deadman-3d's 64x48 LM-75 panel and its column painter.

``deadman-3d`` raycasts one viewport column at a time, and painting that column
used to be ~40 CPU paint laps plus a 512-pixel HUD unroll — most of the frame's
tick bill. This block is :mod:`path_unit`'s architecture (a write-only servant
that owns the display) with one addition, snake's value ring, used here as a
one-slot scratch FIFO so a counted loop can carry a value across laps.

The unit owns

* the **64x48 panel**, with ADDR/DATA/SWAP on its top/left/bottom walls;
* the two **baked patterns** — the HUD strip and the muzzle flash — so each is
  one command word instead of hundreds;
* the **column paint loops**: wall run then floor run, one ADDR/DATA pair per
  pixel at stride 64, running *concurrently* with the CPU's next raycast.

The CPU only ever *sends* (§7.1: a replying unit cannot coexist with ``JMPF`` —
measured on ``snake``), so every command carries what its drawing needs.

Six commands, one word each, ``8 * arg + code`` (:func:`arm_codes` reads the
codes off the trie — three levels, so the codes are the 3-bit west-branch
masks; ``store.DoomUnit.CODES`` and the generated asm's ``.equ C_*`` are
pinned against it)::

    COL    seed*64 + n   paint one viewport column, seaming every 4th wall
                         row via the mask ring; no commit.
                         seed = (top*64 + col)*16 + colour - 1024, n = wall px.
    RUN    count*16 + c  count pixels of colour c at the panel's own cursor
                         (row-major auto-advance) — the title screen's RLE,
                         and the HUD strip's painter behind a CURS.
    CURS   addr          reposition the panel cursor; no commit.
    GUN    0             the baked idle pistol sprite; no commit.
    GUNF   0             the recoil pistol + muzzle flash; no commit.
    COMMIT 0             SWAP 0 — commit the frame, clear next, reset cursor.

Why COL's argument looks like that
----------------------------------

The wall loop's whole per-lap state is one packed word ``v = addr*16 + colour``
circulating through the value ring (A is clobbered by every ``r``, B holds the
lap's constant 1024, BP is the loop counter — there is nowhere else). A lap
reads ``v``, **adds 1024 first** (one row of 64 cells, times 16), sends it
back, then splits it with one ``/ 16`` — quotient to ADDR, remainder to DATA.
So the natural argument is that packed word pre-biased by one lap, with the
wall count in the low digits: one floored ``/ 64`` in the arm recovers both
(and a negative seed, which a top-of-screen wall produces, survives floored
division and the pipes unchanged).

The floor run needs no colour in the ring — its colour 8 is a body glyph — so
the interlude between the loops converts ``v_last`` into the bare address seed,
recomputes the floor count ``39 - bot`` from it, and the drain ``r`` after the
floor loop leaves the ring empty for the next command.

Geometry: the same three rules as path_unit, all asserted
---------------------------------------------------------

1. **Every outgoing pipe attaches to the east wall on the row of the ``s`` that
   uses it** (``ring``, ``addr``, ``data``, ``swap``); the shared east-wall term
   cancels, so row distance alone decides binding and an ``s`` *between* band
   rows binds the nearest — which is what gives DATA a whole *window* of rows
   (those nearer R_DATA than R_ADDR above or R_SWAP below).
2. **The two incoming pipes are ``cmd`` on the north wall and the ring's return
   on the east wall.** Every deep ``r`` sits in the COL arm's far-east columns,
   where the east-wall term beats ``cmd``'s column-plus-depth by ~80 cells.
3. **The panel's pipe lengths are related, not free**: ``len(addr) ==
   len(data)`` (a DATA that overtakes its ADDR paints the wrong pixel — the
   wall lap sends them 10 ticks apart), and ``len(swap) > len(data)`` (SWAP
   only ever carries COMMIT, which trails the last paint by a whole collector
   walk, but the tie is refused on principle, as in path_unit).

The sprite arms and the backtick discipline
-------------------------------------------

The pistol arms bake the Freedoom-derived sprites (``deadman3d.GUN_IDLE`` /
``GUN_FIRE`` — see that module's art credits) as chains of one descent column
per sprite run — ``path_unit``'s MOVE pattern — with colours derived, not
spelled: B holds 8 for the whole arm, so a digit loads 0..9 and ``(c-8)~``
loads a..f, e.g. ``3~`` is 11 and ``7~`` is 15 (the engine-verified
workaround for the backtick pairing traps). The engine also pairs backticks
**along each row**, and a pair swallows everything between as one numeric
literal — which is why every sprite descent opens its address literal on the
same row (pairs stay local, climb-column blanks between), and why RUN's and
COL's argument unpacks are literal-free (16 = 8+8, 64 = 8*8, the argument
parked in ring 1 across the build).
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
    "BANDS",
    "DoomBlock",
    "DoomUnitError",
    "FLOOR_ROW",
    "PANEL_H",
    "PANEL_W",
    "UNIT_IH",
    "UNIT_IW",
    "Unit",
    "arm_codes",
    "arm_columns",
    "binding_margins",
    "build_doom",
    "build_probe",
    "unit_interior",
    "word",
]


class DoomUnitError(RuntimeError):
    """The block's geometry did not close, with the constraint that failed."""


# ── the unit's row map ───────────────────────────────────────────────────────
R_MAIN = 1
R_TRIE = 2  # rows 2..4 (TRIE_BITS levels)
R_ARG = 5  # COL's and RUN's `M8W/`; the other arms ignore the word
R_LOOP = 27  # the corridor row every counted loop is entered on, heading east
R_RET = 28  # east wall, in: value ring 1's return
R_RING = 30  # east wall, out: into value ring 1
R_ADDR = 46  # east wall, out: panel ADDR
R_DATA = 56  # east wall, out: panel DATA
R_RET2 = 70  # east wall, in: the mask ring's return (V3 banding)
R_RING2 = 72  # east wall, out: into the mask ring
R_SWAP = 80  # east wall, out: panel SWAP — far below, so DATA's window is deep
R_COLLECT = 82  # every arm rejoins here and walks back to MAIN

#: The DATA window: rows strictly nearer R_DATA than R_ADDR or R_RING2. The HUD
#: serpentine and every arm's DATA send must stay inside it.
WIN_TOP = (R_ADDR + R_DATA) // 2 + 1  # 52
WIN_BOT = (R_DATA + R_RING2) // 2 - 1  # 61

UNIT_IW = 156
UNIT_IH = R_COLLECT

#: The command port's column on the north wall (near MAIN, far from every deep
#: ``r`` in the COL arm's eastern columns — rule 2).
CMD_COL = 16

#: band -> the wall it attaches to and the row/column on it.
BANDS: dict[str, tuple[str, int]] = {
    "cmd": ("north", CMD_COL),
    "ring_ret": ("east", R_RET),
    "ring": ("east", R_RING),
    "addr": ("east", R_ADDR),
    "data": ("east", R_DATA),
    "ring2_ret": ("east", R_RET2),
    "ring2": ("east", R_RING2),
    "swap": ("east", R_SWAP),
}

#: Trie geometry: eight leaves at ``LEAF0 + LEAF_PITCH*i``, entry column midway.
#: The pitch is what buys the HUD field its width: the serpentine lives between
#: the RUN arm's columns and the COL arm's leaf, riding over the three spare
#: leaves (spares have no machinery below the trie's leaf row).
LEAF0 = 3
LEAF_PITCH = 20
TRIE_BITS = 3
TRIE_COL = LEAF0 + LEAF_PITCH * ((1 << TRIE_BITS) - 1) // 2  # 73

#: Which leaf each arm hangs from, **west to east**. Not free: the leaves fix
#: :func:`arm_codes` (a west branch is a set bit), COL must be leaf 7 so its
#: code is 0 (the CPU's per-column send is a bare ``MULI 8``) and because its
#: loop machinery spills ten columns east, and RUN must sit west of HUD so the
#: serpentine field east of HUD's descent clears RUN's loop columns. Leaves
#: 2 and 5 are spare — headroom for later arms (GUN moved from leaf 2 to 1
#: when the Freedoom-derived sprite's 12 runs outgrew the ten columns before
#: CURS; each arm's run chain may ride east over a spare leaf, which has no
#: machinery below the trie's leaf row).
#: RUN sits on an EASTERN leaf: its literal-free ``/16`` parks the argument in
#: ring 1 and takes it back with an ``r``, and rule 2 only lets an ``r`` beat
#: the cmd pipe's north-wall distance from the far-east columns. The sprite
#: arms fill the west instead (their chains are ``s``-only).
ARM_LEAF: dict[str, int] = {
    "COMMIT": 0, "GUN": 1, "CURS": 3, "GUNF": 4, "RUN": 6, "COL": 7,
}
ARMS: tuple[str, ...] = tuple(ARM_LEAF)

#: The panel (``machine.DISPLAY_OVERRIDE["deadman-3d"]``).
PANEL_W, PANEL_H = 64, 48
H3D = 40  # viewport rows 0..39; the HUD strip is rows 40..47
FLOOR = 8

#: The last panel row COL's floor run fills, baked into the arm as a two-digit
#: literal.  On the 64x48 panel it is the viewport's own last row, 39.  It is a
#: parameter only because the tiled wall (``d3_router.py``) needs a *different*
#: one per tile row: at 128x96 the viewport is logical rows 0..79, so a top tile
#: floors to its own row 47 and a bottom tile stops at 31 where the HUD begins.
#: Two digits either way, so the arm's geometry below the literal never moves —
#: which is what keeps the default build byte-identical.
FLOOR_ROW = H3D - 1

#: The COL arm's two counted-loop bodies (rows R_RET..; sends on their bands).
#: The banded wall lap (V3): pop v from ring 1, v += 1024, push it back; split
#: addr | colour with one `/`; send ADDR; pop this row's mask from the mask
#: ring, send DATA = colour & mask (every 4th row's mask is 7 — the seam that
#: drops the bright variant to the dark shade), and push the mask back — the
#: FIFO is the rotation. Register flow: A carries the working value, B parks
#: the one being held across a glyph, the two rings hold everything else.
#:
#: Why the mask alphabet is exactly {7, 15} (measured against Freedoom's
#: STARTAN-family patches, sw17_*/sw19_*, for a richer tile): a mask that
#: clears any LOW bit changes hue (7 & 11 = 3 turns white walls brown), and a
#: mask touching bit 3 alone (8, 0) turns far walls — all-dark ``t`` columns —
#: into black scanlines. So each lap row can only express {seam, keep}, and
#: the (7,15,15,15) ring + the CPU's parity stripes already ARE the patches'
#: downscaled character (thin dark seams over alternating panels). The next
#: step up, a lit bevel row (bright on BOTH stripe columns), needs
#: ``(c & 7) ^ 8`` — two masks per row — and cannot be scheduled: A+B is the
#: whole register file (BP is the lap counter, ring 1 carries the packed
#: cursor), a packed mask pair needs B=16 while B holds the colour, and a
#: push-back-free circulating ring cannot re-anchor the seam phase at each
#: column's wall top (the per-command reseed below is what anchors it).
BAND_BODY = "rM`1024`+sM`16`W/ sWM rW&sW" + " " * 10 + "s"
#: Floor lap: addr += 64, back to the ring, ADDR, then the baked colour 8.
FLOOR_BODY = "r+s" + " " * 15 + "s8" + " " * 8 + "s"
#: RUN lap: one bare DATA send of the colour parked in A (the panel's cursor
#: advances itself); the blanks put the ``s`` on the DATA window's first row.
RUN_BODY = " " * (WIN_TOP - R_LOOP - 1) + "s"


def _bit_of(level: int) -> int:
    return 1 << (level - 1)


def arm_codes() -> dict[str, int]:
    """Command code per arm, *read off* the trie rather than assigned.

    ``x`` turns on BP's low bit and a man heading south turns clockwise to the
    **west**, so a west branch means that bit is 1. Move a leaf and these
    numbers move with it — the emulator model (``store.DoomUnit.CODES``) and
    the generated asm's ``.equ C_*`` are pinned against this function.
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
    if len(leaves) != (1 << TRIE_BITS) or max(ARM_LEAF.values()) >= len(leaves):
        raise DoomUnitError(f"trie has {len(leaves)} leaves for arms at {ARM_LEAF}")
    if ARM_LEAF["COL"] != len(leaves) - 1:
        raise DoomUnitError("COL must be the easternmost leaf (its loops spill east)")
    return {arm: codes[leaves[leaf]] for arm, leaf in ARM_LEAF.items()}


def arm_columns() -> dict[str, int]:
    """Interior column of each arm's leaf, west to east."""
    return {arm: LEAF0 + LEAF_PITCH * leaf for arm, leaf in ARM_LEAF.items()}


def word(code: int, arg: int) -> int:
    """One command word. Floored ``/`` recovers a negative ``arg`` too."""
    return 8 * arg + code


def _pixel_tokens(colors: str) -> list[str]:
    """Colour loads and bare DATA sends for one contiguous sprite run.

    B holds 8 for the whole arm, so every colour is a literal-free load —
    a bare digit for 0..9 and ``(c-8)~`` (XOR the parked 8) for a..f, e.g.
    ``b`` (11) is ``3~`` and ``f`` (15) is ``7~`` — which keeps the sprite
    columns free of backtick-pairing traps. A repeated colour is a bare send
    (the panel cursor advances itself).
    """
    loads = {
        **{"%x" % c: ["%d" % c] for c in range(10)},
        **{"%x" % c: ["%d" % (c - 8), "~"] for c in range(10, 16)},
    }
    toks: list[str] = []
    cur = None
    for ch in colors:
        if ch != cur:
            toks += loads[ch]
            cur = ch
        toks.append("s")
    return toks


# ── the unit's interior ──────────────────────────────────────────────────────
@dataclass
class Unit:
    """The unit's interior, plus where each of its pipes must attach."""

    cells: dict[tuple[int, int], str]
    width: int = UNIT_IW
    height: int = UNIT_IH
    east: dict[str, int] = field(default_factory=dict)
    north: dict[str, int] = field(default_factory=dict)
    #: every pipe glyph: ``(x, y, glyph, band)`` in interior coordinates
    glyphs: list[tuple[int, int, str, str]] = field(default_factory=list)
    codes: dict[str, int] = field(default_factory=dict)


#: Which band an ``s`` on a given row belongs to: the nearest band row wins
#: (the east-wall term is shared and cancels — rule 1).
_S_BANDS = ("ring", "addr", "data", "ring2", "swap")


def _send_band(row: int) -> str:
    rows = {"ring": R_RING, "addr": R_ADDR, "data": R_DATA,
            "ring2": R_RING2, "swap": R_SWAP}
    return min(_S_BANDS, key=lambda b: abs(row - rows[b]))


def unit_interior(floor_row: int = FLOOR_ROW) -> Unit:
    """Lay the unit: MAIN, the trie, four arms, the HUD field, the collector.

    ``floor_row`` is the last panel row COL's floor run fills; see
    :data:`FLOOR_ROW` for why it is a parameter and why changing it moves no
    other cell.
    """
    if not 10 <= floor_row <= 99:
        raise DoomUnitError(
            f"floor_row {floor_row} is not two digits: the literal's width sets "
            "every row below it in the COL arm, and the arm is tuned around two"
        )
    c = Circuit(UNIT_IW + 2, UNIT_IH + 2)
    glyphs: list[tuple[int, int, str, str]] = []
    col = arm_columns()

    def pipe(x: int, y: int, glyph: str, band: str) -> None:
        c.set(x, y, glyph)
        glyphs.append((x, y, glyph, band))

    def body_glyphs(x: int, y0: int, body: str) -> None:
        """Register a counted-loop body's ``r``/``s`` against their bands."""
        for i, ch in enumerate(body):
            row = y0 + 1 + i
            if ch == "r":
                band = "ring_ret" if abs(row - R_RET) <= abs(row - R_RET2) else "ring2_ret"
                glyphs.append((x + 1, row, ch, band))
            elif ch == "s":
                glyphs.append((x + 1, row, ch, _send_band(row)))

    # ── MAIN: the command arrives from the north, BP decodes it ──────────────
    c.set(1, R_MAIN, ">")
    c.set(2, R_MAIN, "@")
    pipe(3, R_MAIN, "r", "cmd")
    c.set(4, R_MAIN, "b")
    c.horizontal(R_MAIN, 4, TRIE_COL)
    c.set(TRIE_COL, R_MAIN, "v")

    # ── the decode trie, fanning *sideways*: leaves are columns ──────────────
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

    # ── COMMIT: SWAP 0 — commit the frame, clear next, reset the cursor ──────
    x = col["COMMIT"]
    c.vertical(x, R_ARG - 1, R_SWAP - 1)
    c.set(x, R_SWAP - 1, "0")
    pipe(x, R_SWAP, "s", "swap")
    c.vertical(x, R_SWAP, R_COLLECT)

    # ── RUN: colour run at the panel's own cursor (the title screen's RLE,
    # and since V4 the HUD strip's painter behind a CURS) ────────────────────
    # arg = count*16 + colour: split it, park the colour in A (d/m only touch
    # BP, so it survives every lap), BP = count, then a counted loop of one
    # bare DATA send per lap — the cursor advances itself, so consecutive RUNs
    # paint the panel row-major exactly like deadman3d.title_runs().
    x = col["RUN"]
    c.run(x, R_ARG, "M8W/", d=S)  # A = arg, B = the code (dead)
    # literal-free /16 (a backtick here would row-pair across the sprite
    # arms' digit rows): park arg in ring 1, build 16 = 8 + 8, take it back
    pipe(x, R_ARG + 4, "s", "ring")  # ring 1 holds [arg]
    c.run(x, R_ARG + 5, "8M8+M", d=S)  # B = 16
    pipe(x, R_ARG + 10, "r", "ring_ret")  # A = arg, B = 16
    c.run(x, R_ARG + 11, "/bW", d=S)  # A -> count -> BP; A = colour
    c.vertical(x, R_ARG + 13, R_LOOP)
    c.set(x, R_LOOP, ">")
    c.counted_loop(x + 1, R_LOOP, RUN_BODY)
    body_glyphs(x + 1, R_LOOP, RUN_BODY)
    rx = x + 3  # counted_loop's exit cell, heading east
    c.set(rx, R_LOOP, "v")
    c.vertical(rx, R_LOOP, R_COLLECT)

    # ── CURS (V4): reposition the panel cursor — the RLE painter's ADDR ──────
    x = col["CURS"]
    c.run(x, R_ARG, "M8W/", d=S)  # A = arg = the target address
    c.vertical(x, R_ARG + 3, R_ADDR)
    pipe(x, R_ADDR, "s", "addr")
    c.vertical(x, R_ADDR, R_COLLECT)

    # ── GUN / GUNF (V4): the baked pistol sprites, one descent per row run ───
    # path_unit's MOVE pattern like the old FLASH arm, generated straight from
    # deadman3d's sprite tables: `addr` -> ADDR, then the run's colour loads
    # and sends walked down the DATA window, linked by blank climb columns.
    def sprite_arm(x0: int, runs: tuple[tuple[int, int, str], ...], bound: int) -> None:
        """One descent per sprite run, chained east; the chain (and each run's
        climb column) must stay strictly west of ``bound`` — the next arm's
        leaf column."""
        a = x0
        for k, (row, col0, colors) in enumerate(runs):
            lit = f"`{row * PANEL_W + col0}`"
            if k > 0:
                c.set(a, R_ARG, "v")
            c.run(a, R_ARG + 1, lit, d=S)  # every descent's backticks share rows
            c.vertical(a, R_ARG + len(lit), R_ADDR)
            pipe(a, R_ADDR, "s", "addr")
            toks = _pixel_tokens(colors)
            if k == 0:
                toks = ["8", "M"] + toks  # B = 8 for the whole arm (b=3~, f=7~)
            first = toks.index("s")
            lead, body = toks[:first], toks[first:]
            if len(lead) > WIN_TOP - R_ADDR - 2:
                raise DoomUnitError(f"sprite run {(row, col0)}: {len(lead)} leading loads")
            if len(body) > WIN_BOT - WIN_TOP + 1:
                raise DoomUnitError(
                    f"sprite run {(row, col0)} needs {len(body)} DATA-window rows; "
                    f"split it or shorten it"
                )
            y0 = WIN_TOP - len(lead)
            c.vertical(a, R_ADDR, y0)
            for i, t in enumerate(toks):
                if t == "s":
                    pipe(a, y0 + i, "s", "data")
                else:
                    c.set(a, y0 + i, t)
            y_end = y0 + len(toks)
            if k + 1 < len(runs):
                c.set(a, y_end, ">")
                c.set(a + 1, y_end, "^")
                c.vertical(a + 1, y_end, R_ARG)
                c.set(a + 1, R_ARG, ">")
                a += 2
            else:
                c.vertical(a, y_end - 1, R_COLLECT)
        if a + 1 >= bound:
            raise DoomUnitError(
                f"sprite arm at {x0} spills to column {a}, into the arm at {bound}"
            )

    from ..deadman3d import GUN_FIRE, GUN_IDLE

    sprite_arm(col["GUN"], tuple(GUN_IDLE), bound=col["CURS"])
    sprite_arm(col["GUNF"], tuple(GUN_FIRE), bound=col["RUN"])

    # ── COL: unpack, seed the mask ring, the banded wall loop, the interlude,
    # the floor loop, the drains ─────────────────────────────────────────────
    x = col["COL"]
    c.run(x, R_ARG, "M8W/", d=S)  # A = arg, B = the code
    # literal-free /64 (same backtick row-pairing dodge as RUN's): park arg,
    # build 64 = 8 * 8, take arg back and split it
    pipe(x, R_ARG + 4, "s", "ring")  # ring 1 holds [arg]
    c.run(x, R_ARG + 5, "8M8*M", d=S)  # B = 64
    pipe(x, R_ARG + 10, "r", "ring_ret")  # A = arg, B = 64
    c.run(x, R_ARG + 11, "/WbW", d=S)  # A = seed, then BP = n_wall, A = seed
    pipe(x, R_ARG + 15, "s", "ring")  # ring 1 holds [seed]
    # seed the mask ring for THIS command — [7, 15, 15, 15], so the banding
    # seam anchors at the wall run's top row — then climb back to the loop
    c.vertical(x, R_ARG + 15, R_RING2 - 6)
    c.set(x, R_RING2 - 6, "7")  # A = 7 (the seam mask)
    pipe(x, R_RING2 - 5, "s", "ring2")
    c.run(x, R_RING2 - 4, "`15`", d=S)  # A = 15 (the no-op mask)
    pipe(x, R_RING2, "s", "ring2")
    pipe(x, R_RING2 + 1, "s", "ring2")
    pipe(x, R_RING2 + 2, "s", "ring2")
    c.set(x, R_RING2 + 3, ">")
    c.set(x + 1, R_RING2 + 3, "^")
    c.vertical(x + 1, R_RING2 + 3, R_LOOP)
    c.set(x + 1, R_LOOP, ">")
    c.counted_loop(x + 2, R_LOOP, BAND_BODY)
    body_glyphs(x + 2, R_LOOP, BAND_BODY)
    wx = x + 4  # counted_loop's exit cell (x+2 of its own x), heading east

    # the interlude: v_last -> the floor seed and count (see the docstring)
    c.set(wx, R_LOOP, "v")
    pipe(wx, R_RET, "r", "ring_ret")  # A = v_last; the ring is empty
    c.set(wx, R_RET + 1, "M")
    c.run(wx, R_RET + 2, "`16`", d=S)
    c.run(wx, R_RET + 6, "W/", d=S)  # A = addr_last, B = colour (dead)
    c.set(wx, R_RET + 8, ">")
    climb = wx + 1
    c.set(climb, R_RET + 8, "^")
    c.vertical(climb, R_RET + 8, R_RING)
    pipe(climb, R_RING, "s", "ring")  # the ring holds [addr_last]
    c.vertical(climb, R_RING, R_LOOP)
    c.set(climb, R_LOOP, ">")
    ix = wx + 2
    c.set(ix, R_LOOP, "v")
    c.set(ix, R_RET, "M")  # B = addr_last
    c.run(ix, R_RET + 1, "`64`", d=S)
    c.run(ix, R_RET + 5, "W/M", d=S)  # A = bot (addr/64), B = bot
    c.run(ix, R_RET + 8, f"`{floor_row}`", d=S)
    c.set(ix, R_RET + 12, "-")  # A = floor_row - bot = the floor count
    c.set(ix, R_RET + 13, "b")
    c.run(ix, R_RET + 14, "`64`", d=S)
    c.set(ix, R_RET + 18, "M")  # B = 64, the floor lap's constant
    c.set(ix, R_RET + 19, ">")
    climb2 = ix + 1
    c.set(climb2, R_RET + 19, "^")
    c.vertical(climb2, R_RET + 19, R_LOOP)
    c.set(climb2, R_LOOP, ">")
    fx = ix + 2
    c.counted_loop(fx, R_LOOP, FLOOR_BODY)
    body_glyphs(fx, R_LOOP, FLOOR_BODY)
    dx = fx + 2
    c.set(dx, R_LOOP, "v")
    pipe(dx, R_RET, "r", "ring_ret")  # drain ring 1: it must end empty
    c.vertical(dx, R_RET, R_RING2 - 8)
    for yy in range(R_RING2 - 8, R_RING2 - 4):  # drain the four masks too
        pipe(dx, yy, "r", "ring2_ret")
    c.vertical(dx, R_RING2 - 5, R_COLLECT)

    if dx >= UNIT_IW:
        raise DoomUnitError(f"the COL arm reaches column {dx}, past the {UNIT_IW}-wide interior")

    # ── the collector: every arm arrives southbound and turns west ───────────
    for xx in range(2, dx + 1):
        c.set(xx, R_COLLECT, "<")
    c.set(1, R_COLLECT, "^")
    c.vertical(1, R_COLLECT, R_MAIN)

    unit = Unit(
        cells={k: v for k, v in c.cell.items() if v != " "},
        east={b: r for b, (wall, r) in BANDS.items() if wall == "east"},
        north={b: r for b, (wall, r) in BANDS.items() if wall == "north"},
        glyphs=glyphs,
        codes=arm_codes(),
    )
    _check_unit(unit)
    return unit


def _check_unit(unit: Unit) -> None:
    """Rule 1 and rule 2, checked: bands, windows, and binding margins."""
    for x, y, glyph, band in unit.glyphs:
        if glyph == "s" and band != "cmd":
            want = _send_band(y)
            if want != band:
                raise DoomUnitError(f"s@{(x, y)} claims band {band!r}, row {y} is {want!r}")
    margins = binding_margins(unit)
    worst = min(margins.items(), key=lambda kv: kv[1])
    if worst[1] < 1:
        raise DoomUnitError(
            f"the glyph at {worst[0]} is only {worst[1]} nearer its own pipe than the "
            "runner-up: a margin of 0 is a reading-order tie, i.e. a coin flip"
        )


# ── binding margins, computed rather than argued ─────────────────────────────
def binding_margins(unit: Unit | None = None) -> dict[tuple[int, int], int]:
    """Per pipe glyph, how much nearer its own pipe is than the runner-up.

    Distance is Manhattan to the pipe's segment *attached to this room* — the
    source end for an ``s``, the destination end for an ``r`` (``SPEC.md``) —
    with the east wall at ``UNIT_IW + 1`` and the north wall at row 0.
    """
    unit = unit or unit_interior()
    out: dict[tuple[int, int], int] = {}
    for gx, gy, glyph, band in unit.glyphs:
        rivals = {
            b: (UNIT_IW + 1 - gx) + abs(gy - r) if w == "east" else abs(gx - r) + gy
            for b, (w, r) in BANDS.items()
            if (b in ("ring_ret", "ring2_ret", "cmd")) == (glyph == "r")
        }
        mine = rivals.pop(band)
        out[(gx, gy)] = min(rivals.values()) - mine
    return out


# ── the placed block ────────────────────────────────────────────────────────
@dataclass
class DoomBlock:
    """A placed DOOM block: cells, the one anchor the CPU needs, its pipes."""

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


# Placement. The relays sit just east of their ring rows so both value rings
# are short (ring 1's latency bounds a paint lap; the mask ring must beat the
# ~72-tick lap walk); the panel hangs further east with its three descent
# columns in the order SWAP < DATA < panel <= ADDR, which is what keeps the
# routes planar with SWAP leaving on the *lowest* band row. DATA's descent
# runs east of RELAY2 (cols EAST+3..EAST+7) so it cannot cross the mask
# ring's pipes at rows R_RET2/R_RING2.
UX, UY = 0, 1  # the unit room's north-west wall corner
EAST = UX + UNIT_IW + 2  # first free column east of its east wall
RELAY_AT = (EAST + 3, UY + R_RET - 1)  # ring 1's turnaround room
RELAY2_AT = (EAST + 3, UY + R_RET2 - 1)  # the mask ring's turnaround (V3)
SWAP_COL = EAST + 2
DATA_COL = EAST + 9
# The panel's top wall must sit below ADDR's east-wall row (ADDR descends into
# the top wall), and that is the *only* floor on its height: DATA's window and
# SWAP's under-run are column-planar whatever the row, and DATA_ROW's
# length-equality is py-independent (addr and data both grow one cell per row).
# R_SWAP + 4 was 37 rows lower for no reason the routes needed, and the whole
# machine's bounding box paid them — the DOOM block hangs below everything.
PANEL_AT = (EAST + 11, UY + R_ADDR + 2)
ADDR_COL = EAST + 13  # inside the panel's column span (ARCH §4.4)
SWAP_UP_COL = EAST + 14  # where SWAP comes back north into the bottom wall
DATA_ROW = 12  # which interior row of the panel DATA enters; tuned so len == addr
#: The probe's own two anchors (see :func:`build_probe`).
FEED_AT = (0, 1)
BLOCK_AT = (8, 4)


def build_doom(floor_row: int = FLOOR_ROW) -> DoomBlock:
    """Place the unit, its value ring and relay, the panel, and three ports.

    ``floor_row`` is passed straight to :func:`unit_interior`; the default is the
    64x48 panel's own viewport bound and reproduces the checked-in block exactly.
    """
    from .machine import _Grid

    unit = unit_interior(floor_row)
    g = _Grid()
    g.room(UX, UY, UX + UNIT_IW + 1, UY + UNIT_IH + 1)
    g.blit(UX, UY, unit.cells)

    lengths: dict[str, int] = {}

    def pipe(band: str, points: list[tuple[int, int]]) -> None:
        lengths[band] = g.draw_pipe(points)

    # ── the value rings: east wall -> relay -> east wall, kept short ─────────
    rx, ry = RELAY_AT
    g.room(rx, ry, rx + RELAY_IW + 1, ry + RELAY_IH + 1)
    g.blit(rx, ry, relay_cells())
    pipe("ring", [(EAST, UY + R_RING), (rx - 1, UY + R_RING)])
    pipe("ring_ret", [(rx - 1, UY + R_RET), (EAST, UY + R_RET)])
    # the mask ring (V3): same relay, lower band rows; its capacity (two 2-cell
    # pipes + the relay man's hand) holds the four masks in rotation
    rx2, ry2 = RELAY2_AT
    g.room(rx2, ry2, rx2 + RELAY_IW + 1, ry2 + RELAY_IH + 1)
    g.blit(rx2, ry2, relay_cells())
    pipe("ring2", [(EAST, UY + R_RING2), (rx2 - 1, UY + R_RING2)])
    pipe("ring2_ret", [(rx2 - 1, UY + R_RET2), (EAST, UY + R_RET2)])

    # ── the panel and its three ports ────────────────────────────────────────
    px, py = PANEL_AT
    g.room(px, py, px + PANEL_W + 1, py + PANEL_H + 1, h="=", v=":")
    # ADDR lands on the top wall, so it simply descends into it — which is why
    # the panel must span its column (ARCH §4.4).
    pipe("addr", [(EAST, UY + R_ADDR), (ADDR_COL, UY + R_ADDR), (ADDR_COL, py - 1)])
    # DATA lands on the left wall: descend *west* of the panel, one step east.
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
    # SWAP leaves on the lowest band row, goes under the panel's south-west
    # corner and turns north. Two cells on the northward leg: the terminal
    # arrowhead has to *point* north.
    pipe(
        "swap",
        [
            (EAST, UY + R_SWAP),
            (SWAP_COL, UY + R_SWAP),
            (SWAP_COL, py + PANEL_H + 3),
            (SWAP_UP_COL, py + PANEL_H + 3),
            (SWAP_UP_COL, py + PANEL_H + 2),
        ],
    )

    # ── the assertions the placement exists to satisfy ───────────────────────
    if lengths["addr"] != lengths["data"]:
        raise DoomUnitError(
            f"ADDR is {lengths['addr']} cells and DATA {lengths['data']}: a pixel would "
            "be painted at the wrong cursor; retune DATA_ROW"
        )
    if lengths["swap"] <= lengths["data"]:
        raise DoomUnitError(
            f"SWAP is {lengths['swap']} cells against DATA's {lengths['data']}: a commit "
            "could overtake the pixels it commits"
        )
    if not px < ADDR_COL < px + PANEL_W + 1:
        raise DoomUnitError(
            f"the panel spans columns {px + 1}..{px + PANEL_W} and cannot take ADDR's "
            f"descent at {ADDR_COL} (ARCH §4.4)"
        )
    if not SWAP_COL < DATA_COL < px:
        raise DoomUnitError(
            f"the descent columns are SWAP {SWAP_COL}, DATA {DATA_COL}, panel {px}: "
            "they must run west of the panel in that order or the legs cross"
        )

    rows = g.rows()
    regions = {
        "unit": (UX, UY, UNIT_IW + 2, UNIT_IH + 2),
        "unit:main": (UX + 1, UY + R_MAIN, TRIE_COL, 1),
        "unit:trie": (UX + LEAF0, UY + R_TRIE, LEAF_PITCH * 7 + 1, TRIE_BITS),
        **{
            f"unit:{arm}": (UX + x - 1, UY + R_ARG, LEAF_PITCH, R_COLLECT - R_ARG + 1)
            for arm, x in arm_columns().items()
            if arm != "COL"
        },
        "unit:COL": (UX + arm_columns()["COL"] - 1, UY + R_ARG, 12, R_COLLECT - R_ARG + 1),
        "relay": (rx, ry, RELAY_IW + 2, RELAY_IH + 2),
        "relay2": (rx2, ry2, RELAY_IW + 2, RELAY_IH + 2),
        "panel": (px, py, PANEL_W + 2, PANEL_H + 2),
    }
    blk = DoomBlock(
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
        raise DoomUnitError(f"the builder drew {len(blk.lengths)} pipes but reports {blk.pipes}")
    return blk


# ── the probe: the block plus the smallest possible driver ───────────────────
def _feeder_cells(commands: list[int]) -> tuple[dict[tuple[int, int], str], int, int]:
    """A driver room that sends ``commands`` and halts (path_unit's ladder,
    plus an ``N`` after the literal when the word is negative — a COL word for
    a top-of-screen wall is, and the ladder has no sign glyph)."""
    cells: dict[tuple[int, int], str] = {(1, 1): "@", (2, 1): "v"}
    y = 2
    for w in commands:
        for ch in f"`{abs(w)}`" + ("N" if w < 0 else "") + "s":
            cells[(2, y)] = ch
            y += 1
    cells[(2, y)] = "H"
    return cells, 2, y


def build_probe(commands: list[int]) -> tuple[list[str], DebugMap, DoomBlock]:
    """The block plus the smallest possible CPU: a room that recites ``commands``.

    With no response pipe the whole protocol is write-only, so the grid is
    standalone: run it with no input at all and it plays the commands.
    """
    from .machine import _Grid

    blk = build_doom()
    ox, oy = BLOCK_AT
    g = _Grid()
    for (x, y), ch in blk.cells.items():
        g.put(ox + x, oy + y, ch)

    fx, fy = FEED_AT
    cells, fw, fh = _feeder_cells(commands)
    g.room(fx, fy, fx + fw + 1, fy + fh + 1)
    g.blit(fx, fy, cells)

    cx, cy = ox + blk.cmd_cell[0], oy + blk.cmd_cell[1]
    lane = fy + 1
    g.draw_pipe([(fx + fw + 2, lane), (cx, lane), (cx, cy)])

    rows = g.rows()
    dbg = DebugMap("doom unit probe")
    for name, (x, y, w, h) in blk.regions.items():
        dbg.region(name, ox + x, oy + y, w, h, note=name)
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
        help="command words for the probe, e.g. '2 3' (see word()/arm_codes())",
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
