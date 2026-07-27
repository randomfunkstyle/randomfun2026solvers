#!/usr/bin/env python3
"""deadman-3d: the golden model for the first-person raycaster demo on the LM-1 CPU.

This module is the **single source of truth** for the deadman-3d demo: an
integer transliteration of lodev.org's ``raycaster_flat.cpp``
(https://lodev.org/cgtutor/raycasting.html), rendered 64x48 on the LM-75 —
3D viewport rows 0..39, HUD strip rows 40..47.  The generated LM-1 assembly
must match this model **pixel for pixel**; every constant, table, and
expression here is written the way the asm computes it.

The map is **DOOM's E1M1** (Hangar), hand-quantized to a 64x64 grid — a
recognizable homage rather than a survey: the hangar start room (the octagon,
entered from a spawn corridor flanked by the two entry alcoves, its east end
flanked by the two raised side platforms), the courtyard with the armor bonus
behind three window slits to the north-east, the computer area west (a
corridor between green computer banks, with a block in the recess behind
them), the zigzag nukage room beyond it with its alternating sawtooth spurs,
and the exit room at that room's south end, the red exit door set between
gray posts in its west wall.  Walls render two cells tall (``WALL_H``) and a
move is two cells (``MOVE_NUM``): the finer grid keeps the 32x32 demo's
proportions and pace.

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
north wall (x 44..51) sit *east* of the spawn column x=42 — the player's
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
    6 / 3 zigzag nukage room: alternating sawtooth spurs (cyan / brown);
          3 is also the hangar's two raised side platforms
    4 blue armor-bonus pedestal in the courtyard (blue)
    1 the RED exit door, set in the west wall between gray posts

The HUD block colours are ANSI too: ammo red = 9, face yellow = 11, armor
blue = 12.

Tape slot map (the asm's .equ table; slot 0 is scratch)
-------------------------------------------------------
``preamble_words()`` yields the boot data in exact tape order, slots 1..295:

    MAPB    1..256  packed quarter-columns: word ``4x + q`` holds cells
                    (x, 16q .. 16q+15), nibble ``y mod 16`` each
    POWB  257..272  16**y for y = 0..15
    HDGB  273..288  packed headings, one word per heading h:
                    (dirX+1024)*2^36 + (dirY+1024)*2^24
                    + (planeX+1024)*2^12 + (planeY+1024)   (48 bits, positive)
    POSX  289       spawn posX = 43520  (cell 42, Q10 centre)
    POSY  290       spawn posY = 4608   (cell 4)
    HDG   291       spawn heading = 4   (north — E1M1's real facing)
    DIRX  292       spawn dirX  = 0         DIRY  293   spawn dirY   = 1024
    PLANEX 294      spawn planeX = 676      PLANEY 295  spawn planeY = 0

The cell lookup is ``floor(MAPW[4x + y/16] / 16**(y mod 16)) mod 16`` —
``slot = MAPB + 4*mapX + (mapY / 16)``, divisor ``POWB + (mapY mod 16)`` —
inlined at both move-collision checks; the DDA instead maintains the word slot
(``WADDR``) and the nibble divisor (``PW``) *incrementally* across steps, so
its per-step lookup is three instructions (LDA; DIV PW; MODI 16).

Painting is not the CPU's job: the machine carries the **DOOM unit**
(``lm1/d3_unit.py``, ``.unit doom``), a write-only coprocessor that owns the
64x48 panel. Each viewport column (banded — the wall loop's mask ring seams
every 4th row), each title/HUD RLE run, each cursor move, the pistol sprite
and the frame commit are one command word each (``8*arg + code`` — COL 0,
CURS 1, RUN 4, GUN 5, GUNF 6, COMMIT 7, pinned to
``lm1.store.DoomUnit.CODES``), and the unit's paint loops run concurrently
with the CPU's next raycast.

The title screen (round 0)
--------------------------
The demo opens on a DOOM-homage **title screen**: :data:`TITLE_HEX_ROWS`, the
64x48 hand-quantized homage to a certain 1993 shooter's title art (the same
block-Lab pipeline as ``lambda_deadman.HEX_ROWS``, at the full panel size).
It travels as its own row-major RLE — :func:`title_runs` — and each run is ONE
pre-encoded command word for the DOOM unit's RUN arm (:func:`title_words`,
``8*(count*16 + colour) + C_RUN``): the CPU forwards each word untouched
(``IN``/``SND``) and the unit paints ``count`` pixels of ``colour`` at the
panel's own auto-advancing cursor. Round 0 is the preamble, the title words,
and the title COMMIT — one frame, no gameplay command; the walk starts in
round 1.

Input protocol
--------------
Round 0 = the data preamble (:func:`preamble_words`, 295 words) + the title
screen's RLE (:func:`title_words`), committing the title frame; every later
round is exactly one command word — a **key bitmask** (a MUX of the keys held
this frame, because space can be held while moving):

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
cancel; per-axis collision), then render — the pistol sprite over the
finished columns (:data:`GUN_FIRE`, recoil and muzzle bloom, when FIRE is
held and :data:`GUN_IDLE` otherwise), and the live HUD after it: firing
spends a round of :data:`AMMO_START`'s clip (floor 0) and the yellow bar
shrinks with it.
The asm decodes the bits with a MODI 2 / DIVI 2 ladder into the ``BW BS BA BD
FIRE`` scalars and runs the turn and move arms conditionally.  :func:`keys`
encodes chords readably (``keys("wa ") == 21``); ``--play`` runs the checked-in
asm on a persistent emulator, one single-key word per keypress — the machine,
played live (chords remain reachable by script).

The demo walk (``WALK``)
------------------------
Spawn view (no-op) at E1M1's start: up the spawn corridor into the octagon.
Eight steps north, the window slits, the blue armor pedestal behind them and
the two raised side platforms growing ahead; a half-look right at the
pedestal (turn right, FIRE, turn left); four left turns to face west,
sweeping the platforms and the octagon's walls; then straight west — down
the computer-area corridor between its green banks, into the zigzag nukage
room, where a second half-look sweeps the cyan/brown sawtooth spurs; five
left turns on round to south and five steps down the walkway into the exit
room, four right turns back to west on the way — ending before the red exit
door set between gray posts.  FIRE at the dramatic beats: at the armor
pedestal, then *while stepping* up to the door (the ``"w "`` chord — the MUX
at work), and once more standing before it.  Two-cell steps (DOOM's run on
the 64x64 grid); 50 words, spelled ``WALK_CHORDS``.

``deadman3d_source()`` emits the LM-1 assembly lowered from this model;
``tape_slots()`` is its ``.equ`` table (the docstring's slot map plus the
scalars, numbered consecutively from 296).
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
    "KEY_FWD", "KEY_BACK", "KEY_LEFT", "KEY_RIGHT", "KEY_FIRE",
    "GUN_IDLE", "GUN_FIRE", "AMMO_START", "HEALTH_START",
    "SPAWN", "State", "WALK", "WALK_CHORDS", "keys", "fire_bit",
    "TITLE_HEX_ROWS", "title_frame", "title_runs", "title_words",
    "div", "map_cell", "map_words", "heading_table",
    "unpack_heading", "step", "render", "hud_rows", "hud_bg_rows", "hud_bg_runs",
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
#: A move is TWO whole cells along dir — DOOM's run speed on the 64x64 grid,
#: and what keeps the demo walk's command count near V1's (the world doubled;
#: single-cell steps would double it). The asm computes it as ``MULI
#: {MOVE_NUM}; DIVI {MOVE_DEN}`` — written here as ``floor_div(dir * s *
#: MOVE_NUM, MOVE_DEN)``.
MOVE_NUM = 2
MOVE_DEN = 1
BIG = 2 ** 30       # stands in for an infinite deltaDist when rayDir == 0
#: Walls are two cells tall: the 64x64 grid halved the cell against the eye
#: height, so the projection doubles back to the 32x32 world's proportions
#: (lineHeight = WALL_H * h / perpWallDist).
WALL_H = 2
#: Distance shading (V3): a wall whose perpWallDist reaches this many Q10
#: units (16 cells) drops to its dark shade whatever its face or stripe.
NEAR_D = 16 * UNITS

#: DOOM's controls, MUXed: each round's command word is a bitmask of the keys
#: held this frame (see the module docstring's protocol section).
KEY_FWD = 1         # bit 0: W — step forward
KEY_BACK = 2        # bit 1: S — step backward (with W: they cancel)
KEY_LEFT = 4        # bit 2: A — turn left (CCW, +1 heading)
KEY_RIGHT = 8       # bit 3: D — turn right (with A: they cancel)
KEY_FIRE = 16       # bit 4: space/click — fire the pistol (GUN_FIRE + ammo)

_KEY_BITS = {"w": KEY_FWD, "s": KEY_BACK, "a": KEY_LEFT, "d": KEY_RIGHT, " ": KEY_FIRE}

#: The input protocol, one screenful — quoted into the generated machine's
#: debug sidecar (the ``io:I`` region note) so the grid itself documents how to
#: drive it. The module docstring's "Input protocol" section is the long form.
INPUT_PROTOCOL = (
    "Round 0: the 295-word data preamble (deadman3d.preamble_words()) then the "
    "title screen's RLE (title_words(): one pre-encoded RUN command word per "
    "run), committing the title frame; each later round is ONE command word, a "
    "bitmask of the keys held this frame: 1=W fwd, 2=S back, 4=A left, 8=D "
    "right, 16=space FIRE; 0 renders idle, higher bits are ignored, opposing "
    "keys cancel. Turn first, then move, then render: exactly one committed "
    "frame per round, no program output. keys('wa ')==21 walks, turns and "
    "fires at once; the demo walk is WALK_CHORDS."
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


# ── the map (E1M1 at 64x64, north at the top; see the module docstring) ──────
MAP_SIZE = 64
#: ``.`` = empty, hex nibble = wall type 1..7.  Printed row p is y = 63 - p.
#: West to east: the zigzag nukage room (sawtooth spurs 6/3 alternating off its
#: west and east walls, green armor platform 2 at the north end, the exit room
#: at the south end with the RED door 1 set between gray posts in its west
#: wall), the computer area (a corridor between green computer banks 2, with a
#: recess and a green block behind the north bank), the hangar octagon with the
#: two raised side platforms 3 flanking its east end, the south spawn corridor
#: with its two entry alcoves (spawn between them at x=42), and the courtyard
#: (east strip + NE outdoor area, blue armor pedestal 4) behind three window
#: slits in the hangar's north wall.
MAP_STR = """\
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
7777............777777777777777777777777777777777777777777777777
7777.2222.......777777777777777777777777777777777777777777777777
7777.2222.......777777777777777777777777777777777777777777777777
7777.2222.......777777777777777777777777777777777777777777777777
7777............777777777777777777777777777777777777777777777777
7777............777777777777777777777777777777777777777777777777
7777............777777777777777777777777777777777777777777777777
7777............777777777777777777777777777777777777777777777777
7777666666......777777777777777777777777777777777777777777777777
7777666666......777777777777777777777777777777777777777777777777
7777666666......777777777777777777777777777777777777777777777777
7777666666......777777777777777777777777......................77
7777............777777777777777777777777......................77
7777............777777777777777777777777......................77
7777............777777777777777777777777......................77
7777............777777777777777777777777......................77
7777......333333777777777777777777777777......................77
7777......333333777777777777777777777777......................77
7777......333333777777777777777777777777......................77
7777......333333777777777777777777777777......................77
7777............777777777777777777777777......................77
7777............777777777777777777777777......................77
7777............777777777777777777777777......44..............77
7777............777777777777777777777777......44..............77
7777666666......777777777777777777777777......................77
7777666666......7777777777777777777777777777..7..7..777777....77
7777666666......7777777777777777777777777777..7..7..777777....77
7777666666......7777777777777777777777...............77777....77
7777............777777777777777777777.................7777....77
7777............77777777777777777777...............3333377....77
7777............7777........7777777................3333377....77
7777............7777..2222..777777.................33333.7....77
7777......3333337777..2222..777777.................33333.7....77
7777......3333332222........222277.................33333.7....77
7777......3333332222........222277.......................7....77
7777......333333.........................................7....77
7777.....................................................7....77
7777.....................................................7....77
7777.....................................................7777777
7777.....................................................7777777
7777666666......222222222222222277.......................7777777
7777666666......222222222222222277.................33333.7777777
777766666.......777777777777777777.................33333.7777777
777766666.......777777777777777777.................33333.7777777
7777.........7777777777777777777777................3333377777777
77777........77777777777777777777777...............3333377777777
7771.........777777777777777777777777.................7777777777
7771.........7777777777777777777777777...............77777777777
7771.........777777777777777777777..................777777777777
7777.........777777777777777777777..................777777777777
7777.........7777777777777777777777777777....7777777777777777777
7777.........777777777777777777777777...7....7...777777777777777
7777777777777777777777777777777777777...7....7...777777777777777
7777777777777777777777777777777777777...7....7...777777777777777
77777777777777777777777777777777777777777....7777777777777777777
77777777777777777777777777777777777777777....7777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
"""

_PRINTED_ROWS = MAP_STR.splitlines()
assert len(_PRINTED_ROWS) == MAP_SIZE and all(len(r) == MAP_SIZE for r in _PRINTED_ROWS)


def _grid_cell(x: int, y: int) -> int:
    """Wall type straight from ``MAP_STR`` (x east, y north; row 0 is y=31)."""
    ch = _PRINTED_ROWS[MAP_SIZE - 1 - y][x]
    return 0 if ch == "." else int(ch, 16)


def map_words() -> list[int]:
    """The 256 packed map words the tape holds, four per column: word ``4x + q``
    packs cells (x, 16q .. 16q+15), nibble y mod 16."""
    words = []
    for x in range(MAP_SIZE):
        for half in range(4):
            word = 0
            for k in range(16):
                t = _grid_cell(x, 16 * half + k)
                assert t == 0 or 1 <= t <= 7, f"wall type {t} at {(x, 16 * half + k)} not in 1..7"
                word += t * 16 ** k
            # 0 is legal now: a fully-open quarter-column packs to 0 on 64x64.
            assert 0 <= word < 2 ** 63, f"packed map word ({x},{half}) overflows a signed word"
            words.append(word)
    return words


_MAP_WORDS = map_words()
#: POW16[k] = 16**k — the tape's divisor table for nibble extraction.
POW16 = [16 ** k for k in range(16)]


def map_cell(x: int, y: int) -> int:
    """Cell lookup exactly as the asm does it: divisor ``POWB + (y mod 16)``
    via ``LDA``, word ``MAPB + 4x + (y / 16)`` via ``LDA``, then ``DIV PW;
    MODI 16``."""
    pw = POW16[sign_mod(y, 16)]
    word = _MAP_WORDS[4 * x + div(y, 16)]
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


#: E1M1's start: the spawn corridor south of the octagon, between the two
#: entry alcoves, facing north — cell (42, 4), heading 4.
SPAWN = State(posX=43520, posY=4608, heading=4)


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


#: The pistol (V4) — original chunky pixel work, our own homage style: one
#: contiguous run per sprite row, ``(viewport row, first column, colours)``
#: with colours as hex digits (0 outline, 7 gray body, 8 shade, f highlight,
#: b muzzle yellow). The DOOM unit's GUN arm bakes exactly these runs and the
#: CPU sends ONE command word per frame; ``GUN_FIRE`` is the recoil variant —
#: the gun a row higher with the muzzle flash blooming above it — sent when
#: FIRE is held (it replaced V1's bare 8-pixel diamond).
GUN_IDLE: list[tuple[int, int, str]] = [
    (30, 30, "0770"),
    (31, 29, "07f770"),
    (32, 29, "077770"),
    (33, 28, "00777700"),
    (34, 27, "0777777770"),
    (35, 27, "07788770"),
    (36, 28, "00778770"),
    (37, 30, "077870"),
    (38, 30, "07770"),
    (39, 31, "0770"),
]
GUN_FIRE: list[tuple[int, int, str]] = [
    (25, 32, "bb"),
    (26, 31, "bffb"),
    (27, 30, "bffffb"),
    (28, 31, "bffb"),
] + [(r - 1, c, colors) for r, c, colors in GUN_IDLE]

#: The live HUD's scalars (V4): ammo starts full and drops one per shot down
#: to an empty clip; health is static until the demo grows damage. The bars
#: paint 2 rows each over the baked background: red health rows 41..42 from
#: column 4, one pixel per 4 health; yellow ammo rows 44..45, one per 2 ammo.
AMMO_START = 50
HEALTH_START = 100
BAR_COL = 4
HEALTH_BAR_ROWS = (41, 42)
AMMO_BAR_ROWS = (44, 45)


# ── the renderer (lodev raycaster_flat.cpp, in Q10) ──────────────────────────
def render(state: State, *, fire: bool = False,
           ammo: int = AMMO_START, health: int = HEALTH_START) -> list[str]:
    """One frame: 48 rows of 64 hex chars (rows 0..39 the 3D view, 40..47 HUD).

    The pistol (:data:`GUN_IDLE`, or :data:`GUN_FIRE` when ``fire``) paints
    over the finished columns — the golden twin of the machine's one GUN/GUNF
    command word per frame — and the HUD carries the live bars for ``ammo``
    and ``health``.
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
        # The texture stripe (V3): the parity of the map coordinate *along* the
        # wall face — mapY for an x-side, mapX for a y-side — exactly as the
        # asm derives it from its incremental lookup state (PW % 17 / 16 is
        # (mapY mod 16) & 1 == mapY & 1; (WADDR - 1) / 4 % 2 is mapX & 1).
        # A sunlit x-side inverts the stripe so corners keep their contrast.
        if side == 0:
            stripe = 1 - div(sign_mod(POW16[sign_mod(mapY, 16)], 17), 16)
        else:
            stripe = sign_mod(div(4 * mapX + div(mapY, 16), 4), 2)
        # lodev: perpWallDist = sideDist - deltaDist of the hit side, kept Q10.
        perpWallDist = (sideDistX - deltaDistX) if side == 0 else (sideDistY - deltaDistY)
        if perpWallDist < 1:
            perpWallDist = 1
        # lodev: lineHeight = WALL_H * h / perpWallDist -> Q10: 81920 / perp
        # (two-cell walls: the 64x64 world at the 32x32 proportions).
        lineHeight = div(WALL_H * H3D * UNITS, perpWallDist)
        halfh = div(lineHeight, 2)
        drawStart = MID - halfh
        if drawStart < 0:
            drawStart = 0
        drawEnd = MID + halfh
        if drawEnd > H3D - 1:
            drawEnd = H3D - 1
        # Distance shading + panel stripes (V3): a wall nearer than NEAR_D
        # paints its stripe pattern in the bright variant t + 8; a far wall is
        # all dark t. (V1's flat rule was bright on every sunlit x-side.)
        color = t + 8 if perpWallDist - NEAR_D < 0 and stripe != 0 else t
        # The unit's banded wall loop (V3) masks every 4th painted row down to
        # the dark shade — the horizontal seam of a wall panel.
        run = [
            color & 7 if sign_mod(i, 4) == 0 else color
            for i in range(drawEnd - drawStart + 1)
        ]
        cols.append([0] * drawStart + run + [8] * (H3D - 1 - drawEnd))
    for r, c, colors in (GUN_FIRE if fire else GUN_IDLE):
        for i, ch in enumerate(colors):
            cols[c + i][r] = int(ch, 16)
    rows = ["".join("%x" % cols[x][y] for x in range(WIDTH)) for y in range(H3D)]
    return rows + hud_rows(health, ammo)


