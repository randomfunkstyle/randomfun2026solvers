#!/usr/bin/env python3
"""deadman-3d: the golden model for the first-person raycaster demo on the LM-1 CPU.

This module is the **single source of truth** for the deadman-3d demo: an
integer transliteration of lodev.org's ``raycaster_flat.cpp``
(https://lodev.org/cgtutor/raycasting.html), rendered 64x48 on the LM-75 —
3D viewport rows 0..39, HUD strip rows 40..47.  The hand-lowered LM-1 assembly
(milestone M1+) must match this model **pixel for pixel**; every constant,
table, and expression here is written the way the asm will compute it.

Numeric contract
----------------
All arithmetic is Q10 fixed point (``UNITS = 1024``).  Every division and
modulo goes through the LM-1 emulator's own semantics, imported from
``randomfun2026solvers.lm1.emulator``:

* ``floor_div(a, b) -> (q, r)`` — floored quotient (``DIV``); **b == 0 gives
  q == 0**, so every ray divisor is guarded (``BIG = 2**30`` substitutes for a
  zero ray component).
* ``sign_mod(a, b)`` — remainder takes the divisor's sign (``MODI``); all
  operands here are kept nonnegative (plan risk R9).

Expressions keep the exact operation order of the plan's asm lowerings, e.g.
``rayDirX = floor_div(planeX * cameraX, 1024) + dirX`` (MUL; DIVI 1024; ADD),
and ``deltaDist`` is ``q = floor_div(1048576, rayDir)`` then ``-q if rayDir <
0 else q`` (DIV; BRN -> NEG), so a floored quotient of a negative ray gives
exactly the asm's value.

Map and orientation (plan risk R10 — the mirror decision)
---------------------------------------------------------
``MAP_STR`` is printed like a conventional map: **north at the top**.  The
printed row ``p`` holds the cells with ``y = 15 - p``, i.e. ``map_cell(x, y)``
uses x east, y north (y grows *up* the printed page).  Headings are
``h * 22.5``° counterclockwise from east: ``dirX = round(1024*cos)``,
``dirY = round(1024*sin)`` — heading 0 = east, 4 = north, so ``+1`` heading is
a **left** turn, exactly as command 2 promises.

The camera plane must point to the *player's right* (cameraX = +1 is the right
screen edge, and rayDir = dir + plane*cameraX/1024), which in this y-up frame
is dir rotated -90°: ``(dy, -dx)``.  Hence

    planeX = round(675.84 * sin(h * 22.5°))     # 675.84 = 0.66 * 1024
    planeY = round(-675.84 * cos(h * 22.5°))

Evidence this is the non-mirrored sign: at spawn (cell (1,3), facing east) the
cyan pillar at (2,4) is *north* of the player — the player's left — and it
renders in the **left** half of the frame; the brown zigzag on the corridor's
north side stays on the screen's left the whole walk.  Flipping the plane sign
mirrors both to the right (that is R10's failure mode).

Wall types and the display palette
----------------------------------
Map nibbles are the wall type t in 1..7 (0 = empty; <= 7 keeps every packed
row word under 2**63).  Shading: a side==0 (x-side, sunlit) hit paints
``t + 8`` (the ANSI bright variant), a side==1 hit paints ``t``.  Floor is 8,
ceiling 0 (black).

The LM-75 palette in this repo is the **ANSI** 16-colour set (see
``lambda_deadman.py`` and ``PALETTE`` below), *not* CGA.  The plan named the
map colours by CGA indices ("cyan-3, brown-6, blue-1, red-4"), which under the
ANSI palette would render as brown pillars and a blue door; the visual intent
wins, so the types are the ANSI indices of the intended colours:

    7 outer walls  (gray / bright white)      2 green accents (room walls)
    6 cyan pillars (SW start room)            4 blue accents  (door-frame trim)
    3 brown zigzag (corridor's north side)    1 RED exit door (east wall, inset
                                                between two gray 7 posts)

The HUD block colours got the same ANSI correction: ammo red = 9, face
yellow = 11, armor blue = 12 (the plan's 12/14/9 were CGA again).

Tape slot map (the asm's .equ table; slot 0 is scratch)
-------------------------------------------------------
``preamble_words()`` yields the boot data in exact tape order, slots 1..71:

    MAPB    1..16   packed map rows: nibble y of word x = map_cell(x, y)
    POWB   17..32   16**y for y = 0..15
    DIRB   33..48   packed dir vectors,   (dirX+1024)*4096 + (dirY+1024)
    PLNB   49..64   packed plane vectors, (planeX+1024)*4096 + (planeY+1024)
    POSX   65       spawn posX = 1536   (cell 1, Q10 centre)
    POSY   66       spawn posY = 3584   (cell 3)
    HDG    67       spawn heading = 0   (east)
    DIRX   68       spawn dirX  = 1024      DIRY   69   spawn dirY   = 0
    PLANEX 70       spawn planeX = 0        PLANEY 71   spawn planeY = -676

Input protocol: round 0 = preamble + first command; every later round is one
command word: 0 forward, 1 backward, 2 turn left (+1 heading), 3 turn right
(+15 heading), >= 4 no-op — each command renders exactly one frame.

The demo walk (``WALK``)
------------------------
Spawn view (no-op), three steps forward between the pillars, a look to the
left (turn left, hold, turn right) that sweeps the cyan pillar and green north
wall, then straight east: out the blue-trimmed doorway, down the corridor with
the brown zigzag strobing past on the left, ending two cells short of the red
exit door filling the middle of the screen.  Half-cell steps; 28 commands
(the plan guessed ~17, but the SW room to the east wall is 12 cells, i.e. 24
half-cell steps — the route is as short as the geometry allows).

``deadman3d_source()`` (milestones M1+M2) emits the LM-1 assembly lowered from
this model; ``tape_slots()`` is its ``.equ`` table (the docstring's slot map
plus the scalars, numbered consecutively from 72).
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

from randomfun2026solvers.lm1.emulator import floor_div, sign_mod

__all__ = [
    "WIDTH", "HEIGHT", "H3D", "MID", "UNITS", "HEADINGS",
    "MOVE_NUM", "MOVE_DEN", "BIG", "MAP_SIZE", "MAP_STR", "PALETTE",
    "SPAWN", "State", "WALK",
    "div", "map_cell", "map_row_words", "dir_table", "plane_table",
    "unpack_vec", "step", "render", "hud_rows", "hud_runs",
    "preamble_words", "input_words", "frames_for_commands", "cases_json",
    "tape_slots", "deadman3d_source", "main",
]

# ── the fixed geometry ────────────────────────────────────────────────────────
WIDTH = 64          # LM-75 panel columns
HEIGHT = 48         # LM-75 panel rows (DOOM's 4:3)
H3D = 40            # 3D viewport rows 0..39
MID = 20            # horizon row of the 3D viewport
UNITS = 1024        # Q10 fixed point: 1024 units = one map cell
HEADINGS = 16       # baked headings, 22.5° apart
#: A move is half a cell along dir: dir * 512 / 1024, which the asm computes as
#: ``DIVI 2`` — written here as ``floor_div(dir * s * MOVE_NUM, MOVE_DEN)``.
MOVE_NUM = 1
MOVE_DEN = 2
BIG = 2 ** 30       # stands in for an infinite deltaDist when rayDir == 0


def div(a: int, b: int) -> int:
    """The LM-1 ``DIV`` quotient: floored, and **0 when b == 0** (SPEC.md)."""
    return floor_div(a, b)[0]


# ── the map (16x16, north at the top; see the module docstring) ──────────────
MAP_SIZE = 16
#: ``.`` = empty, hex nibble = wall type 1..7.  Printed row p is y = 15 - p.
MAP_STR = """\
7777777777777777
7..............7
7..............7
7..............7
7..............7
7..............7
7........33....7
7......33......7
7....33........7
7222222........7
7.....2..33..337
7.6...433..33.77
7..............1
7...6.4777777777
7.....2777777777
7777777777777777
"""

_PRINTED_ROWS = MAP_STR.splitlines()
assert len(_PRINTED_ROWS) == MAP_SIZE and all(len(r) == MAP_SIZE for r in _PRINTED_ROWS)


def _grid_cell(x: int, y: int) -> int:
    """Wall type straight from ``MAP_STR`` (x east, y north; row 0 is y=15)."""
    ch = _PRINTED_ROWS[MAP_SIZE - 1 - y][x]
    return 0 if ch == "." else int(ch, 16)


def map_row_words() -> list[int]:
    """The 16 packed map words the tape holds: nibble y of word x = cell(x,y)."""
    words = []
    for x in range(MAP_SIZE):
        word = 0
        for y in range(MAP_SIZE):
            t = _grid_cell(x, y)
            assert t == 0 or 1 <= t <= 7, f"wall type {t} at {(x, y)} not in 1..7"
            word += t * 16 ** y
        assert 0 < word < 2 ** 63, f"packed row {x} overflows a signed word"
        words.append(word)
    return words


_ROW_WORDS = map_row_words()
#: POW16[y] = 16**y — the tape's divisor table for nibble extraction.
POW16 = [16 ** y for y in range(MAP_SIZE)]


def map_cell(x: int, y: int) -> int:
    """Cell lookup exactly as the asm does it: ``LDA MAPB+x; DIV POWB+y; MODI 16``."""
    return sign_mod(div(_ROW_WORDS[x], POW16[y]), 16)


# ── heading tables ───────────────────────────────────────────────────────────
def _pack_vec(v: int, w: int) -> int:
    """``(v+1024)*4096 + (w+1024)`` — both components in one positive word."""
    word = (v + UNITS) * 4096 + (w + UNITS)
    assert word > 0, f"packed vector ({v},{w}) is not positive"
    return word


def unpack_vec(word: int) -> tuple[int, int]:
    """The asm's unpack: ``DIVI 4096`` (quotient *and* remainder), minus 1024."""
    q, r = floor_div(word, 4096)
    return q - UNITS, r - UNITS


