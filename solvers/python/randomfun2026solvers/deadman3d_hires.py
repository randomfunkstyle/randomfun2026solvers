#!/usr/bin/env python3
"""deadman-3d_hires — the raycaster at 128x96, on four tiled 64x48 LM-75s.

The same demo as :mod:`deadman3d`: Freedoom Phase 1's E1M1, walked first
person, one frame per input word.  Twice the resolution in each direction, which
the LM-75 cannot give you on one panel — its interior stops at 64x64
(``SPEC.md``) — so the frame is a 2x2 of 64x48 panels behind the 1-of-4 router in
:mod:`lm1.d3_router`, and

    tile = (x >= 64) + 2 * (y >= 48)      in-tile = (x % 64, y % 48)

Almost none of the raycaster changed.  ``deadman3d`` now takes a
:class:`~deadman3d.Geom`, its default is the committed 64x48 screen, and this
module passes :data:`~deadman3d.GEOM128` instead.  Three things are genuinely
new and they all live in ``deadman3d`` proper: the per-column send became one
COL word *per panel the column touches* (``_column_send_asm``), the status bar
and the mugshot became tile-split spans, and the pistol moved from the unit's
baked arm to CURS/RUN words the CPU sends (``_pistol_asm`` says why).

The art, and its one honest limitation
--------------------------------------

The repo commits **quantized output**, not source art: ``TITLE_HEX_ROWS``,
``HUD_BG_ROWS``, ``FACE_*`` and the pistol tables are the result of running
:mod:`wadimport` over a Freedoom checkout that is not itself in the tree (see
that module's ``--freedoom DIR``).  So the committed 128x96 art here is those
tables **doubled**, not re-quantized from the 320x200 originals: real art, real
runs, correct geometry, but not the extra detail a true 128x96 quantization
would recover.  Re-deriving it needs a Freedoom checkout and is a one-argument
change — :func:`hires_art` takes whatever tables it is given.

``--wad`` is the exception and the demonstration: pointed at a local IWAD,
:mod:`wadimport` quantizes id's own art straight to the hi-res geometry
(:data:`wadimport.HIRES_W` and friends, :func:`wadimport.face_box`'s 13x14
mugshot slot), and nothing from that path is committed.

Not in this stage
-----------------

**The monster billboards.**  A billboard is one tape word per sprite column with
a nibble per row, so a column is at most 15 rows before ``16**h`` leaves 64 bits.
The committed bands are 14, 9 and 5 rows; doubling them needs 28 and 18, which do
not fit a word — a 2x billboard is a two-word column and a re-cut nibble chain.
:attr:`deadman3d.Geom.sprites` is the switch, off here, and the model and the
machine agree about it: neither paints monsters.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers.deadman3d import GEOM128, Art, Geom

__all__ = [
    "GEOM",
    "SCALE",
    "cases_json",
    "frames_for_commands",
    "hires_art",
    "hires_source",
    "input_words",
    "install",
    "title_frame",
    "upscale_rows",
    "upscale_runs",
]

#: The screen this module drives.
GEOM: Geom = GEOM128

#: How much the committed 64x48 art is enlarged to reach it.  Exactly 2 in each
#: direction, which is why every screen-space table doubles cleanly.
SCALE = 2


# ── enlarging the committed art ──────────────────────────────────────────────
def upscale_rows(rows: Sequence[str], scale: int = SCALE) -> list[str]:
    """A hex-digit raster enlarged ``scale``x, nearest neighbour."""
    return [
        "".join(ch * scale for ch in row) for row in rows for _ in range(scale)
    ]


def upscale_runs(runs: Sequence[tuple[int, int, str]],
                 scale: int = SCALE) -> list[tuple[int, int, str]]:
    """A sprite run table enlarged ``scale``x — ``scale`` rows out of every one.

    A run is ``(row, first column, colours)`` in *screen* coordinates, so both
    the position and the colour string scale.  The copies are left as separate
    runs: each becomes its own span and the encoder run-length codes it anyway.
    """
    out: list[tuple[int, int, str]] = []
    for row, col, colours in runs:
        wide = "".join(ch * scale for ch in colours)
        out += [(row * scale + k, col * scale, wide) for k in range(scale)]
    return out


def hires_art(
    title: Sequence[str] | None = None,
    hud_bg: Sequence[str] | None = None,
    gun_idle: Sequence[tuple[int, int, str]] | None = None,
    gun_fire: Sequence[tuple[int, int, str]] | None = None,
    faces: dict[str, list[tuple[int, int, str]]] | None = None,
    face_box: tuple[int, int, int, int] | None = None,
) -> Art:
    """The 128x96 screen-space tables, defaulting to the committed art doubled.

    Every argument is an override for the ``--wad`` path, which quantizes id's
    art straight to this geometry rather than enlarging Freedoom's.

    The wells are re-cut rather than scaled blindly: a doubled well is twice as
    many pixels for the same 50 rounds and 100 health, so the per-pixel divisor
    halves.  Computed from the well's own width, rounded up, so a full clip
    fills its well exactly and never overruns it.
    """
    g = GEOM
    ammo_cols = (d3.AMMO_BAR_COLS[0] * SCALE, d3.AMMO_BAR_COLS[1] * SCALE)
    health_cols = (d3.HEALTH_BAR_COLS[0] * SCALE, d3.HEALTH_BAR_COLS[1] * SCALE)
    art = Art(
        title=list(title) if title is not None else upscale_rows(d3.TITLE_HEX_ROWS),
        hud_bg=list(hud_bg) if hud_bg is not None else upscale_rows(d3.HUD_BG_ROWS),
        gun_idle=list(gun_idle) if gun_idle is not None else upscale_runs(d3.GUN_IDLE),
        gun_fire=list(gun_fire) if gun_fire is not None else upscale_runs(d3.GUN_FIRE),
        faces=faces or {
            "healthy": upscale_runs(d3.FACE_HEALTHY),
            "hurt": upscale_runs(d3.FACE_HURT),
            "bloody": upscale_runs(d3.FACE_BLOODY),
            "grim": upscale_runs(d3.FACE_GRIM),
        },
        face_box=face_box or (d3.FACE_COL * SCALE, d3.FACE_ROW * SCALE,
                              d3.FACE_W * SCALE, d3.FACE_H * SCALE),
        bar_rows=(d3.BAR_ROWS[0] * SCALE, d3.BAR_ROWS[1] * SCALE),
        ammo_cols=ammo_cols,
        health_cols=health_cols,
        ammo_per_px=-(-d3.AMMO_START // (ammo_cols[1] - ammo_cols[0])),
        health_per_px=-(-d3.HEALTH_START // (health_cols[1] - health_cols[0])),
    )
    if len(art.title) != g.height or any(len(r) != g.width for r in art.title):
        raise ValueError(f"the title is not {g.width}x{g.height}")
    if len(art.hud_bg) != g.hud_h or any(len(r) != g.width for r in art.hud_bg):
        raise ValueError(f"the status bar is not {g.width}x{g.hud_h}")
    return art


def install(art: Art | None = None) -> None:
    """Register the hi-res art so ``deadman3d.art_for(GEOM128)`` resolves."""
    d3.ART_REGISTRY["hires"] = art or hires_art()


install()


# ── the demo, at 128x96 ──────────────────────────────────────────────────────
def title_frame() -> list[str]:
    """Round 0's committed frame."""
    return d3.title_frame(GEOM)