# ── the HUD strip (rows 40..47) ──────────────────────────────────────────────
AMMO_COLOR = 9    # bright red   (ANSI; the plan's "12" was the CGA index)
FACE_COLOR = 11   # bright yellow (the plan's "14" was CGA yellow)
ARMOR_COLOR = 12  # bright blue  (the plan's "9" was CGA light blue)


def hud_bg_rows() -> list[str]:
    """Rows 40..47's static background: bezel 7, six field rows of base 8 with
    the blue armor block (cols 50..58), base row 8 — the bars paint over it."""
    field = [8] * WIDTH
    for c in range(50, 59):    # "armor", cols 50..58 — the static blue block
        field[c] = ARMOR_COLOR
    mid = "".join("%x" % c for c in field)
    return ["7" * WIDTH] + [mid] * 6 + ["8" * WIDTH]


def hud_bg_runs() -> list[tuple[int, int]]:
    """RLE (color, count) of the 8 background rows concatenated — the CPU
    repaints the strip every frame as one CURS word (cursor to 2560) plus one
    pre-encoded RUN word per run, so this list IS the asm's constant table."""
    runs: list[tuple[int, int]] = []
    for ch in "".join(hud_bg_rows()):
        c = int(ch, 16)
        if runs and runs[-1][0] == c:
            runs[-1] = (c, runs[-1][1] + 1)
        else:
            runs.append((c, 1))
    return runs