def _dir_vec(h: int) -> tuple[int, int]:
    th = math.radians(h * 22.5)
    return round(UNITS * math.cos(th)), round(UNITS * math.sin(th))


def _plane_vec(h: int) -> tuple[int, int]:
    # dir rotated -90° (to the player's right), scaled 0.66: see the docstring.
    th = math.radians(h * 22.5)
    return round(675.84 * math.sin(th)), round(-675.84 * math.cos(th))


def dir_table() -> list[int]:
    """16 packed direction vectors, heading h at index h."""
    return [_pack_vec(*_dir_vec(h)) for h in range(HEADINGS)]


def plane_table() -> list[int]:
    """16 packed camera-plane vectors, heading h at index h."""
    return [_pack_vec(*_plane_vec(h)) for h in range(HEADINGS)]


_DIR_WORDS = dir_table()
_PLANE_WORDS = plane_table()


# ── state and the command step ───────────────────────────────────────────────
@dataclass(frozen=True)
class State:
    """The whole mutable game state: Q10 position and the baked heading."""

    posX: int
    posY: int
    heading: int


SPAWN = State(posX=1536, posY=3584, heading=0)  # centre of cell (1,3), facing east


def step(state: State, cmd: int) -> State:
    """Apply one command: 0 fwd, 1 back, 2 left, 3 right, >=4 no-op."""
    posX, posY, heading = state.posX, state.posY, state.heading
    if cmd == 0 or cmd == 1:
        s = 1 if cmd == 0 else -1
        dirX, dirY = unpack_vec(_DIR_WORDS[heading])
        # lodev's per-axis collision: X first, then Y against the updated posX.
        newX = posX + div(dirX * s * MOVE_NUM, MOVE_DEN)
        if map_cell(div(newX, UNITS), div(posY, UNITS)) == 0:
            posX = newX
        newY = posY + div(dirY * s * MOVE_NUM, MOVE_DEN)
        if map_cell(div(posX, UNITS), div(newY, UNITS)) == 0:
            posY = newY
    elif cmd == 2:
        heading = sign_mod(heading + 1, HEADINGS)   # turn left (CCW)
    elif cmd == 3:
        heading = sign_mod(heading + 15, HEADINGS)  # turn right (-1 ≡ +15 mod 16)
    return State(posX, posY, heading)


