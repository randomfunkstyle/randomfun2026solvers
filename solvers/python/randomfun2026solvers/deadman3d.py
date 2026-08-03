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
legal wall types (a ``--wad`` import may produce them).  The HUD's colours
are DOOM's own: the bar is its imported metal greys, and both live readouts
are :data:`BAR_COLOR` — the quantized colour of DOOM's big ``STTNUM`` digits.

Tape slot map (the asm's .equ table; slot 0 is scratch)
-------------------------------------------------------
``preamble_words()`` yields the boot data in exact tape order, slots 1..451:

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
    MONB  360..375  the monster table (M7a): ((cx*64)+cy)*2 + species each
    MHPB  376..391  initial monster HP (1 zombieman / 2 imp; M7b mutates it)
    SPRB  392..451  60 packed sprite columns: species 0 bands 10+6+4, species
                    1, corpse — nibble 0 = bottom pixel, colour 0 transparent

The 64 ZBUF slots (452..515) follow the boot data but ride no input: every
frame's column loop writes all 64 wall depths before the sprite pass reads
any, so they boot as garbage and it never matters.

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
spends a round of :data:`AMMO_START`'s clip (floor 0) and the bar in DOOM's
ammo well shrinks with it.
The asm decodes the bits with a MODI 2 / DIVI 2 ladder into the ``BW BS BA BD
FIRE`` scalars and runs the turn and move arms conditionally.  :func:`keys`
encodes chords readably (``keys("wa ") == 21``); ``--play`` runs the checked-in
asm on a persistent emulator, one single-key word per keypress — the machine,
played live (chords remain reachable by script).

The demo walk (``WALK``)
------------------------
Spawn view (no-op) at Freedoom E1M1's real start: east down the striped
start hall.  Ten steps east; a 90° left turn and four steps north up the
brown concrete corridor, where two imps are queued — two shots drop the
first (M7b: the sprite is still standing under the muzzle flash on the frame
that kills it, the corpse heap appears on the next), and a third shot at the
same spot passes through the corpse into the imp behind it; three steps on
past the body into the great cavern's south-west lobe; back right to east and
two steps onto the cavern floor, where a half-look right FIREs at the sunlit
south rim; six steps east across the cavern, the ZIMMER cliffs and green
MCSTAT screens growing ahead; a quiet half-look right at the north rim; two
steps on, a 90° left turn to north, and two steps toward the slime fall —
then a *firing* step (the ``"w "`` chord — the MUX at work) and one last FIRE
standing before the bright green fall.  Two-cell steps (DOOM's run on the
64x64 grid); 57 words, spelled ``WALK_CHORDS``.

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
* the status bar itself (M8: :data:`HUD_BG_ROWS`) — ``graphics/stbar.png``,
  the 320x32 bar block-quantized onto the 64x8 HUD strip by
  ``wadimport.stbar_rows`` at exactly 5x4 source pixels per cell;
* the status-bar face (M5: :data:`FACE_HEALTHY`/:data:`FACE_HURT`/
  :data:`FACE_BLOODY`/:data:`FACE_GRIM`) — ``graphics/stfst00.png``,
  ``stfst20.png``, ``stfst40.png`` and ``stfevl0.png``, quantized at 6x7 by
  ``wadimport.face_tables`` into the bar's own mugshot inset;
* the monster billboards (M7a: :data:`MON_SPRITES`, placed at
  :data:`MONSTERS` — the level's own THINGS lump) — ``sprites/possa1.png``
  (the former human), ``sprites/trooa1.png`` (the imp) and ``possl0.png``
  (the corpse frame a shot monster drops into, M7b), hue-forward quantized at
  three scale bands by ``wadimport.monster_sprite_words``;
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
from dataclasses import dataclass, field
from pathlib import Path

from randomfun2026solvers.lm1.emulator import floor_div, sign_mod

__all__ = [
    "WIDTH", "HEIGHT", "H3D", "MID", "UNITS", "HEADINGS", "INPUT_PROTOCOL",
    "MOVE_NUM", "MOVE_DEN", "BIG", "MAP_SIZE", "MAP_STR", "PALETTE",
    "KEY_FWD", "KEY_BACK", "KEY_LEFT", "KEY_RIGHT", "KEY_FIRE",
    "GUN_IDLE", "GUN_FIRE", "AMMO_START", "HEALTH_START",
    "NUKAGE_STR", "NUKE_DAMAGE", "FLOOR_NUKE", "nukage_words", "nukage_cell",
    "FACE_HEALTHY", "FACE_HURT", "FACE_BLOODY", "FACE_GRIM", "face_for",
    "MAX_MON", "MONSTERS", "MON_SPRITES", "MON_BANDS", "MON_BAND_OFF",
    "MON_STRIDE", "BAND_T", "MON_NEAR", "MON_FAR", "MON_HP",
    "monster_words", "monster_hp_words", "det_for",
    "install_level", "install_art",
    "SPAWN", "State", "WALK", "WALK_CHORDS", "keys", "fire_bit",
    "TITLE_HEX_ROWS", "title_frame", "title_runs", "title_words",
    "div", "map_cell", "map_words", "heading_table",
    "unpack_heading", "step", "render", "hud_rows", "hud_bg_rows", "hud_bg_runs",
    "HUD_BG_ROWS", "BAR_COLOR", "BAR_ROWS", "AMMO_BAR_COLS", "HEALTH_BAR_COLS",
    "AMMO_PER_PX", "HEALTH_PER_PX",
    "preamble_words", "input_words", "frames_for_commands", "cases_json",
    "tape_slots", "deadman3d_source", "taped_program", "main",
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


#: DOOM's status bar and its big numerals, in the bar's own pixels — the same
#: numbers as ``wadimport.STBAR_W``/``STBAR_H``/``DIGIT_W``/``DIGIT_H``, and the
#: tests pin them equal.  They are here so :class:`Geom` can answer how big a
#: numeral is at a given strip without importing the art pipeline.
STBAR_W, STBAR_H = 320, 32
DIGIT_W, DIGIT_H = 14, 16
#: ``STTNUM0``..``STTNUM9`` and ``STTPRCNT``.
DIGIT_GLYPHS = 11


@dataclass(frozen=True)
class Geom:  # noqa: PLW1641
    """The screen the renderer and the generator are aiming at.

    Everything above is the *world*; this is the picture.  It exists because the
    LM-75's interior stops at 64x64 (``SPEC.md``), so 128x96 has to be four
    panels — and a 2x2 of 64x48 tiles keeps every panel exactly the geometry the
    DOOM unit already paints.  The tile is the addressing unit: ``tile_w`` is the
    stride in a ``CURS``/``COL`` word and ``tile_w * 16`` is the bias the unit's
    wall lap adds before it paints, which is why both stay 64 at either
    resolution and the unit's wire format never changes.

    ``width``/``height``/``h3d`` are the *logical* frame.  When they equal the
    tile the machine is the single-panel one and every tiled branch collapses to
    the code that was always there — :data:`GEOM64` is that case, and it is the
    default of every function below, so the 64x48 family is byte-identical by
    construction rather than by care.
    """

    width: int
    height: int
    h3d: int
    tile_w: int = 64
    tile_h: int = 48
    #: which :data:`ART_REGISTRY` bundle the screen-space tables come from
    art: str = "committed"
    #: Whether the monster billboards are painted at all.  On everywhere now;
    #: kept as a switch because it is the one part of a frame that can be turned
    #: off without changing anything else, which is what made bringing the 2x
    #: billboards up separately possible.
    sprites: bool = True
    #: Whether the live readouts are DOOM's **numerals** rather than bar fills.
    #:
    #: The strip is ``hud_h`` rows and a ``STTNUM`` glyph is 16 of the bar's own
    #: 32, so a numeral is ``hud_h / 2`` cells tall: 4 on the committed 8-row
    #: strip, where a digit is unreadable and the well carries a proportional
    #: bar instead (:data:`AMMO_BAR_COLS`), and 8 on the doubled one, where it
    #: is DOOM's actual number.  Off by default for that reason, and because the
    #: committed machine's assembly must not move.
    digits: bool = False

    @property
    def mid(self) -> int:
        """The horizon row of the 3D viewport."""
        return self.h3d // 2

    @property
    def crosshair(self) -> int:
        """The column the hit test reads — the centre of the screen."""
        return self.width // 2

    @property
    def cam_step(self) -> int:
        """``cameraX = 2*x/w - 1`` in Q10: ``cam_step * x - UNITS``.

        Exact only while ``2 * UNITS`` divides by the width, which 64 and 128
        both do (32 and 16).  The asm bakes this as a ``MULI``.
        """
        step, rem = divmod(2 * UNITS, self.width)
        if rem:
            raise ValueError(f"cameraX is not exact at width {self.width}")
        return step

    @property
    def lh_num(self) -> int:
        """``lineHeight``'s numerator: ``WALL_H * h3d * UNITS``."""
        return WALL_H * self.h3d * UNITS

    @property
    def tiles(self) -> tuple[int, int]:
        """How many panels across and down."""
        return self.width // self.tile_w, self.height // self.tile_h

    @property
    def tiled(self) -> bool:
        """True when the frame needs more than one panel."""
        return self.tiles != (1, 1)

    @property
    def mon_scale(self) -> int:
        """How much bigger a billboard is here than on the committed screen.

        A billboard's size is a *projection*, so it scales with the viewport
        exactly as a wall does — which is why :data:`BAND_T`, the depth
        thresholds that pick a band, need no scaling at all: they are
        ``lh_num / 2 // midpoint_height`` and both halves double together.
        """
        return self.width // 64

    @property
    def mon_bands(self) -> tuple[tuple[int, int], ...]:
        """``(width, height)`` per scale band, near to far."""
        k = self.mon_scale
        return tuple((w * k, h * k) for w, h in MON_BANDS)

    @property
    def mon_words(self) -> int:
        """Packed words per sprite column.

        One nibble per row and ``16**15`` is the last power inside 64 bits, so a
        column taller than 14 rows is more than one word.  The columns are cut
        into 14-row slices from the bottom (``wadimport.pack_sprite_columns_wide``)
        and padded to a whole number of them, which is what lets one paint chain
        serve every band: the short ones walk into transparent padding and the
        chain already branches over a 0 nibble.
        """
        return -(-max(h for _w, h in self.mon_bands) // 14)

    @property
    def mon_span(self) -> int:
        """Rows one chain walks: ``14 * mon_words``, padding included."""
        return 14 * self.mon_words

    @property
    def mon_band_off(self) -> tuple[int, ...]:
        """Column offset of each band inside a sprite's stripe."""
        offs, acc = [], 0
        for w, _h in self.mon_bands:
            offs.append(acc)
            acc += w
        return tuple(offs)

    @property
    def mon_stride(self) -> int:
        """Columns per sprite stripe — species 0, species 1, then the corpse."""
        return sum(w for w, _h in self.mon_bands)

    @property
    def dig_box(self) -> tuple[int, int]:
        """``(w, h)`` of one ``STTNUM`` numeral in strip cells.

        The strip is as wide as the frame and DOOM's bar is
        :data:`STBAR_W` x :data:`STBAR_H`, so a :data:`DIGIT_W` x
        :data:`DIGIT_H` numeral scales straight onto it: 6x8 at 128x16, 3x4 at
        the committed 64x8 — which is the whole argument for :attr:`digits`.
        The same arithmetic as ``wadimport.digit_box``, kept here so the tape
        layout is answerable without an IWAD, and pinned equal by the tests.
        """
        return (round(DIGIT_W * self.width / STBAR_W),
                round(DIGIT_H * self.hud_h / STBAR_H))

    @property
    def dig_words(self) -> int:
        """Words in the ``DIGB`` table: one per column of each of the 11 glyphs."""
        return DIGIT_GLYPHS * self.dig_box[0] if self.digits else 0

    @property
    def hud_h(self) -> int:
        """Rows of status bar below the viewport."""
        return self.height - self.h3d

    def tile_of(self, x: int, y: int) -> int:
        """Which panel a logical pixel lands on, row-major over the tile grid."""
        across, _down = self.tiles
        return (x // self.tile_w) + across * (y // self.tile_h)

    def tile_addr(self, x: int, y: int) -> int:
        """The panel-local ADDR (``row * tile_w + column``) for a logical pixel."""
        return (y % self.tile_h) * self.tile_w + (x % self.tile_w)

    def floor_row(self, tile: int) -> int:
        """The last panel row COL's floor run fills, on ``tile``.

        A tile wholly inside the viewport floors to its own last row; the tile
        the viewport *ends* in stops where the status bar begins.  On the single
        panel that is the familiar 39.
        """
        across, _down = self.tiles
        top = (tile // across) * self.tile_h
        return min(self.tile_h, self.h3d - top) - 1


#: The committed machine: one 64x48 panel, a 40-row viewport, an 8-row bar.
GEOM64 = Geom(width=WIDTH, height=HEIGHT, h3d=H3D, tile_w=WIDTH, tile_h=HEIGHT)

#: The tiled machine: 128x96 as a 2x2 of 64x48 panels, an 80-row viewport over a
#: 16-row bar.  ``lm1/d3_router.py`` is the hardware; ``deadman3d_hires`` drives it.
GEOM128 = Geom(width=128, height=96, h3d=80, tile_w=64, tile_h=48, art="hires",
               digits=True)


@dataclass(frozen=True)
class Art:
    """Every table whose coordinates are the *screen's* rather than the world's.

    Split out from the module globals for one reason: at 128x96 all of them move.
    The pistol is bottom-centred on the viewport, the bar art is as wide as the
    frame, the mugshot sits in the bar's own inset and the wells are cut from the
    bar — none of that survives a resolution change, and none of it is derivable
    from the 64x48 tables by arithmetic alone.

    :func:`art_for` returns the committed art by reading the module globals *at
    call time*, which is what keeps ``--wad``'s :func:`install_art` working: that
    swap rebinds the globals and the next call picks them up.
    """

    title: list[str]
    hud_bg: list[str]
    gun_idle: list[tuple[int, int, str]]
    gun_fire: list[tuple[int, int, str]]
    faces: dict[str, list[tuple[int, int, str]]]
    face_box: tuple[int, int, int, int]  # (col, row, w, h), STBAR's own inset
    sprites: list[int]
    bar_rows: tuple[int, int]
    ammo_cols: tuple[int, int]
    health_cols: tuple[int, int]
    ammo_per_px: int
    health_per_px: int
    #: DOOM's own ``STTNUM``/``STTPRCNT`` numerals, packed one word per glyph
    #: column exactly like :attr:`sprites` (a nibble a row, bottom row first, 0
    #: transparent).  Empty on the committed screen, where the strip is 8 rows
    #: and a numeral would be 3x4 — see :attr:`Geom.digits`.
    digits: list[int] = field(default_factory=list)
    #: ``(w, h)`` of one numeral in cells (``wadimport.digit_box``).
    dig_box: tuple[int, int] = (0, 0)
    #: ``(col, bottom_row)`` per glyph slot in screen coordinates, in paint
    #: order: ammo's three digits, health's three, then the percent sign
    #: (``wadimport.digit_slots``).
    dig_slots: tuple[tuple[int, int], ...] = ()


#: Art bundles other than the committed one, keyed by :attr:`Geom.art`.
#: ``deadman3d_hires`` registers ``"hires"`` on import; nothing else writes here.
ART_REGISTRY: dict[str, Art] = {}


def art_for(geom: Geom = GEOM64) -> Art:
    """The screen-space tables for ``geom``.

    ``"committed"`` is assembled from the module globals on every call so an
    ``install_art`` swap (``--wad`` mode) is picked up by the very next render.
    """
    if geom.art != "committed":
        if geom.art not in ART_REGISTRY:
            raise KeyError(
                f"no art registered for {geom.art!r} (have {sorted(ART_REGISTRY)}). "
                "The 128x96 family takes every table from an IWAD lump quantized "
                "at that size and has no fallback on purpose — call "
                "deadman3d_hires.install_wad(path_to_iwad) first."
            )
        return ART_REGISTRY[geom.art]
    return Art(
        title=TITLE_HEX_ROWS,
        hud_bg=HUD_BG_ROWS,
        gun_idle=GUN_IDLE,
        gun_fire=GUN_FIRE,
        faces={"healthy": FACE_HEALTHY, "hurt": FACE_HURT,
               "bloody": FACE_BLOODY, "grim": FACE_GRIM},
        face_box=(FACE_COL, FACE_ROW, FACE_W, FACE_H),
        sprites=_SPR_WORDS,
        bar_rows=BAR_ROWS,
        ammo_cols=AMMO_BAR_COLS,
        health_cols=HEALTH_BAR_COLS,
        ammo_per_px=AMMO_PER_PX,
        health_per_px=HEALTH_PER_PX,
    )

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
    "Round 0: the 451-word data preamble (deadman3d.preamble_words()) then the "
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


# ── monsters (M7a billboards, M7b shootable) ───────────────────────────────
#: The monster table cap: the tape's ``MONB``/``MHPB`` blocks are sized to it.
MAX_MON = 16

#: ``(cx, cy, species)`` per monster — species 0 = former humans (Freedoom's
#: POSS art; THINGS 3004 zombieman and 9 shotgun guy collapse at this
#: resolution), species 1 = imps (TROO art, THINGS 3001).  GENERATED by
#: ``wadimport``'s ``_monster_things`` from Freedoom E1M1's real THINGS lump
#: (commit ``d14dbbe``): medium-skill, single-player, open-cell, deduped,
#: first :data:`MAX_MON` in THINGS order (43 monster things -> 19 skill/MP
#: drops, 4 closed-cell drops, 4 over the cap).  The imps at (25, 38) and
#: (25, 40) stand in the brown concrete corridor the demo walk climbs; the
#: pair at (46, 34)/(47, 36) hold the cavern's east rim.
MONSTERS: list[tuple[int, int, int]] = [
    (25, 38, 1),
    (25, 40, 1),
    (34, 44, 1),
    (46, 34, 1),
    (47, 36, 1),
    (17, 43, 0),
    (17, 46, 0),
    (26, 51, 0),
    (23, 49, 0),
    (14, 52, 0),
    (10, 53, 0),
    (23, 27, 0),
    (25, 12, 0),
    (29, 11, 0),
    (47, 19, 0),
    (48, 19, 1),
]

#: The three baked billboard scale bands, ``(width, height)`` near/mid/far.
#: Heights <= 14 keep one whole sprite column in ONE packed word (16 nibbles;
#: 16**14 == 2**56 < 2**63), so the paint chain is a bottom-up MODI 16 /
#: DIVI 16 nibble walk with no POW16 lookup at all.
MON_BANDS = ((10, 14), (6, 9), (4, 5))
#: Column-word offset of each band inside a sprite's 20-word stripe.
MON_BAND_OFF = (0, 10, 16)
#: Words per sprite stripe: species 0 at 0, species 1 at 20, corpse at 40.
MON_STRIDE = 20
#: Band thresholds on the Q10 camera depth TY: ``40960 // midpoint_height``
#: against ``MON_H_NUM = 40960`` (a one-cell-tall monster under the 81920
#: two-cell wall) — band 0 below ``BAND_T[0]``, band 1 below ``BAND_T[1]``,
#: band 2 out to the far cull.
BAND_T = (3562, 5851)
#: Near cull: the player is inside the monster (TY < one cell).
MON_NEAR = UNITS
#: Far cull: walls go dark at NEAR_D = 16 cells; monsters vanish at 12.
MON_FAR = 12 * UNITS
#: Initial HP per species (boot-loads ``MHPB``; consumed by M7b's hit logic).
MON_HP = (1, 2)
#: The crosshair column: the pistol's hitscan is the one screen column the
#: barrel points down, so a shot hits whatever billboard covers column 32
#: and survives that column's wall depth test (M7b).
CROSSHAIR = WIDTH // 2

#: The packed sprite columns, one word each, 60 words: species 0's three
#: bands (10+6+4 columns), species 1's, then the shared corpse frame padded
#: to the same band boxes (transparent top rows; an M7b consumer).  Nibble 0
#: is the column's BOTTOM pixel; colour 0 = transparent (an opaque black-ish
#: source block quantizes to 8 — a billboard cannot paint black).  GENERATED
#: by ``wadimport.monster_sprite_words`` from the **Freedoom** project's
#: ``sprites/possa1.png``, ``sprites/trooa1.png`` and ``sprites/possl0.png``
#: (commit ``d14dbbe``, BSD-style licence — see the module credits), via the
#: hue-forward block quantize (``wadimport.quantize_monster``): the imp reads
#: brown 3 with red 1 spikes, the zombieman olive 2 fatigues under a brown 3
#: torso.
MON_SPRITES: list[int] = [
    # species 0 (POSS): band0 10 cols, band1 6, band2 4
    2147483648, 34628173824, 221190815744, 1305672714368, 302585331878016,
    14725978847215752, 36331403808573576, 56075095279752, 196812581371904,
    3298534883328,
    65536, 3145728, 322058376, 17501923464, 318775944, 0,
    0, 12936, 340536, 0,
    # species 1 (TROO): band0 10 cols, band1 6, band2 4
    53477376, 13740539904, 6817212610576384, 5404319552844595,
    4839170576167731, 4839170576167731, 5492279677760307, 422432317059888,
    858783744, 3145728,
    208896, 6501184256, 5120406323, 5153960755, 3158832, 0,
    0, 78643, 78643, 0,
    # corpse (POSSL0, padded to the band boxes): band0 10, band1 6, band2 4
    2176, 2184, 136, 40, 40, 296, 4488, 20872, 16, 0,
    8, 8, 2, 24, 88, 0,
    8, 2, 24, 1,
]


def monster_words() -> list[int]:
    """The packed ``MONB`` tape block: ``((cx * 64) + cy) * 2 + species`` per
    monster — cell and species in one positive word, unpacked by the asm's
    MODI 2 / MODI 64 / DIVI 64 ladder."""
    assert len(MONSTERS) <= MAX_MON, f"{len(MONSTERS)} monsters > cap {MAX_MON}"
    assert len({(cx, cy) for cx, cy, _sp in MONSTERS}) == len(MONSTERS), \
        "monster cells must be unique (one billboard per cell)"
    words = []
    for cx, cy, sp in MONSTERS:
        assert sp in (0, 1), f"species {sp} has no sprite stripe"
        assert 0 <= cx < MAP_SIZE and 0 <= cy < MAP_SIZE
        assert map_cell(cx, cy) == 0, f"monster on a wall cell {(cx, cy)}"
        words.append(((cx * 64) + cy) * 2 + sp)
    return words


def monster_hp_words() -> list[int]:
    """The ``MHPB`` tape block: each monster's initial HP (:data:`MON_HP` by
    species) — boot-loaded now, mutable by M7b's hit resolution."""
    return [MON_HP[sp] for _cx, _cy, sp in MONSTERS]


def _check_sprites(words: list[int]) -> list[int]:
    assert len(words) == 3 * MON_STRIDE, f"{len(words)} sprite words != 60"
    assert all(0 <= w < 16 ** 14 for w in words), "a sprite column overflows"
    return list(words)


_MON_WORDS = monster_words()
_MHP_WORDS = monster_hp_words()
_SPR_WORDS = _check_sprites(MON_SPRITES)


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


def det_for(heading: int) -> int:
    """The sprite projection's divisor ``DET = planeX*dirY - dirX*planeY``
    (Q20), exactly as the asm's per-frame prologue computes it.  Positive for
    every heading because the plane is dir rotated -90° (the tests assert all
    16) — the projection divides by it, so this is a structural invariant."""
    dirX, dirY, planeX, planeY = unpack_heading(_HDG_WORDS[heading])
    return planeX * dirY - dirX * planeY


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
#: frame, floor 0.  M8 moved the bars into DOOM's OWN number wells — see
#: :data:`AMMO_BAR_COLS` and the geometry note under :func:`hud_bg_rows`.
AMMO_START = 50
HEALTH_START = 100

#: The status-bar face (M5, re-boxed in M8): 6x7 at panel rows 40..46,
#: columns 29..34 — DOOM's own recessed mugshot inset, ``wadimport``'s
#: ``stbar_cells("face")`` scaled off the 320x32 bar, not a hand-picked hole
#: in an invented bezel.  Derived from the **Freedoom** project's status-bar
#: faces (commit d14dbbe: ``graphics/stfst00.png`` healthy, ``stfst20.png``
#: hurt, ``stfst40.png`` bloodied, ``stfevl0.png`` the firing grimace;
#: BSD-style licence, see the module credits), GENERATED by
#: ``wadimport.face_tables`` — the WHOLE portrait (M5's box was twice DOOM's
#: aspect and had to keep only the brow-to-chin band; the real inset is
#: taller than it is wide, so hair, face and shoulders all fit) composited
#: onto the bar's own colour under the inset and block-Lab quantized at a
#: x1.4 brightness lift.  Encoding: one ``(panel row, first column,
#: colours)`` run per face row, painted by the CPU as one CURS plus RLE RUN
#: command words per frame — no unit arm, so no descent-window budget.
#: The variant is picked per frame: the grimace on FIRE frames, else by the
#: HEALTH band (> 66 healthy, > 33 hurt, else bloodied).
FACE_ROW, FACE_COL, FACE_W, FACE_H = 40, 29, 6, 7
FACE_HEALTHY: list[tuple[int, int, str]] = [
    (40, 29, "888008"),
    (41, 29, "000000"),
    (42, 29, "033380"),
    (43, 29, "837330"),
    (44, 29, "833338"),
    (45, 29, "833338"),
    (46, 29, "888888"),
]
FACE_HURT: list[tuple[int, int, str]] = [
    (40, 29, "888008"),
    (41, 29, "000000"),
    (42, 29, "033380"),
    (43, 29, "833330"),
    (44, 29, "833338"),
    (45, 29, "833938"),
    (46, 29, "888188"),
]
FACE_BLOODY: list[tuple[int, int, str]] = [
    (40, 29, "800008"),
    (41, 29, "000000"),
    (42, 29, "008000"),
    (43, 29, "833330"),
    (44, 29, "893338"),
    (45, 29, "833338"),
    (46, 29, "889988"),
]
FACE_GRIM: list[tuple[int, int, str]] = [
    (40, 29, "808008"),
    (41, 29, "880000"),
    (42, 29, "008000"),
    (43, 29, "883330"),
    (44, 29, "873338"),
    (45, 29, "837338"),
    (46, 29, "887888"),
]


def face_for(health: int, fire: bool, *,
             geom: Geom = GEOM64) -> list[tuple[int, int, str]]:
    """Which face this frame paints, exactly as the asm branches: FIRE wins,
    else the HEALTH band (> 66 / > 33 / the rest)."""
    faces = art_for(geom).faces
    if fire:
        return faces["grim"]
    if health > 66:
        return faces["healthy"]
    if health > 33:
        return faces["hurt"]
    return faces["bloody"]


# ── the tile split: one screen, one lane, four panels ────────────────────────
def unit_word(arm: str, arg: int, tile: int, geom: Geom) -> int:
    """One command word for ``arm`` aimed at ``tile``.

    On a single panel this is the unit's own ``8 * arg + code`` and there is no
    tile to name.  On the wall it gains the router's selector underneath —
    ``8 * (8 * arg + code) + sel`` — which is the whole difference between the
    two machines' wire formats (``lm1/d3_router.py``).
    """
    from randomfun2026solvers.lm1.store import DoomUnit

    word = 8 * arg + DoomUnit.CODES[arm]
    if not geom.tiled:
        return word
    from randomfun2026solvers.lm1 import d3_router

    return 8 * word + d3_router.SEL[f"T{tile}"]


def commit_word(geom: Geom = GEOM64) -> int:
    """COMMIT — on the wall, the router's **broadcast** leaf, never a tile.

    That is not a convenience: four panels swap on four separate pipes, and it
    is the broadcast (``S``, all-or-nothing) that makes every panel see the same
    COMMIT sequence, so tile frame *N* is always a piece of logical frame *N*.
    """
    from randomfun2026solvers.lm1.store import DoomUnit

    word = 8 * 0 + DoomUnit.CODES["COMMIT"]
    if not geom.tiled:
        return word
    from randomfun2026solvers.lm1 import d3_router

    return 8 * word + d3_router.SEL["ALL"]


def _rle(pixels: str) -> list[tuple[int, int]]:
    """Row-major ``(colour, count)`` runs — the RUN arm's own encoding."""
    runs: list[tuple[int, int]] = []
    for ch in pixels:
        c = int(ch, 16)
        if runs and runs[-1][0] == c:
            runs[-1] = (c, runs[-1][1] + 1)
        else:
            runs.append((c, 1))
    return runs


def span_words(row: int, col: int, colours: str, geom: Geom) -> list[int]:
    """One horizontal span of pixels as ``CURS`` + ``RUN`` words, tile-split.

    The status bar's wells and the mugshot are spans in *screen* coordinates and
    the mugshot straddles ``x = 64``, so a span may be two panels' worth of
    words.  Each panel's cursor auto-advances over its own raster, so within a
    tile the span is one CURS and its runs.
    """
    out: list[int] = []
    x = col
    while x < col + len(colours):
        tile = geom.tile_of(x, row)
        end = min(col + len(colours), (x // geom.tile_w + 1) * geom.tile_w)
        out.append(unit_word("CURS", geom.tile_addr(x, row), tile, geom))
        for colour, count in _rle(colours[x - col:end - col]):
            out.append(unit_word("RUN", count * 16 + colour, tile, geom))
        x = end
    return out


def frame_words(rows: list[str], geom: Geom) -> list[int]:
    """A whole frame as ``CURS`` + ``RUN`` words, one stream per panel, interleaved.

    Interleaved because the panels paint **concurrently** and can only do that
    while all of them have work: feed one tile's whole raster before the next
    one's and the last panel starts after the others have finished, so the frame
    costs the sum of the tiles instead of the maximum.  Order *within* a tile is
    all that matters to a cursor, and that is preserved.
    """
    if len(rows) != geom.height or any(len(r) != geom.width for r in rows):
        raise ValueError(f"the frame is not {geom.width}x{geom.height}")
    across, down = geom.tiles
    streams: list[list[int]] = []
    for tile in range(across * down):
        cx, cy = (tile % across) * geom.tile_w, (tile // across) * geom.tile_h
        flat = "".join(rows[cy + r][cx:cx + geom.tile_w] for r in range(geom.tile_h))
        out = [unit_word("CURS", 0, tile, geom)]
        out += [unit_word("RUN", n * 16 + c, tile, geom) for c, n in _rle(flat)]
        streams.append(out)
    return [s[i] for i in range(max(map(len, streams)))
            for s in streams if i < len(s)]


# ── the sprite pass: selection, occlusion, billboard paint, the hit test ──────
def _paint_monsters(cols: list[list[int]], zbuf: list[int],
                    posX: int, posY: int, dirX: int, dirY: int,
                    planeX: int, planeY: int,
                    hp: list[int] | None = None, live: bool = False,
                    geom: Geom = GEOM64) -> int:
    """Paint up to three monster billboards over the finished columns.

    Written in the generated asm's exact operation order — this function IS
    the sprite pixel contract.  Selection: unpack each ``MONB`` word, read its
    live HP (0 ⇒ a corpse: the shared corpse stripe, and never a hit
    candidate), cull (behind the plane, nearer than :data:`MON_NEAR`, past
    :data:`MON_FAR`, off screen), pick the scale band and floor line, and keep
    the nearest three in a far-first slot file (slot 0 = farthest; strict
    ``<`` everywhere, so an equal-depth later THINGS index never displaces an
    earlier one — a slot is occupied when its depth is strictly nearer than
    the far cull, which is what an empty slot holds).  Paint: slots 0 -> 2
    (back to front), per column the wall depth test ``TY < ZBUF[x]``, then the
    bottom-up MODI 16 / DIVI 16 nibble walk of the column's one packed word
    (colour 0 transparent).

    Returns the **hit** (M7b): with ``live`` (a shot that actually spent a
    round) the crosshair column ``x == 32`` records the slot's monster index +
    1 the moment that column survives the wall depth test — so the hit is the
    nearest *unoccluded, still-alive* monster under the crosshair, because
    slots paint far -> near and the last write wins.  0 = nothing hit.  The
    caller applies it AFTER this frame renders (see
    :func:`frames_for_commands`): the corpse appears from the next frame.
    """
    if not geom.sprites:
        return 0
    bands, band_off = geom.mon_bands, geom.mon_band_off
    stride, wpc, span = geom.mon_stride, geom.mon_words, geom.mon_span
    sprites = art_for(geom).sprites
    WIDTH, H3D, MID = geom.width, geom.h3d, geom.mid  # noqa: N806 — the screen's
    CROSSHAIR = geom.crosshair  # noqa: N806
    det = planeX * dirY - dirX * planeY  # Q20 — the asm's per-frame prologue
    assert det > 0, f"DET {det} must be positive (plane = dir rotated -90 deg)"
    if hp is None:
        hp = _MHP_WORDS
    hit = 0
    empty = {"ty": MON_FAR, "sx0": 0, "sx1": 0, "base": 0, "bot": 0,
             "band": 0, "idx": 0}
    slots = [dict(empty) for _ in range(3)]
    for i, w in enumerate(_MON_WORDS):
        sp = sign_mod(w, 2)
        q = div(w, 2)
        mdy = sign_mod(q, 64) * UNITS + 512 - posY   # cell centre - player
        mdx = div(q, 64) * UNITS + 512 - posX
        tyn = planeX * mdy - planeY * mdx            # camera depth num (Q20)
        if tyn <= 0:
            continue                                 # behind the plane
        txn = dirY * mdx - dirX * mdy                # camera x numerator
        ty = div(tyn * UNITS, det)                   # Q10 — ZBUF's own units
        if ty < MON_NEAR:
            continue                                 # player inside the monster
        if ty - MON_FAR >= 0:
            continue                                 # beyond the far cull
        band = 0 if ty < BAND_T[0] else (1 if ty < BAND_T[1] else 2)
        w_band = bands[band][0]
        # the projected centre column: div(txn * (WIDTH/2), tyn) + WIDTH/2
        sx = div(txn * (WIDTH // 2), tyn) + WIDTH // 2
        sx0 = sx - div(w_band, 2)
        sx1 = sx0 + w_band - 1
        if sx1 < 0:
            continue                                 # off the left edge
        if sx0 > WIDTH - 1:
            continue                                 # off the right edge
        bot = div(div(geom.lh_num, ty), 2) + MID     # the floor line
        if bot > H3D - 1:
            bot = H3D - 1                            # near clamp: slides up whole
        if hp[i] == 0:                               # a corpse (M7b)
            cid = 0                                  # never a hit candidate
            frame = 2                                # the shared corpse stripe
        else:
            cid = i + 1                              # alive: index + 1
            frame = sp
        cand = {"ty": ty, "sx0": sx0, "sx1": sx1,
                "base": frame * stride + band_off[band],
                "bot": bot, "band": band, "idx": cid}
        # Far-first 3-slot insertion, strict < (the asm's branch ladder).
        if not ty < slots[0]["ty"]:
            continue                                 # not nearer than the farthest kept
        if ty < slots[1]["ty"]:
            slots[0] = slots[1]
            if ty < slots[2]["ty"]:
                slots[1] = slots[2]
                slots[2] = cand
            else:
                slots[1] = cand
        else:
            slots[0] = cand
    for s in slots:                                  # slot 0 first: back to front
        if not s["ty"] < MON_FAR:
            continue                                 # an empty slot paints nothing
        x = s["sx0"]
        ptr = 0
        while x <= s["sx1"]:
            if x < 0:
                x += 1
                ptr += 1
                continue                             # clipped off the left edge
            if x > WIDTH - 1:
                break                                # clipped off the right edge
            if s["ty"] < zbuf[x]:                    # the wall depth test
                if live and x == CROSSHAIR and s["idx"] != 0:
                    hit = s["idx"]                   # far -> near: nearest wins
                # The bottom-up nibble walk, `span` rows of it whatever the
                # band is: a column is padded to a whole number of 14-row
                # slices and a 0 nibble is transparent, so a short band simply
                # walks into its own padding. That is what lets ONE chain in
                # the generated asm serve every band (see Geom.mon_words).
                col_at = (s["base"] + ptr) * wpc
                row = s["bot"]
                q = sprites[col_at]
                for j in range(span):
                    if row < 0:
                        break
                    if j and j % 14 == 0:            # this slice is spent
                        q = sprites[col_at + j // 14]
                    c = sign_mod(q, 16)
                    q = div(q, 16)
                    if c != 0:
                        cols[x][row] = c
                    row -= 1
            x += 1
            ptr += 1
    return hit


# ── the renderer (lodev raycaster_flat.cpp, in Q10) ──────────────────────────
def render(state: State, *, fire: bool = False,
           ammo: int = AMMO_START, health: int = HEALTH_START,
           nukage: bool = False, hp: list[int] | None = None,
           live: bool = False, hit_out: list[int] | None = None,
           geom: Geom = GEOM64) -> list[str]:
    """One frame: ``geom.height`` rows of ``geom.width`` hex chars.

    Rows ``0..h3d-1`` are the 3D view and the rest is the HUD strip; at the
    default :data:`GEOM64` that is the committed 48 rows of 64 (0..39 and
    40..47).  The local rebinding below is what makes the rest of the body read
    the *screen* rather than the module: every expression is unchanged, and at
    ``GEOM64`` every name it reads is the value it always had.

    The pistol (:data:`GUN_IDLE`, or :data:`GUN_FIRE` when ``fire``) paints
    over the finished columns — the golden twin of the machine's one GUN/GUNF
    command word per frame — and the HUD carries the live bars for ``ammo``
    and ``health`` plus the banded status face. With ``nukage`` (the player's
    cell stands on a damage floor) every column's floor run paints
    :data:`FLOOR_NUKE` green instead of the gray 8 — DOOM's palette-shift
    homage, and exactly what the machine's per-column green overlay COL word
    repaints (colour 2 is mask-invariant: ``2 & 7 == 2 & 15 == 2``).

    ``hp`` is the live monster HP ledger (default: the boot values, i.e. all
    16 alive); a zeroed entry paints the corpse frame.  ``live`` says this
    frame's shot actually spent a round, and ``hit_out``, when given, receives
    the one monster the crosshair hit (index + 1, 0 for none) — the render is
    pure, the caller applies the hit afterwards (M7b's timing contract).
    """
    WIDTH, H3D, MID = geom.width, geom.h3d, geom.mid  # noqa: N806 — the screen's
    art = art_for(geom)
    posX, posY = state.posX, state.posY
    dirX, dirY, planeX, planeY = unpack_heading(_HDG_WORDS[state.heading])
    cols: list[list[int]] = []
    zbuf: list[int] = []  # per-column wall depth (M7a) — the asm's ZBUF slots
    for x in range(WIDTH):
        # lodev: cameraX = 2*x/w - 1; exact in Q10 while the width divides
        # 2*UNITS — 32*x - 1024 at w=64, 16*x - 1024 at w=128 (Geom.cam_step).
        cameraX = geom.cam_step * x - UNITS
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
        # The z-buffer (M7a): the clamped depth, exactly the value the asm
        # stores into ZBUF + x right here (before the shading reads it).
        zbuf.append(perpWallDist)
        # lodev: lineHeight = WALL_H * h / perpWallDist -> Q10: 81920 / perp
        # (two-cell walls: the 64x64 world at the 32x32 proportions).
        lineHeight = div(geom.lh_num, perpWallDist)
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
        # the dark shade — the horizontal seam of a wall panel. The ring is
        # reseeded per COMMAND, so on a tiled screen the phase restarts at each
        # panel's own first painted row: a column that crosses the seam is two
        # commands and the lower one begins its banding afresh.
        run = [
            color & 7 if sign_mod(y - max(drawStart, geom.tile_h * (y // geom.tile_h)),
                                  4) == 0 else color
            for y in range(drawStart, drawEnd + 1)
        ]
        floor_c = FLOOR_NUKE if nukage else 8
        cols.append([0] * drawStart + run + [floor_c] * (H3D - 1 - drawEnd))
    hit = _paint_monsters(cols, zbuf, posX, posY, dirX, dirY, planeX, planeY,
                          hp, live, geom)
    if hit_out is not None:
        hit_out.append(hit)
    for r, c, colors in (art.gun_fire if fire else art.gun_idle):
        for i, ch in enumerate(colors):
            cols[c + i][r] = int(ch, 16)
    rows = ["".join("%x" % cols[x][y] for x in range(WIDTH)) for y in range(H3D)]
    return rows + hud_rows(health, ammo, fire, geom=geom)


# ── the HUD strip (rows 40..47): DOOM's REAL status bar ──────────────────────
#: Rows 40..47 are DOOM's own ``STBAR`` — the 320x32 status bar quantized onto
#: the strip by ``wadimport.stbar_rows`` at exactly 5x4 source pixels per cell
#: (no resampling distortion; the auto-exposure lift is the ANSI grey ramp's
#: fault, see ``wadimport.STBAR_MEAN_GREY``).  This is the committed
#: **Freedoom** ``graphics/stbar.png`` at commit d14dbbe; a ``--wad`` build
#: swaps in the IWAD's own bar through :func:`install_art`.
#:
#: What replaced what: V4's invented strip was a gray bezel, a dark field, and
#: a static blue "armor" block, none of which exists in DOOM.  The real bar
#: brings its own wells (ammo, health, the ARMS/FRAGS panel, the mugshot
#: inset, armor, the key column and the small ammo table), its own label band
#: along the bottom, and its own bevels — at 64x8 a status bar is mostly one
#: slab of dark metal, which is exactly what DOOM's is.
HUD_BG_ROWS: list[str] = [
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888788888888888888888887788788888888888",
    "8888888888888888888888888888788888888888888888888887788888888887",
    "8888888888888888888888888888788888888888888888888877788888888887",
    "7777777777777777777777777777788888888877777777778888788888888888",
    "8877777888877878788888878877888888888877777778888888888888888888",
    "0888888800088888888888888888888888888888888888888888888888888888",
]

#: DOOM paints its big ``STTNUM`` status digits in ONE colour — dark red,
#: whose block-Lab quantize is ANSI 1 on both art sources (measured: the mode
#: of ``STTNUM0``/``STTNUM8``'s opaque pixels, mean (107,6,6) retail /
#: (131,5,5) Freedoom).  Ammo and health are told apart by their WELL, exactly
#: as in the original, not by a made-up colour code.
BAR_COLOR = 1

#: The live readouts, in DOOM's own wells (``wadimport.stbar_cells``, i.e.
#: ``st_stuff.c``'s placement constants scaled /5 and /4).  Three 14-wide
#: ``STTNUM`` digits end at ``ST_AMMOX`` 44 -> the ammo well is columns 0..8;
#: three more end at ``ST_HEALTHX`` 90 with ``STTPRCNT`` after them -> health
#: is columns 10..20, the bar between them being the wells' own divider.  Both
#: numbers sit at ``ST_*Y`` 171 and are 16 tall -> panel rows 41..44.
#: Digits at 2-3 px would be mush, so each well carries a proportional bar
#: instead; the divisors are the well width over the scalar's range (50 rounds
#: over 9 cells, 100 health over 11).  DOOM's armor well (columns 36..46), key
#: column (48) and small ammo table (55..62) stay bare: the marine has no
#: armor and no keys in this demo, and DOOM draws nothing for a zero.
AMMO_BAR_COLS = (0, 9)
HEALTH_BAR_COLS = (10, 21)
BAR_ROWS = (41, 45)          # half-open: panel rows 41..44
AMMO_PER_PX = 6
HEALTH_PER_PX = 10


def hud_bg_rows(geom: Geom = GEOM64) -> list[str]:
    """Rows 40..47's static background: the quantized status bar
    (:data:`HUD_BG_ROWS`) — the live bars and the face paint over it."""
    return list(art_for(geom).hud_bg)


def hud_bg_runs(geom: Geom = GEOM64) -> list[tuple[int, int]]:
    """RLE (color, count) of the 8 background rows concatenated — the CPU
    repaints the strip every frame as one CURS word (cursor to 2560) plus one
    pre-encoded RUN word per run, so this list IS the asm's constant table."""
    return _rle("".join(hud_bg_rows(geom)))


def _paint_glyph(rows: list[list[int]], art: Art, geom: Geom,
                 glyph: int, slot: int) -> None:
    """One numeral into the strip, column by column, bottom nibble first.

    The same walk as a monster billboard's — ``c = Q % 16; Q //= 16``, colour 0
    transparent — because the glyphs are packed the same way, which is what
    makes the strokes' gaps show the bar art instead of a box of background.
    """
    dw, dh = art.dig_box
    col, bottom = art.dig_slots[slot]
    for k in range(dw):
        q = art.digits[glyph * dw + k]
        row = bottom
        for _j in range(dh):
            c = sign_mod(q, 16)
            q = div(q, 16)
            if c != 0:
                rows[row - geom.h3d][col + k] = c
            row -= 1


def _paint_digits(rows: list[list[int]], art: Art, geom: Geom,
                  ammo: int, health: int) -> None:
    """The live readouts as DOOM's own numbers — the golden twin of the machine's
    digit chain.

    ``STlib_drawNum`` draws right-aligned and **blanks a leading zero**, which
    is one compare per digit here and one ``BRN`` there: a well's hundreds box
    is painted only when the number reaches 100, its tens only at 10, and the
    units box always (so a health of 0 still reads ``0``).  ``STTPRCNT`` then
    goes in the box after the health number, exactly as
    ``STlib_updatePercent`` puts it.
    """
    for base, value in ((0, ammo), (3, health)):
        d = 100
        for k in range(3):
            if d == 1 or value - d >= 0:
                _paint_glyph(rows, art, geom, sign_mod(div(value, d), 10), base + k)
            d = div(d, 10)
    _paint_glyph(rows, art, geom, 10, 6)


def hud_rows(health: int = HEALTH_START, ammo: int = AMMO_START,
             fire: bool = False, *, geom: Geom = GEOM64) -> list[str]:
    """Rows 40..47: the status bar plus the live readouts and the mugshot,
    exactly as the machine paints them — the ammo bar in DOOM's ammo well
    (rows 41..44, from column 0, one pixel per :data:`AMMO_PER_PX` rounds),
    the health bar in DOOM's health well (from column 10, one pixel per
    :data:`HEALTH_PER_PX` health), both in the digits' own red (an empty bar
    sends no RUN at all, so the bar art shows through), then the
    :func:`face_for` variant's rows in the mugshot inset."""
    art = art_for(geom)
    rows = [[int(ch, 16) for ch in r] for r in hud_bg_rows(geom)]
    if geom.digits:
        _paint_digits(rows, art, geom, ammo, health)
    else:
        for (col0, _col1), px in (
            (art.ammo_cols, div(ammo, art.ammo_per_px)),
            (art.health_cols, div(health, art.health_per_px)),
        ):
            for row in range(*art.bar_rows):
                for c in range(col0, col0 + px):
                    rows[row - geom.h3d][c] = BAR_COLOR
    for r, c, colors in face_for(health, fire, geom=geom):
        for i, ch in enumerate(colors):
            rows[r - geom.h3d][c + i] = int(ch, 16)
    return ["".join("%x" % c for c in row) for row in rows]


# ── the demo walk ────────────────────────────────────────────────────────────
#: One chord per frame, each encoded by :func:`keys`.  The beats: hold the
#: spawn view down the start hall; ten ``w`` east along it, the striped BASE2
#: columns sliding by; four ``a`` to face north and four ``w`` up the brown
#: concrete corridor at x=25 — where **the shooting gallery** stands (M7b):
#: two of Freedoom E1M1's imps queue up the corridor at (25, 38) and (25, 40),
#: and from (25, 34) the first is dead under the crosshair.  Two FIREs drop it
#: (an imp is :data:`MON_HP` 2): frame 19 takes it to 1, frame 20 to 0 with the
#: sprite still standing under the muzzle flash — the hit is applied after the
#: frame it was resolved in — and the held beat at frame 21 shows the corpse
#: heap on the floor where the billboard was.  Frame 22 fires at the SAME spot
#: again: the corpse is not a hit candidate any more, so that round carries on
#: through it and wounds the second imp behind.  Then three ``w`` past the body
#: and out of the corridor mouth into the cavern's south-west lobe; four ``d``
#: back to east and two ``w`` into the great cavern proper; ``d``, FIRE, ``a``
#: — a half-look right at the sunlit south rim, the shot lighting the muzzle;
#: six ``w`` east across the cavern floor, the ZIMMER cliffs and green MCSTAT
#: screens far ahead; ``d``, hold, ``a`` — the quiet half-look at the north
#: rim; two ``w`` on to x=45, four ``a`` round to north, two ``w`` INTO the
#: slime moat that rings the fall (M5: the floor floods green and health
#: drains 5 a frame, the red bar visibly shrinking); three held beats standing
#: in the slime, the fall dead ahead, the face degrading to bloodied; then a
#: *firing* step OUT of the moat (``"w "`` — the MUX at work) and one last
#: FIRE standing clean before the bright green fall.  The cavern crossing at
#: frames 36..39 already forded the moat's west lobe, so the bar drains in two
#: episodes — 14 nukage frames in all, health 100 -> 30, unchanged by M7b's
#: three extra beats (they stand on dry concrete).  Two-cell steps on the
#: 64x64 grid; 57 words, spelled ``WALK_CHORDS``.
WALK_CHORDS: list[str] = (
    ["."] + ["w"] * 10 + ["a"] * 4 + ["w"] * 4 + [" ", " ", ".", " "]
    + ["w"] * 3 + ["d"] * 4 + ["w"] * 2
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


def title_frame(geom: Geom = GEOM64) -> list[str]:
    """The title screen as a committed frame: the art's own rows verbatim."""
    return list(art_for(geom).title)


def title_runs(geom: Geom = GEOM64) -> list[tuple[int, int]]:
    """The title as row-major RLE ``(colour, count)`` runs over all 3072 pixels.

    The DOOM unit's RUN arm replays one run per command word at the panel's own
    auto-advancing cursor, so encoding is trivially lossless by construction.
    """
    return _rle("".join(art_for(geom).title))


def title_words(geom: Geom = GEOM64) -> list[int]:
    """Round 0's title burst: one pre-encoded RUN command word per run.

    ``8*(count*16 + colour) + C_RUN`` — exactly the word the unit's trie
    expects, so the CPU's whole title loop is ``IN``/``SND`` pairs.
    """
    if geom.tiled:  # four panels, four rasters, one interleaved lane
        return frame_words(title_frame(geom), geom)
    return [unit_word("RUN", count * 16 + colour, 0, geom)
            for colour, count in title_runs(geom)]


# ── boot data and the cases file ─────────────────────────────────────────────
def preamble_words(geom: Geom = GEOM64) -> list[int]:
    """Round 0's data burst, in exact tape order (slots 1..451; see docstring).

    The M5 order (map, POW16, headings, nukage plane, the seven named spawn
    scalars) is unchanged through slot 359; M7a appends the monster table
    (``MONB``), the initial HP block (``MHPB``, an M7b consumer boot-loaded
    now so the tape never moves again) and the 60 packed sprite columns
    (``SPRB``).  ``DIGB``'s eleven packed numerals follow when the screen is big
    enough to read one (:attr:`Geom.digits`).  The boot loop's 8x-unrolled body
    adapts to the length; the ZBUF slots after all of it are NOT preamble —
    every one is written each frame before the sprite pass reads any.
    """
    dirX, dirY, planeX, planeY = unpack_heading(_HDG_WORDS[SPAWN.heading])
    art = art_for(geom)
    return (
        _MAP_WORDS
        + POW16
        + _HDG_WORDS
        + _NUKE_WORDS
        + [SPAWN.posX, SPAWN.posY, SPAWN.heading, dirX, dirY, planeX, planeY]
        + _MON_WORDS
        + _MHP_WORDS
        + art.sprites
        + (art.digits if geom.digits else [])
    )


def input_words(cmds: list[int], geom: Geom = GEOM64) -> list[int]:
    """Everything the program ever reads: the preamble, the title screen's RLE,
    then one word per command.

    The title's word count is the *art's*, so it changes with the resolution —
    a 128x96 title is a different number of runs from a 64x48 one, and the CPU's
    title loop is generated against exactly this list.  Rebuild it, never reuse
    the other machine's."""
    return preamble_words(geom) + title_words(geom) + list(cmds)


def frames_for_commands(cmds: list[int], geom: Geom = GEOM64) -> list[list[str]]:
    """Apply each command in turn and render after it — one frame per command.

    Threads the live counters exactly as the asm does: a FIRE with rounds left
    decrements ammo BEFORE the render (the decode ladder runs first) and is
    the ONLY thing that arms the shot (``live`` — an empty clip dry-fires at
    0, still flashes, and kills nothing); then the move lands, and standing
    on nukage costs :data:`NUKE_DAMAGE` health (floor 0) before the frame is
    drawn — the frame you take damage on already shows the green floor, the
    shorter bar and the degraded face.

    The **hit timing contract** (M7b, mirrored line for line in the asm): the
    shot is resolved against *this* frame's post-move geometry, inside the
    sprite pass, but the HP it costs is applied only after the frame has been
    rendered — so the frame you fire on shows the monster alive under the
    muzzle flash and the corpse appears from the NEXT frame.
    """
    return [beat[0] for beat in walk_beats(cmds, geom)]


def walk_beats(cmds: list[int],
               geom: Geom = GEOM64) -> list[tuple[list[str], bool, int, tuple[int, ...]]]:
    """:func:`frames_for_commands`' engine, with the state it threads exposed.

    One ``(frame, live, hit, hp)`` per command: ``live`` is the armed-shot
    flag, ``hit`` the monster the crosshair caught in that frame (index + 1,
    0 for none) and ``hp`` the HP ledger **after** the hit is applied — i.e.
    the ledger the next frame renders from, which is what makes the corpse a
    frame late.  The tests read the kill this way; nothing else needs it.
    """
    state = SPAWN
    ammo = AMMO_START
    health = HEALTH_START
    hp = list(_MHP_WORDS)          # the live ledger: the asm's MHPB slots
    beats = []
    for cmd in cmds:
        fire = fire_bit(cmd)
        live = fire and ammo > 0   # exactly where the asm spends the round
        if live:
            ammo -= 1
        state = step(state, cmd)
        nuk = nukage_cell(div(state.posX, UNITS), div(state.posY, UNITS)) == 1
        if nuk:
            health -= NUKE_DAMAGE
            if health < 0:
                health = 0
        hit_out: list[int] = []
        frame = render(state, fire=fire, ammo=ammo, health=health,
                       nukage=nuk, hp=hp, live=live, hit_out=hit_out, geom=geom)
        if hit_out[0]:             # applied AFTER the render: next frame dies
            hp[hit_out[0] - 1] -= 1
        beats.append((frame, live, hit_out[0], tuple(hp)))
    return beats


def cases_json(cmds: list[int], geom: Geom = GEOM64,
               name: str = "deadman-3d") -> dict:
    """The demo's cases file: ONE case, one round per frame, round-gated frames.

    Shape matches ``littleman/examples/lambda-deadman-cpu.cases.json`` /
    ``littleman/tools/display-frames.mjs``: round 0 carries the preamble and
    the title screen's RLE and expects the title frame; each later round is
    exactly one command word; every round expects exactly one committed frame
    and no program output.
    """
    frames = frames_for_commands(cmds, geom)
    boot = [str(w) for w in preamble_words(geom) + title_words(geom)]
    rounds = [{"in": boot, "out": [], "frames": [title_frame(geom)]}]
    for k, cmd in enumerate(cmds):
        rounds.append({
            "in": [str(cmd)],
            "out": [],
            "frames": [frames[k]],
        })
    return {"publicTestData": [{"name": name, "rounds": rounds}]}


# ── the asm generator ────────────────────────────────────────────────────────
#: The scalar tape slots after the boot data, numbered consecutively from 360.
#: (No paint cursor: the DOOM unit owns the panel, so the CPU keeps no ADDRV/AEND.)
_SCALARS = (
    "CMD", "XCOL", "CAMX", "RDX", "RDY", "SDX", "SDY",
    "DDX", "DDY", "S4X", "STPY", "PERP", "HALFH", "DSTART", "DEND",
    "COLOR", "PW", "WADDR", "FRACX", "FRACY", "PW0", "WADDR0",
    "TMP", "TMP2", "NEWX", "NEWY",
    "BW", "BS", "BA", "BD", "FIRE", "AMMO", "HEALTH", "NUKE",
    # M7b's two frame-scoped shot scalars: LIVE arms the hit test exactly
    # where the round is spent, HIT collects the crosshair's victim.
    "LIVE", "HIT",
    # M7a, the sprite pass: the per-frame projection divisor, the selection
    # loop's candidate scalars ...
    "DET", "MI", "MSP", "MDX", "MDY", "TXN", "TYN",
    "CTY", "CBAND", "COFF", "CHW", "CW1", "CSX0", "CSX1", "CBOT", "CBASE",
    "CID",
    # ... the three kept slots, field-major triples (slot k at base + k, so
    # the paint loop LDAs them by SLOT; slot 0 = farthest, painted first) ...
    "STY0", "STY1", "STY2", "SSX0", "SSX1", "SSX2", "SEX0", "SEX1", "SEX2",
    "SBA0", "SBA1", "SBA2", "SBO0", "SBO1", "SBO2",
    "SBN0", "SBN1", "SBN2", "SID0", "SID1", "SID2",
    # ... and the paint loop's working set.
    "SLOT", "WTY", "WX", "WX1", "WPTR", "WBOT", "WBAND", "Q", "ADDRV",
    "PTR",
)

#: The tiled machine's extra scalars, appended only when the frame needs more
#: than one panel.  A column then carries a tile column and two selectors (the
#: panel above the seam and the panel below it), and the wall run is clipped
#: once per tile — see the ``send:`` block.  Appended rather than interleaved so
#: every committed slot number stays where it is.
_TILE_SCALARS = ("TXT", "TSELT", "TSELB", "TTE", "TBS", "TBE",
                 # the billboard chain: the second 14-row slice of the
                 # column, the panel-local row it is climbing, that
                 # panel's router selector, and the column inside it
                 "Q2", "WROW", "WSEL", "WXT")

#: The numeral chain's own scalars (:attr:`Geom.digits`), appended after the
#: tiled ones for the same reason: every committed slot number stays put.
_DIGIT_SCALARS = ("DSRC", "DDIV", "DRET", "DVAL", "DPTR", "DKN", "DA", "DAC")


#: The hi-res machine's own enumeration order for exactly those scalars.
#:
#: A scalar's slot number is nothing but its index in this tuple (``tape_slots``:
#: ``ZBUF + width + i``), and the scalars are the one region of the tape that is
#: **not** boot data — they sit above ``ZBUF``, so permuting them re-numbers a
#: hundred ``.equ`` lines and changes no input word. On the ``men-v3`` tier that
#: buys nothing, because man-memory is address-flat. On the **taped** tier it is
#: the largest lever there is: the banks are contiguous address ranges behind a
#: gate chain that a request scans linearly, and a gate can only peel a bank off
#: an **end** of the space it is handed, so *which* addresses are hot decides how
#: many gates every read walks. Measured over the 21-round tour, the shipped
#: numbering puts 89% of reads in the top 101 slots but with 58 cold ones
#: (``TMP2``..``WBAND``) sitting between the two hot clusters — which forces the
#: hottest bank to chain position 3 and costs ~23 ticks a hop.
#:
#: Only ``GEOM128`` reads it; ``deadman-3d``'s byte-pinned grid is on ``GEOM64``,
#: whose scalar set is ``_SCALARS`` alone and whose order is pinned by
#: ``tests/test_deadman3d.py::test_tape_slots_are_the_documented_map``. An empty
#: tuple falls back to the plain concatenation, i.e. every machine unchanged.
#:
#: **How this order was derived, so it can be re-derived rather than trusted.**
#: The tuple is read bottom-up: the *last* names get the highest addresses, and
#: :data:`lm1.machine.TAPED_BANK_ORDER` peels the chain off the **top** of the
#: space, so the tail of this tuple is chain position 0. The tail is therefore
#: the hot set, laid out against a cost of ~23 ticks per gate hop plus a ring
#: term that rises steeply with the slot's position inside its bank.
#:
#: The pairing with :data:`lm1.machine.TAPED_BANKS` is load-bearing and the two
#: must move together: the banks' **sizes** are unchanged as a multiset (the
#: rings are exactly as big as they were, and there is exactly one gate per bank
#: as before) but their order in address space is now ``... 58, 21, 6, 9, 7``, so
#: chain positions 0..4 are rings of 7, 9, 6, 21 and 58 slots. Resizing a bank is
#: a different and much worse operation — a previous sweep merged banks and
#: measured +14.8% and +61% — because a ring rotates one way and its lap is the
#: read's latency. Nothing here is resized; only the contents move.
#:
#: Measured on the 21-round tour, native engine, ``passed=True fatal=None``,
#: 625x403 unchanged, against the same-tree baseline of 130,519,332:
#: **114,847,979, -12.01%**. Ranking candidates by ``23 * hops + c * local`` and
#: measuring the top few, ``c=16`` with these bank sizes was the best of fifteen
#: builds; ``c=0`` (hops only) is +6.6% on it and putting the hot set in the
#: 58-slot ring — which pure hop-counting asks for — is +77% on it. Weighting
#: writes into the ranking is worse than ignoring them.
#:
#: Left as data rather than computed at import: the traffic it is fitted to is
#: level-derived, and ``tape_slots`` has to answer without an IWAD.
#:
#: **This tuple is settled, and it took ten builds to settle it.** A fitted
#: latency model — ``93.4 + 3.91*E[offset in bank] + 5.03*E[rotation]``, 0.7
#: ticks leave-one-out over 13 built-and-run *bank cuts* — attributes 19.43 of
#: the hi-res store's 34.5 layout ticks to this span and says a permutation can
#: take 11 of them back. **It cannot, and the model is why.** Every one of those
#: 13 cuts varied bank *sizes*; not one varied the permutation at fixed banks.
#: Over the address span an offset averages ~m/2, so ``offset`` was silently
#: proxying for the bank's own ring size, and it carries no causal force at all
#: once the banks are held still. Ten permutations were built and run on the
#: 21-round tour against this order's 96,280,186 (614x403, ``passed=True``,
#: ``store->cpu`` 2, control reproduced to the tick first and last in every
#: session):
#:
#: | permutation, and what it optimises | E[m] | E[off] | E[rot] | mean lat | Δ ticks |
#: |---|---|---|---|---|---|
#: | **this order** | 11.19 | 1.83 | 2.44 | **127.88** | — |
#: | model argmin, seed 4 | 16.23 | 1.54 | 0.40 | 139.11 | +3.79% |
#: | model argmin, seed 3 | 16.64 | 1.57 | 0.38 | 136.30 | +2.84% |
#: | rotation alone (12.27 -> 1.72) | 22.88 | 11.39 | 0.34 | 194.95 | +22.61% |
#: | offset alone (7.16 -> 5.78) | 18.43 | 1.48 | 3.71 | 147.24 | +6.53% |
#: | hottest into cheapest bank, hop=23 | 8.27 | 2.80 | 2.52 | 138.55 | +3.60% |
#: | hottest into smallest ring, hop=0 | 8.03 | 2.73 | 2.59 | 145.14 | +5.82% |
#: | hottest into lowest chain position | 8.52 | 2.85 | 2.61 | 138.32 | +3.52% |
#: | drain the 58-ring into the 21-ring | 9.14 | 2.20 | 2.74 | 129.24 | +0.46% |
#: | argmin of the refit below | 14.17 | 3.13 | 3.89 | 149.91 | +7.43% |
#:
#: This order has the **worst** offset and among the worst rotation of the ten
#: and the best latency, which settles the direction of both terms: they are
#: descriptive, not causal. Refitting on the ten (same banks, same chain order,
#: so the whole store is held still) the surviving description is
#:
#:     mean = 195.9 + 562.3 * w58 - 0.79 * E[service] + 112.5 * f_same
#:
#: — 1.0 ticks leave-one-out — where ``w58`` is the read share landing in the
#: 58-slot ring and ``f_same`` the share of reads whose immediately preceding
#: request hit the **same** bank. That last term is the one the offset model
#: never had and it is the reason every "put the hot set in the cheap bank"
#: candidate loses: a bank is a single-server ring, so concentrating traffic
#: makes each read wait out the previous one's lap, and the ~23-ticks-a-hop the
#: chain charges for spreading is the cheaper of the two. It is also why draining
#: the 58-slot ring — which every static cost model asks for first, and which
#: leaves banks 8/9/10 byte-identical — measures **+0.46%**: the 5.5% of reads it
#: moves land on top of the 21-slot ring's own traffic.
#:
#: The refit is a *description of ten points*, not a lever: its own argmin (the
#: last row) is +7.43%. Do not fit a static per-read cost to this span again —
#: what is left here is queueing, and it needs a queue model or a build.
_HIRES_SCALARS: tuple[str, ...] = (
    'CMD', 'PERP', 'TBS', 'TSELT', 'PW0', 'DA', 'PTR', 'DKN',
    'WX', 'WPTR', 'DSRC', 'MI', 'TYN', 'MDX', 'DVAL', 'WTY',
    'DET', 'WBOT', 'HEALTH', 'NEWX', 'STY0', 'STY1', 'STY2', 'AMMO',
    'CHW', 'CW1', 'NEWY', 'BW', 'BS', 'BA', 'BD', 'HIT',
    'SSX0', 'SSX1', 'SSX2', 'SEX0', 'SEX1', 'SEX2', 'SBA0', 'SBA1',
    'SBA2', 'SBO0', 'SBO1', 'SBO2', 'SBN0', 'SBN1', 'SBN2', 'SID0',
    'SID1', 'SID2', 'MSP', 'CBAND', 'COFF', 'CSX1', 'CBOT', 'CBASE',
    'CID', 'STPY', 'WBAND', 'TMP', 'COLOR', 'RDY', 'TTE', 'FRACX',
    'WADDR0', 'WXT', 'DPTR', 'ADDRV', 'DDIV', 'TMP2', 'SLOT', 'DRET',
    'CTY', 'MDY', 'WX1', 'TXN', 'LIVE', 'Q2', 'FIRE', 'CSX0',
    'XCOL', 'S4X', 'DEND', 'HALFH', 'WROW', 'CAMX', 'DDY', 'WADDR',
    'DAC', 'SDY', 'NUKE', 'TSELB', 'TBE', 'FRACY', 'WSEL', 'SDX',
    'PW', 'Q', 'DDX', 'DSTART', 'TXT', 'RDX'
)


def _scalars_for(geom: Geom) -> tuple[str, ...]:
    names = (_SCALARS + (_TILE_SCALARS if geom.tiled else ())
             + (_DIGIT_SCALARS if geom.digits else ()))
    if geom.digits and _HIRES_SCALARS:
        if set(_HIRES_SCALARS) != set(names) or len(_HIRES_SCALARS) != len(names):
            raise ValueError(
                "_HIRES_SCALARS must be a permutation of the geometry's own scalars; "
                f"missing {sorted(set(names) - set(_HIRES_SCALARS))}, "
                f"extra {sorted(set(_HIRES_SCALARS) - set(names))}"
            )
        if _HIRES_SCALARS[0] != "CMD":
            # `tests/test_deadman3d_hires.py` pins `CMD == ZBUF + width`: the
            # command word is the first slot above the z-buffer, not a free one.
            raise ValueError("_HIRES_SCALARS must still start with CMD")
        return _HIRES_SCALARS
    return names

#: How many copies of the DDA step the generated asm unrolls. A backward jump
#: costs ``8 * (P - loop)`` ticks on this machine, and a frame walks ~1,000 DDA
#: steps (~16 per column), so once painting moved to the unit these laps were
#: the dominant control-flow cost. On the 32x32 map the emulator-model sweep
#: put the knee at 8 (2 -> 4.65x, 4 -> 5.53x, 8 -> 6.03x, 16 -> 6.14x); the
#: 64x64 map doubled the rays, and the native re-sweep (rom_rows 40, men-v3)
#: moved it: frame 1 = 8,519,342 at 8, 8,380,261 at 12, 8,160,375 at 16 —
#: the +720-word P tax is repaid twice over by the halved backward laps.
DDA_UNROLL = 16

#: The per-loop unroll when ``deadman3d_source(dda_stepy_split=True)`` emits the
#: DDA **twice**, once per sign of stepY. Two loops at 16 would be ~576 more ROM
#: words than one, and the taped machine's drum stops routing between P=4,514 and
#: P=4,602 (``scratch/deadman3d-opt/rom_headroom.py``: unroll 22 binds, 23 does
#: not, at every ``ROM_ROWS`` — the seek teleport runs out of clear column east of
#: the drum). A split copy is *smaller* than an unsplit one — 62 words against 88,
#: because it carries one PW arm and no ``LD STPY`` — so 14 each fits inside the
#: same budget, and the only thing a shorter unroll costs is more backward laps,
#: which ``lap_via_jump`` has already made a ~1,008-tick seek apiece.
DDA_SPLIT_UNROLL = 14


def tape_slots(geom: Geom = GEOM64) -> dict[str, int]:
    """The asm's whole ``.equ`` table, name -> tape address (slot 0 is scratch).

    Slots 1..451 are the boot data in ``preamble_words()`` order (see the
    module docstring: the M5 tape through slot 359, then M7a's MONB, MHPB
    and SPRB blocks); the 64 ZBUF slots follow (written every frame, never
    boot-loaded), then the scalars run consecutively — so the machine's
    ``TAPE_SIZE`` is ``max(tape_slots().values()) + 1`` — an exactly-sized
    tape stalls silently (plan risk R6), which is why tests pin this.
    """
    n_mon = len(MONSTERS)
    slots = {
        "MAPB": 1, "POWB": 257, "HDGB": 273, "NUKB": 289,
        "POSX": 353, "POSY": 354, "HDG": 355, "DIRX": 356, "DIRY": 357,
        "PLANEX": 358, "PLANEY": 359,
        "MONB": 360, "MHPB": 360 + n_mon, "SPRB": 360 + 2 * n_mon,
    }
    # Every block after SPRB is sized by *geometry*, not by the art itself, so
    # the layout is answerable without an IWAD — which the resolution-only tests
    # rely on. :func:`deadman3d_source` asserts it against the real preamble.
    top = slots["SPRB"] + 3 * geom.mon_stride * geom.mon_words
    if geom.digits:
        slots["DIGB"] = top
        top += geom.dig_words
    slots["ZBUF"] = top
    for i, name in enumerate(_scalars_for(geom)):
        slots[name] = slots["ZBUF"] + geom.width + i
    return slots


def _sprite_phase_asm(slots: dict[str, int], n_mon: int, codes: dict[str, int],
                      geom: Geom = GEOM64) -> list[str]:
    """The sprite phase, lowered line for line from :func:`_paint_monsters`.

    Selection loop over the ``MONB`` table (cull chain, band pick, the live
    HP read that picks the species stripe or the corpse one, far-first 3-slot
    insertion with strict compares), then the far->near paint: per slot the
    field-major scalars are LDA'd by SLOT, per column the ZBUF occlusion test
    picks whether the column's one packed word enters the **shared unrolled
    14-block chain** — three static entry labels
    (``chain_h14``/``chain_h9``/``chain_h5``, the last 5 blocks common to
    all), each block one bottom-up nibble: MODI 16, transparent-skip, a CURS
    word from ADDRV and a 1-pixel RUN word, then DIVI 16 and the row step.

    M7b rides in the same pass: the crosshair column's hit candidacy sits on
    the far side of the occlusion test (so a wall saves the monster), and the
    one HP decrement runs after the last slot has painted — the frame that
    kills still shows the living sprite.
    """
    lh_num = geom.lh_num
    run1 = 8 * 16 + codes["RUN"]  # a 1-pixel RUN word is 8*(16 + c) + C_RUN
    WIDTH, H3D, MID = geom.width, geom.h3d, geom.mid  # noqa: N806 — the screen's
    CROSSHAIR = geom.crosshair  # noqa: N806
    MON_STRIDE = geom.mon_stride  # noqa: N806
    bands, band_off = geom.mon_bands, geom.mon_band_off
    span, wpc = geom.mon_span, geom.mon_words
    half = WIDTH // 2
    # CBASE is an ADDRESS and the band tables are in COLUMNS, so the scale from
    # one to the other is `wpc` — one line, and the only one, because at the
    # committed screen a column is one word and the scale is the identity.
    colscale = "" if wpc == 1 else f"        MULI {wpc}             ; a column is {wpc} words\n"
    TW, TH = geom.tile_w, geom.tile_h  # noqa: N806
    sel: list[int] = []
    up = 0
    if geom.tiled:
        from randomfun2026solvers.lm1 import d3_router

        sel = [d3_router.SEL[f"T{t}"] for t in range(4)]
        # A billboard climbing off a bottom panel lands on the one above it, and
        # T2 -> T0 and T3 -> T1 happen to be the same step in selector space —
        # which is what lets the crossing be one SUBI instead of a branch. It is
        # a property of the router's leaf assignment, so it is checked, not
        # assumed.
        up = sel[2] - sel[0]
        if up != sel[3] - sel[1] or up <= 0:
            raise ValueError(
                f"the panel-above step is not uniform ({sel}); the billboard "
                "chain's seam crossing needs a branch per side"
            )
    lines = f"""
; ── the sprite phase (M7a): static occluded monster billboards ───────────────
; Selection first: every MONB word is unpacked and run down the cull chain
; (behind the plane; nearer than {MON_NEAR}; past {MON_FAR}; off screen), the
; scale band picked by depth (TY < {BAND_T[0]} near 10x14 / < {BAND_T[1]} mid
; 6x9 / far 4x5), and the nearest three kept in a far-first slot file —
; slot 0 = farthest, strict compares, so an equal-depth later THINGS index
; never displaces an earlier one. DET = planeX*dirY - dirX*planeY > 0 for
; every baked heading (plane is dir rotated -90 deg; the tests assert all 16).
spsel:  LD  PLANEX
        MUL DIRY
        ST  DET
        LD  DIRX
        MUL PLANEY
        ST  TMP
        LD  DET
        SUB TMP
        ST  DET             ; the projection divisor, Q20
        LDI {MON_FAR}
        ST  STY0            ; empty slots sit AT the far cull: any candidate
        ST  STY1            ; that survived it compares strictly nearer
        ST  STY2
        LDI 0
        ST  SID0
        ST  SID1
        ST  SID2
        ST  MI
msel:   LD  MI
        SUBI {n_mon}
        BRN mbody
        JMP mpaint          ; all {n_mon} monsters considered
mbody:  LD  MI
        ADDI MONB
        LDA                 ; ((cx*64) + cy)*2 + species
        ST  TMP
        MODI 2
        ST  MSP
        LD  TMP
        DIVI 2
        ST  TMP             ; cx*64 + cy
        MODI 64
        MULI {UNITS}
        ADDI 512
        SUB POSY
        ST  MDY             ; cell centre - player, Q10
        LD  TMP
        DIVI 64
        MULI {UNITS}
        ADDI 512
        SUB POSX
        ST  MDX
        LD  PLANEX
        MUL MDY
        ST  TYN
        LD  PLANEY
        MUL MDX
        ST  TMP
        LD  TYN
        SUB TMP
        ST  TYN             ; camera depth numerator (Q20)
        SUBI 1
        BRN mnext           ; TYN <= 0: behind the camera plane
        LD  DIRY
        MUL MDX
        ST  TXN
        LD  DIRX
        MUL MDY
        ST  TMP
        LD  TXN
        SUB TMP
        ST  TXN             ; camera x numerator (Q20)
        LD  TYN
        MULI {UNITS}
        DIV DET
        ST  CTY             ; TY, Q10 — the same units as PERP/ZBUF
        SUBI {MON_NEAR}
        BRN mnext           ; the player stands inside the monster
        LD  CTY
        SUBI {MON_FAR}
        BRN mband
        JMP mnext           ; beyond the far cull
mband:  LD  CTY
        SUBI {BAND_T[0]}
        BRN mb0
        LD  CTY
        SUBI {BAND_T[1]}
        BRN mb1
        LDI 2               ; the far band: {bands[2][0]}x{bands[2][1]}
        ST  CBAND
        LDI {band_off[2]}
        ST  COFF
        LDI {bands[2][0] // 2}
        ST  CHW
        LDI {bands[2][0] - 1}
        ST  CW1
        JMP msx
mb0:    LDI 0               ; the near band: {bands[0][0]}x{bands[0][1]}
        ST  CBAND
        ST  COFF
        LDI {bands[0][0] // 2}
        ST  CHW
        LDI {bands[0][0] - 1}
        ST  CW1
        JMP msx
mb1:    LDI 1               ; the mid band: {bands[1][0]}x{bands[1][1]}
        ST  CBAND
        LDI {band_off[1]}
        ST  COFF
        LDI {bands[1][0] // 2}
        ST  CHW
        LDI {bands[1][0] - 1}
        ST  CW1
msx:    LD  TXN
        MULI {half}
        DIV TYN
        ADDI {half}             ; SX = {half} + {half}*TXN/TYN (the DETs cancel)
        SUB CHW
        ST  CSX0            ; first screen column (ST preserves ACC)
        ADD CW1
        ST  CSX1            ; last screen column
        BRN mnext           ; SX1 < 0: wholly off the left edge
        LD  CSX0
        SUBI {WIDTH}
        BRN mbot
        JMP mnext           ; SX0 > {WIDTH - 1}: wholly off the right edge
mbot:   LDI {lh_num}
        DIV CTY
        DIVI 2
        ADDI {MID}
        ST  CBOT            ; the floor line at TY == the wall drawEnd there
        SUBI {H3D}
        BRN mbase
        LDI {H3D - 1}
        ST  CBOT            ; near clamp: the sprite slides up, stays whole
mbase:  LD  MI
        ADDI MHPB
        LDA                 ; this monster's live HP (M7b's ledger)
        BRZ mdead
        LD  MI
        ADDI 1
        ST  CID             ; alive: a hit candidate, THINGS index + 1
        LD  MSP
        JMP mstrip
mdead:  LDI 0
        ST  CID             ; a corpse: still selected, painted and z-tested,
        LDI 2               ; but never a hit candidate again — and it paints
mstrip: MULI {MON_STRIDE}   ; from the shared corpse stripe (frame 2)
        ADD COFF
{colscale}        ADDI SPRB
        ST  CBASE           ; the band's column words start here
; the 3-slot far-first insertion: nearest three kept, slot 0 = farthest;
; strict < everywhere, so ties keep the earlier THINGS index
        LD  CTY
        SUB STY0
        BRN insa
        JMP mnext           ; not nearer than the farthest kept: dropped
insa:   LD  CTY
        SUB STY1
        BRN sh01
        JMP put0            ; nearer than slot 0 only: it replaces slot 0
sh01:   LD  STY1            ; slot 1 retreats to slot 0 …
        ST  STY0
        LD  SSX1
        ST  SSX0
        LD  SEX1
        ST  SEX0
        LD  SBA1
        ST  SBA0
        LD  SBO1
        ST  SBO0
        LD  SBN1
        ST  SBN0
        LD  SID1
        ST  SID0
        LD  CTY
        SUB STY2
        BRN sh12
        JMP put1
sh12:   LD  STY2            ; … slot 2 retreats to slot 1 …
        ST  STY1
        LD  SSX2
        ST  SSX1
        LD  SEX2
        ST  SEX1
        LD  SBA2
        ST  SBA1
        LD  SBO2
        ST  SBO1
        LD  SBN2
        ST  SBN1
        LD  SID2
        ST  SID1
        LD  CTY             ; … and the candidate takes slot 2 (nearest)
        ST  STY2
        LD  CSX0
        ST  SSX2
        LD  CSX1
        ST  SEX2
        LD  CBASE
        ST  SBA2
        LD  CBOT
        ST  SBO2
        LD  CBAND
        ST  SBN2
        LD  CID
        ST  SID2            ; the hit id (0 for a corpse — occupancy is STY)
        JMP mnext
put1:   LD  CTY
        ST  STY1
        LD  CSX0
        ST  SSX1
        LD  CSX1
        ST  SEX1
        LD  CBASE
        ST  SBA1
        LD  CBOT
        ST  SBO1
        LD  CBAND
        ST  SBN1
        LD  CID
        ST  SID1
        JMP mnext
put0:   LD  CTY
        ST  STY0
        LD  CSX0
        ST  SSX0
        LD  CSX1
        ST  SEX0
        LD  CBASE
        ST  SBA0
        LD  CBOT
        ST  SBO0
        LD  CBAND
        ST  SBN0
        LD  CID
        ST  SID0
mnext:  INCM MI
        JMP msel

; ── the paint: slots 0 -> 2 (far to near), per column ZBUF-occluded ─────────
; The slot scalars are field-major triples, LDA'd by SLOT; per visible column
; ONE packed word carries the whole strip and the shared chain below walks it
; bottom-up. A monster never touches rows > its BOT <= 39, so the gun and HUD
; paint after this phase overpaint nothing they don't own.
mpaint: LDI 0
        ST  SLOT
mslot:  LD  SLOT
        ADDI STY0
        LDA
        ST  WTY
        SUBI {MON_FAR}
        BRN mslotv          ; occupied: a kept candidate is strictly nearer
        JMP mslotn          ; … than the far cull an empty slot still holds
mslotv: LD  SLOT
        ADDI SSX0
        LDA
        ST  WX
        LD  SLOT
        ADDI SEX0
        LDA
        ST  WX1
        LD  SLOT
        ADDI SBA0
        LDA
        ST  WPTR            ; advances every column, skipped ones included
        LD  SLOT
        ADDI SBO0
        LDA
        ST  WBOT
        LD  SLOT
        ADDI SBN0
        LDA
        ST  WBAND
mcol:   LD  WX
        SUB WX1
        SUBI 1
        BRN mcolb
        JMP mslotn          ; past the last column: the slot is painted
mcolb:  LD  WX
        BRN mcadv           ; x < 0: clipped off the left edge
        SUBI {WIDTH}
        BRN mcz
        JMP mslotn          ; x > {WIDTH - 1}: clipped off the right edge
mcz:    LD  WX
        ADDI ZBUF
        LDA                 ; this column's wall depth
        ST  TMP
        LD  WTY
        SUB TMP
        BRN mcvis
        JMP mcadv           ; the wall is nearer: occluded, per column
; the hit test (M7b) rides HERE — the crosshair column, on the far side of the
; occlusion test, so a wall between the pistol and the monster saves it. Slots
; run far -> near, so the LAST write is the nearest live billboard under it.
mcvis:  LD  LIVE
        BRZ mcpix           ; no round spent this frame: nothing can be hit
        LD  WX
        SUBI {CROSSHAIR}
        BRZ mchit
        JMP mcpix
mchit:  LD  SLOT
        ADDI SID0
        LDA                 ; the slot's hit id — 0 for a corpse
        BRZ mcpix
        ST  HIT
""".splitlines()
    lines += f"""\
mcpix:  LD  WPTR
        LDA
        ST  Q               ; the whole sprite column in one packed word
        LD  WBOT
        MULI {WIDTH}
        ADD WX
        MULI 8
        ADDI C_CURS
        ST  ADDRV           ; the bottom pixel's pre-encoded CURS word
        LD  WBAND
        BRZ mch0
        SUBI 1
        BRZ mch1
        JMP chain_h5
mch0:   JMP chain_h14
mch1:   JMP chain_h9
""".splitlines() if not geom.tiled else f"""\
mcpix:  LD  WPTR
        LDA
        ST  Q               ; slice 0: the column's bottom 14 rows
        LD  WPTR
        ADDI 1
        LDA
        ST  Q2              ; slice 1: the 14 above them
        ; Which panel the bottom pixel is on, and where in it. The chain climbs
        ; from here, so it may cross the seam at row {TH} once — never twice, and
        ; never off the top: BOT >= {MID} and the walk is {span} rows.
        LD  WBOT
        SUBI {TH}
        BRN mcup            ; BOT < {TH}: the bottom pixel is already on a top panel
        ST  WROW
        LD  WX
        SUBI {TW}
        BRN mcbl
        LDI {sel[3]}
        JMP mcgo
mcbl:   LDI {sel[2]}
        JMP mcgo
mcup:   LD  WBOT
        ST  WROW
        LD  WX
        SUBI {TW}
        BRN mctl
        LDI {sel[1]}
        JMP mcgo
mctl:   LDI {sel[0]}
mcgo:   ST  WSEL
        LD  WX
        MODI {TW}
        ST  WXT             ; the column inside its own panel
""".splitlines()
    # The shared unrolled chain. On one panel it is 14 blocks with three entry
    # points, one per band height. On the wall it is {span} blocks with ONE
    # entry: a column is padded to a whole number of 14-row slices, so a short
    # band walks into its own transparent padding and the chain's existing
    # branch over a 0 nibble is all the dispatch it needs.
    for j in range(span):
        if not geom.tiled:
            entry = {0: "chain_h14:", 5: "chain_h9:", 9: "chain_h5:"}.get(j)
            note = {0: " ; 14 blocks: the near band's strip",
                    5: "  ; enter here for the mid band's 9",
                    9: "  ; enter here for the far band's 5"}.get(j, "")
            if entry:
                lines.append(f"{entry}{note}")
            lines += f"""\
        LD  Q
        MODI 16
        BRZ csk{j}            ; nibble 0: a transparent pixel
        ST  TMP
        LD  ADDRV
        SND                 ; CURS: the pixel's panel address
        LD  TMP
        MULI 8
        ADDI {run1}             ; RUN word: 1 pixel of the nibble's colour
        SND
csk{j}:   LD  Q
        DIVI 16
        ST  Q
        LD  ADDRV
        SUBI 512
        ST  ADDRV           ; up one panel row (64 cells * 8)
""".splitlines()
            continue
        if j == 0:
            lines.append(f"chain:  ; one entry, {span} blocks (see Geom.mon_words)")
        if j and j % 14 == 0:
            lines += f"""\
        LD  Q2
        ST  Q               ; slice {j // 14}: the packed word is spent, take the next
""".splitlines()
        lines += f"""\
        LD  WROW
        MULI {TW}
        ADD WXT
        MULI 8
        ADDI C_CURS
        MULI 8
        ADD WSEL
        ST  ADDRV           ; this pixel's router CURS word, panel and all
        LD  Q
        MODI 16
        BRZ csk{j}            ; nibble 0: a transparent pixel
        ST  TMP
        LD  ADDRV
        SND
        LD  TMP
        MULI 8
        ADDI {run1}
        MULI 8
        ADD WSEL
        SND                 ; RUN word: 1 pixel of the nibble's colour
csk{j}:   LD  Q
        DIVI 16
        ST  Q
        LD  WROW
        BRZ ccr{j}            ; row 0: the next step is on the panel above
        SUBI 1
        ST  WROW
        JMP cdn{j}
ccr{j}:   LDI {TH - 1}
        ST  WROW
        LD  WSEL
        SUBI {up}
        ST  WSEL            ; T2 -> T0 and T3 -> T1 are the same step
cdn{j}:
""".splitlines()
    lines += """\
mcadv:  INCM WPTR
        INCM WX
        JMP mcol
mslotn: INCM SLOT           ; ACC = the slot just finished
        SUBI 2
        BRZ mhitap          ; that was slot 2: all billboards painted
        JMP mslot

; ── the shot lands (M7b): one HP off the monster the crosshair caught ───────
; AFTER the phase that drew it, so this frame still shows the live sprite under
; the muzzle flash and the corpse appears in the NEXT one — the selection loop
; above read MHPB before this decrement ever ran. hp 0 is a corpse: it stays
; selected and painted but records id 0, so a later round at the same crosshair
; carries straight through it into whatever still stands behind.
mhitap: LD  HIT
        BRZ gun             ; a dry fire, a miss, or a corpse under the sight
        ADDI MHPB
        SUBI 1
        ST  TMP2            ; the victim's HP slot
        LDA
        SUBI 1
        ST  TMP
        LD  TMP2
        MOVA TMP            ; store[MHPB + HIT - 1] -= 1
""".splitlines()
    if wpc > 1:
        # A column is `wpc` packed words now, so the cursor over them steps by
        # that rather than by one. Patched rather than branched into the block
        # above because it is one line of a hundred and the rest is identical.
        i = lines.index("mcadv:  INCM WPTR")
        lines[i:i + 1] = ["mcadv:  LD  WPTR",
                          f"        ADDI {wpc}",
                          f"        ST  WPTR            ; one column on = {wpc} words"]
    return lines



def _pistol_asm(geom: Geom, art: Art) -> list[str]:
    """The pistol: one command word on the single panel, CURS + RUN words on the wall.

    The unit *bakes* both sprites, which is why the committed machine spends one
    word a frame on the gun — but a baked sprite is a chain of descent columns
    inside one trie arm, and the arm has a fixed budget: ten rows of DATA window
    per run and a leaf's worth of columns for the chain.  A 2x pistol is twice as
    many runs, each twice as long, and it also straddles the panel seam at
    ``x = 64`` so every run splits in two.  It does not fit, and widening the arm
    would move every band row in :mod:`lm1.d3_unit` — a re-tune of the committed
    unit to buy the *other* machine a sprite.

    So at 128x96 the CPU draws it, exactly as it already draws the mugshot:
    ``span_words`` per run, two baked variants, the same FIRE branch.  It costs
    ROM rather than an arm, and ROM is what this machine has spare.
    """
    if not geom.tiled:
        return f"""
; ── the pistol (V4): ONE command word — the unit bakes both sprites ──────────
gun:    LD  FIRE
        BRZ gidle
        LDI C_GUNF
        SND                 ; the recoil frame, muzzle flash blooming above
        JMP hud
gidle:  LDI C_GUN
        SND                 ; the idle pistol, bottom-centre
""".splitlines()
    lines = f"""
; ── the pistol: CURS + RUN words, the CPU's own (see _pistol_asm) ───────────
; The sprite straddles the seam at x = {geom.tile_w}, so a run is up to two panels'
; spans; the words below are constants and the FIRE bit picks the variant.
gun:    LD  FIRE
        BRZ gidle
        JMP gfire
""".splitlines()
    for label, table, nxt, note in (("gidle", art.gun_idle, "hud", "the idle pistol"),
                                    ("gfire", art.gun_fire, None, "recoil + muzzle flash")):
        first = True
        for r, c, colors in table:
            for word in span_words(r, c, colors, geom):
                head = f"{label}:" if first else ""
                lines.append(f"{head:<8}LDI {word}" + (f"          ; {note}" if first else ""))
                lines.append("        SND")
                first = False
        if nxt:
            lines.append(f"        JMP {nxt}")
    return lines


def _curs_word(geom: Geom, col: int, row: int) -> int:
    """The ``CURS`` command word that parks a panel's cursor on a screen pixel."""
    return unit_word("CURS", geom.tile_addr(col, row), geom.tile_of(col, row), geom)


def _sel_suffix(geom: Geom, col: int, row: int) -> list[str]:
    """The two instructions that turn a unit word into a router word, if needed.

    A ``RUN`` whose count is only known at run time is built in the accumulator,
    so on the wall it needs the selector shifted in there too — one ``MULI 8``
    and one ``ADDI``.  On a single panel there is no router and this is empty,
    which is how the committed asm stays what it was.
    """
    if not geom.tiled:
        return []
    from randomfun2026solvers.lm1 import d3_router

    tile = geom.tile_of(col, row)
    return ["        MULI 8", f"        ADDI {d3_router.SEL[f'T{tile}']}    ; ... to panel T{tile}"]


def _digits_asm(geom: Geom, art: Art) -> list[str]:
    """The live readouts as DOOM's own numerals, lowered from :func:`_paint_digits`.

    Seven glyph boxes — ammo's three, health's three and ``STTPRCNT`` — painted
    by **one** chain, because a glyph is packed exactly like a billboard column
    (a nibble a row, bottom row first, 0 transparent) and the only thing that
    differs between boxes is where the cursor starts.  ``DA`` carries that, and
    the chain leaves it on the *next* box, which is what makes a three-digit
    well a three-lap loop rather than three copies of the art.

    That sharing is the whole reason the numerals fit.  Baking a CURS+RUN table
    per glyph per box is 10 glyphs x 6 boxes x 8 rows of art — thousands of ROM
    words, and on this machine a ROM word is a per-frame tick tax forever.
    Packed columns put the art in the *tape* (11 x ``dw`` words, boot-loaded
    with the rest of the preamble) and leave the ROM one loop.

    Blanking is ``STlib_drawNum``'s: the hundreds box paints only from 100, the
    tens only from 10, the units always — so 50 rounds reads ``50`` and not
    ``050``, and 0 health still reads ``0``.
    """
    dw, dh = art.dig_box
    boxes = art.dig_slots
    if len(boxes) != 7 or not art.digits:
        raise ValueError("the numerals need seven boxes and a packed glyph table")
    if len(art.digits) != DIGIT_GLYPHS * dw:
        raise ValueError(f"{len(art.digits)} glyph words, {DIGIT_GLYPHS * dw} expected")
    for base in (0, 3):
        want = [(boxes[base][0] + k * dw, boxes[base][1]) for k in range(3)]
        if list(boxes[base:base + 3]) != want:
            raise ValueError(
                f"the three numeral boxes at {base} are not consecutive ({want} "
                "wanted): the digit loop advances the cursor by one box a lap")
    # One panel for every pixel of every glyph, so the selector is a constant and
    # a RUN word can be built with an ADDI rather than the sprite chain's ADD.
    tiles = {geom.tile_of(col + k, row - j)
             for col, row in boxes for k in range(dw) for j in range(dh)}
    if len(tiles) != 1:
        raise ValueError(f"the numerals straddle panels {sorted(tiles)}; the "
                         "digit chain builds one selector into its words")
    tile = tiles.pop()
    step = 64 if geom.tiled else 8       # one ADDR column, or one colour index
    rstep = step * geom.tile_w           # one panel row UP
    run1 = unit_word("RUN", 16, tile, geom)   # RUN 1 x colour 0
    r0 = boxes[0][1] - dh + 1
    lines = f"""
; ── the live readouts (M10): DOOM's REAL numerals ────────────────────────────
; STTNUM0..9 and STTPRCNT, {dw}x{dh} cells on rows {r0}..{boxes[0][1]} — three
; digits ending at ST_AMMOX (columns {boxes[0][0]}..{boxes[2][0] + dw - 1}) and three plus the
; percent sign at ST_HEALTHX ({boxes[3][0]}..{boxes[6][0] + dw - 1}), which is
; wadimport.STBAR_REGIONS scaled onto this strip and so id's own placement.
; A glyph is packed like a billboard column (DIGB: one word per glyph column, a
; nibble a row, bottom first, 0 transparent) and ONE chain paints all seven
; boxes: DA is the cursor of the box being painted and the chain leaves it on
; the next one. Blanking is STlib_drawNum's — hundreds from 100, tens from 10,
; units always.
dnums:  LD  AMMO
        ST  DSRC
        LDI {_curs_word(geom, *boxes[0])}
        ST  DA              ; the ammo number's leftmost numeral box
        LDI 0
        ST  DRET
dnum:   LDI 100
        ST  DDIV            ; three digits, most significant first
dnd:    LD  DDIV
        SUBI 1
        BRZ dndraw          ; the units box draws even for a 0
        LD  DSRC
        SUB DDIV
        BRN dnskip          ; a leading zero: DOOM draws nothing there
dndraw: LD  DSRC
        DIV DDIV
        MODI 10
        ST  DVAL
        JMP dglyph
dnskip: LD  DA
        ADDI {step * dw}
        ST  DA              ; the blank box is stepped over, not painted
dnnext: LD  DDIV
        DIVI 10
        ST  DDIV
        BRZ dnend           ; 100, 10, 1, then 0: the number is done
        JMP dnd
dnend:  LD  DRET
        BRZ dnhp
        JMP dpct
dnhp:   LD  HEALTH
        ST  DSRC
        LDI {_curs_word(geom, *boxes[3])}
        ST  DA              ; the health number's own well
        LDI 1
        ST  DRET
        JMP dnum
dpct:   LDI {DIGIT_GLYPHS - 1}
        ST  DVAL            ; STTPRCNT, the glyph after the health number
        LDI {_curs_word(geom, *boxes[6])}
        ST  DA
        LDI 2
        ST  DRET
; the shared glyph chain: {dw} columns of {dh} bottom-up nibbles
dglyph: LD  DVAL
        MULI {dw}
        ADDI DIGB
        ST  DPTR            ; this glyph's first packed column
        LDI {dw}
        ST  DKN
dgcol:  LD  DPTR
        LDA
        ST  Q               ; the whole column in one packed word
        LD  DA
        ST  DAC             ; the cursor climbing it
""".splitlines()
    for j in range(dh):
        lines += f"""\
        LD  Q
        MODI 16
        BRZ dgs{j}            ; nibble 0: the bar art shows through
        ST  TMP
        LD  DAC
        SND
        LD  TMP
        MULI {step}
        ADDI {run1}
        SND                 ; RUN word: 1 pixel of the nibble's colour
""".splitlines()
        if j == dh - 1:
            lines.append(f"dgs{j}:")
            continue
        lines += f"""\
dgs{j}:   LD  Q
        DIVI 16
        ST  Q
        LD  DAC
        SUBI {rstep}
        ST  DAC             ; up one panel row
""".splitlines()
    lines += f"""\
        LD  DA
        ADDI {step}
        ST  DA              ; the glyph's next column — and, after the last of
        INCM DPTR           ; them, the next numeral box
        LD  DKN
        SUBI 1
        ST  DKN
        BRZ dgend
        JMP dgcol
dgend:  LD  DRET
        SUBI 2
        BRZ face            ; the percent sign was the last thing on the strip
        JMP dnnext
""".splitlines()
    return lines


def _hud_bg_words(geom: Geom) -> list[tuple[int, str]]:
    """The status bar's static art as ``CURS`` + ``RUN`` words, per panel.

    The strip is the frame's bottom ``hud_h`` rows.  On one panel that is a
    single cursor park and the RLE of the whole strip.  On the wall it is the
    bottom row of panels, and each one's slice of the strip is contiguous in
    *its* raster — so it is still one cursor and one RLE per panel, which is why
    a 2x2 of 64x48 is so much cheaper to drive than any other cut of 128x96.
    """
    art = art_for(geom)
    across, _down = geom.tiles
    rows = art.hud_bg
    out: list[list[tuple[int, str]]] = []
    for tx in range(across):
        x0, y0 = tx * geom.tile_w, geom.h3d
        tile = geom.tile_of(x0, y0)
        flat = "".join(r[x0:x0 + geom.tile_w] for r in rows)
        words = [(unit_word("CURS", geom.tile_addr(x0, y0), tile, geom),
                  "CURS: the panel cursor to the strip's top-left")]
        words += [(unit_word("RUN", n * 16 + c, tile, geom),
                   f"RUN {n} x colour {c}") for c, n in _rle(flat)]
        out.append(words)
    return [w[i] for i in range(max(map(len, out))) for w in out if i < len(w)]


def _column_send_asm(geom: Geom, slots: dict[str, int]) -> list[str]:
    """The per-column paint: one COL command word per panel the column touches.

    On a single panel this is one word and always has been — the wall run
    ``drawStart..drawEnd`` in COLOR, then the unit's own floor run down to the
    viewport's last row, painted while the CPU raycasts the next column.

    On the wall it is up to two, because a 128x96 viewport column crosses the
    seam at row ``tile_h`` and the panels either side of it are different rooms.
    The arithmetic does not change at all — a panel is still ``tile_w`` wide, its
    wall lap still adds ``tile_w * 16`` before painting, the argument still
    splits on 64 — only *which* rows go to *which* selector does.  Three cases,
    and the third is the one worth naming:

    * the wall lies above the seam: the top panel takes it and floors to its own
      last row; the bottom panel has no wall at all, so it gets a one-pixel
      floor-coloured run at its row 0 purely to give the unit something to floor
      *from* (the floor run only exists after a wall run);
    * the wall crosses the seam: one command each, clipped;
    * the wall starts below the seam: the top panel gets **nothing**, because
      everything above a wall is ceiling and COMMIT already cleared it to black.

    The nukage flood (M5) is per panel for the same reason, and the no-wall case
    parks ``TBE`` at -1 so the flood covers the panel's row 0 as well — otherwise
    a grey scanline survives along the seam on every damage floor.
    """
    H3D, TW, TH = geom.h3d, geom.tile_w, geom.tile_h  # noqa: N806
    STRIDE, BIAS, RADIX = TW, TW * 16, 64  # noqa: N806 — the unit's, not the frame's
    if not geom.tiled:
        return f"""\
        ; (no shade block: whx/why already picked the sunlit or dark colour)
        ; the whole column is ONE command word to the unit, which paints the wall
        ; run (drawStart..drawEnd in COLOR) and the floor run (drawEnd+1..{H3D - 1}
        ; in 8) at stride {STRIDE} while the CPU raycasts the next column; the
        ; ceiling stays black because COMMIT cleared the next buffer. The arg is
        ; the unit's own loop seed: seed = (drawStart*{STRIDE} + x)*16 + colour - 1024
        ; (its wall lap adds 1024 *before* painting), then arg = seed*64 + n_wall
send:   LD  DSTART
        MULI {STRIDE}
        ADD XCOL
        MULI 16
        ADD COLOR
        SUBI {BIAS}           ; seed (may go negative in the top row: that is fine)
        MULI {RADIX}
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
        MULI {STRIDE}
        ADD XCOL
        MULI 16
        ADDI {FLOOR_NUKE}
        SUBI {BIAS}
        MULI {RADIX}
        ADDI {H3D - 1}
        SUB DEND            ; arg = seed*64 + ({H3D - 1} - drawEnd)
        MULI 8
        SND""".splitlines()

    from randomfun2026solvers.lm1 import d3_router
    from randomfun2026solvers.lm1.store import DoomUnit

    codes = DoomUnit.CODES
    sel = [d3_router.SEL[f"T{t}"] for t in range(4)]
    bot = H3D - TH - 1  # the bottom panel's last viewport row (31 at 128x96)
    return f"""\
        ; ── the tiled send: one COL word per panel this column touches ────────
        ; The frame is four {TW}x{TH} panels, so a viewport column crosses the seam
        ; at row {TH}. TXT is the column inside its panel; TSELT and TSELB are the
        ; router selectors for the panel above the seam and the one below it.
        ; Every other number here is the single-panel machine's, unchanged:
        ; stride {STRIDE}, lap bias {BIAS}, argument radix {RADIX}.
send:   LD  XCOL
        SUBI {TW}
        BRN sleft           ; x < {TW}: the left pair, panels T0/T2
        ST  TXT             ; the right pair: TXT = x - {TW}, panels T1/T3
        LDI {sel[1]}
        ST  TSELT
        LDI {sel[3]}
        ST  TSELB
        JMP stop
sleft:  LD  XCOL
        ST  TXT
        LDI {sel[0]}
        ST  TSELT
        LDI {sel[2]}
        ST  TSELB
        ; ── the top panel: wall rows drawStart..min(drawEnd, {TH - 1}) ─────────
stop:   LD  DSTART
        SUBI {TH}
        BRN stwall          ; drawStart < {TH}: this panel carries wall
        JMP sbot            ; the wall starts below the seam: all ceiling here
stwall: LD  DEND
        SUBI {TH - 1}
        BRN stkeep
        LDI {TH - 1}
        ST  TTE             ; clipped at the seam
        JMP stsend
stkeep: LD  DEND
        ST  TTE
stsend: LD  DSTART
        MULI {STRIDE}
        ADD TXT
        MULI 16
        ADD COLOR
        SUBI {BIAS}
        MULI {RADIX}
        ADD TTE
        SUB DSTART
        ADDI 1              ; arg = seed*64 + n_wall
        MULI 8              ; the unit word: 8*arg + C_COL, C_COL == 0
        MULI 8
        ADD TSELT
        SND                 ; ... and the router word: 8*unit + sel
        LD  NUKE
        BRZ sbot            ; clean floor: the COL word's gray floor stands
        LD  TTE
        SUBI {TH - 1}
        BRZ sbot            ; wall to the panel's last row: no floor to flood
        LD  TTE
        ADDI 1
        MULI {STRIDE}
        ADD TXT
        MULI 16
        ADDI {FLOOR_NUKE}
        SUBI {BIAS}
        MULI {RADIX}
        ADDI {TH - 1}
        SUB TTE
        MULI 8
        MULI 8
        ADD TSELT
        SND
        ; ── the bottom panel: viewport rows {TH}..{H3D - 1} = its own 0..{bot} ──
sbot:   LD  DEND
        SUBI {TH}
        BRN sbfl            ; the wall ended above the seam: all floor down here
        LD  DSTART
        SUBI {TH}
        BRN sbs0
        ST  TBS             ; the wall starts below the seam
        JMP sbe
sbs0:   LDI 0
        ST  TBS             ; the wall crosses it: this panel starts at row 0
sbe:    LD  DEND
        SUBI {TH}
        ST  TBE
        LD  TBS
        MULI {STRIDE}
        ADD TXT
        MULI 16
        ADD COLOR
        SUBI {BIAS}
        MULI {RADIX}
        ADD TBE
        SUB TBS
        ADDI 1
        MULI 8
        MULI 8
        ADD TSELB
        SND
        JMP sbnuk
sbfl:   LDI 0
        SUBI 1
        ST  TBE             ; -1: the flood below must cover row 0 too
        LD  TXT
        MULI 16
        ADDI 8
        SUBI {BIAS}
        MULI {RADIX}
        ADDI 1              ; one seed pixel at row 0 — the unit floors the rest
        MULI 8
        MULI 8
        ADD TSELB
        SND
        ; The seed pixel is the wall loop's FIRST lap, and lap 0 is the banding
        ; seam: its mask is 7, and 8 & 7 == 0. The floor colour is the one colour
        ; that cannot survive the mask (the nukage flood picks 2 precisely
        ; because it can), so row 0 would be a black scanline right along the
        ; seam. Repaint it with a CURS + RUN pair, which the mask never touches.
        LD  TXT
        MULI 8
        ADDI C_CURS
        MULI 8
        ADD TSELB
        SND                 ; CURS: the bottom panel's row 0, this column
        LDI {8 * (16 + 8) + codes["RUN"]}
        MULI 8
        ADD TSELB
        SND                 ; RUN 1 x colour 8 — the seam pixel, unmasked
sbnuk:  LD  NUKE
        BRZ colnxt
        LD  TBE
        SUBI {bot}
        BRZ colnxt          ; wall to the panel's last row: no floor to flood
        LD  TBE
        ADDI 1
        MULI {STRIDE}
        ADD TXT
        MULI 16
        ADDI {FLOOR_NUKE}
        SUBI {BIAS}
        MULI {RADIX}
        ADDI {bot}
        SUB TBE
        MULI 8
        MULI 8
        ADD TSELB
        SND""".splitlines()


def deadman3d_source(
    geom: Geom = GEOM64,
    *,
    dda_acc_reload: bool = True,
    dda_diff: bool = False,
    lap_via_jump: bool = False,
    dda_stepy_split: bool = False,
    ray_acc_chain: bool = False,
) -> str:
    """The LM-1 assembly of the demo, lowered line for line from this model.

    Structure: boot loop (round 0's data preamble -> tape slots 1..451, the
    loop 8x-unrolled because a backward jump costs ``8*(P - loop)`` ticks) ->
    ``title:`` (round 0's title screen: the pre-encoded RUN words forwarded
    ``IN``/``SND`` 8 per counted lap, then one COMMIT) -> ``round:`` MUX decode (MODI 2 / DIVI 2 ladder -> BW BS BA BD FIRE) ->
    conditional turn (the packed heading word re-unpacked) -> conditional move
    (per-axis collision, the map-cell lookup inlined) -> ``render:`` (a
    per-frame prologue seeds PW0/WADDR0/FRACX/FRACY; then per column: setup,
    the :data:`DDA_UNROLL`-way unrolled DDA maintaining PW/WADDR incrementally,
    per-arm hit tails ``whx``/``why`` that bake the sunlit/dark shading,
    projection, the M7a ZBUF depth store, and ONE ``SND`` command word to the
    DOOM unit) -> the sprite phase (:func:`_sprite_phase_asm` — monster
    selection over MONB/MHPB, the far-first slot file, the ZBUF-occluded
    banded billboard paint through the shared unrolled nibble chain, M7b's
    crosshair hit test inside it and the HP decrement after it) -> the
    FIRE pistol sprite (GUN or GUNF by the FIRE bit), the HUD (one CURS, the
    background RUN constants, then the live bars) and the commit -> back to
    ``round:``.  The lodev variable each block computes is named in its
    comments; every expression keeps :func:`render`'s exact operation order,
    which is the pixel contract.

    ``dda_acc_reload`` is the one *program* difference between the two tiers, and
    it exists only because the tiers share this source. The unrolled x-arm used to
    do ``ST WADDR`` and then ``LD WADDR``; ``ST`` is ACC-preserving, so the reload
    re-fetched a word the accumulator already held — 5,536 executions in nine
    frames at ~471 ticks each, **4.32% of the run** (``scratch/DOOM-OPCODES.md``
    §5). Passing ``False`` deletes it from all :data:`DDA_UNROLL` copies, which is
    what :func:`taped_program` does. It defaults to ``True`` because the canonical
    men-v3 grid (``deadman-3d.man`` / ``_trim`` / ``_v2``) is byte-frozen at
    ``f62d63fd`` and the checked-in ``deadman-3d.asm`` is what pins it; only the
    taped family is allowed to move (the same rule :data:`machine.TIER_LAYOUT`,
    :data:`machine.MEM_PAD_FOR` and friends already follow, one level down).

    ``lap_via_jump`` is a third, and it is about *control flow* rather than
    memory. :data:`machine.SEEK_OPS` is ``("JMPF",)`` — only a **jump** is
    rewritten to the seek drum, and a ``BRN``/``BRZ`` keeps its classic discard
    loop whatever its distance. A *backward* branch's forward-skip count is
    nearly the whole ring, so every one of this program's three loop laps
    recirculates ~2,200–3,900 words at 8 ticks each:

    ======================  =====================  ==========  =============
    lap                     branch                 laps/frame  ticks/frame
    ======================  =====================  ==========  =============
    ``xarm{last}``          ``BRZ dda0``           15.2        269,564
    ``hity{last}``          ``BRZ dda0``           3.3         41,424
    ``boot`` / ``title``    ``BRN boot/title``     round 0     3.3M one-off
    ======================  =====================  ==========  =============

    Passing ``True`` routes each lap through a two-word ``JMP`` stub, which
    ``seek_split`` *can* take, trading ~17,700 ticks a lap for one ~1,008-tick
    seek. Nothing else about the loops changes; it costs three instructions.

    ``dda_diff`` is the second such tier-only program change, and it attacks the
    same quantity from the other end: **how many store reads the DDA issues.**
    The loop's only use of ``sideDistX`` inside the step is the sign of
    ``sideDistX - sideDistY``, so the canonical form re-derives that difference
    every step (``LD SDX`` / ``SUB SDY``) and then re-reads ``SDX`` again inside
    the x-arm to increment it — four reads of the pair per step. Passing ``True``
    keeps the *difference* in the ``SDX`` slot instead, renamed ``SDD``:

    ======================  =========================  ======================
    quantity                canonical                  ``dda_diff``
    ======================  =========================  ======================
    the compare             ``LD SDX`` / ``SUB SDY``   ``LD SDD``
    ``sideDistX += ddX``    ``LD SDX``/``ADD DDX``     ``ADD DDX`` (ACC is
                            /``ST SDX``                already ``SDD``:
                                                       ``BRN`` preserves it)
    ``sideDistY += ddY``    ``LD SDY``/``ADD DDY``     the same, **plus**
                            /``ST SDY``                ``SUB DDY``/``ST SDD``
    ``perpWallDist`` (x)    ``LD SDX``/``SUB DDX``     ``LD SDD``/``ADD SDY``
                                                       /``SUB DDX``
    ======================  =========================  ======================

    ``ray_acc_chain`` is the fourth, and it is the same lever aimed at the
    *per-column* setup instead of the per-step loop: the canonical ``colset``
    stores ``rayDirX`` and then loads it straight back to test it for zero, and
    does it again for ``rayDirY``. Reordering the block around ``ST``'s
    ACC-preservation deletes both loads and folds ``LD PLANEX`` into a
    commuted ``MUL``. Bit-identical arithmetic, four fewer store reads a
    column; the comment at the emission site has the whole rewrite.

    ``SDY`` is still carried absolutely, so the y-side tail is untouched and the
    x-side one reconstructs ``sideDistX = SDD + SDY``. Every value is a signed
    64-bit word under the same :func:`lm1.emulator.wrap` on every operation, so
    the incrementally-maintained ``SDD`` is bit-identical to a freshly computed
    ``SDX - SDY`` and ``BRN`` cannot see a difference. Net over nine frames:
    the x-arm (5,536 executions) loses one ``LD`` and one ``SUB``, the y-arm
    (1,584) gains one ``ST``, and the per-ray setup and x-side tail each gain
    one ``SUB``/``ADD``. Same reasoning, same gate, same tier rule as
    ``dda_acc_reload``.

    Regenerate with::

        from randomfun2026solvers.deadman3d import deadman3d_source
        from randomfun2026solvers.lm1.programs import PROGRAM_DIR
        (PROGRAM_DIR / "deadman-3d.asm").write_text(deadman3d_source())
    """
    from randomfun2026solvers.lm1.store import DoomUnit

    # The screen, rebound locally so every expression below reads it instead of
    # the module. At GEOM64 each of these IS the module constant, which is what
    # makes the committed machine byte-identical rather than merely intended to
    # be. The unit's own numbers — the panel stride, the wall lap's bias and the
    # radix its `/ 64` decode splits a COL argument on — live in
    # `_column_send_asm`, because all three stay 64 at either resolution and
    # that is the whole reason a 2x2 of 64x48 tiles needs no new unit.
    WIDTH, H3D, MID = geom.width, geom.h3d, geom.mid  # noqa: N806
    CROSSHAIR = geom.crosshair  # noqa: N806
    art = art_for(geom)
    slots = tape_slots(geom)
    first_free = len(preamble_words(geom)) + 1  # 452: the boot loop's stop address
    assert first_free == slots["ZBUF"], "the boot data ends where ZBUF begins"
    n_mon = len(MONSTERS)
    # `dda_diff` re-purposes the `SDX` slot to hold `sideDistX - sideDistY`; the
    # address is unchanged (it is still the hottest word in the machine and stays
    # in the bank the split was tuned for), only the name and its meaning move.
    if dda_diff:
        slots = {("SDD" if k == "SDX" else k): v for k, v in slots.items()}
    # ... and it needs sideDistY seeded before sideDistX, so the two per-ray
    # seed arms swap places and `ddy:` falls into the other one.
    _first_side = "sidey" if dda_diff else "sidex"

    def _backward_lap(op: str, target: str, stub: str, done: str, note: str) -> list[str]:
        """A loop's backward branch, optionally routed through a ``JMP``.

        ``machine.SEEK_OPS`` is ``("JMPF",)``: only a *jump* is rewritten to the
        seek drum. A ``BRN``/``BRZ`` keeps its classic discard loop, and a
        backward target's skip count is nearly the whole ring — so a backward
        branch recirculates ~P words at 8 ticks each, every lap, forever. Sending
        the lap through a two-word ``JMP`` stub trades that for one seek.
        """
        branch = f"        {op} {target if not lap_via_jump else stub}"
        if not lap_via_jump:
            return [f"{branch:<28}; {note}"]
        return [
            f"{branch:<28}; {note}",
            f"        {'JMP ' + done:<20}; ... and out, past the lap stub",
            f"{stub + ':':<8}{'JMP ' + target:<20}; the lap as a JMP, so `seek_split` can",
            "                            ; make it a seek: a taken BRN/BRZ would",
            "                            ; recirculate every word back to the top",
            f"{done}:",
        ]
    machine_tape = max(slots.values()) + 1  # the tape the registry must cover
    inv = UNITS * UNITS          # 1048576  — deltaDist numerator (1/rayDir, Q10*Q10)
    lh_num = geom.lh_num         # 81920 at 64x48 — lineHeight's numerator
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
        "SDX": "lodev sideDistX",
        "SDD": "lodev sideDistX - sideDistY, maintained incrementally",
        "SDY": "lodev sideDistY",
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
        "LIVE": "1 when this frame's FIRE actually spent a round (M7b): a dry "
                "fire flashes but kills nothing",
        "HIT": f"the monster the crosshair (column {CROSSHAIR}) caught, index + 1; "
               "0 = none",
        "MONB": f"..{slots['MONB'] + max(n_mon - 1, 0):<3} monster table (M7a): ((cx*64)+cy)*2 + species",
        "MHPB": f"..{slots['MHPB'] + max(n_mon - 1, 0):<3} initial monster HP (M7b's hit ledger)",
        "SPRB": f"..{slots['SPRB'] + 3 * geom.mon_stride * geom.mon_words - 1:<3} packed sprite columns: nibble 0 = bottom px, 0 = clear",
        "ZBUF": f"..{slots['ZBUF'] + WIDTH - 1:<3} per-column wall depth, rewritten whole every frame",
        "DET": "planeX*dirY - dirX*planeY (Q20) — the projection divisor, > 0",
        "MI": "the selection loop's monster index",
        "MSP": "the candidate's species (0 POSS / 1 TROO)",
        "MDX": "monster cell centre - posX, Q10", "MDY": "… - posY",
        "TXN": "camera x numerator dirY*MDX - dirX*MDY (Q20)",
        "TYN": "camera depth numerator planeX*MDY - planeY*MDX (Q20)",
        "CTY": "the candidate's depth TY = TYN*1024/DET — ZBUF's own units",
        "CBAND": "the candidate's scale band 0/1/2",
        "COFF": "the band's column offset in the sprite stripe (0/10/16)",
        "CHW": "the band's half width (5/3/2)",
        "CW1": "the band's width - 1 (9/5/3)",
        "CSX0": "the candidate's first screen column (may be < 0)",
        "CSX1": "the candidate's last screen column (may be > 63)",
        "CBOT": "the candidate's bottom row: the floor line at TY, clamped 39",
        "CBASE": "SPRB + frame*20 + band offset: the column words' base slot",
        "CID": "the candidate's hit id: monster index + 1, or 0 for a corpse",
        "STY0": "slot 0 (farthest kept) depth; FAR = empty",
        "STY1": "slot 1 depth", "STY2": "slot 2 (nearest kept) depth",
        "SSX0": "slot 0 first column", "SSX1": "slot 1 first column",
        "SSX2": "slot 2 first column",
        "SEX0": "slot 0 last column", "SEX1": "slot 1 last column",
        "SEX2": "slot 2 last column",
        "SBA0": "slot 0 sprite base", "SBA1": "slot 1 sprite base",
        "SBA2": "slot 2 sprite base",
        "SBO0": "slot 0 bottom row", "SBO1": "slot 1 bottom row",
        "SBO2": "slot 2 bottom row",
        "SBN0": "slot 0 band", "SBN1": "slot 1 band", "SBN2": "slot 2 band",
        "SID0": "slot 0 hit id: monster index + 1, 0 = a corpse (M7b)",
        "SID1": "slot 1 hit id", "SID2": "slot 2 hit id",
        "SLOT": "the paint loop's slot cursor 0..2 (far -> near)",
        "WTY": "the painting slot's depth (the ZBUF compare term)",
        "WX": "the painting column", "WX1": "the painting slot's last column",
        "WPTR": "the painting column's sprite word slot (BASE + column)",
        "WBOT": "the painting slot's bottom row",
        "WBAND": "the painting slot's band — picks the chain entry",
        "Q": "the column's packed nibbles, shifted down as the chain climbs",
        "ADDRV": "the pre-encoded CURS word of the pixel being painted",
        "PTR": "the boot loop's tape cursor",
    }
    if geom.tiled:
        equ_notes |= {
            "TXT": f"the column inside its panel: x mod {geom.tile_w}",
            "TSELT": f"router selector for the panel above the seam at row {geom.tile_h}",
            "TSELB": "router selector for the panel below it",
            "TTE": "the wall run's last row on the top panel (clipped at the seam)",
            "TBS": "the wall run's first row on the bottom panel",
            "TBE": "its last row; -1 when the wall ended above the seam",
            "Q2": "the billboard column's second 14-row slice",
            "WROW": "the row the billboard chain is climbing, inside its panel",
            "WSEL": "that panel's router selector",
            "WXT": f"the billboard column inside its panel: x mod {geom.tile_w}",
        }
    if geom.digits:
        dw, dh = geom.dig_box
        equ_notes |= {
            "DIGB": f"..{slots['DIGB'] + geom.dig_words - 1:<3} DOOM's {DIGIT_GLYPHS} numerals "
                    f"({dw}x{dh}), packed like a sprite column",
            "DSRC": "the number being drawn: AMMO, then HEALTH",
            "DDIV": "which digit of it: 100, then 10, then 1",
            "DRET": "which readout the glyph chain came from (0/1/2)",
            "DVAL": f"the glyph to paint: 0..9, or {DIGIT_GLYPHS - 1} for STTPRCNT",
            "DPTR": "the glyph's current packed column slot",
            "DKN": "columns of it left to paint",
            "DA": "the CURS word of the numeral box being painted",
            "DAC": "that cursor, climbing one column of it",
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
        f"; lm1/d3_unit.py), its input is its own, and its {machine_tape}-slot STORE rides the",
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
        "; POW16, the 16 packed heading words, the 64-word nukage bit plane, the spawn",
        f"; state, the {n_mon}-monster table with its HP block and the 60 packed sprite",
        "; columns — deadman3d.preamble_words())",
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
        f"; ── tape slots (deadman3d.tape_slots(); slots 1..{len(preamble_words(geom))} are the boot data) ──────",
    ]
    for name, addr in slots.items():
        lines.append(f".equ {name:<6} {addr:<4}         ; {equ_notes[name]}")
    if geom.tiled:
        lines += [
            "",
            "; ── the tiled wall (lm1/d3_router.py): four DOOM units behind a 1-of-4 ──────",
            "; router. A command word is the unit's own 8*arg + code with the router's tile",
            "; selector shifted in underneath it: 8*(8*arg + code) + sel. The panel is still",
            f"; {geom.tile_w}x{geom.tile_h} and so is every stride, bias and radix below —",
            "; only which panel a word reaches is new.",
            ".unit doom4",
        ]
        gun_tile = geom.tile_of(geom.width // 2, geom.h3d - 1)
        lines += [
            f".equ C_GUN    {unit_word('GUN', 0, gun_tile, geom)}"
            "         ; the unit's baked pistol — unused at this geometry",
            f".equ C_GUNF   {unit_word('GUNF', 0, gun_tile, geom)}"
            "         ; ... nor its recoil frame (see _pistol_asm)",
            f".equ C_RUN    {codes['RUN']}            ; {DoomUnit.ARM_NOTES['RUN']}",
            f".equ C_CURS   {codes['CURS']}            ; {DoomUnit.ARM_NOTES['CURS']}",
            f".equ C_COMMIT {commit_word(geom)}           ; SWAP 0 on ALL four panels"
            " at once (the router's broadcast leaf)",
        ]
    else:
        lines += [
            "",
            "; ── the DOOM unit (lm1/d3_unit.py): 8*arg + code, codes read off its trie ────",
            ".unit doom",
        ]
        for arm in ("COL", "RUN", "CURS", "GUN", "GUNF", "COMMIT"):
            lines.append(f".equ C_{arm:<6} {codes[arm]}            ; {DoomUnit.ARM_NOTES[arm]}")
    n_pre = len(preamble_words(geom))
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
        *_backward_lap("BRN", "boot", "bootl", "bootd",
                       f"keep looping while PTR < {boot_full + 1}"),
    ]
    for addr in range(boot_full + 1, n_pre + 1):
        # The tail slots are named when a name exists (the spawn scalars);
        # M7a's SPRB words end the preamble unnamed, so the ST is numeric.
        lines += ["        IN", f"        ST  {addr_name.get(addr, addr)}"]
    n_title = len(title_words(geom))
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
            *_backward_lap("BRN", "title", "titll", "titld",
                           f"keep looping while PTR < {title_laps}"),
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
        LDI 0
        ST  LIVE            ; both cleared every frame: only a round actually
        ST  HIT             ; spent arms the shot, and HIT is this frame's
        LD  CMD
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
        LDI 1
        ST  LIVE            ; … and THIS is the shot that can kill (M7b)

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
""".splitlines()
    # ── the per-column ray setup ────────────────────────────────────────────
    # `ray_acc_chain` reorders this block — and reorders *only* this block —
    # around one fact the canonical form spends four store reads a column
    # ignoring: `ST` is ACC-preserving, so `ST RDX` leaves rayDirX in the
    # accumulator and the very next thing the canonical form does with it is
    # `LD RDX` / `BRZ`. Three edits, all of them deletions:
    #
    #   * `LD PLANEX` / `MUL CAMX` becomes `MUL PLANEX`. Multiplication
    #     commutes and cameraX is already in ACC from its own `ST CAMX`, so the
    #     accumulator can be the *other* operand.
    #   * the deltaDistX zero test moves up against `ST RDX`, which deletes
    #     `LD RDX`; the same move against `ST RDY` deletes `LD RDY`.
    #   * `LD PLANEY` becomes `LD CAMX` (the RDY chain now starts from cameraX
    #     rather than ending on it) and the PW/WADDR seed, which neither arm
    #     reads or writes, moves below both of them to `rayseed`.
    #
    # Every expression keeps its operand order and its operation order, so the
    # arithmetic is bit-identical; what moves is *where the accumulator is*.
    # Worth 5,120 store reads and 5,120 instructions on the 21-round hi-res
    # tour — two a column over 2,560 columns.
    #
    # `rayseed` falls through into the first seed block, which is `sidey` only
    # when `dda_diff` swapped the two; without it the fall-through target is
    # `sidex` and the seed has to jump.
    _seed_tail = "" if dda_diff else f"        JMP {_first_side}\n"
    lines += ((f"""\
colset: LD  XCOL
        MULI {geom.cam_step}
        SUBI {UNITS}
        ST  CAMX            ; cameraX = 2*x/w - 1 -> {geom.cam_step}*x - 1024, exact at w = {WIDTH}
        MUL PLANEX          ; ACC is still cameraX, and `*` commutes
        DIVI {UNITS}
        ADD DIRX
        ST  RDX             ; rayDirX = dirX + planeX*cameraX
        ; deltaDistX = abs(1/rayDirX) -> |{inv} / rayDirX|; DIV by 0 is 0 on
        ; this CPU, so a zero ray substitutes BIG = 2**30 (plan risk R2)
        BRZ ddxinf          ; `ST` preserved it: rayDirX has not left ACC
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
ddy:    LD  CAMX
        MUL PLANEY
        DIVI {UNITS}
        ADD DIRY
        ST  RDY             ; rayDirY = dirY + planeY*cameraX
        BRZ ddyinf          ; deltaDistY, the same three arms
        LDI {inv}
        DIV RDY
        BRN ddyneg
        ST  DDY
        JMP rayseed
ddyneg: NEG
        ST  DDY
        JMP rayseed
ddyinf: LDI {BIG}
        ST  DDY
rayseed: LD  PW0
        ST  PW              ; the ray starts in the player's cell
        LD  WADDR0
        ST  WADDR
{_seed_tail}""" if ray_acc_chain else f"""\
colset: LD  XCOL
        MULI {geom.cam_step}
        SUBI {UNITS}
        ST  CAMX            ; cameraX = 2*x/w - 1 -> {geom.cam_step}*x - 1024, exact at w = {WIDTH}
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
        JMP {_first_side}
ddyneg: NEG
        ST  DDY
        JMP {_first_side}
ddyinf: LDI {BIG}
        ST  DDY
""").splitlines())
    # The seeds. `dda_diff` swaps the two arms' order so `sidey` runs first: the
    # x-arm then still has ACC = sideDistX when it is done (`ST` is the only
    # thing that would have consumed it) and folds the initial difference in
    # place — `SUB SDY` / `ST SDD` where the canonical arm writes one `ST SDX`.
    # Nothing downstream reads sideDistX absolutely again; the x-side hit tail
    # reconstructs it as SDD + SDY.
    def _sdx_seed(expr: str) -> str:
        if not dda_diff:
            return f"        ST  SDX             ; sideDistX = {expr}\n"
        return (f"        SUB SDY             ; sideDistX = {expr}\n"
                f"        ST  SDD             ; ... and only its lead over sideDistY is kept\n")

    def _sidex_block(suffix: str, after: str) -> str:
        """The stepX seed arms. ``suffix`` names the DDA loop they feed."""
        head = (
            "        ; stepX / sideDistX from the fractional position (lodev's two arms);\n"
            "        ; stepX itself is only ever used to move the word address, so the arm\n"
            "        ; records S4X = 4*stepX instead of stepX\n"
        ) if not suffix else (
            "        ; the same stepX seed, for rays whose stepY is -1 (dda_stepy_split)\n"
        )
        return head + f"""\
sidex{suffix}: {"" if suffix else " "}LD  RDX
        BRN sxneg{suffix}
        LDI 4
        ST  S4X             ; stepX = 1 -> the quarter-column slot moves +4
        LDI {UNITS}
        SUB FRACX
        MUL DDX
        DIVI {UNITS}
{_sdx_seed("(1024 - fracX) * deltaDistX / 1024")}        JMP {after}
sxneg{suffix}: {"" if suffix else " "}LDI 0
        SUBI 4
        ST  S4X             ; stepX = -1 -> -4
        LD  FRACX
        MUL DDX
        DIVI {UNITS}
{_sdx_seed("fracX * deltaDistX / 1024")}"""

    # `dda_stepy_split` emits the DDA twice, once per sign of stepY, so a copy
    # carries neither `LD STPY` nor the branch on it nor the PW arm it will never
    # take. The sign is known here, at the seed, and it picks which loop's own
    # stepX seed the ray falls into — so the scalar is not needed at all.
    _sidey_lines = [
        "sidey:  LD  RDY             ; stepY / sideDistY, the same two arms",
        "        BRN syneg",
        *([] if dda_stepy_split else ["        LDI 1", "        ST  STPY"]),
        f"        LDI {UNITS}",
        "        SUB FRACY",
        "        MUL DDY",
        f"        DIVI {UNITS}",
        "        ST  SDY",
        f'        JMP {"sidex" if dda_diff else "dda0"}',
        *(["syneg:  LD  FRACY"] if dda_stepy_split else
          ["syneg:  LDI 0", "        SUBI 1", "        ST  STPY", "        LD  FRACY"]),
        "        MUL DDY",
        f"        DIVI {UNITS}",
        "        ST  SDY",
    ]
    _sidey = "\n".join(_sidey_lines) + "\n"
    if dda_stepy_split:
        # sidey -> the up loop's seed (a jump); syneg falls into the down loop's.
        lines += (_sidey + _sidex_block("n", "ddan0")).splitlines()
    elif dda_diff:
        lines += (_sidey + _sidex_block("", "dda0")).splitlines()
    else:
        lines += (_sidex_block("", "sidey") + _sidey).splitlines()
    _unroll = DDA_SPLIT_UNROLL if dda_stepy_split else DDA_UNROLL
    _split_note = (f"""\
        ; ... and it is emitted twice, once per sign of stepY, at {DDA_SPLIT_UNROLL}x each:
        ; a copy then carries one PW arm instead of two and no `LD STPY` at all,
        ; which is 26 fewer words for the x-step's own BRN to discard
""" if dda_stepy_split else "")
    lines += (f"""\
        ; the DDA, unrolled {_unroll}x: a backward jump costs 8*(P - loop) ticks on
        ; this machine, so only every {_unroll}th empty step pays a full lap; a
        ; sideDist tie goes to the Y arm (lodev's else — risk R5)
""" + _split_note).splitlines()
    # The x-arm's reload of WADDR, kept only for the byte-frozen canonical tier.
    # `ST` is ACC-preserving (isa.py: "store[addr] = ACC (ACC preserved)", and
    # emulator._store writes `em.b` without ever assigning it), so the `LD WADDR`
    # that used to sit between `ST WADDR` and `LDA` re-read the word the
    # accumulator already held. `LDA` takes its address from ACC and clobbers A
    # first, so nothing downstream can see the difference.
    _xarm_reload = "        LD  WADDR\n" if dda_acc_reload else ""
    # The DDA's own backward lap is the machine's single most expensive branch:
    # `xarm{last}` / `hity{last}` end `BRZ dda0`, a *backward* BRZ, and `BRZ` is
    # not in `machine.SEEK_OPS` — so each lap recirculates ~2,200 ring words at
    # 8 ticks. Under `lap_via_jump` both arms branch to one local stub whose
    # `JMP dda0` the seek split can take.
    # `s` is "" for the single loop and for the stepY = +1 half of a split, "n"
    # for the stepY = -1 half. Everything the loop names carries it, so the two
    # halves are two independent label spaces over the same generator.
    for s in (("n", "") if dda_stepy_split else ("",)):
        _last = _unroll - 1
        _lap = f"lap{s}{_last}"
        if dda_stepy_split and s == "":
            # The up loop's own stepX seed sits between the two loops: `sidey`'s
            # positive arm jumps to it and it falls through into `dda0`.
            lines += _sidex_block("", "dda0").splitlines()
        for k in range(_unroll):
            nxt = (f"dda{s}{k + 1}" if k < _last
                   else (f"dda{s}0" if not lap_via_jump else _lap))
            nxt_note = "the next unrolled step" if k < _last else "the backward lap"
            # The step's compare. Canonically two reads (`SDX`, `SDY`) rebuild the
            # difference every step; under `dda_diff` the difference IS the state,
            # so the compare is one read and `BRN` hands it to the x-arm in ACC.
            _head = (f"""dda{s}{k}:{" " * (3 - len(s))}LD  SDD
        BRN xarm{s}{k}           ; SDD < 0 is sideDistX < sideDistY -> step in x
        SUB DDY             ; sideDistY += deltaDistY, as a fall in the difference
        ST  SDD"""
                     if dda_diff else
                     f"""dda{k}:   LD  SDX
        SUB SDY
        BRN xarm{k}           ; sideDistX < sideDistY -> step in x""")
            # The x-arm's own increment. `BRN` never assigns ACC (emulator
            # `_br_neg` reads `em.b` and returns; `ddxneg: NEG` in the prologue
            # already depends on it on the taken path), so SDD is still there.
            _xarm = (f"""xarm{s}{k}:  ADD DDX             ; ACC is still SDD: sideDistX += deltaDistX
        ST  SDD"""
                     if dda_diff else
                     f"""xarm{k}:  LD  SDX
        ADD DDX
        ST  SDX""")
            # mapY += stepY as PW/WADDR increments. Split, the sign is a fact
            # about the whole loop: one arm, no `LD STPY`, and the wrap arm falls
            # straight through into the hit test instead of jumping to it.
            if not dda_stepy_split:
                _yarm = f"""        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
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
        JMP hity{k}"""
            elif not s:
                _yarm = f"""        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru{k}           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity{k}
ywru{k}:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word"""
            else:
                _yarm = f"""        LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd{s}{k}          ; ... and 1/16 floors to 0
        JMP hity{s}{k}
ywrd{s}{k}: LDI {16 ** 15}
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word"""
            lines += f"""
{_head}
        LD  SDY
        ADD DDY
        ST  SDY
{_yarm}
hity{s}{k}:{" " * (2 - len(s))}LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ {nxt}            ; empty -> {nxt_note}
        JMP why             ; a y-side wall: t is dark
{_xarm}
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
{_xarm_reload}        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ {nxt}            ; empty -> {nxt_note}
        JMP whx             ; an x-side wall: t is sunlit""".splitlines()
            if lap_via_jump and k == _last:
                lines += [
                    f"{_lap + ':':<8}{'JMP dda' + s + '0':<20}"
                    "; the lap as a JMP, so `seek_split` can",
                    "                            ; make it a seek: a taken BRZ would",
                    f"                            ; recirculate every word back to dda{s}0",
                ]
    # perpWallDist on the x side is the sideDistX the step just consumed. Under
    # `dda_diff` that absolute is not stored any more, so the tail rebuilds it —
    # once per wall hit (64 a frame) against the 615 x-steps that paid for it.
    _perpx = ("""        LD  SDD
        ADD SDY             ; sideDistX = SDD + sideDistY
        SUB DDX
""" if dda_diff else """        LD  SDX
        SUB DDX
""")
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
{_perpx}        ST  PERP
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
        JMP zstore
pone:   LDI 1
        ST  PERP
; the z-buffer (M7a): the column's final clamped depth, persisted for the
; sprite pass's occlusion compare — store[ZBUF + XCOL] = PERP
zstore: LD  XCOL
        ADDI ZBUF
        MOVA PERP
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
""".splitlines()
    lines += _column_send_asm(geom, slots)
    lines += f"""\
colnxt: INCM XCOL           ; ACC = the old column number
        SUBI {WIDTH - 1}
        BRZ spsel           ; that was column {WIDTH - 1}: the viewport is sent
        JMP colset
""".splitlines()
    lines += (_sprite_phase_asm(slots, n_mon, codes, geom) if geom.sprites
              else ["", "; ── no sprite phase: see Geom.sprites ──", "spsel:"])
    lines += _pistol_asm(geom, art)
    bg = hud_bg_runs(geom)
    readout = ([
        "; onto the strip), then the LIVE readouts as DOOM's OWN numerals — see",
        "; the dnums block below.",
    ] if geom.digits else [
        "; onto the strip), then the LIVE readouts in the bar's OWN number wells:",
        f"; ammo columns {AMMO_BAR_COLS[0]}..{AMMO_BAR_COLS[1] - 1} "
        f"(1px per {AMMO_PER_PX} rounds), health",
        f"; columns {HEALTH_BAR_COLS[0]}..{HEALTH_BAR_COLS[1] - 1} "
        f"(1px per {HEALTH_PER_PX}), both on rows {BAR_ROWS[0]}..{BAR_ROWS[1] - 1}",
        f"; in the digits' own red {BAR_COLOR}; an empty bar sends nothing and",
        "; the bar art shows through",
    ])
    lines += [
        "",
        f"; ── HUD (M8): cursor to slot {H3D * WIDTH}, then DOOM's REAL status bar as",
        f"; {len(bg)} pre-encoded RUN words (hud_bg_runs() — STBAR block-quantized 5x4",
        *readout,
        "hud:",
    ]
    for i, (word, note) in enumerate(_hud_bg_words(geom)):
        head = "hud:" if i == 0 else ""
        lines += [f"{head:<8}LDI {word}", f"        SND                 ; {note}"]
    del lines[lines.index("hud:")]
    if geom.digits:
        lines += _digits_asm(geom, art)
    bar_rows, sel_suffix = art.bar_rows, _sel_suffix(geom, 0, art.bar_rows[0])
    bars = () if geom.digits else (
        ("hbar", "abar", "HEALTH", art.health_per_px, art.health_cols[0], "health"),
        ("abar", "face", "AMMO", art.ammo_per_px, art.ammo_cols[0], "ammo"),
    )
    for label, nxt, slot, per, col, note in bars:
        # A well that straddled a panel seam would need two runs whose lengths
        # both depend on the counter; both wells sit in the left half, and this
        # is the assertion that keeps a future re-cut of the bar honest.
        if geom.tile_of(col, bar_rows[0]) != geom.tile_of(
                art.health_cols[1] - 1, bar_rows[1] - 1):
            raise ValueError("a status well crosses a panel seam; split the RUN")
        lines += [
            f"{label + ':':<8}LD  {slot}",
            f"        DIVI {per}",
            f"        ST  TMP             ; the {note} bar in pixels",
            f"        BRZ {nxt}           ; nothing to paint: the bar art shows through",
            f"        LDI {_curs_word(geom, col, bar_rows[0])}",
            f"        SND                 ; CURS: row {bar_rows[0]}, column {col}",
            "        LD  TMP",
            "        MULI 16",
            f"        ADDI {BAR_COLOR}",
            "        MULI 8",
            "        ADDI C_RUN",
        ] + sel_suffix + [
            "        ST  TMP2            ; the bar's RUN word — reused for its other rows",
            "        SND",
        ]
        for row in range(bar_rows[0] + 1, bar_rows[1]):
            lines += [
                f"        LDI {_curs_word(geom, col, row)}",
                f"        SND                 ; CURS: row {row}, column {col}",
                "        LD  TMP2",
                "        SND",
            ]
    fcol, frow, fw, fh = art.face_box
    lines += f"""
; ── the face (M5/M8): the Freedoom mugshot, {fw}x{fh} in STBAR's own inset —
; rows {frow}..{frow + fh - 1}, columns {fcol}..{fcol + fw - 1};
; four baked variants (face_for), each a constant list of
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
        ("fwell", art.faces["healthy"], "healthy (stfst00)"),
        ("fhurt", art.faces["hurt"], "hurt (stfst20)"),
        ("fbld", art.faces["bloody"], "bloodied (stfst40)"),
        ("fgrim", art.faces["grim"], "the FIRE grimace (stfevl0)"),
    )
    for bi, (label, table, note) in enumerate(face_blocks):
        first = True
        for r, c, colors in table:
            # The mugshot sits in the middle of the bar, so at 128x96 it
            # straddles the seam at x = 64 and a row becomes two panels' spans.
            # `span_words` cuts it; on one panel it returns exactly the CURS and
            # runs this loop always emitted.
            x = c
            for word in span_words(r, c, colors, geom):
                head = f"{label}:" if first else ""
                is_curs = word == _curs_word(geom, x, r)
                lines.append(f"{head:<8}LDI {word}"
                             + (f"          ; {note}" if first else ""))
                if is_curs:
                    lines.append(f"        SND                 ; CURS: face row {r}, column {x}")
                else:
                    arg = (word // 8 if geom.tiled else word) // 8
                    n, colour = arg // 16, arg % 16
                    lines.append(f"        SND                 ; RUN {n} x colour {colour}")
                    x += n
                first = False
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
    ihdr = struct.pack(">IIBBBBB", len(frame[0]) * scale, len(frame) * scale,
                       8, 2, 0, 0, 0)
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
            w, h = len(frame[0]), len(frame)
            img = Image.new("RGB", (w, h))
            img.putdata([PALETTE[int(ch, 16)] for row in frame for ch in row])
            img.resize((w * scale, h * scale), Image.NEAREST).save(path)


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
                  nukage_rows: list[str] | None = None,
                  monsters: list[tuple[int, int, int]] | None = None,
                  geom: Geom = GEOM64) -> None:
    """Swap the module onto an imported level (``wadimport`` output).

    Everything downstream — :func:`map_cell`, :func:`preamble_words`,
    :func:`render`, :func:`title_words`, :func:`deadman3d_source` — reads the
    module globals at call time, so the swap makes the whole model, generator
    and player follow the imported level.  ``nukage_rows`` is the importer's
    damage-floor plane (``N`` marks; omitted = no damage floors);
    ``monsters`` its ``(cx, cy, species)`` THINGS extraction (M7a; omitted =
    an empty table, the tape's MONB/MHPB blocks shrink to nothing and the
    selection loop culls zero candidates).
    """
    global MAP_STR, _PRINTED_ROWS, _MAP_WORDS, SPAWN, TITLE_HEX_ROWS, _WAD_INSTALLED
    global NUKAGE_STR, _NUKE_ROWS, _NUKE_WORDS, MONSTERS, _MON_WORDS, _MHP_WORDS
    assert len(map_rows) == MAP_SIZE and all(len(r) == MAP_SIZE for r in map_rows)
    assert len(title_rows) == geom.height and all(len(r) == geom.width for r in title_rows)
    MAP_STR = "\n".join(map_rows) + "\n"
    _PRINTED_ROWS = list(map_rows)
    _MAP_WORDS = map_words()
    if nukage_rows is None:
        nukage_rows = ["." * MAP_SIZE] * MAP_SIZE
    assert len(nukage_rows) == MAP_SIZE and all(len(r) == MAP_SIZE for r in nukage_rows)
    NUKAGE_STR = "\n".join(nukage_rows) + "\n"
    _NUKE_ROWS = list(nukage_rows)
    _NUKE_WORDS = nukage_words()
    MONSTERS = [tuple(mon) for mon in (monsters or [])]
    _MON_WORDS = monster_words()   # re-asserts every cell open on the new map
    _MHP_WORDS = monster_hp_words()
    x, y = spawn_cell
    assert map_cell(x, y) == 0, f"imported spawn cell {spawn_cell} is a wall"
    SPAWN = State(posX=x * UNITS + UNITS // 2, posY=y * UNITS + UNITS // 2,
                  heading=heading % HEADINGS)
    # At a tiled geometry the title belongs to that screen's art bundle, not to
    # this module's 64x48 table; `deadman3d_hires.install` puts it there and the
    # global stays what it was so a same-process 64x48 build is unaffected.
    if not geom.tiled:
        TITLE_HEX_ROWS = list(title_rows)
    _WAD_INSTALLED = True
    here = globals()
    for mod in _twin_modules():
        for name in ("MAP_STR", "_PRINTED_ROWS", "_MAP_WORDS", "NUKAGE_STR",
                     "_NUKE_ROWS", "_NUKE_WORDS", "SPAWN", "TITLE_HEX_ROWS",
                     "MONSTERS", "_MON_WORDS", "_MHP_WORDS",
                     "_WAD_INSTALLED"):
            setattr(mod, name, here[name])


def install_art(gun_idle: list[tuple[int, int, str]],
                gun_fire: list[tuple[int, int, str]],
                faces: dict[str, list[tuple[int, int, str]]],
                sprites: list[int] | None = None,
                hud_bg: list[str] | None = None) -> None:
    """Swap the sprite art onto ``wadimport.iwad_art``'s WAD-derived tables
    (Mode B only: the committed machines stay Freedoom-derived).

    The pistol tables are module globals read by both the unit builder
    (``d3_unit.unit_interior`` bakes the GUN/GUNF arms from them at build
    time) and the emulator's unit model (``store.DoomUnit`` duplicates them
    as class attributes — rebound here so a local machine emulates its own
    art); the face tables are plain CPU-side RUN constants, so rebinding the
    module globals re-generates the asm with them.  ``sprites`` (M7a) is the
    60-word packed monster column table (``iwad_art``'s ``monster_sprites``):
    input-borne data, so the swap touches no unit at all.  ``hud_bg`` (M8) is
    the IWAD's own status bar quantized to the strip — also a CPU-side RUN
    table (:func:`hud_bg_runs` re-encodes it), so its run COUNT may differ
    from the committed bar's and the generated asm simply carries more or
    fewer pre-encoded words.
    """
    global GUN_IDLE, GUN_FIRE, FACE_HEALTHY, FACE_HURT, FACE_BLOODY, FACE_GRIM
    global MON_SPRITES, _SPR_WORDS, HUD_BG_ROWS
    from randomfun2026solvers.lm1.store import DoomUnit

    GUN_IDLE = list(gun_idle)
    GUN_FIRE = list(gun_fire)
    FACE_HEALTHY = list(faces["healthy"])
    FACE_HURT = list(faces["hurt"])
    FACE_BLOODY = list(faces["bloody"])
    FACE_GRIM = list(faces["grim"])
    if sprites is not None:
        MON_SPRITES = list(sprites)
        _SPR_WORDS = _check_sprites(MON_SPRITES)
    if hud_bg is not None:
        assert len(hud_bg) == HEIGHT - H3D and all(len(r) == WIDTH for r in hud_bg)
        HUD_BG_ROWS = list(hud_bg)
    here = globals()
    for mod in _twin_modules():  # __main__ AND the package instance d3_unit reads
        for name in ("GUN_IDLE", "GUN_FIRE", "FACE_HEALTHY", "FACE_HURT",
                     "FACE_BLOODY", "FACE_GRIM", "MON_SPRITES", "_SPR_WORDS",
                     "HUD_BG_ROWS"):
            setattr(mod, name, here[name])
    DoomUnit.GUN_IDLE = tuple(gun_idle)
    DoomUnit.GUN_FIRE = tuple(gun_fire)


def taped_program():
    """The taped tier's program: :func:`deadman3d_source` with both read-count
    levers on — no redundant ``LD WADDR`` in the DDA x-arm (``dda_acc_reload``),
    and the DDA comparing a maintained difference rather than two scalars
    (``dda_diff``).

    Registered as ``machine.TIER_PROGRAM[("deadman-3d", "taped")]``, so
    ``build_for("deadman-3d", store="taped")`` stays the one-liner that makes the
    shipped taped machine while the canonical men-v3 build keeps loading the
    byte-frozen ``deadman-3d.asm``. Assembled in memory rather than checked in as a
    second ``.asm``: both differences are generated from this one source, and
    ``deadman-3d_taped.man`` already pins the result byte for byte.
    """
    from randomfun2026solvers.lm1.asm import assemble

    return assemble(
        deadman3d_source(dda_acc_reload=False, dda_diff=True, lap_via_jump=True,
                         dda_stepy_split=True),
        name="deadman-3d",
    )


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
        " ".join(str(w) for w in input_words(list(cmds))) + "\n", encoding="utf-8")
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

    ``counters`` threads the golden path's live ammo/health — and M7b's
    monster HP ledger — between frames (the machine keeps its own on the tape).
    """
    fire = fire_bit(code)
    live = fire and counters["ammo"] > 0
    if live:
        counters["ammo"] -= 1
    state = step(state, code)
    nuk = nukage_cell(div(state.posX, UNITS), div(state.posY, UNITS)) == 1
    if nuk:
        counters["health"] = max(0, counters["health"] - NUKE_DAMAGE)
    if player is None:  # --golden: the model, instant
        hit_out: list[int] = []
        frame = render(state, fire=fire, ammo=counters["ammo"],
                       health=counters["health"], nukage=nuk,
                       hp=counters["hp"], live=live, hit_out=hit_out)
        if hit_out[0]:  # applied after the frame: the corpse shows up next
            counters["hp"][hit_out[0] - 1] -= 1
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
    counters = {"ammo": AMMO_START, "health": HEALTH_START,
                "hp": list(_MHP_WORDS)}
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
    counters = {"ammo": AMMO_START, "health": HEALTH_START,
                "hp": list(_MHP_WORDS)}
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
                      level.nukage_rows,
                      [tuple(mon) for mon in level.monsters])
        art = wadimport.iwad_art(args.wad)
        install_art(art["gun_idle"], art["gun_fire"], art["faces"],
                    art["monster_sprites"], art["hud_bg"])
        print(f"installed {level.stats['source']}: spawn {level.spawn} "
              f"heading {level.heading}, {level.stats['wall_cells']} wall cells, "
              f"{level.stats.get('nukage_cells', 0)} nukage cells, "
              f"{len(level.monsters)} monsters, "
              f"{level.stats['title_runs']} title runs; WAD art: "
              f"{len(art['gun_idle'])}+{len(art['gun_fire'])} pistol runs, "
              f"{len(art['faces'])} faces, "
              f"{len(hud_bg_runs())} status-bar runs, 60 monster sprite words")
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