def hud_rows(health: int = HEALTH_START, ammo: int = AMMO_START) -> list[str]:
    """Rows 40..47: the background plus the live bars, exactly as the machine
    paints them — red health rows 41..42 (one pixel per 4 health), yellow ammo
    rows 44..45 (one per 2 ammo), both from column 4; an empty bar sends no
    RUN at all, so the background shows through."""
    rows = [[int(ch, 16) for ch in r] for r in hud_bg_rows()]
    hpx = div(health, 4)
    apx = div(ammo, 2)
    for bar_rows, px, colour in (
        (HEALTH_BAR_ROWS, hpx, AMMO_COLOR),
        (AMMO_BAR_ROWS, apx, FACE_COLOR),
    ):
        for row in bar_rows:
            for c in range(BAR_COL, BAR_COL + px):
                rows[row - H3D][c] = colour
    return ["".join("%x" % c for c in row) for row in rows]


# ── the demo walk ────────────────────────────────────────────────────────────
#: One chord per frame, each encoded by :func:`keys`.  The beats: hold the
#: spawn view up the spawn corridor; eight ``w`` north into the octagon, the
#: window slits and both raised side platforms growing ahead; ``d`` half-look
#: right at the armor pedestal behind the slits and FIRE at it; five ``a``
#: back and round to west, sweeping the platforms and walls; fifteen ``w``
#: across the octagon and down the computer-area corridor into the zigzag
#: nukage room; ``d``, hold, ``a`` — the half-look up the sawtooth spurs; five
#: ``a`` on round to south and five ``w`` down the walkway into the exit room
#: (four ``d`` back to west on the way in); a step and a *firing* step at the
#: red door (``"w "`` — the MUX at work); and FIRE once more, standing before
#: it.
WALK_CHORDS: list[str] = (
    ["."] + ["w"] * 8 + ["d", " ", "a"] + ["a"] * 4
    + ["w"] * 15 + ["d", ".", "a"] + ["a"] * 4 + ["w"] * 5 + ["d"] * 4
    + ["w", "w "] + [" "]
)

