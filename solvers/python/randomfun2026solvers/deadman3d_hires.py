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

This family is IWAD-only, and nothing it produces is committed
--------------------------------------------------------------

**Everything** here comes from a locally owned IWAD: the level geometry is id's
own E1M1, the title, status bar, 13x14 mugshot, pistol and monster sprites are
all quantized from the WAD's lumps *at* 128x96 rather than enlarged from
someone else's quantization of them, and the wall colours come from the IWAD's
own textures.  There is no Freedoom fallback and no doubled art.

Which means the generated grid and input stream **embed IWAD data**, and
``DOOM1.WAD`` is not redistributable (``littleman/DEADMAN-3D.md``).  So this
family commits nothing at all: :func:`build_local` writes into
``littleman/examples/local/``, which is in ``.gitignore``, exactly as
``deadman3d.py``'s own ``--wad`` mode does.  The three committed families —
``deadman-3d``, ``deadman-3d_taped``, ``deadman-3d_trim`` — stay Freedoom-based,
byte-identical and fully attributed; none of this touches them.

Build it with::

    python -m randomfun2026solvers.deadman3d_hires --wad ~/DOOM1.WAD --build

The monster billboards, and why they needed a re-cut
----------------------------------------------------

A billboard column is one tape word with a nibble per row, and ``16**15`` is the
last power inside 64 bits — so 14 rows is the ceiling.  The committed bands are
14, 9 and 5 rows tall; doubled for a doubled screen they are 28, 18 and 10, and
none of the three fits a word.  ``wadimport.pack_sprite_columns_wide`` cuts a
column into 14-row slices from the bottom and **pads every band to a whole
number of them**, which is what buys the machine its one shared paint chain:
a short band walks up into its own transparent padding and the chain's existing
branch over a 0 nibble is all the dispatch it needs.  So the hi-res table is
240 words (40 columns x 2 words x 3 stripes) against the committed 60, the
chain is 28 blocks with one entry against 14 with three, and each block also
carries the panel arithmetic — a 28-row billboard standing on the floor line
crosses the seam at row 48, so the chain steps its selector as it climbs.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from randomfun2026solvers import deadman3d as d3
from randomfun2026solvers import wadimport as wi
from randomfun2026solvers.deadman3d import GEOM128, Art, Geom

__all__ = [
    "GEOM",
    "LOCAL_DIR",
    "SCALE",
    "WALK",
    "WALK_CHORDS",
    "build_local",
    "cases_json",
    "frames_for_commands",
    "hires_art",
    "hires_source",
    "input_words",
    "install",
    "install_wad",
    "title_frame",
]

#: Where a build lands.  Gitignored, because everything in it is IWAD-derived.
LOCAL_DIR = Path(__file__).resolve().parents[3] / "littleman" / "examples" / "local"

#: The screen this module drives.
GEOM: Geom = GEOM128

#: The hi-res screen against the committed one, in each direction.  Not an
#: upscale factor — no art is enlarged — but the ratio the *layout* numbers
#: scale by: the status wells and the bar rows are geometry, not pictures.
SCALE = 2

#: This family's own demo walk, and it has to be its own: ``deadman3d.WALK`` was
#: choreographed against **Freedoom's** E1M1, whose imps queue up a corridor two
#: cells from the route.  id's E1M1 is a different level with three kept
#: monsters — cells (54, 41), (56, 30) and (55, 18) — and the marine spawns at
#: (27, 30) facing away from all of them, twenty-seven cells west of the nearest,
#: which is more than twice ``MON_FAR``.  Walk ``deadman3d.WALK`` here and not one
#: frame in fifty-seven contains a billboard.
#:
#: So the route below was searched rather than composed: the shortest sequence of
#: turns and steps from id's own spawn that puts a monster in the **near** 20x28
#: band **under the crosshair**.  It arrives at frame 20 with the sergeant at
#: depth 3513 spanning columns 51..70 and rows 36..63 — across the seam at x = 64
#: **and** the seam at y = 48, so the one billboard is a piece of all four
#: panels — then FIREs (he has 1 HP: the frame that kills still shows him
#: standing) and holds twice on the corpse.
WALK_CHORDS: list[str] = (
    ["."] + ["w"] * 2 + ["d"] * 2 + ["w", "d", "w", "d"] + ["w"] * 7 + ["d"]
    + ["w"] * 5 + ["d"] + [" ", ".", "."]
)