# ── the renderer (lodev raycaster_flat.cpp, in Q10) ──────────────────────────
def render(state: State) -> list[str]:
    """One frame: 48 rows of 64 hex chars (rows 0..39 the 3D view, 40..47 HUD)."""
    posX, posY = state.posX, state.posY
    dirX, dirY = unpack_vec(_DIR_WORDS[state.heading])
    planeX, planeY = unpack_vec(_PLANE_WORDS[state.heading])
    cols: list[list[int]] = []
    for x in range(WIDTH):
        # lodev: cameraX = 2*x/w - 1; exact in Q10 at w=64: 32*x - 1024.
        cameraX = 32 * x - 1024
        # lodev: rayDirX = dirX + planeX*cameraX (MUL; DIVI 1024; ADD DIRX).
        rayDirX = div(planeX * cameraX, UNITS) + dirX
        rayDirY = div(planeY * cameraX, UNITS) + dirY
        # lodev: mapX = int(posX)
        mapX = div(posX, UNITS)
        mapY = div(posY, UNITS)
        # lodev: deltaDistX = abs(1/rayDirX) -> Q10: |1024*1024 / rayDirX|,
        # with DIV-by-0 == 0 guarded by substituting BIG (plan risk R2).
        if rayDirX == 0:
            deltaDistX = BIG
        else:
            q = div(1048576, rayDirX)
            deltaDistX = -q if rayDirX < 0 else q
        if rayDirY == 0:
            deltaDistY = BIG
        else:
            q = div(1048576, rayDirY)
            deltaDistY = -q if rayDirY < 0 else q
        # lodev: step and initial sideDist from the fractional position (MODI 1024).
        fracX = sign_mod(posX, UNITS)
        if rayDirX < 0:
            stepX = -1
            sideDistX = div(fracX * deltaDistX, UNITS)
        else:
            stepX = 1
            sideDistX = div((UNITS - fracX) * deltaDistX, UNITS)
        fracY = sign_mod(posY, UNITS)
        if rayDirY < 0:
            stepY = -1
            sideDistY = div(fracY * deltaDistY, UNITS)
        else:
            stepY = 1
            sideDistY = div((UNITS - fracY) * deltaDistY, UNITS)
        # lodev's DDA; a sideDist tie goes to the Y arm (the else — risk R5).
        while True:
            if sideDistX < sideDistY:
                sideDistX += deltaDistX
                mapX += stepX
                side = 0
            else:
                sideDistY += deltaDistY
                mapY += stepY
                side = 1
            t = map_cell(mapX, mapY)
            if t > 0:
                break
        # lodev: perpWallDist = sideDist - deltaDist of the hit side, kept Q10.
        perpWallDist = (sideDistX - deltaDistX) if side == 0 else (sideDistY - deltaDistY)
        if perpWallDist < 1:
            perpWallDist = 1
        # lodev: lineHeight = h / perpWallDist -> Q10: (40*1024) / perp.
        lineHeight = div(H3D * UNITS, perpWallDist)
        halfh = div(lineHeight, 2)
        drawStart = MID - halfh
        if drawStart < 0:
            drawStart = 0
        drawEnd = MID + halfh
        if drawEnd > H3D - 1:
            drawEnd = H3D - 1
        # lodev halves the colour on y-sides; here: x-side (sunlit) is t+8.
        color = t + 8 if side == 0 else t
        cols.append(
            [0] * drawStart
            + [color] * (drawEnd - drawStart + 1)
            + [8] * (H3D - 1 - drawEnd)
        )
    rows = ["".join("%x" % cols[x][y] for x in range(WIDTH)) for y in range(H3D)]
    return rows + hud_rows()