#: The command words the demo feeds the machine: ``WALK_CHORDS`` encoded.
WALK: list[int] = [keys(ch) for ch in WALK_CHORDS]


# ── the title screen (round 0) ───────────────────────────────────────────────
#: The 64x48 DOOM-homage title screen, hand-quantized from the same source as
#: ``lambda_deadman.HEX_ROWS`` (doomwiki's Doom-1-.gif) with the block-optimal
#: Lab-distance method at the full panel size, plus a 37-pixel isolated-dot
#: despeckle. One hex digit per pixel (ANSI palette index), row-major.
TITLE_HEX_ROWS: list[str] = [
    "1811118111111111111111111111111111111111111111111111111111111111",
    "11111118c77777778111115888888111118888881178c8885115888c51111111",
    "1111111110000000011110000000011110000000015800000141000011111111",
    "1111111110000000001010000000001100000000001800000400000011111111",
    "1991339110000000003030000000003304000000003330000300000011111111",
    "1199991810003330003830003330003304033330003330000330000011111111",
    "8811918810000030003830003030003304030030003330000034000011111111",
    "0488884810003030003330003830003300030830003330000000000011111111",
    "8544441130003830003330003830003300038830003330000000040031111111",
    "1888881130003830003334003830003300038334003330030000040031111111",
    "1111111130003830083338003330003304433330833338030000440031111111",
    "8111111134003830883338803330003300433338833333830004040011111111",
    "8111111134003338883333803330003300433333333333333004000011111111",
    "1001111130003338883333303330003300033333333333333380880011111111",
    "1111111130003338883333333330003300033333333333333338780011111111",
    "111111113088383833b3b333333880b3083333333333333bb333b80011111111",
    "11111111b8883b333377773337b388bb333b33333333333b8333b380b1111111",
    "11111111b88833333887777333b333f7333b33333b33333b8b333383b1111111",
    "11111111b88833388888888833b3bb33333333bb3b83333b3b333333b1111111",
    "11111111b38333388088888773b3b3333bb333333333333b3b333333b1111111",
    "11111111b3333338808888877b8bb33333bbb3bb313333bb33b33333b1111111",
    "11111111b333333880888877888882333333bb331111333b33b33333b1111111",
    "11111111b33333888888888788888823133333111111133883833333b1111111",
    "11111111b333bb388888888888888882111831111111111083833333b1118811",
    "11111111b33333300888088888288882811111111111111000883b33b1188811",
    "11111111b33333800008088882228888311111111111110001883b3330888111",
    "11111111bb333800000000088822208773111111111111000183333800081111",
    "1111011333338000080008808822283373111111111001000000388000111111",
    "1100001183380882280888808822228333311111110000000008888081111111",
    "1100001113180888820088880888828333331111100000000088888881111111",
    "1100001111080888882088880000882883337110000000000888800000111011",
    "1100011110020888888808888882882888333700000000000880000000000011",
    "1111111000020008833700088888882288833330000000000000000110000011",
    "1111111000008088333f80083333800018883338800000000000000010000001",
    "1111111100008088333330033333880811388388800000000000111110000000",
    "1111111110008830883330033333888881108800000000000011111110000001",
    "1111111110011138883377888338888888100000000800000011111484440880",
    "11111111110001118883377888888888881088000081000001111118b000bbb0",
    "111111111110001118833338888888888880800011111100111111108888bbb0",
    "1111101111100011118833388888888888280011111111111111111bbbbbbbb0",
    "11111011111000111188838888888888882801111111111111111118b8b48bb0",
    "11111111111000113288800888888888882811111111111111111118b8b08bb0",
    "11111111111100128888800888888888782211111111111111111118b8b08bb0",
    "11111111111103288888300888888888772b3333111111111111111bbbbbbbb3",
    "1111111110010888888888088008888877ffb773811111111111111888888880",
    "000111100011880888888008000888787ffffff7801111111111111888080000",
    "0001111000018800000800080088887ffffffff7731110001111111131133300",
    "0011111111008000000800000888887fffffffbbb31110010111911111131000",
]
assert len(TITLE_HEX_ROWS) == HEIGHT and all(len(r) == WIDTH for r in TITLE_HEX_ROWS)


