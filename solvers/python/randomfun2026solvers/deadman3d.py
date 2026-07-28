#!/usr/bin/env python3
"""deadman-3d: the golden model for the first-person raycaster demo on the LM-1 CPU.

This module is the **single source of truth** for the deadman-3d demo: an
integer transliteration of lodev.org's ``raycaster_flat.cpp``
(https://lodev.org/cgtutor/raycasting.html), rendered 64x48 on the LM-75 —
3D viewport rows 0..39, HUD strip rows 40..47.  The generated LM-1 assembly
must match this model **pixel for pixel**; every constant, table, and
expression here is written the way the asm computes it.

The map is **Freedoom Phase 1's E1M1** — real level geometry, not a homage:
``randomfun2026solvers/wadimport.py`` parses the map's WAD lumps
(github.com/freedoom/freedoom at commit ``d14dbbe``, BSD licence), supercovers
every long one-sided linedef onto the 64x64 grid, closes the map watertight,
and colours each wall cell from its dominant sidedef texture (see the credits
section and ``wadimport``'s own docstring for the pipeline).  The player
spawns at the level's real THINGS start in the west start hall facing east,
walks the striped BASE2 hall, turns north up the brown concrete corridor into
the great central cavern with its green MCSTAT screens and slime fall; the
south wing holds the tiled corridors, the blue WFALL waterfall and the
big-door exit lobby.  Walls render two cells tall (``WALL_H``) and a move is
two cells (``MOVE_NUM``): the finer grid keeps the 32x32 demo's proportions
and pace.  The same importer builds a **local** premium map from a retail
DOOM shareware IWAD (``--wad``; outputs stay in the git-ignored
``littleman/examples/local/`` — nothing IWAD-derived is committed).

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
printed row ``p`` holds the cells with ``y = 63 - p``, i.e. ``map_cell(x, y)``
uses x east, y north (y grows *up* the printed page).  Headings are
``h * 22.5``° counterclockwise from east: ``dirX = round(1024*cos)``,
``dirY = round(1024*sin)`` — heading 0 = east, 4 = north, so ``+1`` heading is
a **left** turn, exactly as the A key promises.

The camera plane must point to the *player's right* (cameraX = +1 is the right
screen edge, and rayDir = dir + plane*cameraX/1024), which in this y-up frame
is dir rotated -90°: ``(dy, -dx)``.  Hence

    planeX = round(675.84 * sin(h * 22.5°))     # 675.84 = 0.66 * 1024
    planeY = round(-675.84 * cos(h * 22.5°))

Evidence this is the non-mirrored sign, exactly as in Freedoom's E1M1: the
walk's finale stands at (45, 46) facing **north** at the slime fall, and the
ZIMMER gold-brown rock west of the fall (cells (40..41, 48..49), family 3) —
the player's *left* — renders in the **left** half of the frame, the bright
green fall filling the centre-right (the finale pin).  Flipping the plane
sign mirrors it (that is R10's failure mode).

Wall types and the display palette
----------------------------------
Map nibbles are the wall type t in 1..7 (0 = empty; <= 7 keeps every packed
map word under 2**63).  Shading: a side==0 (x-side, sunlit) hit paints
``t + 8`` (the ANSI bright variant), a side==1 hit paints ``t``.  Floor is 8,
ceiling 0 (black).

The LM-75 palette in this repo is the **ANSI** 16-colour set (see
``lambda_deadman.py`` and ``PALETTE`` below), *not* CGA, and each wall type is
the quantized hue of the cell's dominant Freedoom texture (``wadimport``'s
family step — gray under the CIELAB chroma gate, else the nearest ANSI hue):

    7 gray metal and concrete (BASE2, the AQMETL set, STARGR, SHAWN, COMP*)
    3 brown rock and tile (ZIMMER3, BROWN*, AQCONC05, AQTILE01, MCSTAT8)
    2 green: the MCSTAT computer screens and the SFALL slime fall
    4 blue: the WFALL waterfall in the south nukage room

Families 1, 5 and 6 are unused by Freedoom E1M1's texture set but remain
legal wall types (a ``--wad`` import may produce them).  The HUD block
colours are ANSI too: ammo red = 9, face yellow = 11, armor blue = 12.

Tape slot map (the asm's .equ table; slot 0 is scratch)
-------------------------------------------------------
``preamble_words()`` yields the boot data in exact tape order, slots 1..359:

    MAPB    1..256  packed quarter-columns: word ``4x + q`` holds cells
                    (x, 16q .. 16q+15), nibble ``y mod 16`` each
    POWB  257..272  16**y for y = 0..15
    HDGB  273..288  packed headings, one word per heading h:
                    (dirX+1024)*2^36 + (dirY+1024)*2^24
                    + (planeX+1024)*2^12 + (planeY+1024)   (48 bits, positive)
    NUKB  289..352  the nukage bit plane (M5): word x holds column x, bit y —
                    1 = damage floor; bit 63 is structurally 0 (border wall)
    POSX  353       spawn posX = 5632   (cell 5, Q10 centre)
    POSY  354       spawn posY = 27136  (cell 26)
    HDG   355       spawn heading = 0   (east — Freedoom E1M1's real facing)
    DIRX  356       spawn dirX  = 1024      DIRY  357   spawn dirY   = 0
    PLANEX 358      spawn planeX = 0        PLANEY 359  spawn planeY = -676

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
The demo opens on **Freedoom's own title art**: :data:`TITLE_HEX_ROWS` is
``graphics/titlepic/titlepic.png`` (commit ``d14dbbe`` — the serpent-demon
mascot and the armored fighter under a red sky) quantized to 64x48 by
``wadimport.quantize_title`` — the repo's block-Lab method (per target block,
the ANSI-16 colour minimizing the summed CIELAB distance, as
``lambda_deadman.HEX_ROWS`` was made) after a fixed x1.6 brightness lift for
the dark source, plus one isolated-dot despeckle pass.
It travels as its own row-major RLE — :func:`title_runs` — and each run is ONE
pre-encoded command word for the DOOM unit's RUN arm (:func:`title_words`,
``8*(count*16 + colour) + C_RUN``): the CPU forwards each word untouched
(``IN``/``SND``) and the unit paints ``count`` pixels of ``colour`` at the
panel's own auto-advancing cursor. Round 0 is the preamble, the title words,
and the title COMMIT — one frame, no gameplay command; the walk starts in
round 1.

Input protocol
--------------
Round 0 = the data preamble (:func:`preamble_words`, 359 words) + the title
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
Spawn view (no-op) at Freedoom E1M1's real start: east down the striped
start hall.  Ten steps east; a 90° left turn and seven steps north up the
brown concrete corridor into the great cavern's south-west lobe; back right
to east and two steps onto the cavern floor, where a half-look right FIREs
at the sunlit south rim; six steps east across the cavern, the ZIMMER cliffs
and green MCSTAT screens growing ahead; a quiet half-look right at the north
rim; two steps on, a 90° left turn to north, and two steps toward the slime
fall — then a *firing* step (the ``"w "`` chord — the MUX at work) and one
last FIRE standing before the bright green fall.  Two-cell steps (DOOM's run
on the 64x64 grid); 50 words, spelled ``WALK_CHORDS``.

``deadman3d_source()`` emits the LM-1 assembly lowered from this model;
``tape_slots()`` is its ``.equ`` table (the docstring's slot map plus the
scalars, numbered consecutively from 360).

Art credits
-----------
The level, the title screen and the pistol are all derived from the
**Freedoom** project (https://github.com/freedoom/freedoom, also
https://freedoom.github.io/), fetched at commit ``d14dbbe``:

* the map — Phase 1's ``levels/e1m1.wad`` imported by
  ``randomfun2026solvers/wadimport.py`` (geometry supercovered onto the
  64x64 grid; wall families are the quantized hues of the level's own
  sidedef textures, composited from ``lumps/textures/textures.cfg`` +
  ``patches/*.png``);
* the title screen — ``graphics/titlepic/titlepic.png`` quantized to the
  ANSI-16 palette at 64x48 (block-Lab, x1.6 brightness lift, despeckle);
* the pistol sprites (:data:`GUN_IDLE`, :data:`GUN_FIRE`) —
  ``sprites/pisga0.png`` (idle) and ``sprites/pisfa0.png`` (muzzle flash),
  quantized at 11x10;
* the status-bar face (M5: :data:`FACE_HEALTHY`/:data:`FACE_HURT`/
  :data:`FACE_BLOODY`/:data:`FACE_GRIM`) — ``graphics/stfst00.png``,
  ``stfst20.png``, ``stfst40.png`` and ``stfevl0.png``, face-core-cropped
  and quantized at 10x6 by ``wadimport.face_tables``;
* the damage floors (:data:`NUKAGE_STR`) — the level's own SECTORS lump
  (specials 4/5/7/16), region-resolved by ``wadimport``'s flood fill.

Freedoom content is distributed under its BSD-style licence (see the
project's COPYING.adoc), which permits use and modification with
attribution; this credit also rides the generated machine's debug sidecar
(the ``stream:unit`` region note).  No assets from the original DOOM game
are committed anywhere in this demo — the committed art stack is entirely
Freedoom-derived plus procedural rendering.

``--wad`` (Mode B, local only) imports a locally owned retail IWAD's E1M1 and
TITLEPIC instead; everything derived from it stays in the git-ignored
``littleman/examples/local/`` and no test depends on the IWAD existing::

    # build the full local artifact set (level bundle, asm, men-v3 + taped
    # machines with sidecars, cases, input, PNGs) into littleman/examples/local/
    python -m randomfun2026solvers.deadman3d --wad ~/DOOM1.WAD --build

    # play the real E1M1 live on the machine (or --golden for the model)
    python -m randomfun2026solvers.deadman3d --wad ~/DOOM1.WAD --play
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
    "NUKAGE_STR", "NUKE_DAMAGE", "FLOOR_NUKE", "nukage_words", "nukage_cell",
    "FACE_HEALTHY", "FACE_HURT", "FACE_BLOODY", "FACE_GRIM", "face_for",
    "install_level", "install_art",
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
    "Round 0: the 359-word data preamble (deadman3d.preamble_words()) then the "
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


# ── the map (Freedoom Phase 1 E1M1 at 64x64, north at the top) ───────────────
MAP_SIZE = 64
#: ``.`` = empty, hex nibble = wall type 1..7.  Printed row p is y = 63 - p.
#: GENERATED by ``randomfun2026solvers/wadimport.py`` from Freedoom's
#: ``levels/e1m1.wad`` at commit ``d14dbbe`` (real level geometry: every
#: one-sided linedef >= 32 map units supercovered onto the grid, unreachable
#: cells filled solid; see that module for the whole pipeline).  The rooms,
#: west to east: the start hall at y 25..27 (striped BASE2 metal, 7), the
#: north-west computer wing with its four-pillared hall above it, the brown
#: concrete corridor at x=25 north into the great central cavern (ZIMMER rock
#: 3 and gray metal 7 rimmed with green MCSTAT screens 2), the slime fall 2 on
#: its north rim, and the south wing below: tiled corridors, the blue
#: waterfall 4 (WFALL1) down the nukage room's west wall, and the big-door
#: exit lobby at the south edge.  Wall families are the textures' quantized
#: hues — the table rides ``wadimport``'s ``families.txt`` output.
MAP_STR = """\
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
77777777777777777777777777777777777777777777777777777777.....777
77777777777.777.777.777.777777777777777777777777777377........77
77777777777.777.777.777.7777777777777777777777733333..........77
77777777777.777.777.777.777777777777777777773333..............77
77777777...................777777777777777333.................77
77777777...................7777777777777773.....333333........77
77777777...................7777777777777733...233777733.......77
77777777...77777....777....777777777777773....2777777733......77
7777777777.777777777777....777777337777733...22777777773......37
7777777..........333777....77777333333333.....2773323773......37
7777777.............777....777772.............2273..3773......37
7777777.............777....77777...............233.33773.....337
7777777......777....77737.373777...................37773.....377
7777777777777777....777.....3.....................337733.....377
7777777777777777...37.......3.....................22223......377
777777777777773..33373......3.......................22......3377
777777777777773..333737.....................................3777
777777777777773.....777....................................33777
777777777777777.....73.....................................37777
777777777777777...........................................337777
777777777777773.....73...................................3377777
777777777777773.....737.................................22777777
77777777777777777777777.................................27777777
777777777777777777777.......3...........................27777777
777777777777777777777.......3..........................227777777
77777777777777777777777.....3.........................2277777777
7777777777777777777777733.333777.............7.77..2222777777777
7777777777777777777777777.777777.............7.77.22777777777777
7777777777...777777777.33.3.7777..........222....227777777777777
77777...77..................7777722333....2722..2277777777777777
77777.......................77..77777333..37722.2777777777777777
77777...........................777777733337772.2777777777777777
77777...........................77777777777777777777777777777777
77777...77......................77777777777777.....7777777777777
77777777777.................77..77777777777777.....7777777777777
7777777777777777.......7777.777777777777777777.....7777777777777
777777777777777777777777777.7777777777777.7777.....7777777777777
777777777777777777777777777.7777777777777.7777.....7777777777777
777777777777777777777777777.7777333377777.7777.....7777777777777
77777777777777777774444444.............37.7777.....7777777777777
77777777777777777744.....7.........................7777777777777
7777777777777777774................................7777777777777
7777777777777777744................................7777777777777
777777777777777774.................................7777777777777
777777777777777774.................................7777777777777
777777777777777774.....................37.7777777777777777777777
77777777777777777477...................37.7777777777777777777777
7777777777777777777777.................37.7777777777777777777777
7777777777777777777777.................3777777777777777777777777
7777777777777777777777..7..........77733777777777777777777777777
7777777777777777777777..7..........77777777777777777777777777777
7777777777777777777777..7.77777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
7777777777777777777777777777777777777777777777777777777777777777
"""

_PRINTED_ROWS = MAP_STR.splitlines()
assert len(_PRINTED_ROWS) == MAP_SIZE and all(len(r) == MAP_SIZE for r in _PRINTED_ROWS)

#: The damage floors (M5): ``N`` marks an open cell standing on a nukage
#: sector — same orientation as ``MAP_STR``. GENERATED by ``wadimport``'s
#: region flood fill from Freedoom E1M1's SECTORS lump (specials 4/5/7/16,
#: the damage-floor family): the slime moat around the great cavern's fall,
#: 112 cells. The plane rides the tape as its own 64-word 1-bit-per-cell
#: plane (``NUKB``) because the map words cannot carry it: an 8th nibble
#: value at nibble 15 is ``8 * 16**15 == 2**63`` — past the signed word —
#: and any nonzero nibble would read as a wall to the DDA's ``t > 0`` hit
#: test anyway. Bit 63 of a plane word would be the same overflow, but row
#: y=63 is always border wall (asserted in :func:`nukage_words`).
NUKAGE_STR = """\
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
...............................................N................
............................................NNNNNN..............
...........................................NNNNNNN..............
..................................NNNN.....NNNNN................
.................................NNNNNNN.....NN.................
................................NNNNNNNN.....NN.................
................................NNNNN...N....NN.................
.................................NNNN...NNNN.NN.................
................................NNNNNNNNNNNNNN..................
.................................NNNNNNNNNNN....................
.................................NNNNNNNNNNN....................
..................................NNNNNNNN......................
...................................NNNNNNN......................
.....................................N..........................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
................................................................
"""

_NUKE_ROWS = NUKAGE_STR.splitlines()
assert len(_NUKE_ROWS) == MAP_SIZE and all(len(r) == MAP_SIZE for r in _NUKE_ROWS)

#: Standing on nukage costs this much health per frame (floor 0 — no death
#: mechanics yet, the bar is just empty at 0).
NUKE_DAMAGE = 5
#: A nukage cell's floor paints green (ANSI 2) instead of the gray 8.
FLOOR_NUKE = 2


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


def nukage_words() -> list[int]:
    """The 64-word nukage bit plane: word ``x`` holds column x, bit ``y``.

    One bit per cell keeps the whole plane 64 words (a nibble plane would be
    256); bit 63 must never be set (``2**63`` is past the signed word), which
    holds structurally — row 63 is always border wall.
    """
    words = []
    for x in range(MAP_SIZE):
        word = 0
        for y in range(MAP_SIZE):
            if _NUKE_ROWS[MAP_SIZE - 1 - y][x] == "N":
                assert _grid_cell(x, y) == 0, f"nukage on a wall cell {(x, y)}"
                assert y < 63, f"nukage on the border row at {(x, y)} (bit 63 overflows)"
                word += 2 ** y
        assert 0 <= word < 2 ** 63
        words.append(word)
    return words


_NUKE_WORDS = nukage_words()

#: The asm's divisor ladder for the low two bits of the bit index: bit y of a
#: plane word is ``word / 16**(y/4) / 2**(y mod 4) mod 2``, and ``2**(y mod
#: 4)`` is picked by a 4-way branch (there is no POW2 table on the tape).
_POW2 = (1, 2, 4, 8)


def nukage_cell(x: int, y: int) -> int:
    """The nukage bit exactly as the asm reads it: the plane word ``NUKB + x``
    shifted down by ``POWB + (y / 4)`` (a 16**k divisor), then the 2**(y mod 4)
    ladder, then ``MODI 2``."""
    t = div(_NUKE_WORDS[x], POW16[div(y, 4)])
    return sign_mod(div(t, _POW2[sign_mod(y, 4)]), 2)


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


#: Freedoom E1M1's real player-1 start (THINGS type 1: map units (-416, 256),
#: angle 0): the west start hall, facing east — cell (5, 26), heading 0.
SPAWN = State(posX=5632, posY=27136, heading=0)


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
        # A move is two cells (MOVE_NUM), so each axis checks the HALF-way cell
        # too — real level geometry has one-cell walls, and a destination-only
        # check would tunnel straight through them (the asm mirrors this:
        # ``DIVI 2`` on the axis delta, the same inlined lookup twice).
        deltaX = div(dirX * s * MOVE_NUM, MOVE_DEN)
        newX = posX + deltaX
        midX = posX + div(deltaX, 2)
        if map_cell(div(midX, UNITS), div(posY, UNITS)) == 0 \
                and map_cell(div(newX, UNITS), div(posY, UNITS)) == 0:
            posX = newX
        deltaY = div(dirY * s * MOVE_NUM, MOVE_DEN)
        newY = posY + deltaY
        midY = posY + div(deltaY, 2)
        if map_cell(div(posX, UNITS), div(midY, UNITS)) == 0 \
                and map_cell(div(posX, UNITS), div(newY, UNITS)) == 0:
            posY = newY
    return State(posX, posY, heading)


def fire_bit(cmd: int) -> bool:
    """The FIRE key, exactly as the asm decodes it: bit 4 of the word."""
    return sign_mod(div(cmd, 16), 2) == 1


#: The pistol (V5) — derived from the **Freedoom** project's pistol sprites
#: (https://github.com/freedoom/freedoom, commit d14dbbe: ``sprites/pisga0.png``
#: for the idle gun, ``sprites/pisfa0.png`` for the muzzle flash; BSD-style
#: licence, see the module credits below), hand-quantized into the ANSI-16
#: palette with the title screen's block-Lab method at 11x10 and tone-flipped
#: for the panel (the floor is colour 8, so the slide body maps to 7 with its
#: dark face 8/0 *inside* the outline). Encoding: ``(viewport row, first
#: column, colours)`` runs of hex digits — 0 outline/openings, 7 slide, 8
#: shade/grip, 1 the three red detail dots, 3 the tan hand, 9/b/f the muzzle
#: bloom — a busy row splits into two runs so each fits the GUN arm's descent
#: windows (d3_unit's lead/body limits). The DOOM unit bakes exactly these
#: runs and the CPU sends ONE command word per frame; ``GUN_FIRE`` is the
#: recoil variant — the gun a row higher with the pisfa0 bloom above it —
#: sent when FIRE is held.
GUN_IDLE: list[tuple[int, int, str]] = [
    (30, 32, "7"),
    (31, 31, "770"),
    (32, 30, "77770"),
    (33, 29, "7700007"),
    (34, 29, "710101"),
    (34, 35, "7"),
    (35, 29, "7777770"),
    (36, 28, "77000077"),
    (37, 28, "33088033"),
    (38, 27, "0333333338"),
    (39, 27, "033333388"),
    (39, 36, "0"),
]
GUN_FIRE: list[tuple[int, int, str]] = [
    (25, 31, "9bb9"),
    (26, 30, "bffffb"),
    (27, 29, "3bffff"),
    (27, 35, "b3"),
    (28, 31, "9ff9"),
] + [(r - 1, c, colors) for r, c, colors in GUN_IDLE]

#: The live HUD's scalars (V4): ammo starts full and drops one per shot down
#: to an empty clip; health starts full and (M5) nukage floors drain it 5 a
#: frame, floor 0. The bars paint 2 rows each over the baked background: red
#: health rows 41..42 from column 4, one pixel per 4 health; yellow ammo rows
#: 44..45, one per 2 ammo.
AMMO_START = 50
HEALTH_START = 100
BAR_COL = 4
HEALTH_BAR_ROWS = (41, 42)
AMMO_BAR_ROWS = (44, 45)

#: The status-bar face (M5): 10x6 in the HUD field rows 41..46, columns
#: 33..42 — clear of the bars (4..28) and the armor block (50..58). Derived
#: from the **Freedoom** project's status-bar faces (commit d14dbbe:
#: ``graphics/stfst00.png`` healthy, ``stfst20.png`` hurt, ``stfst40.png``
#: bloodied, ``stfevl0.png`` the firing grimace; BSD-style licence, see the
#: module credits), GENERATED by ``wadimport.face_tables`` — the face core
#: (brow to chin) composited onto the field gray 8 and block-Lab quantized
#: at a x1.4 brightness lift. Encoding: one ``(panel row, first column,
#: colours)`` run per face row, painted by the CPU as one CURS plus RLE RUN
#: command words per frame — no unit arm, so no descent-window budget.
#: The variant is picked per frame: the grimace on FIRE frames, else by the
#: HEALTH band (> 66 healthy, > 33 hurt, else bloodied).
FACE_ROW, FACE_COL, FACE_W, FACE_H = 41, 33, 10, 6
FACE_HEALTHY: list[tuple[int, int, str]] = [
    (41, 33, "0008800000"),
    (42, 33, "0833337380"),
    (43, 33, "0337737830"),
    (44, 33, "8333333f38"),
    (45, 33, "8833773388"),
    (46, 33, "8808338088"),
]
FACE_HURT: list[tuple[int, int, str]] = [
    (41, 33, "0000000000"),
    (42, 33, "0833337880"),
    (43, 33, "0339733830"),
    (44, 33, "8333333f38"),
    (45, 33, "8833393388"),
    (46, 33, "8808331088"),
]
FACE_BLOODY: list[tuple[int, int, str]] = [
    (41, 33, "0000000000"),
    (42, 33, "0000308000"),
    (43, 33, "8033f33130"),
    (44, 33, "8393337933"),
    (45, 33, "8193313938"),
    (46, 33, "8813991988"),
]
FACE_GRIM: list[tuple[int, int, str]] = [
    (41, 33, "8880000000"),
    (42, 33, "0000800000"),
    (43, 33, "8083333830"),
    (44, 33, "8377337738"),
    (45, 33, "8833773388"),
    (46, 33, "8883773888"),
]


def face_for(health: int, fire: bool) -> list[tuple[int, int, str]]:
    """Which face this frame paints, exactly as the asm branches: FIRE wins,
    else the HEALTH band (> 66 / > 33 / the rest)."""
    if fire:
        return FACE_GRIM
    if health > 66:
        return FACE_HEALTHY
    if health > 33:
        return FACE_HURT
    return FACE_BLOODY


# ── the renderer (lodev raycaster_flat.cpp, in Q10) ──────────────────────────
def render(state: State, *, fire: bool = False,
           ammo: int = AMMO_START, health: int = HEALTH_START,
           nukage: bool = False) -> list[str]:
    """One frame: 48 rows of 64 hex chars (rows 0..39 the 3D view, 40..47 HUD).

    The pistol (:data:`GUN_IDLE`, or :data:`GUN_FIRE` when ``fire``) paints
    over the finished columns — the golden twin of the machine's one GUN/GUNF
    command word per frame — and the HUD carries the live bars for ``ammo``
    and ``health`` plus the banded status face. With ``nukage`` (the player's
    cell stands on a damage floor) every column's floor run paints
    :data:`FLOOR_NUKE` green instead of the gray 8 — DOOM's palette-shift
    homage, and exactly what the machine's per-column green overlay COL word
    repaints (colour 2 is mask-invariant: ``2 & 7 == 2 & 15 == 2``).
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
        floor_c = FLOOR_NUKE if nukage else 8
        cols.append([0] * drawStart + run + [floor_c] * (H3D - 1 - drawEnd))
    for r, c, colors in (GUN_FIRE if fire else GUN_IDLE):
        for i, ch in enumerate(colors):
            cols[c + i][r] = int(ch, 16)
    rows = ["".join("%x" % cols[x][y] for x in range(WIDTH)) for y in range(H3D)]
    return rows + hud_rows(health, ammo, fire)


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