# ── the HUD strip (rows 40..47) ──────────────────────────────────────────────
AMMO_COLOR = 9    # bright red   (ANSI; the plan's "12" was the CGA index)
FACE_COLOR = 11   # bright yellow (the plan's "14" was CGA yellow)
ARMOR_COLOR = 12  # bright blue  (the plan's "9" was CGA light blue)


def hud_rows() -> list[str]:
    """Rows 40..47: bezel 7, six field rows of 8 with three blocks, base 8."""
    field = [8] * WIDTH
    for c in range(4, 13):     # "ammo", cols 4..12
        field[c] = AMMO_COLOR
    for c in range(28, 36):    # "face", cols 28..35
        field[c] = FACE_COLOR
    for c in range(50, 59):    # "armor", cols 50..58
        field[c] = ARMOR_COLOR
    mid = "".join("%x" % c for c in field)
    return ["7" * WIDTH] + [mid] * 6 + ["8" * WIDTH]


def hud_runs() -> list[tuple[int, int]]:
    """RLE (color, count) of the 8 HUD rows concatenated — the asm's paint list."""
    runs: list[tuple[int, int]] = []
    for ch in "".join(hud_rows()):
        c = int(ch, 16)
        if runs and runs[-1][0] == c:
            runs[-1] = (c, runs[-1][1] + 1)
        else:
            runs.append((c, 1))
    return runs


# ── the demo walk ────────────────────────────────────────────────────────────
#: See the docstring: spawn view, forward between the pillars, look left and
#: back, then straight east down the corridor to the red door.
WALK: list[int] = [
    4,                      # hold: the spawn view
    0, 0, 0,                # forward to cell 3 — pillars slide past both sides
    2, 4, 3,                # look left (pillar + green wall + blue trim), hold, back
    0, 0, 0, 0, 0, 0,       # through the blue-trimmed doorway (cell 6)
    0, 0, 0, 0, 0, 0,       # down the corridor, zigzag strobing on the left
    0, 0, 0, 0, 0, 0, 0, 0, # up to cell 13.5 — the red door fills the view
    4,                      # hold: the final view of the door
]


# ── boot data and the cases file ─────────────────────────────────────────────
def preamble_words() -> list[int]:
    """Round 0's data burst, in exact tape order (slots 1..71; see docstring)."""
    dirX, dirY = unpack_vec(_DIR_WORDS[SPAWN.heading])
    planeX, planeY = unpack_vec(_PLANE_WORDS[SPAWN.heading])
    return (
        _ROW_WORDS
        + POW16
        + _DIR_WORDS
        + _PLANE_WORDS
        + [SPAWN.posX, SPAWN.posY, SPAWN.heading, dirX, dirY, planeX, planeY]
    )


def input_words(cmds: list[int]) -> list[int]:
    """Everything the program ever reads: the preamble, then one word per command."""
    return preamble_words() + list(cmds)


def frames_for_commands(cmds: list[int]) -> list[list[str]]:
    """Apply each command in turn and render after it — one frame per command."""
    state = SPAWN
    frames = []
    for cmd in cmds:
        state = step(state, cmd)
        frames.append(render(state))
    return frames


def cases_json(cmds: list[int]) -> dict:
    """The demo's cases file: ONE case, one round per command, round-gated frames.

    Shape matches ``littleman/examples/lambda-deadman-cpu.cases.json`` /
    ``littleman/tools/display-frames.mjs``: round 0 carries the preamble plus
    the first command, each later round exactly one command; every round
    expects exactly one committed frame and no program output.
    """
    frames = frames_for_commands(cmds)
    preamble = [str(w) for w in preamble_words()]
    rounds = []
    for k, cmd in enumerate(cmds):
        rounds.append({
            "in": (preamble if k == 0 else []) + [str(cmd)],
            "out": [],
            "frames": [frames[k]],
        })
    return {"publicTestData": [{"name": "deadman-3d", "rounds": rounds}]}