def title_frame() -> list[str]:
    """The title screen as a committed frame: :data:`TITLE_HEX_ROWS` verbatim."""
    return list(TITLE_HEX_ROWS)


def title_runs() -> list[tuple[int, int]]:
    """The title as row-major RLE ``(colour, count)`` runs over all 3072 pixels.

    The DOOM unit's RUN arm replays one run per command word at the panel's own
    auto-advancing cursor, so encoding is trivially lossless by construction.
    """
    runs: list[tuple[int, int]] = []
    for ch in "".join(TITLE_HEX_ROWS):
        c = int(ch, 16)
        if runs and runs[-1][0] == c:
            runs[-1] = (c, runs[-1][1] + 1)
        else:
            runs.append((c, 1))
    return runs


def title_words() -> list[int]:
    """Round 0's title burst: one pre-encoded RUN command word per run.

    ``8*(count*16 + colour) + C_RUN`` — exactly the word the unit's trie
    expects, so the CPU's whole title loop is ``IN``/``SND`` pairs.
    """
    from randomfun2026solvers.lm1.store import DoomUnit

    run = DoomUnit.CODES["RUN"]
    return [8 * (count * 16 + colour) + run for colour, count in title_runs()]


# ── boot data and the cases file ─────────────────────────────────────────────
def preamble_words() -> list[int]:
    """Round 0's data burst, in exact tape order (slots 1..295; see docstring)."""
    dirX, dirY, planeX, planeY = unpack_heading(_HDG_WORDS[SPAWN.heading])
    return (
        _MAP_WORDS
        + POW16
        + _HDG_WORDS
        + [SPAWN.posX, SPAWN.posY, SPAWN.heading, dirX, dirY, planeX, planeY]
    )


def input_words(cmds: list[int]) -> list[int]:
    """Everything the program ever reads: the preamble, the title screen's RLE,
    then one word per command."""
    return preamble_words() + title_words() + list(cmds)


def frames_for_commands(cmds: list[int]) -> list[list[str]]:
    """Apply each command in turn and render after it — one frame per command.

    Threads the live ammo counter exactly as the asm does: a FIRE with rounds
    left decrements BEFORE the render (the decode ladder runs first), an empty
    clip dry-fires at 0 and still flashes.
    """
    state = SPAWN
    ammo = AMMO_START
    frames = []
    for cmd in cmds:
        fire = fire_bit(cmd)
        if fire and ammo > 0:
            ammo -= 1
        state = step(state, cmd)
        frames.append(render(state, fire=fire, ammo=ammo, health=HEALTH_START))
    return frames


def cases_json(cmds: list[int]) -> dict:
    """The demo's cases file: ONE case, one round per frame, round-gated frames.

    Shape matches ``littleman/examples/lambda-deadman-cpu.cases.json`` /
    ``littleman/tools/display-frames.mjs``: round 0 carries the preamble and
    the title screen's RLE and expects the title frame; each later round is
    exactly one command word; every round expects exactly one committed frame
    and no program output.
    """
    frames = frames_for_commands(cmds)
    boot = [str(w) for w in preamble_words() + title_words()]
    rounds = [{"in": boot, "out": [], "frames": [title_frame()]}]
    for k, cmd in enumerate(cmds):
        rounds.append({
            "in": [str(cmd)],
            "out": [],
            "frames": [frames[k]],
        })
    return {"publicTestData": [{"name": "deadman-3d", "rounds": rounds}]}


# ── the asm generator ────────────────────────────────────────────────────────
#: The scalar tape slots after the boot data, numbered consecutively from 296.
#: (No paint cursor: the DOOM unit owns the panel, so the CPU keeps no ADDRV/AEND.)
_SCALARS = (
    "CMD", "XCOL", "CAMX", "RDX", "RDY", "SDX", "SDY",
    "DDX", "DDY", "S4X", "STPY", "PERP", "HALFH", "DSTART", "DEND",
    "COLOR", "PW", "WADDR", "FRACX", "FRACY", "PW0", "WADDR0",
    "TMP", "TMP2", "NEWX", "NEWY",
    "BW", "BS", "BA", "BD", "FIRE", "AMMO", "HEALTH", "PTR",
)

