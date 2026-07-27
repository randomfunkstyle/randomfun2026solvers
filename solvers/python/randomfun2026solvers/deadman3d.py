#!/usr/bin/env python3
"""deadman-3d: the golden model for the first-person raycaster demo on the LM-1 CPU.

This module is the **single source of truth** for the deadman-3d demo: an
integer transliteration of lodev.org's ``raycaster_flat.cpp``
(https://lodev.org/cgtutor/raycasting.html), rendered 64x48 on the LM-75 —
3D viewport rows 0..39, HUD strip rows 40..47.  The generated LM-1 assembly
must match this model **pixel for pixel**; every constant, table, and
expression here is written the way the asm computes it.

The map is **DOOM's E1M1** (Hangar), hand-quantized to a 32x32 grid — a
recognizable homage rather than a survey: the hangar start room (the octagon,
entered from a vestibule flanked by the two entry alcoves), the courtyard with
the armor bonus behind window slits to the north-east, the computer-area
corridor west, the zigzag nukage room beyond it with its sawtooth walkway
walls, and the red exit door set in the west wall.

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

Expressions keep the exact operation order of the asm lowerings, e.g.
``rayDirX = floor_div(planeX * cameraX, 1024) + dirX`` (MUL; DIVI 1024; ADD),
and ``deltaDist`` is ``q = floor_div(1048576, rayDir)`` then ``-q if rayDir <
0 else q`` (DIV; BRN -> NEG), so a floored quotient of a negative ray gives
exactly the asm's value.

Map and orientation (plan risk R10 — the mirror decision)
---------------------------------------------------------
``MAP_STR`` is printed like a conventional map: **north at the top**.  The
printed row ``p`` holds the cells with ``y = 31 - p``, i.e. ``map_cell(x, y)``
uses x east, y north (y grows *up* the printed page).  Headings are
``h * 22.5``° counterclockwise from east: ``dirX = round(1024*cos)``,
``dirY = round(1024*sin)`` — heading 0 = east, 4 = north, so ``+1`` heading is
a **left** turn, exactly as the A key promises.

The camera plane must point to the *player's right* (cameraX = +1 is the right
screen edge, and rayDir = dir + plane*cameraX/1024), which in this y-up frame
is dir rotated -90°: ``(dy, -dx)``.  Hence

    planeX = round(675.84 * sin(h * 22.5°))     # 675.84 = 0.66 * 1024
    planeY = round(-675.84 * cos(h * 22.5°))

Evidence this is the non-mirrored sign, exactly as in E1M1: the player spawns
facing **north** (heading 4), and the courtyard window slits in the octagon's
north wall (x 22..24) sit *east* of the spawn column x=21 — the player's
right — and render in the **right** half of the frame, blue armor pedestal
behind them; the zigzag nukage room is west and sweeps in from the screen's
left during the turn toward it.  Flipping the plane sign mirrors both (that is
R10's failure mode).

Wall types and the display palette
----------------------------------
Map nibbles are the wall type t in 1..7 (0 = empty; <= 7 keeps every packed
map word under 2**63).  Shading: a side==0 (x-side, sunlit) hit paints
``t + 8`` (the ANSI bright variant), a side==1 hit paints ``t``.  Floor is 8,
ceiling 0 (black).

The LM-75 palette in this repo is the **ANSI** 16-colour set (see
``lambda_deadman.py`` and ``PALETTE`` below), *not* CGA, and the wall types
are the ANSI indices of each area's intended colour:

    7 hangar, vestibule + alcoves, courtyard (gray / bright white)
    2 computer-area corridor walls + block, green armor platform (green)
    6 / 3 zigzag nukage room: alternating sawtooth spurs (cyan / brown)
    4 blue armor-bonus pedestal in the courtyard (blue)
    1 the RED exit door, set in the west wall between gray posts

The HUD block colours are ANSI too: ammo red = 9, face yellow = 11, armor
blue = 12.

Tape slot map (the asm's .equ table; slot 0 is scratch)
-------------------------------------------------------
``preamble_words()`` yields the boot data in exact tape order, slots 1..103:

    MAPB    1..64   packed half-columns: word ``2x`` holds cells (x, 0..15),
                    word ``2x+1`` cells (x, 16..31), nibble ``y mod 16`` each
    POWB   65..80   16**y for y = 0..15
    HDGB   81..96   packed headings, one word per heading h:
                    (dirX+1024)*2^36 + (dirY+1024)*2^24
                    + (planeX+1024)*2^12 + (planeY+1024)   (48 bits, positive)
    POSX   97       spawn posX = 22016  (cell 21, Q10 centre)
    POSY   98       spawn posY = 3584   (cell 3)
    HDG    99       spawn heading = 4   (north — E1M1's real facing)
    DIRX  100       spawn dirX  = 0         DIRY  101   spawn dirY   = 1024
    PLANEX 102      spawn planeX = 676      PLANEY 103  spawn planeY = 0

The cell lookup is ``floor(MAPW[2x + y/16] / 16**(y mod 16)) mod 16`` —
``slot = MAPB + 2*mapX + (mapY / 16)``, divisor ``POWB + (mapY mod 16)`` —
inlined at both move-collision checks; the DDA instead maintains the word slot
(``WADDR``) and the nibble divisor (``PW``) *incrementally* across steps, so
its per-step lookup is three instructions (LDA; DIV PW; MODI 16).

Painting is not the CPU's job: the machine carries the **DOOM unit**
(``lm1/d3_unit.py``, ``.unit doom``), a write-only coprocessor that owns the
64x48 panel. Each viewport column, the muzzle flash, the HUD strip and the
frame commit are one command word each (``8*arg + code`` — COL 0, FLASH 1,
HUD 2, COMMIT 3, pinned to ``lm1.store.DoomUnit.CODES``), and the unit's paint
loops run concurrently with the CPU's next raycast.

Input protocol
--------------
Round 0 = the data preamble (:func:`preamble_words`, 103 words) + the first
command; every later round is exactly one command word — a **key bitmask**
(a MUX of the keys held this frame, because space can be held while moving):

    bit 0 (1)  W  forward         bit 1 (2)  S  backward
    bit 2 (4)  A  turn left       bit 3 (8)  D  turn right
    bit 4 (16) space/click FIRE   0 = idle (render only); higher bits ignored

Example: ``keys("wa ") == 21`` steps forward, turns left and fires in one
frame; the demo walk is spelled in :data:`WALK_CHORDS`. Each round commits
exactly one frame and emits no program output. This table also rides the
generated machine's debug sidecar (:data:`INPUT_PROTOCOL` — the ``io:I``
region's note in ``deadman-3d.debug.json``/``.html``).

Each word renders exactly one frame, applied in lodev's order: **turn first**
(A and D both held cancel), then **move** along the *new* heading (W and S
cancel; per-axis collision), then render — with :data:`FLASH` overlaid when
FIRE is held: an 8-pixel muzzle flash, bright yellow with a white core, at the
bottom-centre of the 3D viewport, painted *after* the wall/floor columns so it
overwrites them (M5 has no game state yet; the flash is the whole of firing).
The asm decodes the bits with a MODI 2 / DIVI 2 ladder into the ``BW BS BA BD
FIRE`` scalars and runs the turn and move arms conditionally.  :func:`keys`
encodes chords readably (``keys("wa ") == 21``); ``--play`` runs the checked-in
asm on a persistent emulator, one single-key word per keypress — the machine,
played live (chords remain reachable by script).

The demo walk (``WALK``)
------------------------
Spawn view (no-op) at E1M1's start: up the vestibule into the octagon.  Seven
steps north, the courtyard windows and the blue armor pedestal growing on the
right; a half-look right at them (turn right, hold, turn left); four left
turns to face west, sweeping the octagon's north and west walls; then straight
west — through the green computer-area corridor, the computer block sliding
past on the right, to the nukage-room doorway, where a second half-look sweeps
the cyan/brown sawtooth spurs of the zigzag walkway with the red door in
frame — ending on the walkway with the red exit door centred between sunlit
walls.  FIRE at the dramatic beats: at the armor pedestal, then *while
stepping* onto the final walkway cell (the ``"w "`` chord — the MUX at work),
and once more standing at the door.  Whole-cell steps (E1M1 distances at
half-cell steps would cost ~60 commands); 35 words, spelled ``WALK_CHORDS``.

``deadman3d_source()`` emits the LM-1 assembly lowered from this model;
``tape_slots()`` is its ``.equ`` table (the docstring's slot map plus the
scalars, numbered consecutively from 104).
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

from randomfun2026solvers.lm1.emulator import floor_div, sign_mod

__all__ = [
    "WIDTH", "HEIGHT", "H3D", "MID", "UNITS", "HEADINGS", "INPUT_PROTOCOL",
    "MOVE_NUM", "MOVE_DEN", "BIG", "MAP_SIZE", "MAP_STR", "PALETTE",
    "KEY_FWD", "KEY_BACK", "KEY_LEFT", "KEY_RIGHT", "KEY_FIRE", "FLASH",
    "SPAWN", "State", "WALK", "WALK_CHORDS", "keys", "fire_bit",
    "div", "map_cell", "map_words", "heading_table",
    "unpack_heading", "step", "render", "hud_rows", "hud_runs",
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
#: A move is one whole cell along dir (E1M1 distances: the demo walk crosses
#: ~17 cells, and half-cell steps would double the command count past budget).
#: The asm computes it as ``DIVI {MOVE_DEN}`` — written here as
#: ``floor_div(dir * s * MOVE_NUM, MOVE_DEN)``.
MOVE_NUM = 1
MOVE_DEN = 1
BIG = 2 ** 30       # stands in for an infinite deltaDist when rayDir == 0

#: DOOM's controls, MUXed: each round's command word is a bitmask of the keys
#: held this frame (see the module docstring's protocol section).
KEY_FWD = 1         # bit 0: W — step forward
KEY_BACK = 2        # bit 1: S — step backward (with W: they cancel)
KEY_LEFT = 4        # bit 2: A — turn left (CCW, +1 heading)
KEY_RIGHT = 8       # bit 3: D — turn right (with A: they cancel)
KEY_FIRE = 16       # bit 4: space/click — FLASH over the finished frame

_KEY_BITS = {"w": KEY_FWD, "s": KEY_BACK, "a": KEY_LEFT, "d": KEY_RIGHT, " ": KEY_FIRE}

#: The input protocol, one screenful — quoted into the generated machine's
#: debug sidecar (the ``io:I`` region note) so the grid itself documents how to
#: drive it. The module docstring's "Input protocol" section is the long form.
INPUT_PROTOCOL = (
    "Round 0: the 103-word data preamble (deadman3d.preamble_words()) then the "
    "first command; each later round is ONE command word, a bitmask of the keys "
    "held this frame: 1=W fwd, 2=S back, 4=A left, 8=D right, 16=space FIRE; "
    "0 renders idle, higher bits are ignored, opposing keys cancel. Turn first, "
    "then move, then render: exactly one committed frame per round, no program "
    "output. keys('wa ')==21 walks, turns and fires at once; the demo walk is "
    "WALK_CHORDS."
)


def keys(chord: str) -> int:
    """Encode simultaneously-held keys: ``keys("wa ") == 21``.

    Any character outside w/s/a/d/space contributes nothing, so ``keys(".")``
    is 0 — the idle render — and holding a key twice is just the key.
    """
    word = 0
    for ch in chord:
        word |= _KEY_BITS.get(ch, 0)
    return word


def div(a: int, b: int) -> int:
    """The LM-1 ``DIV`` quotient: floored, and **0 when b == 0** (SPEC.md)."""
    return floor_div(a, b)[0]


# ── the map (E1M1 at 32x32, north at the top; see the module docstring) ──────
MAP_SIZE = 32
#: ``.`` = empty, hex nibble = wall type 1..7.  Printed row p is y = 31 - p.
#: West to east: the zigzag nukage room (sawtooth spurs 6/3, green armor
#: platform 2 at its north end, RED exit door 1 in the west wall), the green
#: computer-area corridor with its computer block, the hangar octagon with the
#: south vestibule and its two entry alcoves (spawn between them at x=21), and
#: the courtyard (east strip + NE outdoor area, blue armor pedestal 4) behind
#: window slits.
MAP_STR = """\
77777777777777777777777777777777
77777777777777777777777777777777
77777777777777777777777777777777
77......777777777777777777777777
77.2....777777777777777777777777
77...333777777777777777777777777
77...333777777777777777777777777
77......777777777777777777777777
77......777777777777777777777777
77666...77777777777............7
77666...77777777777............7
77......77777777777............7
77......77777777777............7
77...33377777777777............7
77...33377777777777....4.......7
77......77777777777............7
77......77777777777777...777...7
77666...7777777777.......777...7
77666...77777777...........7...7
77......22222222...........7...7
71.........222.................7
71.............................7
71.............................7
77......22222222...........7...7
77......77777777...........7...7
77......7777777777.......777...7
77777777777777777777...77777...7
77777777777777777777...777777777
77777777777777777.........777777
77777777777777777..7...7..777777
77777777777777777777777777777777
77777777777777777777777777777777
"""

_PRINTED_ROWS = MAP_STR.splitlines()
assert len(_PRINTED_ROWS) == MAP_SIZE and all(len(r) == MAP_SIZE for r in _PRINTED_ROWS)


def _grid_cell(x: int, y: int) -> int:
    """Wall type straight from ``MAP_STR`` (x east, y north; row 0 is y=31)."""
    ch = _PRINTED_ROWS[MAP_SIZE - 1 - y][x]
    return 0 if ch == "." else int(ch, 16)


def map_words() -> list[int]:
    """The 64 packed map words the tape holds, two per column: word ``2x``
    packs cells (x, 0..15), word ``2x+1`` cells (x, 16..31), nibble y mod 16."""
    words = []
    for x in range(MAP_SIZE):
        for half in range(2):
            word = 0
            for k in range(16):
                t = _grid_cell(x, 16 * half + k)
                assert t == 0 or 1 <= t <= 7, f"wall type {t} at {(x, 16 * half + k)} not in 1..7"
                word += t * 16 ** k
            assert 0 < word < 2 ** 63, f"packed map word ({x},{half}) overflows a signed word"
            words.append(word)
    return words


_MAP_WORDS = map_words()
#: POW16[k] = 16**k — the tape's divisor table for nibble extraction.
POW16 = [16 ** k for k in range(16)]


def map_cell(x: int, y: int) -> int:
    """Cell lookup exactly as the asm does it: divisor ``POWB + (y mod 16)``
    via ``LDA``, word ``MAPB + 2x + (y / 16)`` via ``LDA``, then ``DIV PW;
    MODI 16``."""
    pw = POW16[sign_mod(y, 16)]
    word = _MAP_WORDS[2 * x + div(y, 16)]
    return sign_mod(div(word, pw), 16)


# ── heading table ────────────────────────────────────────────────────────────
def _dir_vec(h: int) -> tuple[int, int]:
    th = math.radians(h * 22.5)
    return round(UNITS * math.cos(th)), round(UNITS * math.sin(th))


def _plane_vec(h: int) -> tuple[int, int]:
    # dir rotated -90° (to the player's right), scaled 0.66: see the docstring.
    th = math.radians(h * 22.5)
    return round(675.84 * math.sin(th)), round(-675.84 * math.cos(th))


def _pack_heading(dx: int, dy: int, px: int, py: int) -> int:
    """All four components in one positive word: base-4096 digits dx dy px py,
    each biased +1024 (component range is +-1024, so every digit fits)."""
    word = (dx + UNITS) * 2 ** 36 + (dy + UNITS) * 2 ** 24 + (px + UNITS) * 2 ** 12 + (py + UNITS)
    assert 0 < word < 2 ** 48, f"packed heading ({dx},{dy},{px},{py}) out of range"
    return word


def unpack_heading(word: int) -> tuple[int, int, int, int]:
    """The asm's unpack: a ``DIVI 4096`` / ``MODI 4096`` chain, low digit first,
    minus the 1024 bias — returns (dirX, dirY, planeX, planeY)."""
    q, r = floor_div(word, 4096)
    planeY = r - UNITS
    q, r = floor_div(q, 4096)
    planeX = r - UNITS
    q, r = floor_div(q, 4096)
    return q - UNITS, r - UNITS, planeX, planeY


def heading_table() -> list[int]:
    """16 packed heading words, heading h at index h."""
    return [_pack_heading(*_dir_vec(h), *_plane_vec(h)) for h in range(HEADINGS)]


_HDG_WORDS = heading_table()


# ── state and the command step ───────────────────────────────────────────────
@dataclass(frozen=True)
class State:
    """The whole mutable game state: Q10 position and the baked heading."""

    posX: int
    posY: int
    heading: int


#: E1M1's start: the vestibule south of the octagon, between the two entry
#: alcoves, facing north — cell (21, 3), heading 4.
SPAWN = State(posX=22016, posY=3584, heading=4)


def step(state: State, cmd: int) -> State:
    """Apply one key-bitmask word, exactly as the asm's decode ladder does.

    Bits are peeled with the same MODI 2 / DIVI 2 chain (so any word, junk
    included, decodes identically here and on the machine); then lodev's
    order: turn first (A/D cancel), move second along the *new* heading
    (W/S cancel, per-axis collision).
    """
    posX, posY, heading = state.posX, state.posY, state.heading
    bw = sign_mod(cmd, 2)
    q = div(cmd, 2)
    bs = sign_mod(q, 2)
    q = div(q, 2)
    ba = sign_mod(q, 2)
    q = div(q, 2)
    bd = sign_mod(q, 2)
    if ba - bd != 0:
        heading = sign_mod(heading + (ba - bd), HEADINGS)
    s = bw - bs
    if s != 0:
        dirX, dirY, _, _ = unpack_heading(_HDG_WORDS[heading])
        # lodev's per-axis collision: X first, then Y against the updated posX.
        newX = posX + div(dirX * s * MOVE_NUM, MOVE_DEN)
        if map_cell(div(newX, UNITS), div(posY, UNITS)) == 0:
            posX = newX
        newY = posY + div(dirY * s * MOVE_NUM, MOVE_DEN)
        if map_cell(div(posX, UNITS), div(newY, UNITS)) == 0:
            posY = newY
    return State(posX, posY, heading)


def fire_bit(cmd: int) -> bool:
    """The FIRE key, exactly as the asm decodes it: bit 4 of the word."""
    return sign_mod(div(cmd, 16), 2) == 1


#: The muzzle flash FIRE paints over the finished frame (M5 has no game state,
#: so this is the whole of firing): 8 pixels at the bottom-centre of the 3D
#: viewport, bright yellow (11) with a white (15) core — (row, col, color),
#: painted after the wall/floor columns so they overwrite them.  The DOOM
#: unit's FLASH arm bakes these same pixels (``d3_unit.FLASH_RUNS``); the CPU
#: just sends the one FLASH command word when FIRE is held.
FLASH: list[tuple[int, int, int]] = [
    (35, 31, 11), (35, 32, 11),
    (36, 30, 11), (36, 31, 15), (36, 32, 15), (36, 33, 11),
    (37, 31, 11), (37, 32, 11),
]


# ── the renderer (lodev raycaster_flat.cpp, in Q10) ──────────────────────────
def render(state: State, *, fire: bool = False) -> list[str]:
    """One frame: 48 rows of 64 hex chars (rows 0..39 the 3D view, 40..47 HUD).

    ``fire=True`` paints :data:`FLASH` over the finished columns — the golden
    twin of the asm's ``flash:`` block, which runs after the paint loops and
    before the HUD when the round's command was a space.
    """
    posX, posY = state.posX, state.posY
    dirX, dirY, planeX, planeY = unpack_heading(_HDG_WORDS[state.heading])
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
    if fire:
        for r, c, color in FLASH:
            cols[c][r] = color
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
    """RLE (color, count) of the 8 HUD rows concatenated — the DOOM unit's HUD
    arm is generated from this list (``d3_unit.hud_tokens``), so the baked strip
    and this model cannot drift apart."""
    runs: list[tuple[int, int]] = []
    for ch in "".join(hud_rows()):
        c = int(ch, 16)
        if runs and runs[-1][0] == c:
            runs[-1] = (c, runs[-1][1] + 1)
        else:
            runs.append((c, 1))
    return runs


# ── the demo walk ────────────────────────────────────────────────────────────
#: One chord per frame, each encoded by :func:`keys`.  The beats: hold the
#: spawn view up the vestibule; seven ``w`` north into the octagon, windows
#: growing on the right; ``d`` half-look right at the armor pedestal and FIRE
#: at it; five ``a`` back and round to west, sweeping the walls; thirteen ``w``
#: across the octagon and down the green corridor to the nukage-room doorway;
#: ``d``, hold, ``a`` — the half-look up the zigzag spurs; three steps down the
#: walkway, firing *while moving* on the last (``"w "`` — the MUX at work);
#: and FIRE again, standing at the red exit door.
WALK_CHORDS: list[str] = (
    ["."] + ["w"] * 7 + ["d", " ", "a"] + ["a"] * 4
    + ["w"] * 13 + ["d", ".", "a"] + ["w", "w", "w "] + [" "]
)

#: The command words the demo feeds the machine: ``WALK_CHORDS`` encoded.
WALK: list[int] = [keys(ch) for ch in WALK_CHORDS]


# ── boot data and the cases file ─────────────────────────────────────────────
def preamble_words() -> list[int]:
    """Round 0's data burst, in exact tape order (slots 1..103; see docstring)."""
    dirX, dirY, planeX, planeY = unpack_heading(_HDG_WORDS[SPAWN.heading])
    return (
        _MAP_WORDS
        + POW16
        + _HDG_WORDS
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
        frames.append(render(state, fire=fire_bit(cmd)))
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


# ── the asm generator ────────────────────────────────────────────────────────
#: The scalar tape slots after the boot data, numbered consecutively from 104.
#: (No paint cursor: the DOOM unit owns the panel, so the CPU keeps no ADDRV/AEND.)
_SCALARS = (
    "CMD", "XCOL", "CAMX", "RDX", "RDY", "SDX", "SDY",
    "DDX", "DDY", "S2X", "STPY", "PERP", "HALFH", "DSTART", "DEND",
    "COLOR", "PW", "WADDR", "FRACX", "FRACY", "PW0", "WADDR0",
    "TMP", "TMP2", "NEWX", "NEWY",
    "BW", "BS", "BA", "BD", "FIRE", "PTR",
)

#: How many copies of the DDA step the generated asm unrolls. A backward jump
#: costs ``8 * (P - loop)`` ticks on this machine, and a frame walks ~1,000 DDA
#: steps (~16 per column), so once painting moved to the unit these laps were
#: the dominant control-flow cost. Swept on the emulator model (157 t/instr +
#: 8 t/skipped word, calibrated against the 31.08M native baseline): 2 -> 4.65x,
#: 4 -> 5.53x, 8 -> 6.03x, 16 -> 6.14x; 8 is the knee — 16 doubles P for 1.7%
#: and overfits frame 1's exact ray lengths.
DDA_UNROLL = 8


def tape_slots() -> dict[str, int]:
    """The asm's whole ``.equ`` table, name -> tape address (slot 0 is scratch).

    Slots 1..103 are the boot data in ``preamble_words()`` order (see the
    module docstring); the scalars follow consecutively, so the machine's
    ``TAPE_SIZE`` is ``max(tape_slots().values()) + 1`` — an exactly-sized tape
    stalls silently (plan risk R6), which is why tests pin this.
    """
    slots = {
        "MAPB": 1, "POWB": 65, "HDGB": 81,
        "POSX": 97, "POSY": 98, "HDG": 99, "DIRX": 100, "DIRY": 101,
        "PLANEX": 102, "PLANEY": 103,
    }
    for i, name in enumerate(_SCALARS):
        slots[name] = len(preamble_words()) + 1 + i
    return slots


def deadman3d_source() -> str:
    """The LM-1 assembly of the demo, lowered line for line from this model.

    Structure: boot loop (round 0's data preamble -> tape slots 1..103, the
    loop 8x-unrolled because a backward jump costs ``8*(P - loop)`` ticks) ->
    ``round:`` MUX decode (MODI 2 / DIVI 2 ladder -> BW BS BA BD FIRE) ->
    conditional turn (the packed heading word re-unpacked) -> conditional move
    (per-axis collision, the map-cell lookup inlined) -> ``render:`` (a
    per-frame prologue seeds PW0/WADDR0/FRACX/FRACY; then per column: setup,
    the :data:`DDA_UNROLL`-way unrolled DDA maintaining PW/WADDR incrementally,
    per-arm hit tails ``whx``/``why`` that bake the sunlit/dark shading,
    projection, and ONE ``SND`` command word to the DOOM unit) -> the FIRE
    flash, the HUD strip and the commit, one ``SND`` each -> back to
    ``round:``.  The lodev variable each block computes is named in its
    comments; every expression keeps :func:`render`'s exact operation order,
    which is the pixel contract.

    Regenerate with::

        from randomfun2026solvers.deadman3d import deadman3d_source
        from randomfun2026solvers.lm1.programs import PROGRAM_DIR
        (PROGRAM_DIR / "deadman-3d.asm").write_text(deadman3d_source())
    """
    from randomfun2026solvers.lm1.store import DoomUnit

    slots = tape_slots()
    first_free = len(preamble_words()) + 1  # 104: the boot loop's stop address
    assert first_free == slots["CMD"], "the boot stop address is the first scalar"
    inv = UNITS * UNITS          # 1048576  — deltaDist numerator (1/rayDir, Q10*Q10)
    lh_num = H3D * UNITS         # 40960    — lineHeight numerator (h / perpWallDist)
    codes = DoomUnit.CODES       # the unit's trie codes; d3_unit pins these
    assert codes["COL"] == 0, "COL must be code 0: the column send is a bare MULI 8"

    equ_notes = {
        "MAPB": f"..{slots['MAPB'] + 63:<3} packed map half-columns: word 2x+(y/16), nibble y mod 16",
        "POWB": f"..{slots['POWB'] + 15:<3} 16**k — the nibble-extraction divisors",
        "HDGB": f"..{slots['HDGB'] + 15:<3} packed headings: base-4096 digits dirX dirY planeX planeY, biased +1024",
        "POSX": "player x, Q10 (lodev posX)", "POSY": "player y, Q10 (lodev posY)",
        "HDG": "heading 0..15 (22.5 deg steps, CCW from east)",
        "DIRX": "lodev dirX", "DIRY": "lodev dirY",
        "PLANEX": "lodev planeX", "PLANEY": "lodev planeY",
        "CMD": "this round's command word",
        "XCOL": "the column being rendered (lodev x)",
        "CAMX": "lodev cameraX, Q10", "RDX": "lodev rayDirX", "RDY": "lodev rayDirY",
        "SDX": "lodev sideDistX", "SDY": "lodev sideDistY",
        "DDX": "lodev deltaDistX", "DDY": "lodev deltaDistY",
        "S2X": "2*stepX: the word address moves +-2 per x-step",
        "STPY": "lodev stepY (the sign picks the PW shift arm)",
        "PERP": "lodev perpWallDist",
        "HALFH": "lodev lineHeight / 2",
        "DSTART": "lodev drawStart", "DEND": "lodev drawEnd",
        "COLOR": "the wall type t, then the shaded colour",
        "PW": "16**(mapY mod 16), maintained incrementally across DDA steps",
        "WADDR": "MAPB + 2*mapX + mapY/16, maintained incrementally too",
        "FRACX": "posX mod 1024, hoisted per frame", "FRACY": "posY mod 1024",
        "PW0": "PW's per-frame seed (the player's own cell)",
        "WADDR0": "WADDR's per-frame seed",
        "TMP": "scratch (s, frac, packed word)",
        "TMP2": "scratch (the cell lookup's half-column selector)",
        "NEWX": "the candidate posX", "NEWY": "the candidate posY",
        "BW": "key bit 0 (1): W, forward", "BS": "key bit 1 (2): S, backward",
        "BA": "key bit 2 (4): A, turn left", "BD": "key bit 3 (8): D, turn right",
        "FIRE": "key bit 4 (16): space held — paint FLASH over this frame",
        "PTR": "the boot loop's tape cursor",
    }
    lines = [
        "; deadman-3d — GENERATED from randomfun2026solvers/deadman3d.py, do not hand-edit.",
        "; Regenerate with:",
        ";   from randomfun2026solvers.deadman3d import deadman3d_source",
        ";   from randomfun2026solvers.lm1.programs import PROGRAM_DIR",
        ';   (PROGRAM_DIR / "deadman-3d.asm").write_text(deadman3d_source())',
        ";",
        "; lodev.org's raycaster_flat.cpp on the LM-1: DOOM's E1M1, quantized to a",
        "; 32x32 grid, walked first person at 64x48 on the LM-75 — one frame per input",
        "; word, and each word is a MUX of the keys held that frame: bit0 (1) W fwd,",
        "; bit1 (2) S back, bit2 (4) A left, bit3 (8) D right, bit4 (16) space FIRE",
        "; (muzzle-flash overlay); 0 idle, higher bits ignored. Turn first (A/D",
        "; cancel), then move along the new heading (W/S cancel), then render.",
        "; An ungraded demo — the slug borrows plotter's problem JSON for nothing",
        "; but registration; its 64x48 panel belongs to the DOOM unit (.unit doom,",
        "; lm1/d3_unit.py), its input is its own, and its 134-slot STORE rides the",
        "; grid_block man-memory (STORE_TIER), ~31 ticks an access.",
        ";",
        "; The CPU never touches the display: each viewport column is ONE command",
        "; word to the write-only column-painter unit — 8*arg + code, code COL=0,",
        "; arg = ((drawStart*64 + drawEnd)*64 + x)*16 + colour — and the unit paints",
        "; the wall run and the floor run (stride 64) while the CPU raycasts the",
        "; next column. FLASH (the baked 8-pixel muzzle diamond), HUD (the baked",
        "; 512-pixel strip) and COMMIT (SWAP 0) are one command word each; the",
        "; ceiling stays black because COMMIT clears the next buffer.",
        ";",
        "; Round 0's input carries the whole data preamble (64 packed map half-columns,",
        "; POW16, the 16 packed heading words, spawn state — deadman3d.preamble_words())",
        "; followed by the first command: tables ride on INPUT because every ROM word",
        "; taxes every backward jump by 8 ticks forever. The pixel contract is",
        "; deadman3d.render(): every expression below is that model's, in its exact",
        "; operation order.",
        ";",
        "; The map-cell lookup floor(MAPW[2x + y/16] / 16**(y mod 16)) mod 16 is",
        "; inlined at its three sites (no stack, no calls): the two move-collision",
        "; tests and the DDA hit test.",
        "",
        "; ── tape slots (deadman3d.tape_slots(); slots 1..103 are the boot data) ──────",
    ]
    for name, addr in slots.items():
        lines.append(f".equ {name:<6} {addr:<4}         ; {equ_notes[name]}")
    lines += [
        "",
        "; ── the DOOM unit (lm1/d3_unit.py): 8*arg + code, codes read off its trie ────",
        ".unit doom",
    ]
    for arm in ("COL", "FLASH", "HUD", "COMMIT"):
        lines.append(f".equ C_{arm:<6} {codes[arm]}            ; {DoomUnit.ARM_NOTES[arm]}")
    n_pre = len(preamble_words())
    boot_full = (n_pre // 8) * 8  # the 8x-unrolled loop loads slots 1..boot_full
    addr_name = {v: k for k, v in slots.items()}
    lines += f"""
; ── boot: round 0's data preamble -> tape slots 1..{n_pre}, the loop unrolled 8x ──
; (a backward jump costs 8*(P - loop) ticks, so 12 laps beat 103; the last
; {n_pre - boot_full} slots are loaded straight-line at their own addresses)
        LDI 1
        ST  PTR
""".splitlines()
    for _ in range(8):
        lines += [
            "boot:   IN                  ; the next preamble word" if _ == 0 else "        IN",
            "        ST  TMP",
            "        LD  PTR",
            "        MOVA TMP            ; store[PTR] = the word",
            "        INCM PTR",
        ]
    lines += [
        "        LD  PTR",
        f"        SUBI {boot_full + 1}",
        f"        BRN boot            ; keep looping while PTR < {boot_full + 1}",
    ]
    for addr in range(boot_full + 1, n_pre + 1):
        lines += ["        IN", f"        ST  {addr_name[addr]}"]
    lines += f"""

; ── round: one key-bitmask word in, exactly one committed frame out ──────────
; The MUX decode: bits peeled low to high with a MODI 2 / DIVI 2 ladder, so
; every word — junk and high bits included — decodes exactly as the golden
; model's step() does.
round:  IN                  ; blocks here when the walk is over (the legal end)
        ST  CMD             ; ST preserves ACC
        MODI 2
        ST  BW              ; bit 0 (1): W, forward
        LD  CMD
        DIVI 2
        ST  TMP
        MODI 2
        ST  BS              ; bit 1 (2): S, backward
        LD  TMP
        DIVI 2
        ST  TMP
        MODI 2
        ST  BA              ; bit 2 (4): A, turn left
        LD  TMP
        DIVI 2
        ST  TMP
        MODI 2
        ST  BD              ; bit 3 (8): D, turn right
        LD  TMP
        DIVI 2
        MODI 2
        ST  FIRE            ; bit 4 (16): space — higher bits fall off here

; ── turn first (lodev's order): heading += A - D, cancelling when both held ──
        LD  BA
        SUB BD
        BRZ mvchk           ; no net turn: dir/plane stay as they are
        ADD HDG
        MODI {HEADINGS}
        ST  HDG             ; heading + (BA - BD), MODI's floored sign wraps -1
        LD  HDG             ; re-unpack the packed heading word
        ADDI HDGB
        LDA                 ; base-4096 digits dirX dirY planeX planeY, +1024 each
        ST  TMP
        MODI 4096
        SUBI {UNITS}
        ST  PLANEY
        LD  TMP
        DIVI 4096
        ST  TMP
        MODI 4096
        SUBI {UNITS}
        ST  PLANEX
        LD  TMP
        DIVI 4096
        ST  TMP
        MODI 4096
        SUBI {UNITS}
        ST  DIRY
        LD  TMP
        DIVI 4096
        SUBI {UNITS}
        ST  DIRX

; ── then move, along the NEW heading: s = W - S, cancelling when both held ───
mvchk:  LD  BW
        SUB BS
        BRZ render          ; no net move: just render
        ST  TMP             ; s = +1 forward, -1 backward
        LD  DIRX
        MUL TMP
        DIVI {MOVE_DEN}              ; floor(dirX * s * {MOVE_NUM} / {MOVE_DEN}) — the whole-cell step
        ADD POSX
        ST  NEWX            ; newX
        ; collision X: map_cell(newX / 1024, posY / 1024), inlined
        LD  POSY
        DIVI {UNITS}
        ST  TMP2            ; mapY (ST preserves ACC)
        MODI 16
        ADDI POWB
        LDA
        ST  PW              ; 16**(mapY mod 16)
        LD  TMP2
        DIVI 16
        ST  TMP2            ; the half-column selector, mapY / 16
        LD  NEWX
        DIVI {UNITS}
        MULI 2
        ADD TMP2
        ADDI MAPB
        LDA                 ; the packed half-column of newX's cell
        DIV PW
        MODI 16
        BRZ comx            ; empty -> commit posX
        JMP movey           ; wall -> posX unchanged
comx:   LD  NEWX
        ST  POSX
movey:  LD  DIRY
        MUL TMP
        DIVI {MOVE_DEN}
        ADD POSY
        ST  NEWY            ; newY
        ; collision Y: map_cell(posX / 1024, newY / 1024) — the UPDATED posX
        LD  NEWY
        DIVI {UNITS}
        ST  TMP2
        MODI 16
        ADDI POWB
        LDA
        ST  PW
        LD  TMP2
        DIVI 16
        ST  TMP2
        LD  POSX
        DIVI {UNITS}
        MULI 2
        ADD TMP2
        ADDI MAPB
        LDA
        DIV PW
        MODI 16
        BRZ comy
        JMP render
comy:   LD  NEWY
        ST  POSY
        JMP render

; ── render: lodev's per-column raycast, columns 0..{WIDTH - 1} ──────────────────────
; The per-frame prologue: everything that depends only on the player's position
; is computed once — the fractional position, and the cell-lookup seeds PW0 (the
; nibble divisor 16**(mapY mod 16)) and WADDR0 (the packed half-column's slot,
; MAPB + 2*mapX + mapY/16). The DDA then maintains PW/WADDR *incrementally*, so
; the per-step lookup is LDA/DIV/MODI instead of the full 16-instruction unpack.
render: LD  POSX
        MODI {UNITS}
        ST  FRACX           ; posX - mapX*1024, hoisted out of sidex
        LD  POSY
        MODI {UNITS}
        ST  FRACY
        LD  POSY
        DIVI {UNITS}
        ST  TMP             ; mapY
        MODI 16
        ADDI POWB
        LDA
        ST  PW0             ; 16**(mapY mod 16)
        LD  TMP
        DIVI 16
        ST  TMP2            ; the half-column selector, mapY / 16
        LD  POSX
        DIVI {UNITS}
        MULI 2
        ADD TMP2
        ADDI MAPB
        ST  WADDR0          ; the packed half-column's tape slot
        LDI 0
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
        LD  PW0
        ST  PW              ; the ray starts in the player's cell
        LD  WADDR0
        ST  WADDR
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
        ; stepX / sideDistX from the fractional position (lodev's two arms);
        ; stepX itself is only ever used to move the word address, so the arm
        ; records S2X = 2*stepX instead of stepX
sidex:  LD  RDX
        BRN sxneg
        LDI 2
        ST  S2X             ; stepX = 1 -> the half-column slot moves +2
        LDI {UNITS}
        SUB FRACX
        MUL DDX
        DIVI {UNITS}
        ST  SDX             ; sideDistX = (1024 - fracX) * deltaDistX / 1024
        JMP sidey
sxneg:  LDI 0
        SUBI 2
        ST  S2X             ; stepX = -1 -> -2
        LD  FRACX
        MUL DDX
        DIVI {UNITS}
        ST  SDX             ; sideDistX = fracX * deltaDistX / 1024
sidey:  LD  RDY             ; stepY / sideDistY, the same two arms
        BRN syneg
        LDI 1
        ST  STPY
        LDI {UNITS}
        SUB FRACY
        MUL DDY
        DIVI {UNITS}
        ST  SDY
        JMP dda0
syneg:  LDI 0
        SUBI 1
        ST  STPY
        LD  FRACY
        MUL DDY
        DIVI {UNITS}
        ST  SDY
        ; the DDA, unrolled {DDA_UNROLL}x: a backward jump costs 8*(P - loop) ticks on
        ; this machine, so only every {DDA_UNROLL}th empty step pays a full lap; a
        ; sideDist tie goes to the Y arm (lodev's else — risk R5)
""".splitlines()
    for k in range(DDA_UNROLL):
        nxt = f"dda{k + 1}" if k < DDA_UNROLL - 1 else "dda0"
        nxt_note = "the next unrolled step" if k < DDA_UNROLL - 1 else "the backward lap"
        lines += f"""
dda{k}:   LD  SDX
        SUB SDY
        BRN xarm{k}           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg{k}
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru{k}           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity{k}
yneg{k}:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd{k}           ; ... and 1/16 floors to 0
        JMP hity{k}
ywru{k}:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper half-column word
        JMP hity{k}
ywrd{k}:  LDI {16 ** 15}
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower half-column word
        JMP hity{k}
hity{k}:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed half-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ {nxt}            ; empty -> {nxt_note}
        JMP why             ; a y-side wall: t is dark
xarm{k}:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S2X
        ST  WADDR           ; mapX += stepX is the half-column slot moving +-2
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ {nxt}            ; empty -> {nxt_note}
        JMP whx             ; an x-side wall: t is sunlit""".splitlines()
    lines += f"""
; Which arm found the wall picks the whole tail: no per-step side flag needed.
; x-side (sunlit): the bright variant t + 8; perp = sideDistX - deltaDistX.
whx:    ADDI 8
        ST  COLOR
        LD  SDX
        SUB DDX
        ST  PERP
        JMP pclip
why:    ST  COLOR           ; y-side: the dark variant, perp from the y pair
        LD  SDY
        SUB DDY
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
        BRN send            ; drawEnd <= {H3D - 1}: no clamp
        LDI {H3D - 1}
        ST  DEND
        ; (no shade block: whx/why already picked the sunlit or dark colour)
        ; the whole column is ONE command word to the unit, which paints the wall
        ; run (drawStart..drawEnd in COLOR) and the floor run (drawEnd+1..{H3D - 1}
        ; in 8) at stride {WIDTH} while the CPU raycasts the next column; the
        ; ceiling stays black because COMMIT cleared the next buffer. The arg is
        ; the unit's own loop seed: seed = (drawStart*{WIDTH} + x)*16 + colour - 1024
        ; (its wall lap adds 1024 *before* painting), then arg = seed*64 + n_wall
send:   LD  DSTART
        MULI {WIDTH}
        ADD XCOL
        MULI 16
        ADD COLOR
        SUBI {UNITS}           ; seed (may go negative in the top row: that is fine)
        MULI {WIDTH}
        ADD DEND
        SUB DSTART
        ADDI 1              ; arg = seed*64 + (drawEnd - drawStart + 1)
        MULI 8              ; the command word: 8*arg + C_COL, and C_COL == 0
        SND
colnxt: INCM XCOL           ; ACC = the old column number
        SUBI {WIDTH - 1}
        BRZ flash           ; that was column {WIDTH - 1}: the viewport is sent
        JMP colset

; ── muzzle flash: the unit's baked {len(FLASH)}-pixel diamond, when this round FIREd ─
flash:  LD  FIRE
        BRZ hud
        LDI C_FLASH
        SND

; ── HUD strip (rows {H3D}..{HEIGHT - 1}) and the commit: one command word each ───────
hud:    LDI C_HUD
        SND                 ; the baked HUD strip
        LDI C_COMMIT
        SND                 ; SWAP 0: commit THE one frame of this round
        JMP round
""".splitlines()
    lines.append("")
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


# ── --play: the machine, played live ─────────────────────────────────────────
class _MachinePlayer:
    """The checked-in asm on ONE persistent emulator: one frame per keypress.

    A from-scratch replay costs ~85 ms per accumulated frame (measured: 2.9 s
    by frame 35), so the emulator is kept alive instead.  It stops on the
    blocking ``IN`` by raising *after* the opcode fetch has advanced the ring
    phase, so resuming rewinds the phase one word — the re-fetch re-executes
    the ``IN`` against the newly appended command.  The fast tests pin this
    pixel-equal to a from-scratch run.
    """

    def __init__(self) -> None:
        from randomfun2026solvers.lm1 import programs
        from randomfun2026solvers.lm1.emulator import Emulator

        self._em = Emulator(programs.load("deadman-3d"))
        self._words: list[int] = list(preamble_words())
        self.frames = 0

    def feed(self, code: int) -> list[str]:
        """Append one command word, run to the next block, return its frame."""
        from randomfun2026solvers.lm1.display import frames_from_writes
        from randomfun2026solvers.lm1.emulator import Round

        em = self._em
        self._words.append(code)
        if self.frames:
            em.phase = (em.phase - 1) % em.P  # rewind the fetch that hit the block
            em.reason = ""
        res = em.run(
            [Round(input=tuple(self._words))],
            max_instructions=em.instructions + 500_000,
        )
        assert res.reason == "input-exhausted", res.reason
        self.frames += 1
        return frames_from_writes(em.display_writes, width=WIDTH, height=HEIGHT)[-1]


def _ansi_frame(frame: list[str]) -> str:
    """The frame as 24 terminal lines: ▀ half-blocks, truecolor fg/bg pairs."""
    out = []
    for y in range(0, HEIGHT, 2):
        line = []
        for x in range(WIDTH):
            tr, tg, tb = PALETTE[int(frame[y][x], 16)]
            br, bg, bb = PALETTE[int(frame[y + 1][x], 16)]
            line.append(f"\x1b[38;2;{tr};{tg};{tb};48;2;{br};{bg};{bb}m▀")
        out.append("".join(line) + "\x1b[0m")
    return "\n".join(out)


def _play_frame(player: "_MachinePlayer | None", state: State, code: int) -> tuple[State, str]:
    """One keypress against whichever engine: returns (state, terminal text)."""
    state = step(state, code)
    if player is None:  # --golden: the model, instant
        frame = render(state, fire=fire_bit(code))
        n = None
    else:
        frame = player.feed(code)
        n = player.frames
    status = (
        f"frame {n if n is not None else '-'}  pos ({state.posX / UNITS:.1f}, "
        f"{state.posY / UNITS:.1f})  heading {state.heading * 22.5:g}°  "
        f"[w/s move  a/d turn  space fire  q quit]"
    )
    return state, _ansi_frame(frame) + "\n" + status


def _play_script(script: str, golden: bool) -> None:
    """Render each key's frame to stdout exactly as --play would (for tests)."""
    player = None if golden else _MachinePlayer()
    state = SPAWN
    for ch in script:
        state, text = _play_frame(player, state, keys(ch))
        print(text)


def _play(golden: bool) -> None:
    """Raw-keypress loop on the real machine (or --golden): w/a/s/d/space; q quits."""
    import sys
    import termios
    import time
    import tty

    player = None if golden else _MachinePlayer()
    state = SPAWN
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    print("\x1b[2J", end="")
    try:
        tty.setcbreak(fd)
        code = 0  # idle: the spawn view, before any key
        while True:
            t0 = time.perf_counter()
            state, text = _play_frame(player, state, code)
            ms = (time.perf_counter() - t0) * 1000
            print(f"\x1b[H{text}  {ms:.0f} ms", flush=True)
            while True:
                ch = sys.stdin.read(1)
                if ch in ("q", "\x03"):
                    return
                if ch in ("w", "a", "s", "d", " "):
                    code = keys(ch)
                    break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print("\x1b[0m")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--walk", help='one key per frame, e.g. ".wwa d" (default: WALK)')
    parser.add_argument("--cases", type=Path, help="write cases_json(WALK) here")
    parser.add_argument("--png", type=Path, help="dump preview PNGs to this directory")
    parser.add_argument("--play", action="store_true",
                        help="play the machine live: w/a/s/d/space, q quits")
    parser.add_argument("--golden", action="store_true",
                        help="with --play/--play-script: golden model, instant frames")
    parser.add_argument("--play-script", metavar="KEYS",
                        help='render KEYS as --play would, non-interactively')
    args = parser.parse_args(argv)
    if args.play_script is not None:
        _play_script(args.play_script, args.golden)
        return
    if args.play:
        _play(args.golden)
        return
    cmds = [keys(ch) for ch in args.walk] if args.walk else WALK
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