# ── the asm generator (milestones M1+M2) ─────────────────────────────────────
#: The scalar tape slots after the boot data, numbered consecutively from 72.
_SCALARS = (
    "CMD", "XCOL", "CAMX", "RDX", "RDY", "MAPX", "MAPY", "SDX", "SDY",
    "DDX", "DDY", "STPX", "STPY", "SIDE", "PERP", "HALFH", "DSTART", "DEND",
    "COLOR", "ADDRV", "AEND", "PW", "TMP", "NEWX", "NEWY", "PTR",
)


def tape_slots() -> dict[str, int]:
    """The asm's whole ``.equ`` table, name -> tape address (slot 0 is scratch).

    Slots 1..71 are the boot data in ``preamble_words()`` order (see the module
    docstring); the scalars follow consecutively, so the machine's
    ``TAPE_SIZE`` is ``max(tape_slots().values()) + 1`` — an exactly-sized tape
    stalls silently (plan risk R6), which is why tests pin this.
    """
    slots = {
        "MAPB": 1, "POWB": 17, "DIRB": 33, "PLNB": 49,
        "POSX": 65, "POSY": 66, "HDG": 67, "DIRX": 68, "DIRY": 69,
        "PLANEX": 70, "PLANEY": 71,
    }
    for i, name in enumerate(_SCALARS):
        slots[name] = len(preamble_words()) + 1 + i
    return slots