#: How many copies of the DDA step the generated asm unrolls. A backward jump
#: costs ``8 * (P - loop)`` ticks on this machine, and a frame walks ~1,000 DDA
#: steps (~16 per column), so once painting moved to the unit these laps were
#: the dominant control-flow cost. On the 32x32 map the emulator-model sweep
#: put the knee at 8 (2 -> 4.65x, 4 -> 5.53x, 8 -> 6.03x, 16 -> 6.14x); the
#: 64x64 map doubled the rays, and the native re-sweep (rom_rows 40, men-v3)
#: moved it: frame 1 = 8,519,342 at 8, 8,380,261 at 12, 8,160,375 at 16 —
#: the +720-word P tax is repaid twice over by the halved backward laps.
DDA_UNROLL = 16


def tape_slots() -> dict[str, int]:
    """The asm's whole ``.equ`` table, name -> tape address (slot 0 is scratch).

    Slots 1..295 are the boot data in ``preamble_words()`` order (see the
    module docstring); the scalars follow consecutively, so the machine's
    ``TAPE_SIZE`` is ``max(tape_slots().values()) + 1`` — an exactly-sized tape
    stalls silently (plan risk R6), which is why tests pin this.
    """
    slots = {
        "MAPB": 1, "POWB": 257, "HDGB": 273,
        "POSX": 289, "POSY": 290, "HDG": 291, "DIRX": 292, "DIRY": 293,
        "PLANEX": 294, "PLANEY": 295,
    }
    for i, name in enumerate(_SCALARS):
        slots[name] = len(preamble_words()) + 1 + i
    return slots


def deadman3d_source() -> str:
    """The LM-1 assembly of the demo, lowered line for line from this model.

    Structure: boot loop (round 0's data preamble -> tape slots 1..295, the
    loop 8x-unrolled because a backward jump costs ``8*(P - loop)`` ticks) ->
    ``title:`` (round 0's title screen: the pre-encoded RUN words forwarded
    ``IN``/``SND`` 8 per counted lap, then one COMMIT) -> ``round:`` MUX decode (MODI 2 / DIVI 2 ladder -> BW BS BA BD FIRE) ->
    conditional turn (the packed heading word re-unpacked) -> conditional move
    (per-axis collision, the map-cell lookup inlined) -> ``render:`` (a
    per-frame prologue seeds PW0/WADDR0/FRACX/FRACY; then per column: setup,
    the :data:`DDA_UNROLL`-way unrolled DDA maintaining PW/WADDR incrementally,
    per-arm hit tails ``whx``/``why`` that bake the sunlit/dark shading,
    projection, and ONE ``SND`` command word to the DOOM unit) -> the FIRE
    pistol sprite (GUN or GUNF by the FIRE bit), the HUD (one CURS, the
    background RUN constants, then the live bars) and the commit -> back to
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
    first_free = len(preamble_words()) + 1  # 296: the boot loop's stop address
    assert first_free == slots["CMD"], "the boot stop address is the first scalar"
    inv = UNITS * UNITS          # 1048576  — deltaDist numerator (1/rayDir, Q10*Q10)
    lh_num = WALL_H * H3D * UNITS  # 81920  — lineHeight numerator (two-cell walls)
    codes = DoomUnit.CODES       # the unit's trie codes; d3_unit pins these
    assert codes["COL"] == 0, "COL must be code 0: the column send is a bare MULI 8"

    equ_notes = {
        "MAPB": f"..{slots['MAPB'] + 255:<3} packed map quarter-columns: word 4x+(y/16), nibble y mod 16",
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
        "S4X": "4*stepX: the word address moves +-4 per x-step",
        "STPY": "lodev stepY (the sign picks the PW shift arm)",
        "PERP": "lodev perpWallDist",
        "HALFH": "lodev lineHeight / 2",
        "DSTART": "lodev drawStart", "DEND": "lodev drawEnd",
        "COLOR": "the wall type t, then the shaded colour",
        "PW": "16**(mapY mod 16), maintained incrementally across DDA steps",
        "WADDR": "MAPB + 4*mapX + mapY/16, maintained incrementally too",
        "FRACX": "posX mod 1024, hoisted per frame", "FRACY": "posY mod 1024",
        "PW0": "PW's per-frame seed (the player's own cell)",
        "WADDR0": "WADDR's per-frame seed",
        "TMP": "scratch (s, frac, packed word)",
        "TMP2": "scratch (the cell lookup's quarter-column selector)",
        "NEWX": "the candidate posX", "NEWY": "the candidate posY",
        "BW": "key bit 0 (1): W, forward", "BS": "key bit 1 (2): S, backward",
        "BA": "key bit 2 (4): A, turn left", "BD": "key bit 3 (8): D, turn right",
        "FIRE": "key bit 4 (16): space held — fire the pistol this frame",
        "AMMO": f"live rounds left: starts {AMMO_START}, -1 per shot, floor 0",
        "HEALTH": f"static {HEALTH_START} until the demo grows damage",
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
        "; 64x64 grid, walked first person at 64x48 on the LM-75 — one frame per input",
        "; word, and each word is a MUX of the keys held that frame: bit0 (1) W fwd,",
        "; bit1 (2) S back, bit2 (4) A left, bit3 (8) D right, bit4 (16) space FIRE",
        "; (muzzle-flash overlay); 0 idle, higher bits ignored. Turn first (A/D",
        "; cancel), then move along the new heading (W/S cancel), then render.",
        "; An ungraded demo — the slug borrows plotter's problem JSON for nothing",
        "; but registration; its 64x48 panel belongs to the DOOM unit (.unit doom,",
        "; lm1/d3_unit.py), its input is its own, and its 328-slot STORE rides the",
        "; men-v3 man-memory (STORE_TIER), ~11 ticks an access.",
        ";",
        "; The CPU never touches the display: each viewport column is ONE command",
        "; word to the write-only column-painter unit — 8*arg + code, code COL=0,",
        "; arg = ((drawStart*64 + drawEnd)*64 + x)*16 + colour — and the unit paints",
        "; the wall run and the floor run (stride 64) while the CPU raycasts the",
        "; next column, seaming every 4th wall row via its mask ring. The pistol",
        "; (GUN idle / GUNF recoil+flash), each cursor move (CURS), each RLE run",
        "; (RUN) and COMMIT (SWAP 0) are one command word each; the ceiling stays",
        "; black because COMMIT clears the next buffer.",
        ";",
        "; Round 0's input carries the whole data preamble (256 packed map quarter-columns,",
        "; POW16, the 16 packed heading words, spawn state — deadman3d.preamble_words())",
        "; followed by the title screen's RLE (deadman3d.title_words(): one pre-encoded",
        "; RUN command word per run, forwarded IN/SND and committed as round 0's one",
        "; frame): tables and art ride on INPUT because every ROM word taxes every",
        "; backward jump by 8 ticks forever. The pixel contract is deadman3d.render()",
        "; (and TITLE_HEX_ROWS for the title): every expression below is that model's,",
        "; in its exact operation order.",
        ";",
        "; The map-cell lookup floor(MAPW[4x + y/16] / 16**(y mod 16)) mod 16 is",
        "; inlined at its three sites (no stack, no calls): the two move-collision",
        "; tests and the DDA hit test.",
        "",
        "; ── tape slots (deadman3d.tape_slots(); slots 1..295 are the boot data) ──────",
    ]
    for name, addr in slots.items():
        lines.append(f".equ {name:<6} {addr:<4}         ; {equ_notes[name]}")
    lines += [
        "",
        "; ── the DOOM unit (lm1/d3_unit.py): 8*arg + code, codes read off its trie ────",
        ".unit doom",
    ]
    for arm in ("COL", "RUN", "CURS", "GUN", "GUNF", "COMMIT"):
        lines.append(f".equ C_{arm:<6} {codes[arm]}            ; {DoomUnit.ARM_NOTES[arm]}")
    n_pre = len(preamble_words())
    boot_full = (n_pre // 8) * 8  # the 8x-unrolled loop loads slots 1..boot_full
    addr_name = {v: k for k, v in slots.items()}
    lines += f"""