def frames_for_commands(cmds: Sequence[int]) -> list[list[str]]:
    """One 128x96 frame per command — the model the machine must reproduce."""
    return d3.frames_for_commands(list(cmds), GEOM)


def input_words(cmds: Sequence[int]) -> list[int]:
    """Everything the program reads: the preamble, the 128x96 title RLE, the walk.

    **Not** the 64x48 machine's stream.  The preamble and the key bitmasks carry
    over unchanged, but round 0's title is a different picture with a different
    number of runs, and the title loop is generated against exactly this count.
    """
    return d3.input_words(list(cmds), GEOM)


def cases_json(cmds: Sequence[int]) -> dict:
    """The round-gated cases file, one committed 128x96 frame per round."""
    return d3.cases_json(list(cmds), GEOM, name="deadman-3d_hires")


def hires_source() -> str:
    """The LM-1 assembly: :func:`deadman3d.deadman3d_source` at :data:`GEOM`."""
    return d3.deadman3d_source(GEOM)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--asm", type=Path)
    ap.add_argument("--input", type=Path)
    ap.add_argument("--cases", type=Path)
    ap.add_argument("--pngs", type=Path, metavar="DIR")
    ap.add_argument("--frames", type=int, default=6, help="how many walk frames")
    args = ap.parse_args(argv)

    cmds = list(d3.WALK[: args.frames])
    if args.asm:
        args.asm.write_text(hires_source(), encoding="utf-8")
    if args.input:
        args.input.write_text(" ".join(str(w) for w in input_words(cmds)) + "\n",
                              encoding="utf-8")
    if args.cases:
        args.cases.write_text(json.dumps(cases_json(cmds)) + "\n", encoding="utf-8")
    if args.pngs:
        d3._write_pngs([title_frame()] + frames_for_commands(cmds), args.pngs)
    if not any((args.asm, args.input, args.cases, args.pngs)):
        print(f"{GEOM.width}x{GEOM.height}, viewport {GEOM.h3d}, tiles {GEOM.tiles}")
        print(f"title runs: {len(d3.title_words(GEOM))}, input: {len(input_words(cmds))}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