def deadman3d_source() -> str:
    """The LM-1 assembly of the demo, lowered line for line from this model.

    Structure: boot loop (round 0's data preamble -> tape slots 1..71) ->
    ``round:`` command dispatch -> move arms (per-axis collision, the map-cell
    lookup inlined) -> turn arms (heading tables re-unpacked) -> ``render:``
    (lodev's raycaster_flat.cpp per column: setup, DDA, projection, paint) ->
    generated HUD RLE -> one ``DSPS`` -> back to ``round:``.  The lodev
    variable each block computes is named in its comments; every expression
    keeps :func:`render`'s exact operation order, which is the pixel contract.

    Regenerate with::

        from randomfun2026solvers.deadman3d import deadman3d_source
        from randomfun2026solvers.lm1.programs import PROGRAM_DIR
        (PROGRAM_DIR / "deadman-3d.asm").write_text(deadman3d_source())
    """
    slots = tape_slots()
    first_free = len(preamble_words()) + 1  # 72: the boot loop's stop address
    assert first_free == slots["CMD"], "the boot stop address is the first scalar"
    inv = UNITS * UNITS          # 1048576  — deltaDist numerator (1/rayDir, Q10*Q10)
    lh_num = H3D * UNITS         # 40960    — lineHeight numerator (h / perpWallDist)
    hud_addr = H3D * WIDTH       # 2560     — the HUD strip's first pixel
    floor_end = (H3D - 1) * WIDTH  # 2496   — row 39, the floor's last row

    equ_notes = {
        "MAPB": f"..{slots['MAPB'] + 15:<3} packed map rows: nibble y of word x = map_cell(x, y)",
        "POWB": f"..{slots['POWB'] + 15:<3} 16**y — the nibble-extraction divisors",
        "DIRB": f"..{slots['DIRB'] + 15:<3} packed dir vectors, (dirX+1024)*4096 + (dirY+1024)",
        "PLNB": f"..{slots['PLNB'] + 15:<3} packed plane vectors, same packing",
        "POSX": "player x, Q10 (lodev posX)", "POSY": "player y, Q10 (lodev posY)",
        "HDG": "heading 0..15 (22.5 deg steps, CCW from east)",
        "DIRX": "lodev dirX", "DIRY": "lodev dirY",
        "PLANEX": "lodev planeX", "PLANEY": "lodev planeY",
        "CMD": "this round's command word",
        "XCOL": "the column being rendered (lodev x)",
        "CAMX": "lodev cameraX, Q10", "RDX": "lodev rayDirX", "RDY": "lodev rayDirY",
        "MAPX": "lodev mapX", "MAPY": "lodev mapY",
        "SDX": "lodev sideDistX", "SDY": "lodev sideDistY",
        "DDX": "lodev deltaDistX", "DDY": "lodev deltaDistY",
        "STPX": "lodev stepX", "STPY": "lodev stepY",
        "SIDE": "lodev side (0 = x-side hit)", "PERP": "lodev perpWallDist",
        "HALFH": "lodev lineHeight / 2",
        "DSTART": "lodev drawStart", "DEND": "lodev drawEnd",
        "COLOR": "the wall type t, then the shaded colour",
        "ADDRV": "the paint cursor, row*64 + XCOL", "AEND": "the paint loop's last address",
        "PW": "16**mapY during a cell lookup", "TMP": "scratch (s, frac, packed word)",
        "NEWX": "the candidate posX", "NEWY": "the candidate posY",
        "PTR": "the boot loop's tape cursor",
    }
    lines = [
        "; deadman-3d — GENERATED from randomfun2026solvers/deadman3d.py, do not hand-edit.",
        "; Regenerate with:",
        ";   from randomfun2026solvers.deadman3d import deadman3d_source",
        ";   from randomfun2026solvers.lm1.programs import PROGRAM_DIR",
        ';   (PROGRAM_DIR / "deadman-3d.asm").write_text(deadman3d_source())',
        ";",
        "; lodev.org's raycaster_flat.cpp on the LM-1: 64x48 first person on the LM-75,",
        "; one frame per input command (0 fwd, 1 back, 2 left, 3 right, >= 4 no-op).",
        "; An ungraded demo — the slug borrows plotter's problem JSON for nothing but",
        "; registration; its 64x48 panel is DISPLAY_OVERRIDE's, its input is its own.",
        ";",
        "; Round 0's input carries the whole data preamble (map rows, POW16, heading",
        "; tables, spawn state — deadman3d.preamble_words()) followed by the first",
        "; command: tables ride on INPUT because every ROM word taxes every backward",
        "; jump by 8 ticks forever, and because the ROM cannot hold the negative",
        "; components (planeY = -676 at spawn). The pixel contract is deadman3d.render():",
        "; every expression below is that model's, in its exact operation order.",
        ";",
        "; The map-cell lookup floor(MAPROW[x] / 16**y) mod 16 is inlined at its three",
        "; sites (no stack, no calls): the two move-collision tests and the DDA hit test.",
        "",
        "; ── tape slots (deadman3d.tape_slots(); slots 1..71 are the boot data) ───────",
    ]
    for name, addr in slots.items():
        lines.append(f".equ {name:<6} {addr:<4}         ; {equ_notes[name]}")
    lines += f"""
; ── boot: round 0's data preamble -> tape slots 1..{first_free - 1} ────────────────────────
        LDI 1
        ST  PTR
boot:   IN                  ; the next preamble word (negatives arrive here)
        ST  TMP
        LD  PTR
        MOVA TMP            ; store[PTR] = the word
        INCM PTR
        LD  PTR
        SUBI {first_free}
        BRN boot            ; keep loading while PTR < {first_free}

; ── round: one command word in, exactly one committed frame out ──────────────
round:  IN                  ; blocks here when the walk is over (the legal end)
        ST  CMD
        BRZ fwd             ; 0 = forward
        SUBI 1
        BRZ back            ; 1 = backward
        SUBI 1
        BRZ left            ; 2 = turn left  (CCW, +1 heading)
        SUBI 1
        BRZ right           ; 3 = turn right (-1 = +15 mod 16)
        JMP render          ; >= 4 = no-op: just render

; ── move arms (lodev: pos += dir * moveSpeed, collision per axis) ────────────
fwd:    LDI 1
        ST  TMP             ; s = +1
        JMP move
back:   LDI 0
        SUBI 1
        ST  TMP             ; s = -1 (no negative ROM literals)
move:   LD  DIRX
        MUL TMP
        DIVI 2              ; floor(dirX * s / 2) — the half-cell step
        ADD POSX
        ST  NEWX            ; newX
        ; collision X: map_cell(newX / 1024, posY / 1024), inlined
        LD  POSY
        DIVI {UNITS}
        ADDI POWB
        LDA
        ST  PW              ; 16**mapY
        LD  NEWX
        DIVI {UNITS}
        ADDI MAPB
        LDA                 ; the packed map row of newX's cell
        DIV PW
        MODI 16
        BRZ comx            ; empty -> commit posX
        JMP movey           ; wall -> posX unchanged
comx:   LD  NEWX
        ST  POSX
movey:  LD  DIRY
        MUL TMP
        DIVI 2
        ADD POSY
        ST  NEWY            ; newY
        ; collision Y: map_cell(posX / 1024, newY / 1024) — the UPDATED posX
        LD  NEWY
        DIVI {UNITS}
        ADDI POWB
        LDA
        ST  PW
        LD  POSX
        DIVI {UNITS}
        ADDI MAPB
        LDA
        DIV PW
        MODI 16
        BRZ comy
        JMP render
comy:   LD  NEWY
        ST  POSY
        JMP render

; ── turn arms: heading +-1 mod 16, dir/plane re-unpacked from the tables ─────
left:   LD  HDG
        ADDI 1
        MODI {HEADINGS}
        ST  HDG
        JMP unpk
right:  LD  HDG
        ADDI {HEADINGS - 1}
        MODI {HEADINGS}
        ST  HDG
unpk:   LD  HDG
        ADDI DIRB
        LDA                 ; (dirX+1024)*4096 + (dirY+1024)
        ST  TMP
        MODI 4096
        SUBI {UNITS}
        ST  DIRY
        LD  TMP
        DIVI 4096
        SUBI {UNITS}
        ST  DIRX
        LD  HDG
        ADDI PLNB
        LDA
        ST  TMP
        MODI 4096
        SUBI {UNITS}
        ST  PLANEY
        LD  TMP
        DIVI 4096
        SUBI {UNITS}
        ST  PLANEX
        ; falls through to render

; ── render: lodev's per-column raycast, columns 0..{WIDTH - 1} ──────────────────────
render: LDI 0
        ST  XCOL
colset: LD  XCOL
        MULI 32
        SUBI {UNITS}
        ST  CAMX            ; cameraX = 2*x/w - 1 -> 32*x - 1024, exact at w = 64
        LD  PLANEX
        MUL CAMX
        DIVI {UNITS}
        ADD DIRX
        ST  RDX             ; rayDirX = dirX + planeX*cameraX
        LD  PLANEY
        MUL CAMX
        DIVI {UNITS}
        ADD DIRY
        ST  RDY             ; rayDirY = dirY + planeY*cameraX
        LD  POSX
        DIVI {UNITS}
        ST  MAPX            ; mapX = int(posX)
        LD  POSY
        DIVI {UNITS}
        ST  MAPY            ; mapY = int(posY)
        ; deltaDistX = abs(1/rayDirX) -> |{inv} / rayDirX|; DIV by 0 is 0 on
        ; this CPU, so a zero ray substitutes BIG = 2**30 (plan risk R2)
        LD  RDX
        BRZ ddxinf
        LDI {inv}
        DIV RDX
        BRN ddxneg          ; the quotient's sign is rayDirX's
        ST  DDX
        JMP ddy
ddxneg: NEG
        ST  DDX
        JMP ddy
ddxinf: LDI {BIG}
        ST  DDX
ddy:    LD  RDY             ; deltaDistY, the same three arms
        BRZ ddyinf
        LDI {inv}
        DIV RDY
        BRN ddyneg
        ST  DDY
        JMP sidex
ddyneg: NEG
        ST  DDY
        JMP sidex
ddyinf: LDI {BIG}
        ST  DDY
        ; stepX / sideDistX from the fractional position (lodev's two arms)
sidex:  LD  POSX
        MODI {UNITS}
        ST  TMP             ; fracX = posX - mapX*1024
        LD  RDX
        BRN sxneg
        LDI 1
        ST  STPX            ; stepX = 1
        LDI {UNITS}
        SUB TMP
        MUL DDX
        DIVI {UNITS}
        ST  SDX             ; sideDistX = (1024 - fracX) * deltaDistX / 1024
        JMP sidey
sxneg:  LDI 0
        SUBI 1
        ST  STPX            ; stepX = -1
        LD  TMP
        MUL DDX
        DIVI {UNITS}
        ST  SDX             ; sideDistX = fracX * deltaDistX / 1024
sidey:  LD  POSY            ; stepY / sideDistY, the same two arms
        MODI {UNITS}
        ST  TMP
        LD  RDY
        BRN syneg
        LDI 1
        ST  STPY
        LDI {UNITS}
        SUB TMP
        MUL DDY
        DIVI {UNITS}
        ST  SDY
        JMP dda
syneg:  LDI 0
        SUBI 1
        ST  STPY
        LD  TMP
        MUL DDY
        DIVI {UNITS}
        ST  SDY
        ; the DDA; a sideDist tie goes to the Y arm (lodev's else — risk R5)
dda:    LD  SDX
        SUB SDY
        BRN xarm            ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  MAPY
        ADD STPY
        ST  MAPY
        LDI 1
        ST  SIDE            ; side = 1 (y-side)
        JMP hit
xarm:   LD  SDX
        ADD DDX
        ST  SDX
        LD  MAPX
        ADD STPX
        ST  MAPX
        LDI 0
        ST  SIDE            ; side = 0 (x-side)
hit:    LD  MAPY            ; the inlined cell lookup at (mapX, mapY)
        ADDI POWB
        LDA
        ST  PW
        LD  MAPX
        ADDI MAPB
        LDA
        DIV PW
        MODI 16
        BRZ dda             ; empty -> keep stepping (the backward lap)
        ST  COLOR           ; hit: the wall type t in 1..7
        ; perpWallDist = sideDist - deltaDist of the hit side, clamped to >= 1
        LD  SIDE
        BRZ perpx
        LD  SDY
        SUB DDY
        ST  PERP
        JMP pclip
perpx:  LD  SDX
        SUB DDX
        ST  PERP
pclip:  SUBI 1              ; ST preserved ACC = perpWallDist
        BRN pone
        JMP lineh
pone:   LDI 1
        ST  PERP
lineh:  LDI {lh_num}
        DIV PERP            ; lineHeight = h / perpWallDist -> ({H3D}*1024) / perp
        DIVI 2
        ST  HALFH
        LDI {MID}
        SUB HALFH
        ST  DSTART          ; drawStart = {MID} - halfh
        BRN dslo
        JMP dehi
dslo:   LDI 0
        ST  DSTART          ; clamped at the top of the viewport
dehi:   LD  HALFH
        ADDI {MID}
        ST  DEND            ; drawEnd = {MID} + halfh
        SUBI {H3D}
        BRN shade           ; drawEnd <= {H3D - 1}: no clamp
        LDI {H3D - 1}
        ST  DEND
shade:  LD  SIDE            ; lodev halves y-side colours; here x-side is t + 8
        BRZ sunlit
        JMP paint
sunlit: LD  COLOR
        ADDI 8
        ST  COLOR           ; the sunlit (bright) variant
paint:  LD  DSTART          ; the wall run: rows drawStart..drawEnd, stride {WIDTH}
        MULI {WIDTH}
        ADD XCOL
        ST  ADDRV
        LD  DEND
        MULI {WIDTH}
        ADD XCOL
        ST  AEND
wallp:  LD  ADDRV
        DSPA
        LD  COLOR
        DSPD
        LD  ADDRV
        ADDI {WIDTH}
        ST  ADDRV
        SUB AEND
        BRN wallp           ; next row while ADDRV <= AEND
        BRZ wallp
        ; the floor run: rows drawEnd+1..{H3D - 1} paint colour 8 (ceiling stays
        ; black — SWAP 0 cleared the next buffer)
        LDI {floor_end}
        ADD XCOL
        ST  AEND            ; row {H3D - 1}, this column
floorp: LD  AEND
        SUB ADDRV
        BRN colnxt          ; ADDRV past row {H3D - 1}: the column is done
        LD  ADDRV
        DSPA
        LDI 8
        DSPD
        LD  ADDRV
        ADDI {WIDTH}
        ST  ADDRV
        JMP floorp
colnxt: INCM XCOL           ; ACC = the old column number
        SUBI {WIDTH - 1}
        BRZ hud             ; that was column {WIDTH - 1}: the viewport is painted
        JMP colset

; ── HUD strip (rows {H3D}..{HEIGHT - 1}): RLE runs generated from hud_runs() ──────────
hud:    LDI {hud_addr}
        DSPA                ; park the cursor at row {H3D}, column 0
""".splitlines()
    for color, count in hud_runs():
        lines.append(f"        LDI {color}               ; a run of {count}")
        lines.extend(["        DSPD"] * count)
    lines += [
        "",
        "        LDI 0",
        "        DSPS                ; commit THE one frame of this round",
        "        JMP round",
        "",
    ]
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────
#: The ANSI 16-colour palette the LM-75 frames are viewed with.
PALETTE = [
    (0, 0, 0), (170, 0, 0), (0, 170, 0), (170, 85, 0),
    (0, 0, 170), (170, 0, 170), (0, 170, 170), (170, 170, 170),
    (85, 85, 85), (255, 85, 85), (85, 255, 85), (255, 255, 85),
    (85, 85, 255), (255, 85, 255), (85, 255, 255), (255, 255, 255),
]