#: :data:`WALK_CHORDS` encoded — the command words the machine reads.
WALK: list[int] = [d3.keys(ch) for ch in WALK_CHORDS]


def hires_art(
    title: Sequence[str],
    hud_bg: Sequence[str],
    gun_idle: Sequence[tuple[int, int, str]],
    gun_fire: Sequence[tuple[int, int, str]],
    faces: dict[str, list[tuple[int, int, str]]],
    face_box: tuple[int, int, int, int],
    sprites: Sequence[int],
    digits: Sequence[int],
) -> Art:
    """The 128x96 screen-space tables.  Every one is required, and on purpose.

    There is no default and no fallback: this family takes its art from an IWAD
    lump quantized *at* 128x96, and an argument that could be omitted would be
    an invitation to enlarge a 64x48 table instead — which is exactly the thing
    the WAD path exists to avoid.  :func:`install_wad` is the only caller.

    The status wells are the one thing derived rather than imported, because
    they are geometry, not art: the bar is twice as wide, so a well is twice as
    many pixels for the same 50 rounds and 100 health and the per-pixel divisor
    halves.  Rounded up from the well's own width, so a full clip fills it
    exactly and never overruns it.  They are kept for the record and painted by
    nothing: at 128x16 the wells carry DOOM's real ``STTNUM`` numerals instead
    (:attr:`deadman3d.Geom.digits`), which is what a strip this tall is *for*.
    """
    g = GEOM
    ammo_cols = (d3.AMMO_BAR_COLS[0] * SCALE, d3.AMMO_BAR_COLS[1] * SCALE)
    health_cols = (d3.HEALTH_BAR_COLS[0] * SCALE, d3.HEALTH_BAR_COLS[1] * SCALE)
    art = Art(
        title=list(title),
        hud_bg=list(hud_bg),
        gun_idle=list(gun_idle),
        gun_fire=list(gun_fire),
        faces=faces,
        face_box=face_box,
        sprites=list(sprites),
        bar_rows=(d3.BAR_ROWS[0] * SCALE, d3.BAR_ROWS[1] * SCALE),
        ammo_cols=ammo_cols,
        health_cols=health_cols,
        ammo_per_px=-(-d3.AMMO_START // (ammo_cols[1] - ammo_cols[0])),
        health_per_px=-(-d3.HEALTH_START // (health_cols[1] - health_cols[0])),
        digits=list(digits),
        dig_box=wi.digit_box(g.width, g.hud_h),
        dig_slots=wi.digit_slots(g.width, g.hud_h, g.h3d),
    )
    if len(art.title) != g.height or any(len(r) != g.width for r in art.title):
        raise ValueError(f"the title is not {g.width}x{g.height}")
    if len(art.hud_bg) != g.hud_h or any(len(r) != g.width for r in art.hud_bg):
        raise ValueError(f"the status bar is not {g.width}x{g.hud_h}")
    want = 3 * g.mon_stride * g.mon_words
    if len(art.sprites) != want:
        raise ValueError(f"{len(art.sprites)} sprite words, {want} expected "
                         f"({g.mon_stride} columns x {g.mon_words} a column, 3 stripes)")
    if len(art.digits) != g.dig_words:
        raise ValueError(f"{len(art.digits)} numeral words, {g.dig_words} expected "
                         f"({d3.DIGIT_GLYPHS} glyphs x {art.dig_box[0]} columns)")
    return art


def install(art: Art) -> None:
    """Register the hi-res art so ``deadman3d.art_for(GEOM128)`` resolves.

    Nothing calls this at import: until an IWAD has been read there is no art,
    and ``art_for`` raising is the correct answer rather than a doubled stand-in.
    """
    d3.ART_REGISTRY["hires"] = art


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
    """The LM-1 assembly: :func:`deadman3d.deadman3d_source` at :data:`GEOM`,
    without the DDA x-arm's redundant ``LD WADDR``.

    ``dda_acc_reload=False`` is the M13 program lever, and it is taken here
    rather than through ``machine.TIER_PROGRAM`` **because that registry cannot
    reach this family**: it is consulted only by ``machine._tier_program``,
    which ``build_for`` calls only when no ``program=`` is passed, and this
    module always passes one — the level is installed from an IWAD at call time,
    so there is no checked-in ``.asm`` to load.  An entry keyed
    ``("deadman-3d_hires", "taped")`` would be dead config.  Nor is one needed:
    the registry exists to keep a **byte-frozen** grid off a program fix, and
    nothing this module generates is committed (see the module docstring), so
    the source may simply take the win.

    Worth **-4.405%** on the 21-round hi-res tour (1,090,194,166 ->
    1,042,173,023 ticks over frames 1..20, ``scratch/deadman3d-opt/hires_opt.py``)
    and 32 words of ROM, P 8,895 -> 8,863.  That is ``deadman-3d``'s own -4.37%
    almost exactly, which is the expected answer for a lever that deletes one
    store read per DDA step: the step count scales with the pixel count and so
    does everything else.

    ``dda_diff`` and ``dda_stepy_split`` are the same story one round later —
    both landed on ``deadman-3d``'s taped tier after this family had already
    been optimised, and both are program-level, so they arrive here through this
    one function rather than through any registry.  Measured on the same tour,
    on top of the shipped store set:

    ==========================================  =============  =========
    ``dda_diff``                                  997,775,049   -3.708%
    ``dda_diff`` + ``dda_stepy_split``            990,990,612   **-4.362%**
    ``lap_via_jump``                            1,036,259,288   +0.006%
    ==========================================  =============  =========

    ``lap_via_jump`` was **declined twice on those readings and is now taken**,
    worth **-18.503%** — and the reason it reversed is the whole point rather
    than a correction.  It replaces a backward-branch lap with a forward
    ``JMPF``, and :data:`machine.SEEK_OPS` seeks ``JMPF`` **and nothing else**.
    Without a seek drum the rewrite is free of charge and free of benefit, which
    is exactly what +0.006% and +0.036% were measuring.  With one, every lap it
    converts stops discarding the ROM man's ring and starts seeking the row.

    That is also why it was worth -4.47% on the 64x48 machine all along: that
    machine has had a ``SEEK_DRUM`` since ``886ea07``.  The lever never failed to
    transfer — the *drum* had not transferred yet, and nobody had tried.

    ==========================================  =============  =========
    shipped, seek drum on (21-round tour)         254,446,307       —
    ``+ lap_via_jump``                            207,366,882  **-18.503%**
    ==========================================  =============  =========

    ``dda_stepy_split`` cannot be taken *alone* at this geometry (the second
    emission collides on ``dda0`` at hires' unroll factor); with ``dda_diff`` the
    labels are distinct and it builds, which is the combination ``deadman-3d``
    ships anyway.
    """
    return d3.deadman3d_source(GEOM, dda_acc_reload=False, dda_diff=True,
                               dda_stepy_split=True, lap_via_jump=True)


def install_wad(wad: Path, *, brightness: float | None = None) -> dict:
    """Install id's own E1M1 and art, both at 128x96.  Returns the art bundle.

    The level goes onto :mod:`deadman3d`'s globals, exactly as its own
    ``--wad`` mode does — ``map_cell``, ``preamble_words``, ``render`` and the
    generator all read them at call time — but the *title* is handed to the
    hi-res art bundle instead of the module's 64x48 table, so a 64x48 build in
    the same process is left alone.

    Nothing here is committable.  See the module docstring.
    """
    g = GEOM
    level = wi.load_iwad(wad, "E1M1", grid=64, title_size=(g.width, g.height))
    kw = {} if brightness is None else {"brightness": brightness}
    art = wi.iwad_art(
        wad,
        hud_size=(g.width, g.hud_h), h3d=g.h3d,
        gun=wi.HIRES_GUN, screen=(g.width, g.h3d),
        # the CPU draws the pistol at this geometry (deadman3d._pistol_asm), so
        # there is no sprite arm to spill and no descent window to fit
        max_runs=None, max_body=64,
        bands=wi.HIRES_BANDS, words_per_col=2, digits=True,
        **kw,
    )
    box = wi.face_box(g.width, g.hud_h, g.h3d)
    d3.install_level(level.map_rows, level.spawn, level.heading, level.title_rows,
                     level.nukage_rows or None, level.monsters or None, geom=g)
    install(hires_art(title=level.title_rows, hud_bg=art["hud_bg"],
                      gun_idle=art["gun_idle"], gun_fire=art["gun_fire"],
                      faces=art["faces"], face_box=box,
                      sprites=art["monster_sprites"], digits=art["digits"]))
    return art


#: The store tier this family builds on.  The men-v3 store that ``deadman-3d``
#: ships on is ~two little men a slot, and this machine's tape is over 800 —
#: the block alone would set the whole silhouette.  The **taped** tier is banked
#: pipe tapes behind a gate chain (a bank is two men), which is the tier this
#: family is measured and optimised on from here on; the three committed
#: 64x48 families are untouched and still ship on their own tiers.
STORE_TIER = "taped"


def build_local(wad: Path, out_dir: Path | None = None,
                cmds: Sequence[int] | None = None,
                *, pngs: bool = True, store: str = STORE_TIER) -> dict:
    """The whole local artifact set for the IWAD-only hi-res family.

    Writes the assembly, the machine, its debug sidecars, the round-gated cases
    file, the flat input stream and the composed 128x96 frames — all into
    ``littleman/examples/local/``, which is gitignored because every one of them
    carries IWAD data.

    The tape size is a property of *this* level rather than a registry constant
    (id's E1M1 and Freedoom's do not hold the same number of monsters), so it is
    computed here and written into the registry for the build.
    """
    from randomfun2026solvers.lm1 import machine
    from randomfun2026solvers.lm1.asm import assemble

    out_dir = out_dir or LOCAL_DIR
    cmds = list(WALK if cmds is None else cmds)
    install_wad(wad)
    out_dir.mkdir(parents=True, exist_ok=True)

    src = hires_source()
    (out_dir / "deadman-3d_hires.asm").write_text(src, encoding="utf-8")
    prog = assemble(src, name="deadman-3d_hires")
    machine.TAPE_SIZE["deadman-3d_hires"] = max(d3.tape_slots(GEOM).values()) + 1
    m = machine.build_for("deadman-3d_hires", program=prog, store=store)
    (out_dir / "deadman-3d_hires.man").write_text("\n".join(m.rows) + "\n", encoding="utf-8")
    m.debug_map().write_html(m.rows, out_dir / "deadman-3d_hires.debug.html")
    m.debug_map().write_json(out_dir / "deadman-3d_hires.debug.json")
    (out_dir / "deadman-3d_hires.input.txt").write_text(
        " ".join(str(w) for w in input_words(cmds)) + "\n", encoding="utf-8")
    (out_dir / "deadman-3d_hires.cases.json").write_text(
        json.dumps(cases_json(cmds)) + "\n", encoding="utf-8")
    frames = [title_frame()] + frames_for_commands(cmds)
    if pngs:
        d3._write_pngs(frames, out_dir / "hires-frames", scale=6)
    print(f"wrote {out_dir}/deadman-3d_hires.* ({m.width}x{m.height}, "
          f"store={store}, P={prog.P}, "
          f"tape={machine.TAPE_SIZE['deadman-3d_hires']}) "
          f"and {len(frames)} frames")
    return {"machine": m, "program": prog, "frames": frames}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--wad", type=Path, required=True,
                    help="a locally owned IWAD — this family has no other source")
    ap.add_argument("--build", action="store_true",
                    help=f"write the whole artifact set into {LOCAL_DIR}")
    ap.add_argument("--out", type=Path, help="override the output directory")
    ap.add_argument("--frames", type=int, default=len(WALK),
                    help="how many walk frames (the whole walk by default; the "
                         "billboard arrives at frame 20, so a shorter run has no "
                         "monster in it)")
    ap.add_argument("--no-pngs", action="store_true")
    args = ap.parse_args(argv)

    cmds = list(WALK[: args.frames])
    if args.build:
        build_local(args.wad, args.out, cmds, pngs=not args.no_pngs)
        return 0
    install_wad(args.wad)
    print(f"{GEOM.width}x{GEOM.height}, viewport {GEOM.h3d}, tiles {GEOM.tiles}")
    print(f"title runs: {len(d3.title_words(GEOM))}, input: {len(input_words(cmds))}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