; ── boot: round 0's data preamble -> tape slots 1..{n_pre}, the loop unrolled 8x ──
; (a backward jump costs 8*(P - loop) ticks, so {boot_full // 8} laps beat {n_pre}; the last
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
    n_title = len(title_words())
    title_unroll = 8
    title_laps, title_rem = divmod(n_title, title_unroll)
    rem_note = f" + {title_rem} straight-line pairs" if title_rem else ""
    lines += f"""

; ── title: the DOOM-homage title screen — round 0's one frame ────────────────
; The next {n_title} input words are PRE-ENCODED unit commands (title_words():
; one RUN word per RLE run of TITLE_HEX_ROWS, 8*(count*16 + colour) + C_RUN),
; so the CPU forwards each word untouched — IN; SND, {title_unroll} pairs per counted
; lap ({title_laps} laps{rem_note}) — and the unit paints the runs at the panel's
; own auto-advancing cursor, concurrently. One COMMIT ends round 0.
        LDI 0
        ST  PTR             ; PTR now counts title laps
""".splitlines()
    for _ in range(title_laps and 1):
        for k in range(title_unroll):
            lines += [
                "title:  IN                  ; the next pre-encoded RUN word" if k == 0
                else "        IN",
                "        SND",
            ]
        lines += [
            "        INCM PTR",
            "        LD  PTR",
            f"        SUBI {title_laps}",
            f"        BRN title           ; keep looping while PTR < {title_laps}",
        ]
    for _ in range(title_rem):
        lines += ["        IN", "        SND"]
    lines += [
        "        LDI C_COMMIT",
        "        SND                 ; commit: the title screen is round 0's frame",
        f"        LDI {AMMO_START}",
        "        ST  AMMO            ; a full clip (V4's live HUD)",
        f"        LDI {HEALTH_START}",
        "        ST  HEALTH          ; static until the demo grows damage",
    ]
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
        BRZ turn0           ; ST preserved ACC = the fire bit
        LD  AMMO
        BRZ turn0           ; dry-fire on an empty clip: the counter stays 0
        SUBI 1
        ST  AMMO            ; one live round spent — the HUD bar shrinks

; ── turn first (lodev's order): heading += A - D, cancelling when both held ──
turn0:  LD  BA
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
        MULI {MOVE_NUM}
        DIVI {MOVE_DEN}              ; floor(dirX * s * {MOVE_NUM} / {MOVE_DEN}) — the two-cell step
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
        ST  TMP2            ; the quarter-column selector, mapY / 16
        LD  NEWX
        DIVI {UNITS}
        MULI 4
        ADD TMP2
        ADDI MAPB
        LDA                 ; the packed quarter-column of newX's cell
        DIV PW
        MODI 16
        BRZ comx            ; empty -> commit posX
        JMP movey           ; wall -> posX unchanged
comx:   LD  NEWX
        ST  POSX
movey:  LD  DIRY
        MUL TMP
        MULI {MOVE_NUM}
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
        MULI 4
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
; nibble divisor 16**(mapY mod 16)) and WADDR0 (the packed quarter-column's slot,
; MAPB + 4*mapX + mapY/16). The DDA then maintains PW/WADDR *incrementally*, so
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
        ST  TMP2            ; the quarter-column selector, mapY / 16
        LD  POSX
        DIVI {UNITS}
        MULI 4
        ADD TMP2
        ADDI MAPB
        ST  WADDR0          ; the packed quarter-column's tape slot
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
        ; records S4X = 4*stepX instead of stepX
sidex:  LD  RDX
        BRN sxneg
        LDI 4
        ST  S4X             ; stepX = 1 -> the quarter-column slot moves +4
        LDI {UNITS}
        SUB FRACX
        MUL DDX
        DIVI {UNITS}
        ST  SDX             ; sideDistX = (1024 - fracX) * deltaDistX / 1024
        JMP sidey
sxneg:  LDI 0
        SUBI 4
        ST  S4X             ; stepX = -1 -> -4
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
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity{k}
ywrd{k}:  LDI {16 ** 15}
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity{k}
hity{k}:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ {nxt}            ; empty -> {nxt_note}
        JMP why             ; a y-side wall: t is dark
xarm{k}:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ {nxt}            ; empty -> {nxt_note}
        JMP whx             ; an x-side wall: t is sunlit""".splitlines()
    lines += f"""
; Which arm found the wall picks the whole tail — and, since V3, the texture
; stripe: the parity of the map coordinate ALONG the wall face, read straight
; off the incremental lookup state. x-side: mapY & 1 is (PW % 17) / 16
; (16 = -1 mod 17, so 16^k % 17 is 1 or 16), inverted so a sunlit face and a
; neighbouring shadow face keep their corner contrast.
whx:    ST  COLOR           ; the wall type t — the dark base
        LD  PW
        MODI 17
        DIVI 16
        ST  TMP             ; mapY & 1
        LDI 1
        SUB TMP
        ST  TMP             ; stripe = 1 - (mapY & 1)
        LD  SDX
        SUB DDX
        ST  PERP
        JMP pclip
why:    ST  COLOR           ; y-side: stripe = mapX & 1 = (WADDR - 1) / 4 % 2
        LD  WADDR
        SUBI 1
        DIVI 4
        MODI 2
        ST  TMP
        LD  SDY
        SUB DDY
        ST  PERP
pclip:  SUBI 1              ; ST preserved ACC = perpWallDist
        BRN pone
        JMP nearck
pone:   LDI 1
        ST  PERP
; distance shading + the panel stripe (V3): COLOR steps up to the bright
; variant t + 8 exactly when the wall is NEAR (perp < {NEAR_D}) and this
; column's stripe bit is set; a far wall keeps the dark base whatever its face
nearck: LD  PERP
        SUBI {NEAR_D}
        BRN strck
        JMP lineh           ; far: the dark base stands
strck:  LD  TMP
        BRZ lineh           ; the dark panel of the stripe pair
        LD  COLOR
        ADDI 8
        ST  COLOR
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
        BRZ gun             ; that was column {WIDTH - 1}: the viewport is sent
        JMP colset

; ── the pistol (V4): ONE command word — the unit bakes both sprites ──────────
gun:    LD  FIRE
        BRZ gidle
        LDI C_GUNF
        SND                 ; the recoil frame, muzzle flash blooming above
        JMP hud
gidle:  LDI C_GUN
        SND                 ; the idle pistol, bottom-centre
""".splitlines()
    curs = codes["CURS"]
    runc = codes["RUN"]
    bg = hud_bg_runs()
    lines += [
        "",
        f"; ── HUD (V4): cursor to slot {H3D * WIDTH}, the background as {len(bg)} pre-encoded RUN",
        "; words (hud_bg_runs(): bezel, base field, the static blue armor block),",
        "; then the LIVE bars over it — red health rows "
        f"{HEALTH_BAR_ROWS[0]}..{HEALTH_BAR_ROWS[1]} (1px per 4), yellow",
        f"; ammo rows {AMMO_BAR_ROWS[0]}..{AMMO_BAR_ROWS[1]} (1px per 2), both from column {BAR_COL}; an empty bar",
        "; sends nothing and the background shows through",
        f"hud:    LDI {8 * H3D * WIDTH + curs}",
        "        SND                 ; CURS: the panel cursor to the strip's top-left",
    ]
    for colour, count in bg:
        lines += [
            f"        LDI {8 * (count * 16 + colour) + runc}",
            f"        SND                 ; RUN {count} x colour {colour}",
        ]
    hb1 = HEALTH_BAR_ROWS[0] * WIDTH + BAR_COL
    hb2 = HEALTH_BAR_ROWS[1] * WIDTH + BAR_COL
    ab1 = AMMO_BAR_ROWS[0] * WIDTH + BAR_COL
    ab2 = AMMO_BAR_ROWS[1] * WIDTH + BAR_COL
    lines += f"""
        LD  HEALTH
        DIVI 4
        ST  TMP             ; the health bar in pixels
        BRZ abar
        LDI {8 * hb1 + curs}
        SND                 ; CURS: row {HEALTH_BAR_ROWS[0]}, column {BAR_COL}
        LD  TMP
        MULI 16
        ADDI {AMMO_COLOR}
        MULI 8
        ADDI C_RUN
        ST  TMP2            ; the bar's RUN word — reused for its second row
        SND
        LDI {8 * hb2 + curs}
        SND
        LD  TMP2
        SND
abar:   LD  AMMO
        DIVI 2
        ST  TMP             ; the ammo bar in pixels
        BRZ cmit            ; clip empty: no bar at all
        LDI {8 * ab1 + curs}
        SND
        LD  TMP
        MULI 16
        ADDI {FACE_COLOR}
        MULI 8
        ADDI C_RUN
        ST  TMP2
        SND
        LDI {8 * ab2 + curs}
        SND
        LD  TMP2
        SND

; ── the commit: one command word ─────────────────────────────────────────────
cmit:   LDI C_COMMIT
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
        from randomfun2026solvers.lm1.display import frames_from_writes
        from randomfun2026solvers.lm1.emulator import Emulator, Round

        self._em = Emulator(programs.load("deadman-3d"))
        self._words: list[int] = list(preamble_words()) + title_words()
        # Round 0: boot + the title screen, up to the first blocking IN.
        res = self._em.run(
            [Round(input=tuple(self._words))], max_instructions=5_000_000
        )
        assert res.reason == "input-exhausted", res.reason
        self.frames = 1  # the title frame, committed in round 0
        self.title = frames_from_writes(
            self._em.display_writes, width=WIDTH, height=HEIGHT
        )[-1]

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


def _title_text(player: "_MachinePlayer | None") -> str:
    """The title screen as terminal text — the machine's own frame when live."""
    frame = title_frame() if player is None else player.title
    return _ansi_frame(frame) + "\ntitle  [w/s move  a/d turn  space fire  q quit]"


def _play_script(script: str, golden: bool) -> None:
    """Render each key's frame to stdout exactly as --play would (for tests)."""
    player = None if golden else _MachinePlayer()
    print(_title_text(player))
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
        # The demo opens on the title screen; the first keypress starts play.
        print(f"\x1b[H{_title_text(player)}", flush=True)
        while True:
            while True:
                ch = sys.stdin.read(1)
                if ch in ("q", "\x03"):
                    return
                if ch in ("w", "a", "s", "d", " "):
                    code = keys(ch)
                    break
            t0 = time.perf_counter()
            state, text = _play_frame(player, state, code)
            ms = (time.perf_counter() - t0) * 1000
            print(f"\x1b[H{text}  {ms:.0f} ms", flush=True)
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
        _write_pngs([title_frame()] + frames, args.png)  # frame-00 is the title
        print(f"wrote {len(frames) + 1} PNGs to {args.png}")
        return
    print("\n\n".join("\n".join(frame) for frame in frames))


if __name__ == "__main__":
    main()