def _png_bytes(frame: list[str], scale: int) -> bytes:
    """A minimal RGB PNG (stdlib only), each pixel scaled up ``scale``x nearest."""
    import struct
    import zlib

    raw = bytearray()
    for row in frame:
        line = bytearray()
        for ch in row:
            line += bytes(PALETTE[int(ch, 16)]) * scale
        for _ in range(scale):
            raw += b"\x00" + line  # filter 0 per scanline
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data)))
    ihdr = struct.pack(">IIBBBBB", WIDTH * scale, HEIGHT * scale, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


def _write_pngs(frames: list[list[str]], out_dir: Path, scale: int = 8) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image  # optional; the zlib fallback below matches it
    except ModuleNotFoundError:
        Image = None
    for i, frame in enumerate(frames):
        path = out_dir / f"frame-{i:02d}.png"
        if Image is None:
            path.write_bytes(_png_bytes(frame, scale))
        else:
            img = Image.new("RGB", (WIDTH, HEIGHT))
            img.putdata([PALETTE[int(ch, 16)] for row in frame for ch in row])
            img.resize((WIDTH * scale, HEIGHT * scale), Image.NEAREST).save(path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--walk", help='command words, e.g. "4 0 0 2" (default: WALK)')
    parser.add_argument("--cases", type=Path, help="write cases_json(WALK) here")
    parser.add_argument("--png", type=Path, help="dump preview PNGs to this directory")
    args = parser.parse_args(argv)
    cmds = [int(w) for w in args.walk.split()] if args.walk else WALK
    if args.cases:
        args.cases.write_text(json.dumps(cases_json(cmds)) + "\n")
        print(f"wrote {args.cases}")
        return
    frames = frames_for_commands(cmds)
    if args.png:
        _write_pngs(frames, args.png)
        print(f"wrote {len(frames)} PNGs to {args.png}")
        return
    print("\n\n".join("\n".join(frame) for frame in frames))


if __name__ == "__main__":
    main()