def hud_rows(health: int = HEALTH_START, ammo: int = AMMO_START,
             fire: bool = False) -> list[str]:
    """Rows 40..47: the background plus the live bars and the status face,
    exactly as the machine paints them — red health rows 41..42 (one pixel per
    4 health), yellow ammo rows 44..45 (one per 2 ammo), both from column 4
    (an empty bar sends no RUN at all, so the background shows through), then
    the :func:`face_for` variant's six rows."""
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
    for r, c, colors in face_for(health, fire):
        for i, ch in enumerate(colors):
            rows[r - H3D][c + i] = int(ch, 16)
    return ["".join("%x" % c for c in row) for row in rows]


# ── the demo walk ────────────────────────────────────────────────────────────
#: One chord per frame, each encoded by :func:`keys`.  The beats: hold the
#: spawn view down the start hall; ten ``w`` east along it, the striped BASE2
#: columns sliding by; four ``a`` to face north and seven ``w`` up the brown
#: concrete corridor at x=25 (three of them past its mouth into the cavern's
#: south-west lobe); four ``d`` back to east and two ``w`` into the great
#: cavern proper; ``d``, FIRE, ``a`` — a half-look right at the sunlit south
#: rim, the shot lighting the muzzle; six ``w`` east across the cavern floor,
#: the ZIMMER cliffs and green MCSTAT screens far ahead; ``d``, hold, ``a`` —
#: the quiet half-look at the north rim; two ``w`` on to x=45, four ``a``
#: round to north, two ``w`` INTO the slime moat that rings the fall (M5:
#: the floor floods green and health drains 5 a frame, the red bar visibly
#: shrinking); three held beats standing in the slime, the fall dead ahead,
#: the face degrading to bloodied; then a *firing* step OUT of the moat
#: (``"w "`` — the MUX at work) and one last FIRE standing clean before the
#: bright green fall.  The cavern crossing at frames 32..35 already forded
#: the moat's west lobe, so the bar drains in two episodes — 14 nukage
#: frames in all, health 100 -> 30.  Two-cell steps on the 64x64 grid; 53
#: words, spelled ``WALK_CHORDS``.
WALK_CHORDS: list[str] = (
    ["."] + ["w"] * 10 + ["a"] * 4 + ["w"] * 7 + ["d"] * 4 + ["w"] * 2
    + ["d", " ", "a"] + ["w"] * 6 + ["d", ".", "a"] + ["w"] * 2 + ["a"] * 4
    + ["w"] * 2 + ["."] * 3 + ["w ", " "]
)

#: The command words the demo feeds the machine: ``WALK_CHORDS`` encoded.
WALK: list[int] = [keys(ch) for ch in WALK_CHORDS]


# ── the title screen (round 0) ───────────────────────────────────────────────
#: Freedoom's own title art: ``graphics/titlepic/titlepic.png`` at commit
#: ``d14dbbe``, GENERATED by ``wadimport.quantize_title`` — the block-optimal
#: Lab-distance method at the full panel size (as ``lambda_deadman.HEX_ROWS``
#: was made) after a x1.6 brightness lift, plus one isolated-dot despeckle
#: pass. One hex digit per pixel (ANSI palette index), row-major.
TITLE_HEX_ROWS: list[str] = [
    "1111111111111111111111111111111111111111111111111111111111111111",
    "1111111111111111111111111111111111111111111111111111111111111111",
    "1111111111111111111111111111111111111111111111111111111111111111",
    "1111111111111111111111111111111111111111111111111111111111111111",
    "1111111111111111111111111111111111111111111111111111111111111111",
    "1111111111111111111111111111111111111111111111111111111111111111",
    "1111111111111144111111111111111111111111111111111111111111111111",
    "1111111111111114111111111111111111111111111111111111111111111111",
    "1111111111111115411111111111111111111111111111111111111111111111",
    "1111111451333333441111111111111111111111111111111111111111111111",
    "1111111443333333441111111111111111111111111111111111111111111111",
    "1111113444333333848111111111111111111111111111111111111111111111",
    "1111133344833333388811111111111111111111111111111111111111111111",
    "1111133344833883388811111111111111111111111111111111111119991111",
    "1111333384838bb8838311111111111111111111111111111111111119999911",
    "1111338483338bb8838411111111111111111111111111111111111111111911",
    "1111334443388833380441111111111111111111111111111111111111111111",
    "1111334483333333800149111111118888111111111111111111111111111111",
    "1111338383388308101194991111188888811111111111111111111111111111",
    "111118844488110011199419111888c444881111111111111111111111111111",
    "1111111444001111111114191888888448888881111111111111111111111111",
    "1111113440811111111111111882828888888888111111199111111111111111",
    "1111111848311111111111118882222882888888811111111911111111111111",
    "1111111840811111111111118882222222288888871111111911991111111111",
    "1111111980811111111111111888222222288888881111119911191111111111",
    "1111111938011111111111111388222222888888811111111111111111111111",
    "1111111911011111111111111138828822881833331111111111111111111111",
    "1111111111111111111111111133888888881133333111111111111111111111",
    "1111111111111111111111111773888888881133333111111111111111111111",
    "1111111111111111111113317778888888881113383111111111111111111111",
    "1111111111111111888119333788822888822118883111111111111111111111",
    "1111111111111118888883333881222288822238881111111111111111111111",
    "1111111111111118888888888111322888882233881111111111111111111111",
    "1111111111111111888888811111328883888333318111111111119999911111",
    "1111011111111111118888811111188881118833318111111111199991111111",
    "1100011111111111111881111111188881111888118111111111999991111111",
    "1100011111111111111111111111188881118888118111111119999111111111",
    "1110011111111801111111111111188881118888118111111111991111111111",
    "1110011111111001111111111111188888118888118111111111111111111111",
    "1110011111111101111111111111188888338888111111111111111111111111",
    "1118011111111081133331131113888888888888111111111119911111111111",
    "1110111111111138333111383388888888888888131111111199911111111111",
    "1110101111118881113133888888888888888888888811111119911111111111",
    "1110101111118883818388888888888888888888888883111111911111111111",
    "1111101111188888838888888888888888888888888883311111111111111111",
    "1111101111188888888888888888888888888888888888811111111111111111",
    "1111110133888888888888880888888888888888888888831331111111111111",
    "1111118388888888888888880088888888888888888888888888883111111111",
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
    """Round 0's data burst, in exact tape order (slots 1..359; see docstring).

    The nukage bit plane sits between the heading table and the spawn scalars
    so the boot loop's 8x-unrolled body covers slots 1..352 in exactly 44 laps
    and the straight-line tail is the seven *named* spawn scalars.
    """
    dirX, dirY, planeX, planeY = unpack_heading(_HDG_WORDS[SPAWN.heading])
    return (
        _MAP_WORDS
        + POW16
        + _HDG_WORDS
        + _NUKE_WORDS
        + [SPAWN.posX, SPAWN.posY, SPAWN.heading, dirX, dirY, planeX, planeY]
    )


def input_words(cmds: list[int]) -> list[int]:
    """Everything the program ever reads: the preamble, the title screen's RLE,
    then one word per command."""
    return preamble_words() + title_words() + list(cmds)


def frames_for_commands(cmds: list[int]) -> list[list[str]]:
    """Apply each command in turn and render after it — one frame per command.

    Threads the live counters exactly as the asm does: a FIRE with rounds left
    decrements ammo BEFORE the render (the decode ladder runs first), an empty
    clip dry-fires at 0 and still flashes; then the move lands, and standing
    on nukage costs :data:`NUKE_DAMAGE` health (floor 0) before the frame is
    drawn — the frame you take damage on already shows the green floor, the
    shorter bar and the degraded face.
    """
    state = SPAWN
    ammo = AMMO_START
    health = HEALTH_START
    frames = []
    for cmd in cmds:
        fire = fire_bit(cmd)
        if fire and ammo > 0:
            ammo -= 1
        state = step(state, cmd)
        nuk = nukage_cell(div(state.posX, UNITS), div(state.posY, UNITS)) == 1
        if nuk:
            health -= NUKE_DAMAGE
            if health < 0:
                health = 0
        frames.append(render(state, fire=fire, ammo=ammo, health=health, nukage=nuk))
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
#: The scalar tape slots after the boot data, numbered consecutively from 360.
#: (No paint cursor: the DOOM unit owns the panel, so the CPU keeps no ADDRV/AEND.)
_SCALARS = (
    "CMD", "XCOL", "CAMX", "RDX", "RDY", "SDX", "SDY",
    "DDX", "DDY", "S4X", "STPY", "PERP", "HALFH", "DSTART", "DEND",
    "COLOR", "PW", "WADDR", "FRACX", "FRACY", "PW0", "WADDR0",
    "TMP", "TMP2", "NEWX", "NEWY",
    "BW", "BS", "BA", "BD", "FIRE", "AMMO", "HEALTH", "NUKE", "PTR",
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

    Slots 1..359 are the boot data in ``preamble_words()`` order (see the
    module docstring); the scalars follow consecutively, so the machine's
    ``TAPE_SIZE`` is ``max(tape_slots().values()) + 1`` — an exactly-sized tape
    stalls silently (plan risk R6), which is why tests pin this.
    """
    slots = {
        "MAPB": 1, "POWB": 257, "HDGB": 273, "NUKB": 289,
        "POSX": 353, "POSY": 354, "HDG": 355, "DIRX": 356, "DIRY": 357,
        "PLANEX": 358, "PLANEY": 359,
    }
    for i, name in enumerate(_SCALARS):
        slots[name] = len(preamble_words()) + 1 + i
    return slots


def deadman3d_source() -> str:
    """The LM-1 assembly of the demo, lowered line for line from this model.

    Structure: boot loop (round 0's data preamble -> tape slots 1..359, the
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
    first_free = len(preamble_words()) + 1  # 360: the boot loop's stop address
    assert first_free == slots["CMD"], "the boot stop address is the first scalar"
    inv = UNITS * UNITS          # 1048576  — deltaDist numerator (1/rayDir, Q10*Q10)
    lh_num = WALL_H * H3D * UNITS  # 81920  — lineHeight numerator (two-cell walls)
    codes = DoomUnit.CODES       # the unit's trie codes; d3_unit pins these
    assert codes["COL"] == 0, "COL must be code 0: the column send is a bare MULI 8"

    equ_notes = {
        "MAPB": f"..{slots['MAPB'] + 255:<3} packed map quarter-columns: word 4x+(y/16), nibble y mod 16",
        "POWB": f"..{slots['POWB'] + 15:<3} 16**k — the nibble-extraction divisors",
        "HDGB": f"..{slots['HDGB'] + 15:<3} packed headings: base-4096 digits dirX dirY planeX planeY, biased +1024",
        "NUKB": f"..{slots['NUKB'] + 63:<3} the nukage bit plane: word x, bit y — 1 = damage floor (M5)",
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
        "HEALTH": f"live health: starts {HEALTH_START}, nukage -{NUKE_DAMAGE} a frame, floor 0",
        "NUKE": "1 when this frame stands on nukage: green floor, health drain",
        "PTR": "the boot loop's tape cursor",
    }
    lines = [
        "; deadman-3d — GENERATED from randomfun2026solvers/deadman3d.py, do not hand-edit.",
        "; Regenerate with:",
        ";   from randomfun2026solvers.deadman3d import deadman3d_source",
        ";   from randomfun2026solvers.lm1.programs import PROGRAM_DIR",
        ';   (PROGRAM_DIR / "deadman-3d.asm").write_text(deadman3d_source())',
        ";",
        "; lodev.org's raycaster_flat.cpp on the LM-1: Freedoom Phase 1's E1M1 (real",
        "; level geometry, imported from levels/e1m1.wad @ d14dbbe by wadimport.py),",
        "; walked first person at 64x48 on the LM-75 — one frame per input",
        "; word, and each word is a MUX of the keys held that frame: bit0 (1) W fwd,",
        "; bit1 (2) S back, bit2 (4) A left, bit3 (8) D right, bit4 (16) space FIRE",
        "; (muzzle-flash overlay); 0 idle, higher bits ignored. Turn first (A/D",
        "; cancel), then move along the new heading (W/S cancel), then render.",
        "; An ungraded demo — the slug borrows plotter's problem JSON for nothing",
        "; but registration; its 64x48 panel belongs to the DOOM unit (.unit doom,",
        "; lm1/d3_unit.py), its input is its own, and its 395-slot STORE rides the",
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
        "; POW16, the 16 packed heading words, the 64-word nukage bit plane and the",
        "; spawn state — deadman3d.preamble_words())",
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
        "; ── tape slots (deadman3d.tape_slots(); slots 1..359 are the boot data) ──────",
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

; ── title: Freedoom's title art (titlepic @ d14dbbe) — round 0's one frame ───
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
        "        ST  HEALTH          ; full health — nukage drains it (M5)",
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
; A move is {MOVE_NUM} cells, so each axis checks TWO cells — the half-way cell
; (delta DIVI 2, floored like the model's div) and the destination — real level
; geometry has one-cell walls, and a destination-only check would tunnel.
mvchk:  LD  BW
        SUB BS
        BRZ render          ; no net move: just render
        ST  TMP             ; s = +1 forward, -1 backward
        LD  DIRX
        MUL TMP
        MULI {MOVE_NUM}
        DIVI {MOVE_DEN}              ; deltaX = floor(dirX * s * {MOVE_NUM} / {MOVE_DEN})
        DIVI 2
        ADD POSX
        ST  NEWX            ; midX = posX + deltaX/2, the half-way cell
        ; collision X (half-way): map_cell(midX / 1024, posY / 1024), inlined
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
        LDA                 ; the packed quarter-column of midX's cell
        DIV PW
        MODI 16
        BRZ okx             ; half-way open -> check the destination
        JMP movey           ; wall -> posX unchanged
okx:    LD  DIRX
        MUL TMP
        MULI {MOVE_NUM}
        DIVI {MOVE_DEN}
        ADD POSX
        ST  NEWX            ; newX = posX + deltaX
        ; collision X (destination): map_cell(newX / 1024, posY / 1024)
        LD  NEWX
        DIVI {UNITS}
        MULI 4
        ADD TMP2            ; PW and the selector still hold posY's row
        ADDI MAPB
        LDA
        DIV PW
        MODI 16
        BRZ comx            ; empty -> commit posX
        JMP movey           ; wall -> posX unchanged
comx:   LD  NEWX
        ST  POSX
movey:  LD  DIRY
        MUL TMP
        MULI {MOVE_NUM}
        DIVI {MOVE_DEN}              ; deltaY
        DIVI 2
        ADD POSY
        ST  NEWY            ; midY = posY + deltaY/2
        ; collision Y (half-way): map_cell(posX / 1024, midY / 1024) — the UPDATED posX
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
        BRZ oky
        JMP render
oky:    LD  DIRY
        MUL TMP
        MULI {MOVE_NUM}
        DIVI {MOVE_DEN}
        ADD POSY
        ST  NEWY            ; newY = posY + deltaY
        ; collision Y (destination): map_cell(posX / 1024, newY / 1024) — PW/selector
        ; must be newY's own row, so the whole lookup is redone
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

; ── nukage (M5): the player's cell's bit of the 1-bit damage plane ───────────
; bit y of plane word NUKB+x is  word / 16**(y/4) / 2**(y mod 4) mod 2 — the
; high bits of the shift ride the POWB table (16**k == 2**4k), the low two
; come off a 4-way divisor ladder (there is no POW2 table on the tape).
; Standing on nukage: HEALTH -{NUKE_DAMAGE}, floor 0, and NUKE=1 makes every
; column's floor repaint green (the overlay COL words below).
render: LD  POSY
        DIVI {UNITS}
        ST  TMP             ; mapY = the plane word's bit index
        DIVI 4
        ADDI POWB
        LDA                 ; 16**(mapY / 4)
        ST  TMP2
        LD  POSX
        DIVI {UNITS}
        ADDI NUKB
        LDA                 ; the player's column's plane word
        DIV TMP2
        ST  NUKE            ; parked: the word shifted down 4*(mapY/4) bits
        LD  TMP
        MODI 4              ; the low two bits pick the 1/2/4/8 divisor
        BRZ nkm0
        SUBI 1
        BRZ nkm1
        SUBI 1
        BRZ nkm2
        LD  NUKE
        DIVI 8
        JMP nkbit
nkm1:   LD  NUKE
        DIVI 2
        JMP nkbit
nkm2:   LD  NUKE
        DIVI 4
        JMP nkbit
nkm0:   LD  NUKE
nkbit:  MODI 2
        ST  NUKE            ; 1 = this frame stands on a damage floor
        BRZ prolog          ; clean floor: no damage
        LD  HEALTH
        SUBI {NUKE_DAMAGE}
        BRN hzero
        ST  HEALTH          ; the red bar shrinks on this very frame
        JMP prolog
hzero:  LDI 0
        ST  HEALTH          ; floor 0: the bar empties, no death mechanics yet

; ── render: lodev's per-column raycast, columns 0..{WIDTH - 1} ──────────────────────
; The per-frame prologue: everything that depends only on the player's position
; is computed once — the fractional position, and the cell-lookup seeds PW0 (the
; nibble divisor 16**(mapY mod 16)) and WADDR0 (the packed quarter-column's slot,
; MAPB + 4*mapX + mapY/16). The DDA then maintains PW/WADDR *incrementally*, so
; the per-step lookup is LDA/DIV/MODI instead of the full 16-instruction unpack.
prolog: LD  POSX
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
        LD  NUKE
        BRZ colnxt          ; clean floor: the COL word's gray floor stands
        LD  DEND
        SUBI {H3D - 1}
        BRZ colnxt          ; wall to the bottom row: no floor to flood
        ; the green flood (M5): standing on nukage, a SECOND bare COL word
        ; repaints this column's floor run (rows drawEnd+1..{H3D - 1}) in
        ; {FLOOR_NUKE} — the unit needs no new arm: colour {FLOOR_NUKE} is
        ; mask-invariant ({FLOOR_NUKE} & 7 == {FLOOR_NUKE} & 15), the guard
        ; keeps its wall run nonempty, and its own floor lap count is 0
        LD  DEND
        ADDI 1
        MULI {WIDTH}
        ADD XCOL
        MULI 16
        ADDI {FLOOR_NUKE}
        SUBI {UNITS}
        MULI {WIDTH}
        ADDI {H3D - 1}
        SUB DEND            ; arg = seed*64 + ({H3D - 1} - drawEnd)
        MULI 8
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
        BRZ face            ; clip empty: no bar at all
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

; ── the face (M5): the Freedoom status-bar face, {FACE_H}x{FACE_W} at rows {FACE_ROW}..{FACE_ROW + FACE_H - 1},
; columns {FACE_COL}..{FACE_COL + FACE_W - 1} — four baked variants (face_for), each a constant list of
; CURS + RLE RUN words; the branch ladder picks FIRE's grimace first, then
; the HEALTH band (> 66 healthy, > 33 hurt, else bloodied)
face:   LD  FIRE
        BRZ fband           ; not firing: the health band picks the face
        JMP fgrim
fband:  LD  HEALTH
        SUBI 67
        BRN fb2
        JMP fwell           ; health > 66: the healthy face
fb2:    LD  HEALTH
        SUBI 34
        BRN fbld            ; health <= 33: the bloodied face
        JMP fhurt
""".splitlines()
    face_blocks = (
        ("fwell", FACE_HEALTHY, "healthy (stfst00)"),
        ("fhurt", FACE_HURT, "hurt (stfst20)"),
        ("fbld", FACE_BLOODY, "bloodied (stfst40)"),
        ("fgrim", FACE_GRIM, "the FIRE grimace (stfevl0)"),
    )
    for bi, (label, table, note) in enumerate(face_blocks):
        first = True
        for r, c, colors in table:
            head = f"{label}:" if first else ""
            lines.append(f"{head:<8}LDI {8 * (r * WIDTH + c) + curs}"
                         + (f"          ; {note}" if first else ""))
            lines.append(f"        SND                 ; CURS: face row {r}, column {c}")
            first = False
            k = 0
            while k < len(colors):
                j = k
                while j < len(colors) and colors[j] == colors[k]:
                    j += 1
                colour = int(colors[k], 16)
                lines.append(f"        LDI {8 * ((j - k) * 16 + colour) + runc}")
                lines.append(f"        SND                 ; RUN {j - k} x colour {colour}")
                k = j
        if bi + 1 < len(face_blocks):
            lines.append("        JMP cmit")
    lines += """
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


# ── --wad: a locally imported IWAD level (Mode B; commit NOTHING from it) ────
#: True once :func:`install_level` swapped in an imported level; the emulator
#: helpers then assemble the asm from THIS module state instead of loading the
#: checked-in ``deadman-3d.asm`` (whose title-loop constants are the committed
#: Freedoom art's).
_WAD_INSTALLED = False


def _twin_modules() -> list:
    """This module object *and* the canonical package instance.

    ``python -m randomfun2026solvers.deadman3d`` loads this file as
    ``__main__`` while the unit builder (``d3_unit``) imports the sprite
    tables from ``randomfun2026solvers.deadman3d`` — a second module
    instance.  The install_* swaps must land on both, or the --wad build's
    golden and its machine carry different art (measured: the M5 art
    override passed the emulator, which reads the ``__main__`` state, and
    failed the native gate, whose grid was baked from the package one).
    """
    import sys as _sys

    mods = [_sys.modules[__name__]]
    pkg = _sys.modules.get("randomfun2026solvers.deadman3d")
    if pkg is None:
        import randomfun2026solvers.deadman3d as pkg  # noqa: PLC0415
    if pkg is not mods[0]:
        mods.append(pkg)
    return mods


def install_level(map_rows: list[str], spawn_cell: tuple[int, int], heading: int,
                  title_rows: list[str],
                  nukage_rows: list[str] | None = None) -> None:
    """Swap the module onto an imported level (``wadimport`` output).

    Everything downstream — :func:`map_cell`, :func:`preamble_words`,
    :func:`render`, :func:`title_words`, :func:`deadman3d_source` — reads the
    module globals at call time, so the swap makes the whole model, generator
    and player follow the imported level.  ``nukage_rows`` is the importer's
    damage-floor plane (``N`` marks; omitted = no damage floors).
    """
    global MAP_STR, _PRINTED_ROWS, _MAP_WORDS, SPAWN, TITLE_HEX_ROWS, _WAD_INSTALLED
    global NUKAGE_STR, _NUKE_ROWS, _NUKE_WORDS
    assert len(map_rows) == MAP_SIZE and all(len(r) == MAP_SIZE for r in map_rows)
    assert len(title_rows) == HEIGHT and all(len(r) == WIDTH for r in title_rows)
    MAP_STR = "\n".join(map_rows) + "\n"
    _PRINTED_ROWS = list(map_rows)
    _MAP_WORDS = map_words()
    if nukage_rows is None:
        nukage_rows = ["." * MAP_SIZE] * MAP_SIZE
    assert len(nukage_rows) == MAP_SIZE and all(len(r) == MAP_SIZE for r in nukage_rows)
    NUKAGE_STR = "\n".join(nukage_rows) + "\n"
    _NUKE_ROWS = list(nukage_rows)
    _NUKE_WORDS = nukage_words()
    x, y = spawn_cell
    assert map_cell(x, y) == 0, f"imported spawn cell {spawn_cell} is a wall"
    SPAWN = State(posX=x * UNITS + UNITS // 2, posY=y * UNITS + UNITS // 2,
                  heading=heading % HEADINGS)
    TITLE_HEX_ROWS = list(title_rows)
    _WAD_INSTALLED = True
    here = globals()
    for mod in _twin_modules():
        for name in ("MAP_STR", "_PRINTED_ROWS", "_MAP_WORDS", "NUKAGE_STR",
                     "_NUKE_ROWS", "_NUKE_WORDS", "SPAWN", "TITLE_HEX_ROWS",
                     "_WAD_INSTALLED"):
            setattr(mod, name, here[name])


def install_art(gun_idle: list[tuple[int, int, str]],
                gun_fire: list[tuple[int, int, str]],
                faces: dict[str, list[tuple[int, int, str]]]) -> None:
    """Swap the sprite art onto ``wadimport.iwad_art``'s WAD-derived tables
    (Mode B only: the committed machines stay Freedoom-derived).

    The pistol tables are module globals read by both the unit builder
    (``d3_unit.unit_interior`` bakes the GUN/GUNF arms from them at build
    time) and the emulator's unit model (``store.DoomUnit`` duplicates them
    as class attributes — rebound here so a local machine emulates its own
    art); the face tables are plain CPU-side RUN constants, so rebinding the
    module globals re-generates the asm with them.
    """
    global GUN_IDLE, GUN_FIRE, FACE_HEALTHY, FACE_HURT, FACE_BLOODY, FACE_GRIM
    from randomfun2026solvers.lm1.store import DoomUnit

    GUN_IDLE = list(gun_idle)
    GUN_FIRE = list(gun_fire)
    FACE_HEALTHY = list(faces["healthy"])
    FACE_HURT = list(faces["hurt"])
    FACE_BLOODY = list(faces["bloody"])
    FACE_GRIM = list(faces["grim"])
    here = globals()
    for mod in _twin_modules():  # __main__ AND the package instance d3_unit reads
        for name in ("GUN_IDLE", "GUN_FIRE", "FACE_HEALTHY", "FACE_HURT",
                     "FACE_BLOODY", "FACE_GRIM"):
            setattr(mod, name, here[name])
    DoomUnit.GUN_IDLE = tuple(gun_idle)
    DoomUnit.GUN_FIRE = tuple(gun_fire)


def _current_program():
    """The checked-in program — or, once a level is installed, the asm
    regenerated from the module state (same slots, new title-loop constants)."""
    from randomfun2026solvers.lm1 import programs
    from randomfun2026solvers.lm1.asm import assemble

    if _WAD_INSTALLED:
        return assemble(deadman3d_source(), name="deadman-3d")
    return programs.load("deadman-3d")


def _local_build(out_dir: Path, cmds: list[int]) -> None:
    """The full local artifact set for an installed --wad level: asm, the
    men-v3 machine AND the taped machine (the web-editor workflow runs on
    taped) with debug sidecars, the cases file, the flat input, PNGs."""
    from randomfun2026solvers.lm1 import machine

    out_dir.mkdir(parents=True, exist_ok=True)
    src = deadman3d_source()
    (out_dir / "deadman-3d_local.asm").write_text(src, encoding="utf-8")
    prog = _current_program()
    for suffix, kwargs in (("", {}), ("_taped", {"store": "taped"})):
        m = machine.build_for("deadman-3d", program=prog, **kwargs)
        stem = f"deadman-3d_local{suffix}"
        (out_dir / f"{stem}.man").write_text("\n".join(m.rows) + "\n", encoding="utf-8")
        m.debug_map().write_html(m.rows, out_dir / f"{stem}.debug.html")
        m.debug_map().write_json(out_dir / f"{stem}.debug.json")
        print(f"wrote {out_dir / stem}.man ({m.width}x{m.height})")
    (out_dir / "deadman-3d_local.cases.json").write_text(
        json.dumps(cases_json(cmds)) + "\n", encoding="utf-8")
    (out_dir / "deadman-3d_local.input.txt").write_text(
        " ".join(str(w) for w in preamble_words() + list(cmds)) + "\n", encoding="utf-8")
    _write_pngs([title_frame()] + frames_for_commands(cmds), out_dir / "frames")
    print(f"wrote {out_dir}/deadman-3d_local.cases.json, .input.txt, frames/")


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
        from randomfun2026solvers.lm1.display import frames_from_writes
        from randomfun2026solvers.lm1.emulator import Emulator, Round

        self._em = Emulator(_current_program())
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


def _play_frame(player: "_MachinePlayer | None", state: State, code: int,
                counters: dict) -> tuple[State, str]:
    """One keypress against whichever engine: returns (state, terminal text).

    ``counters`` threads the golden path's live ammo/health between frames
    (the machine keeps its own on the tape).
    """
    fire = fire_bit(code)
    if fire and counters["ammo"] > 0:
        counters["ammo"] -= 1
    state = step(state, code)
    nuk = nukage_cell(div(state.posX, UNITS), div(state.posY, UNITS)) == 1
    if nuk:
        counters["health"] = max(0, counters["health"] - NUKE_DAMAGE)
    if player is None:  # --golden: the model, instant
        frame = render(state, fire=fire, ammo=counters["ammo"],
                       health=counters["health"], nukage=nuk)
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
    counters = {"ammo": AMMO_START, "health": HEALTH_START}
    for ch in script:
        state, text = _play_frame(player, state, keys(ch), counters)
        print(text)


def _play(golden: bool) -> None:
    """Raw-keypress loop on the real machine (or --golden): w/a/s/d/space; q quits."""
    import sys
    import termios
    import time
    import tty

    player = None if golden else _MachinePlayer()
    state = SPAWN
    counters = {"ammo": AMMO_START, "health": HEALTH_START}
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
            state, text = _play_frame(player, state, code, counters)
            ms = (time.perf_counter() - t0) * 1000
            print(f"\x1b[H{text}  {ms:.0f} ms", flush=True)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print("\x1b[0m")


def _parse_walk(walk: str) -> list[int]:
    """One key per frame — or, with commas, one CHORD per frame ("w, ,w " holds
    W, fires, then does both at once)."""
    return [keys(ch) for ch in (walk.split(",") if "," in walk else walk)]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--walk", help='one key per frame, e.g. ".wwa d", or '
                                       'comma-separated chords, e.g. ".,w,w " '
                                       '(default: WALK)')
    parser.add_argument("--cases", type=Path, help="write cases_json(WALK) here")
    parser.add_argument("--png", type=Path, help="dump preview PNGs to this directory")
    parser.add_argument("--play", action="store_true",
                        help="play the machine live: w/a/s/d/space, q quits")
    parser.add_argument("--golden", action="store_true",
                        help="with --play/--play-script: golden model, instant frames")
    parser.add_argument("--play-script", metavar="KEYS",
                        help='render KEYS as --play would, non-interactively')
    parser.add_argument("--wad", type=Path, metavar="IWAD",
                        help="import a local retail IWAD's level+TITLEPIC first "
                             "(Mode B; outputs are local-only, commit nothing)")
    parser.add_argument("--wad-map", default="E1M1", metavar="NAME",
                        help="map marker for --wad (default E1M1)")
    parser.add_argument("--build", action="store_true",
                        help="with --wad: write the full local artifact set "
                             "(asm, men-v3 + taped .man + sidecars, cases, "
                             "input, PNGs) into littleman/examples/local/")
    args = parser.parse_args(argv)
    if args.wad:
        from randomfun2026solvers import wadimport

        level = wadimport.load_iwad(args.wad, args.wad_map)
        install_level(level.map_rows, level.spawn, level.heading, level.title_rows,
                      level.nukage_rows)
        art = wadimport.iwad_art(args.wad)
        install_art(art["gun_idle"], art["gun_fire"], art["faces"])
        print(f"installed {level.stats['source']}: spawn {level.spawn} "
              f"heading {level.heading}, {level.stats['wall_cells']} wall cells, "
              f"{level.stats.get('nukage_cells', 0)} nukage cells, "
              f"{level.stats['title_runs']} title runs; WAD art: "
              f"{len(art['gun_idle'])}+{len(art['gun_fire'])} pistol runs, "
              f"{len(art['faces'])} faces")
        if args.build:
            local = Path(__file__).resolve().parents[3] / "littleman" / "examples" / "local"
            wadimport.emit(level, local)
            cmds = _parse_walk(args.walk) if args.walk else WALK
            _local_build(local, cmds)
            return
    elif args.build:
        parser.error("--build needs --wad (the committed artifacts are regenerated "
                     "from the module, not the CLI)")
    if args.play_script is not None:
        _play_script(args.play_script, args.golden)
        return
    if args.play:
        _play(args.golden)
        return
    cmds = _parse_walk(args.walk) if args.walk else WALK
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
