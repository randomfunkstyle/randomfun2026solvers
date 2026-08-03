"""The raycaster at 128x96, on four tiled 64x48 LM-75s.

``deadman-3d_hires`` is the same demo at twice the resolution, which the panel
cannot give you on its own — its interior stops at 64x64 (``SPEC.md``) — so the
frame is a 2x2 behind ``lm1/d3_router.py``'s 1-of-4 router.

The first two tests are the ones that matter most, and they are deliberately
blunt: the 64x48 family's grids and its generated assembly must come back byte
for byte.  The whole port is a ``Geom`` parameter whose default is the committed
screen, so "nothing moved" is checkable rather than argued.

The rest pin what a resolution change actually breaks: the derived screen
constants, the tile map, the per-panel floor bound the COL arm bakes, and — the
one that caught two real bugs — that the machine paints what the model says,
across the seam.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

EXAMPLES = REPO / "littleman" / "examples"

#: A locally owned IWAD, if the person running the suite has one.  The 128x96
#: family takes *every* input from it — id's own E1M1 as well as the art — so
#: without one there is nothing to build and those tests skip.  ``DEADMAN3D_IWAD``
#: overrides; the fallback is where the repo's own docs suggest keeping it.
#: No test reads an IWAD unless it is there, which keeps `DEADMAN-3D.md`'s
#: promise that the suite needs none.
IWAD = Path(os.environ.get("DEADMAN3D_IWAD", "")) if os.environ.get("DEADMAN3D_IWAD") \
    else Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
needs_iwad = pytest.mark.skipif(
    not IWAD.is_file(),
    reason=f"deadman-3d_hires is IWAD-only and no IWAD is present at {IWAD} "
           "(set DEADMAN3D_IWAD to point at one)",
)


#: Everything ``install_level`` rebinds.  The IWAD build swaps the module onto
#: id's E1M1, and a byte-identity test running afterwards in the same worker
#: would then build a *different* deadman-3d — so the fixture puts them back.
_INSTALLED_GLOBALS = (
    "MAP_STR", "_PRINTED_ROWS", "_MAP_WORDS", "NUKAGE_STR", "_NUKE_ROWS",
    "_NUKE_WORDS", "SPAWN", "TITLE_HEX_ROWS", "MONSTERS", "_MON_WORDS",
    "_MHP_WORDS", "_WAD_INSTALLED",
)


@pytest.fixture
def wad_installed():
    """id's own E1M1 and art at 128x96, undone again afterwards."""
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires

    saved = {n: getattr(d3, n) for n in _INSTALLED_GLOBALS}
    saved_art = dict(d3.ART_REGISTRY)
    try:
        hires.install_wad(IWAD)
        yield hires
    finally:
        for name, value in saved.items():
            for mod in d3._twin_modules():
                setattr(mod, name, value)
        d3.ART_REGISTRY.clear()
        d3.ART_REGISTRY.update(saved_art)


# ── the additive contract ────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("artifact", "kwargs"),
    [
        ("deadman-3d.man", {}),
        ("deadman-3d_taped.man", {"store": "taped"}),
        ("deadman-3d_trim.man", {"trim_dead": True}),
    ],
)
def test_the_64x48_family_is_byte_identical(artifact: str, kwargs: dict) -> None:
    from randomfun2026solvers.lm1 import machine

    m = machine.build_for("deadman-3d", **kwargs)
    assert (EXAMPLES / artifact).read_text().rstrip("\n").split("\n") == m.rows


def test_the_committed_assembly_regenerates_byte_for_byte() -> None:
    """The strongest guard on the ``Geom`` refactor.

    ``deadman3d_source`` grew a geometry parameter and its body now reads a
    dozen locals where it read module constants; every one of those defaults to
    the value it always had, and this is what says so.
    """
    from randomfun2026solvers.deadman3d import deadman3d_source
    from randomfun2026solvers.lm1.programs import PROGRAM_DIR

    assert deadman3d_source() == (PROGRAM_DIR / "deadman-3d.asm").read_text()


def test_the_doom_block_is_unchanged_at_its_default_floor() -> None:
    from randomfun2026solvers.lm1 import d3_unit

    assert d3_unit.build_doom().cells == d3_unit.build_doom(d3_unit.H3D - 1).cells


def test_a_tile_floor_moves_the_literal_and_nothing_else() -> None:
    """``floor_row`` is two digits by contract: the arm below it must not shift."""
    from randomfun2026solvers.lm1 import d3_unit

    base, top = d3_unit.build_doom(39), d3_unit.build_doom(47)
    assert (base.width, base.height) == (top.width, top.height)
    assert base.lengths == top.lengths
    cells = base.cells.keys() | top.cells.keys()
    differing = {k for k in cells if base.cells.get(k) != top.cells.get(k)}
    assert len(differing) == 2, differing  # `39` -> `47`, one digit cell each way

    with pytest.raises(d3_unit.DoomUnitError, match="two digits"):
        d3_unit.build_doom(100)


# ── the screen ───────────────────────────────────────────────────────────────
def test_the_committed_geometry_reproduces_the_module_constants() -> None:
    from randomfun2026solvers import deadman3d as d3

    g = d3.GEOM64
    assert (g.width, g.height, g.h3d, g.mid) == (d3.WIDTH, d3.HEIGHT, d3.H3D, d3.MID)
    assert g.cam_step == 32 and g.lh_num == 81920  # the asm's baked literals
    assert not g.tiled and g.tiles == (1, 1)
    assert g.floor_row(0) == d3.H3D - 1 == 39
    assert g.hud_h == d3.HEIGHT - d3.H3D


def test_the_tiled_geometry_agrees_with_the_hardware() -> None:
    """The panel floor bounds the COL arm bakes are derived, not transcribed."""
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers.lm1 import d3_router

    g = d3.GEOM128
    assert (g.width, g.height, g.h3d, g.mid) == (128, 96, 80, 40)
    assert g.cam_step == 16 and g.lh_num == 163840
    assert g.tiles == (2, 2) and g.hud_h == 16
    assert tuple(g.floor_row(t) for t in range(4)) == d3_router.TILE_FLOOR_ROW
    assert (g.tile_of(0, 0), g.tile_of(127, 0)) == (0, 1)
    assert (g.tile_of(0, 95), g.tile_of(127, 95)) == (2, 3)
    assert g.tile_of(63, 47) == 0 and g.tile_of(64, 48) == 3
    assert g.tile_addr(64, 48) == 0 and g.tile_addr(127, 95) == 47 * 64 + 63


def test_the_camera_step_refuses_a_width_it_cannot_be_exact_at() -> None:
    from randomfun2026solvers.deadman3d import Geom

    with pytest.raises(ValueError, match="not exact"):
        _ = Geom(width=100, height=96, h3d=80).cam_step


# ── the router ───────────────────────────────────────────────────────────────
def test_the_selectors_are_read_off_the_trie_and_pinned_to_the_model() -> None:
    from randomfun2026solvers.lm1 import d3_router
    from randomfun2026solvers.lm1.store import DoomWall

    assert d3_router.SEL == DoomWall.SEL
    assert d3_router.TILE_FLOOR_ROW == DoomWall.FLOOR_ROW
    assert sorted(d3_router.SEL.values()) == sorted(set(d3_router.SEL.values()))
    assert all(0 <= v < 8 for v in d3_router.SEL.values())


def test_only_the_broadcast_leaf_is_an_S() -> None:
    """``S`` is what makes a COMMIT all-or-nothing; a tile leaf must not have one."""
    from randomfun2026solvers.lm1 import d3_router

    r = d3_router.router_interior()
    sends = {dest: glyph for _x, _y, glyph, dest in r.glyphs if glyph in ("s", "S")}
    assert sends == {"T0": "s", "T1": "s", "T2": "s", "T3": "s", "ALL": "S"}


def test_every_tile_leaf_binds_its_own_outlet() -> None:
    from randomfun2026solvers.lm1 import d3_router

    r = d3_router.router_interior()
    for gx, gy, glyph, dest in r.glyphs:
        if glyph != "s":
            continue
        dists = [abs(gx - col) + (d3_router.IH + 1 - gy) for col in r.south]
        assert dists.index(min(dists)) == d3_router.TILES.index(dest)
        assert sorted(dists)[1] > sorted(dists)[0]  # no reading-order tie


def test_the_packed_wall_is_one_screen_and_not_four_monitors() -> None:
    """The deliverable: four panels close enough to read as one 128x96 frame.

    ``build_wall`` places whole DOOM blocks, so its four panels end up 177
    columns and 59 rows apart — the machine paints a 128x96 picture across four
    monitors scattered over the grid.  ``build_packed_wall`` takes the panels
    off the blocks and puts them in one cluster, and the gutter is the thing
    worth pinning, because it *is* the seam a viewer sees: two free columns
    (``panel_pack.py``'s sweep says one is undrawable, because the east panel's
    DATA arrowhead has nowhere to sit) and **three** free rows — the first
    carries the north panels' SWAP arrowheads, the last the south panels' ADDR
    arrowheads, and the one between is the lane SE's ADDR turns on.

    Three is a requirement, not a search result.  It was briefly six, because a
    subsystem sweep scored on the wall's bounding box and a wider band bought
    shorter legs; the bounding box is not scored for this family and the seam is
    what the demo is a picture *of*, so the band is pinned here.
    """
    from randomfun2026solvers.lm1 import d3_router, d3_unit

    wall = d3_router.build_packed_wall()
    pw, ph = d3_unit.PANEL_W + 2, d3_unit.PANEL_H + 2
    (nw, ne, sw, se) = wall.panels
    assert nw[1] == ne[1] and sw[1] == se[1]  # two rows of two
    assert nw[0] == sw[0] and ne[0] == se[0]
    assert ne[0] - (nw[0] + pw) == d3_router.GUTTER_X == 2
    assert sw[1] - (nw[1] + ph) == d3_router.GUTTER_Y == 3
    # ...and the cluster really is the whole screen, walls included
    assert wall.regions["cluster"][2:] == (2 * pw + 2, 2 * ph + 3) == (134, 103)
    # the panels are in tile order, which is also the engine's reading order,
    # which is what `display.tiled_frames_from_writes` composes by
    assert wall.panels == sorted(wall.panels, key=lambda p: (p[1], p[0]))


def test_the_packed_wall_keeps_the_blocks_pipe_invariants() -> None:
    """``len(addr) == len(data)`` and ``len(swap) > len(data)``, per tile.

    They are ``d3_unit.build_doom``'s and they do not become optional because
    the pipes got twenty times longer: a DATA that overtakes its ADDR paints at
    the wrong cursor, and a COMMIT still in flight when the next frame's first
    paint lands commits that pixel into the wrong buffer.  ``build_packed_wall``
    raises rather than draw a wall that breaks either, so this is really a check
    that the solver is still finding a solution — and that the wall is still the
    32 pipes the engine has to find.
    """
    from randomfun2026solvers.lm1 import d3_router

    wall = d3_router.build_packed_wall()
    assert wall.pipes == 32 == d3_router.build_wall().pipes
    assert len(wall.legs) == 4


def test_the_commit_is_the_broadcast_and_a_tile_word_is_not() -> None:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers.lm1 import d3_router

    assert d3.commit_word(d3.GEOM64) == 7  # the bare unit code, no router
    assert d3.commit_word(d3.GEOM128) % 8 == d3_router.SEL["ALL"]
    for tile in range(4):
        assert d3.unit_word("CURS", 0, tile, d3.GEOM128) % 8 == d3_router.SEL[f"T{tile}"]


# ── the art ──────────────────────────────────────────────────────────────────
@needs_iwad
def test_the_hires_art_is_the_shape_the_screen_needs(wad_installed) -> None:
    from randomfun2026solvers import deadman3d as d3

    art = d3.art_for(d3.GEOM128)
    assert len(art.title) == 96 and all(len(r) == 128 for r in art.title)
    assert len(art.hud_bg) == 16 and all(len(r) == 128 for r in art.hud_bg)
    # The mugshot is the IWAD's own at the slot STBAR_REGIONS gives at this
    # strip size — 13x14, not a doubled 6x7.
    assert art.face_box == (58, 80, 13, 14)
    assert set(art.faces) == {"healthy", "hurt", "bloody", "grim"}
    # The pistol is quantized at 22x20 and bottom-centred on the 80-row
    # viewport, so its last row is the viewport's last row.
    assert max(r for r, _c, _x in art.gun_idle) == d3.GEOM128.h3d - 1
    # The wells double, so the divisors halve — a full clip fills its own well
    # and never overruns it.
    assert d3.div(d3.AMMO_START, art.ammo_per_px) <= art.ammo_cols[1] - art.ammo_cols[0]
    assert d3.div(d3.HEALTH_START, art.health_per_px) <= art.health_cols[1] - art.health_cols[0]


@needs_iwad
def test_the_mugshot_straddles_the_seam_and_the_encoder_splits_it(wad_installed) -> None:
    """The one span in the frame that is genuinely two panels wide."""
    from randomfun2026solvers import deadman3d as d3

    g, art = d3.GEOM128, d3.art_for(d3.GEOM128)
    col, _row, w, _h = art.face_box
    assert col < g.tile_w < col + w, "the mugshot no longer crosses x = 64"
    words = d3.span_words(_row, col, "1" * w, g)
    assert len({word % 8 for word in words}) == 2  # one selector per panel


def test_a_committed_span_is_one_cursor_and_its_runs() -> None:
    from randomfun2026solvers import deadman3d as d3

    words = d3.span_words(41, 0, "1111", d3.GEOM64)
    assert words == [d3.unit_word("CURS", 41 * 64, 0, d3.GEOM64),
                     d3.unit_word("RUN", 4 * 16 + 1, 0, d3.GEOM64)]


# ── the machine ──────────────────────────────────────────────────────────────
@needs_iwad
def test_the_machine_builds_with_four_display_rooms(wad_installed, tmp_path) -> None:
    from randomfun2026solvers.littleman import Littleman

    built = wad_installed.build_local(IWAD, tmp_path, list(range(1)), pngs=False)
    m = built["machine"]
    info = Littleman().analyze("\n".join(m.rows))
    assert len(info.displays) == 4
    for panel in info.displays:
        lo, hi = panel["min"], panel["max"]
        assert (hi[0] - lo[0] - 1, hi[1] - lo[1] - 1) == (64, 48)
    # and it landed in the directory it was asked for, not in the tree
    assert (tmp_path / "deadman-3d_hires.man").is_file()


def test_the_loop_lift_reaches_all_four_blocks_and_keeps_its_floor() -> None:
    """``DOOM_LOOP_ROW`` pays twice here, and that needs the wall to forward it.

    The registry hands ``build_wall`` a row and ``build_wall`` hands it to four
    *unmodified* blocks; the 2x2 stacks two of them, so one seventeen-row lift
    comes off the wall's height at both levels. This is the pin that the
    forwarding exists at all — before the consolidation pass ``build_wall()``
    took no argument and hires' opt-in would have been silently inert. No IWAD
    needed: the wall is program-independent.
    """
    from randomfun2026solvers.lm1 import d3_router, d3_unit, machine

    row = machine.DOOM_LOOP_ROW[("deadman-3d_hires", "taped")]
    assert row == d3_unit.MIN_LOOP_ROW  # the floor is what we opted into

    shipped = d3_router.build_wall()
    assert shipped.height == d3_router.build_wall(d3_unit.R_LOOP).height
    lifted = d3_router.build_wall(row)
    assert lifted.width == shipped.width  # height only; the 496 floor is the wall's
    assert shipped.height - lifted.height == 2 * (d3_unit.R_LOOP - row)  # 34

    # ...and the floor is still a floor, four blocks or one.
    with pytest.raises(d3_unit.DoomUnitError, match="MIN_LOOP_ROW"):
        d3_router.build_wall(d3_unit.MIN_LOOP_ROW - 1)


@needs_iwad
def test_the_hires_opcode_map_is_its_own_and_a_win(wad_installed) -> None:
    """``OPCODE_SLOTS``' two properties, for the one key whose program is
    IWAD-only and so cannot be checked in ``test_lm1_opcode_slots.py``.

    Row-neutrality first (the knob's whole claim), then the strict win in drum
    cells (the only reason it exists) — and then the thing that made it worth
    re-deriving rather than copying: it is *not* ``deadman-3d``'s map.
    """
    from randomfun2026solvers.lm1 import machine
    from randomfun2026solvers.lm1 import rom as rommod
    from randomfun2026solvers.lm1.asm import assemble

    key = ("deadman-3d_hires", "taped")
    slots = machine.OPCODE_SLOTS[key]
    program = assemble(wad_installed.hires_source(), name="deadman-3d_hires")
    # exactly what `build_for` plans with, and the row order is now part of that:
    # `_relabel_slots` enforces rank preservation, so the map only makes sense
    # against the order it was zipped to (:data:`machine.LANE_ORDER_FOR`).
    #
    # The slug-keyed `LANE_ORDER` must stay empty, and that is not tidiness: it
    # would move hires' men-v3 grid, which is hash-pinned. The tier-keyed table is
    # the escape hatch, and it names taped only.
    assert machine.LANE_ORDER.get("deadman-3d_hires") is None
    assert set(machine.LANE_ORDER_FOR) == {key}
    # the seek split's JMPS filtered out by `_relabel_slots` on both sides (see the
    # map's own test in `test_seekrom.py`); this plan is the classic one
    slots = {m: s for m, s in slots.items() if m != "JMPS"}
    order = tuple(m for m in machine.LANE_ORDER_FOR[key] if m != "JMPS")
    base = machine.plan(program, middle_order=order)
    relabelled = machine.plan(program, middle_order=order, slots=slots)

    by_rank = sorted(base.number, key=lambda m: base.row[m])
    assert sorted(relabelled.number, key=lambda m: relabelled.row[m]) == by_rank
    assert relabelled.number != base.number

    def cells(p) -> int:
        return sum(len(rommod.token_cells(w)) for w in machine.rom_words(program, p))

    assert cells(relabelled) < cells(base)

    # not deadman-3d's: both name JMPS now, but the assignment is its own — the
    # DP was re-run on hires' histogram and agrees on six of twenty-one lanes.
    other = machine.OPCODE_SLOTS[("deadman-3d", "taped")]
    assert "JMPS" in other and "JMPS" in machine.OPCODE_SLOTS[key]
    assert machine.OPCODE_SLOTS[key] != other
    assert sum(1 for m, s in slots.items() if other.get(m) == s) < len(slots)


@needs_iwad
def test_the_family_commits_nothing() -> None:
    """Everything it generates carries IWAD data, so none of it is in the tree.

    The three Freedoom-based families keep their committed grids; this one is
    built locally into a gitignored directory (``DEADMAN-3D.md``: owning an IWAD
    is fine, putting its data in a public repository is not).
    """
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1.programs import PROGRAM_DIR

    assert not (EXAMPLES / "deadman-3d_hires.man").exists()
    assert not (EXAMPLES / "deadman-3d_hires.input.txt").exists()
    assert not (PROGRAM_DIR / "deadman-3d_hires.asm").exists()
    assert hires.LOCAL_DIR.name == "local"
    ignore = (REPO / ".gitignore").read_text()
    assert "littleman/examples/local/" in ignore


def test_the_tape_covers_a_column_per_ray_and_the_tile_scalars() -> None:
    """Resolution-only, so it needs no art and runs everywhere."""
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers.lm1 import machine

    slots = d3.tape_slots(d3.GEOM128)
    assert slots["CMD"] == slots["ZBUF"] + 128  # one z-slot per rendered column
    assert set(d3._TILE_SCALARS) <= set(slots)
    # the committed layout is untouched by any of it
    assert max(d3.tape_slots().values()) + 1 == machine.TAPE_SIZE["deadman-3d"]


# ── the pixels, end to end ───────────────────────────────────────────────────
@needs_iwad
def test_the_machine_paints_what_the_model_says_across_the_seam(wad_installed) -> None:
    """The test that caught both real bugs in the port.

    One: the unit reseeds its banding mask per COMMAND, so a column split at the
    seam restarts its stripe phase on the lower panel and the model has to do
    the same.  Two: the floor-only panel's seed pixel is the wall loop's first
    lap, whose mask is 7 — and ``8 & 7 == 0``, so the floor colour is the one
    colour that cannot survive it.  Both showed up here as a wrong row 48 and
    nowhere else.
    """
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers.lm1 import display
    from randomfun2026solvers.lm1.asm import assemble
    from randomfun2026solvers.lm1.emulator import Emulator, Round

    hires = wad_installed
    cmds = list(d3.WALK[:2])
    prog = assemble(hires.hires_source(), name="deadman-3d_hires")
    assert prog.unit == "doom4"
    res = Emulator(prog).run([Round(input=tuple(hires.input_words(cmds)))],
                             max_instructions=200_000_000)
    frames = display.tiled_frames_from_writes(res.wall_writes)
    assert frames == [hires.title_frame()] + hires.frames_for_commands(cmds)
    # and the seam is a real one: walls on both sides of it in this view
    view = frames[1]
    assert sum(1 for x in range(128)
               if view[47][x] not in "08" and view[48][x] not in "08") > 20


def test_the_billboard_tables_are_two_words_a_column_and_padded_to_them() -> None:
    """Resolution-only: why the hi-res sprite table is 240 words, not 120.

    A packed column is a nibble a row and ``16**15`` is the last power inside 64
    bits, so 14 rows is the ceiling and every doubled band (28, 18, 10) is past
    it.  ``pack_sprite_columns_wide`` pads each column to a whole number of
    14-row slices, which is what lets ONE chain serve all three bands.
    """
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import wadimport as wi

    g = d3.GEOM128
    assert g.mon_bands == ((20, 28), (12, 18), (8, 10)) == wi.HIRES_BANDS
    assert (g.mon_words, g.mon_span) == (2, 28)
    assert g.mon_stride == 40 and g.mon_band_off == (0, 20, 32)
    assert d3.GEOM64.mon_words == 1 and d3.GEOM64.mon_stride == d3.MON_STRIDE
    # a 10-row band padded to 28 is two words whose HIGH slice is all zeroes —
    # the transparent padding the shared chain walks through
    grid = [[1] * 3 for _ in range(10)]
    words = wi.pack_sprite_columns_wide(grid, 2)
    assert len(words) == 6
    assert [words[1], words[3], words[5]] == [0, 0, 0]


@needs_iwad
def test_a_billboard_paints_and_the_machine_agrees_across_both_seams(
        wad_installed) -> None:
    """The acceptance test for the 2x billboards: a monster is actually THERE.

    ``deadman3d.WALK`` is Freedoom's choreography and id's E1M1 puts its three
    kept monsters twenty-seven cells from the spawn, so that walk never renders
    one — which is exactly how the port could have looked finished while
    painting nothing.  :data:`deadman3d_hires.WALK` is this level's own route,
    and it is choreographed inside the 21-round benchmark window (commands
    0..19), not merely somewhere in the demo: the first billboard is command 14,
    command 16 stands the sergeant in the near 20x28 band under the crosshair —
    across the seam at x = 64 *and* the seam at y = 48 — and kills him, and
    commands 18 and 19 are two connecting shots on the 2-HP imp down the x = 55
    corridor.  The four commands after the window hold on both corpses.
    """
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers.lm1 import display
    from randomfun2026solvers.lm1.asm import assemble
    from randomfun2026solvers.lm1.emulator import Emulator, Round

    hires = wad_installed
    prog = assemble(hires.hires_source(), name="deadman-3d_hires")
    res = Emulator(prog).run([Round(input=tuple(hires.input_words(hires.WALK)))],
                             max_instructions=2_000_000_000)
    frames = display.tiled_frames_from_writes(res.wall_writes)
    want = [hires.title_frame()] + hires.frames_for_commands(hires.WALK)
    assert frames == want

    # What the billboards are worth, measured against the same walk with them
    # switched off: the ONLY difference between the two renders is the monsters.
    bare = d3.frames_for_commands(list(hires.WALK),
                                  dataclasses.replace(d3.GEOM128, sprites=False))
    painted = {k for k in range(1, len(want))
               if any(want[k][r] != bare[k - 1][r] for r in range(96))}
    assert painted, "no frame in the hi-res walk contains a monster"
    # want[k] is command k - 1, so the benchmark's 21 rounds are k <= 20.  The
    # sighting, the kill, the two connecting shots — all inside it — and the two
    # corpse beats after it.
    assert {15, 17, 19, 20, 21, 22} <= painted
    assert len(painted & set(range(1, 21))) == 6, "the measured window must paint"

    # and the one that matters — command 16, the near-band kill, inside the
    # window — spans both seams: rows either side of 48 and columns either side
    # of 64 all carry sprite pixels
    view = want[17]
    cells = [(r, x) for r in range(96) for x in range(128)
             if view[r][x] != bare[16][r][x]]
    assert min(r for r, _x in cells) < 48 <= max(r for r, _x in cells)
    assert min(x for _r, x in cells) < 64 <= max(x for _r, x in cells)


# ── the numerals ─────────────────────────────────────────────────────────────
def test_the_numeral_geometry_is_the_bars_own_and_needs_no_wad() -> None:
    """``Geom`` and ``wadimport`` derive a numeral's size the same way.

    The tape layout has to be answerable without an IWAD (that is what makes the
    resolution-only tests run everywhere), so ``Geom`` scales DOOM's 14x16
    numeral onto the strip itself; ``wadimport`` does it again to cut the art.
    Two copies of one rule, pinned equal here — the repo's usual bargain.
    """
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import wadimport as wi

    assert (d3.STBAR_W, d3.STBAR_H) == (wi.STBAR_W, wi.STBAR_H)
    assert (d3.DIGIT_W, d3.DIGIT_H) == (wi.DIGIT_W, wi.DIGIT_H)
    assert d3.DIGIT_GLYPHS == len(wi.IWAD_DIGIT_LUMPS)

    g = d3.GEOM128
    assert g.digits and g.dig_box == wi.digit_box(g.width, g.hud_h) == (6, 8)
    assert g.dig_words == d3.DIGIT_GLYPHS * 6 == 66
    # …and the committed strip is exactly why it keeps its bars: 3x4 is mush
    assert not d3.GEOM64.digits and d3.GEOM64.dig_words == 0
    assert wi.digit_box(d3.GEOM64.width, d3.GEOM64.hud_h) == (3, 4)

    # id's own placement: three numeral boxes ending at ST_AMMOX, three more
    # plus STTPRCNT at ST_HEALTHX, all on one row band and all on one panel
    slots = wi.digit_slots(g.width, g.hud_h, g.h3d)
    assert slots == tuple((c, 89) for c in (0, 6, 12, 18, 24, 30, 36))
    assert {g.tile_of(c + k, 89 - j) for c, _r in slots
            for k in range(6) for j in range(8)} == {2}


@needs_iwad
def test_the_numerals_are_live_and_the_machine_paints_the_same_ones(
        wad_installed) -> None:
    """The readouts track the state, and blank a leading zero as DOOM does."""
    from randomfun2026solvers import deadman3d as d3

    g = d3.GEOM128
    art = d3.art_for(g)
    assert len(art.digits) == g.dig_words

    def boxes(rows: list[str]) -> set[int]:
        """Which of the seven numeral boxes have any glyph pixel in them."""
        bg = d3.hud_bg_rows(g)
        return {i for i, (col, bottom) in enumerate(art.dig_slots)
                if any(rows[r - g.h3d][col + k] != bg[r - g.h3d][col + k]
                       for k in range(6) for r in range(bottom - 7, bottom + 1))}

    # 50 rounds and 100 health: the ammo hundreds box stays blank, health's
    # does not, and the percent sign is always there
    assert boxes(d3.hud_rows(100, 50, geom=g)) == {1, 2, 3, 4, 5, 6}
    # a spent clip and a dying marine: single digits, and a 0 still draws
    assert boxes(d3.hud_rows(7, 0, geom=g)) == {2, 5, 6}
    # and the numbers really are different pictures, not the same one moved
    assert d3.hud_rows(100, 50, geom=g) != d3.hud_rows(100, 49, geom=g)
    assert d3.hud_rows(100, 50, geom=g) != d3.hud_rows(95, 50, geom=g)


def test_composition_refuses_a_frame_stitched_from_mismatched_halves() -> None:
    """The invariant the broadcast COMMIT exists to keep, made checkable."""
    from randomfun2026solvers.lm1 import display
    from randomfun2026solvers.lm1.store import DoomWall

    writes: list[tuple[int, int, int]] = []
    wall = DoomWall(lambda t, p, v: writes.append((t, p, v)))
    wall.send(8 * (8 * 0 + wall.units[0].CODES["COMMIT"]) + wall.SEL["T0"])
    with pytest.raises(ValueError, match="broadcast"):
        display.tiled_frames_from_writes(writes)


def test_the_native_judge_tiles_the_expected_frame_over_four_panels() -> None:
    """``frame_tiles=`` cuts one expected frame into the panels' own tiles.

    The engine's display judge wanted exactly one display, which is why this
    family had no tick number at all: every measurement had to be ungated, and
    an ungated run never stops.  The wall is now judgeable — each panel is
    compared against its own tile of the logical frame on that panel's *n*-th
    COMMIT — and the split is the same arithmetic ``lm1.display`` uses, so it is
    worth pinning without building anything.

    No IWAD: this is the parser, exercised on a four-panel grid drawn here.
    """
    from randomfun2026solvers.fast_littleman import FastLittleman, FastLittlemanError
    from randomfun2026solvers.lm1 import display

    # four one-pixel panels in a 2x2, plus the compute room the parser insists on
    rows = [
        "+-+  +=+ +=+",
        "|@|  : : : :",
        "+-+  +=+ +=+",
        "",
        "     +=+ +=+",
        "     : : : :",
        "     +=+ +=+",
    ]
    fl = FastLittleman("\n".join(row.ljust(12) for row in rows))
    assert len(fl.display_rooms) == 4
    frame = ["ab", "cd"]  # 2x2 logical wall over four 1x1 panels
    parsed = fl._parse_frame_rounds([[frame]], (2, 2))
    assert parsed == [[[0xA, 0xB, 0xC, 0xD]]]
    # reading order is tile order, which is what `display.tile_of` says too
    assert [display.tile_of(x * 64, y * 48) for y in range(2) for x in range(2)] == [0, 1, 2, 3]
    with pytest.raises(FastLittlemanError, match="display"):
        fl._parse_frame_rounds([[frame]], (1, 1))
    with pytest.raises(FastLittlemanError, match="is not 2x2"):
        fl._parse_frame_rounds([[["abc", "def"]]], (2, 2))


@needs_iwad
def test_the_wall_judges_frame_by_frame_and_reports_what_each_one_cost(
    wad_installed, tmp_path,
) -> None:
    """The tick baseline's instrument, on two frames of the real machine.

    Round gating is what makes a per-frame number mean anything: round *n+1* is
    released only once the **slowest** of the four panels has committed frame
    *n*, so ``frame_ticks`` is the tick a whole logical frame landed on rather
    than whenever one tile happened to swap.
    """
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers.fast_littleman import FastLittleman

    hires = wad_installed
    cmds = list(d3.WALK[:1])
    built = hires.build_local(IWAD, tmp_path, cmds, pngs=False)
    rounds = hires.cases_json(cmds)["publicTestData"][0]["rounds"]
    res = FastLittleman("\n".join(built["machine"].rows)).run(
        " / ".join(" ".join(r["in"]) for r in rounds),
        frames=[r["frames"] for r in rounds], frame_tiles=(2, 2),
        max_ticks=400_000_000,
    )
    assert res.fatal is None, res.fatal
    assert res.passed is True
    assert len(res.frame_ticks) == len(rounds) == 2
    # strictly increasing, and the last one is where the run stopped
    assert res.frame_ticks[0] < res.frame_ticks[1] == res.step


def test_the_hires_ring_worker_batches_one_bank_at_a_time() -> None:
    """``TAPED_SKIP_BATCH``'s hires entry, and the five readings behind it.

    This test first pinned the *absence* of the entry, on a decline that was
    correct when measured: the lever pays per slot walked,
    :data:`machine.TAPED_BANKS`' eleven-bank cut had already spent the ring length
    it would have paid on, and batching cost +2.40%.

    The seek drum then took ~40% out of everything that is not the store, which
    raised the store's share of the run back up, and the same knob measured
    **-1.567%** (207,366,882 -> 204,117,437, 21-round tour).  Batch 4 was still a
    loss at +2.773%, so it was a genuine optimum and not a reversal of direction.

    **And then it stopped being one decision.**  ``None`` hands the choice to each
    bank separately (:data:`machine.TAPED_JUMP_THRESHOLD`), and that beats either
    uniform answer outright, because the eleven-bank cut *straddles* the two
    workers' crossover.  Per-bank service time, measured directly by focusing the
    opcode profiler on one bank's worker room — he is the only man in it, so his
    non-blocked ticks divided by the lap count his ring pipe reports is that
    bank's cost per access:

        slots       6      7      9     21          fit
        batch 1   131.7  140.3  157.5  260.8   ~ 80 + 8.6 * slots
        batch 2   156.9  160.0  169.6  244.3   ~122 + 5.8 * slots

    Batch 2 buys ~2.8 ticks a slot for ~42 ticks of setup, so it crosses at ~15,
    and the four banks under that carry most of the store's traffic — they were
    paying the setup where it could never be earned back.  Five readings on an
    unchanged builder: -27.29% (uniform quarters, beaten outright by the cut),
    +3.54%, +0.185%, -1.567%, and now per-bank.  Batching's value tracks the
    store's share of the run, and that share has gone down and back up.
    Pinned as the value plus the reason, so a future re-measure has the history.
    """
    from randomfun2026solvers.lm1 import machine

    assert machine.TAPED_SKIP_BATCH["deadman-3d_hires"] is None
    # 16 is on a plateau, but the plateau is **15..22 now, not 11..22**: the cut
    # has grown a 14-slot ring (see `machine.TAPED_BANKS`), so a threshold at or
    # below 14 puts that bank on the wide batch-2 worker and costs +0.068%.
    # Re-swept 10..24 on the 20-command tour; 15..22 are identical to the tick.
    assert machine.TAPED_JUMP_THRESHOLD["deadman-3d_hires"] == 16
    assert any(m == 14 for m in machine.TAPED_BANKS["deadman-3d_hires"])
    # the cut it has to amortise against is still eleven short banks
    banks = machine.TAPED_BANKS["deadman-3d_hires"]
    assert len(banks) == 11
    # 434, not 441 and not 306: the re-cut of 1..800 merges the whole cold
    # `MONB`/`SPRB`/`DIGB`/`ZBUF` span into one ring to free a fourth boundary
    # for the map (see `machine.TAPED_BANKS`), and a multi-pass descent has since
    # moved seven of its slots to the neighbour. Batch 2 is what makes the big
    # ring affordable — on batch 1 it would be ~1,270 ticks an access to ~866.
    assert max(banks) == 434
    # ... and it straddles the crossover, which is the whole reason for `None`
    assert min(banks) < 15 < max(banks)
    # The old "no ring in 11..20" plateau claim is **gone**, deliberately: the
    # descent put one at 14, which is what narrows the threshold plateau above.
    # What still has to hold is that nothing lands in 15..20, or the threshold
    # would sit on a ring rather than in a gap.
    assert not any(14 < m < 21 for m in banks)
    # and it only pays because the drum is on; the two are not independent
    assert "deadman-3d_hires" in machine.SEEK_DRUM
    # `deadman-3d` untouched throughout: one uniform batch, and its checked-in
    # `deadman-3d_taped.man` is byte-identical either way
    assert machine.TAPED_SKIP_BATCH["deadman-3d"] == 2
    assert "deadman-3d" not in machine.TAPED_JUMP_THRESHOLD


def test_the_hires_rotating_banks_are_four_and_the_other_seven_are_refusals() -> None:
    """``TAPED_ROTATE_BANKS``'s hires entry, and why it is a per-bank list.

    A rotating bank skips ``ROT = (n + addr - head) % n`` instead of ``addr``.
    The ring turns **one way**, so a backwards delta costs a near-full lap where
    the old skip cost only the address, and a bank that walks backwards often on
    a ring that was short anyway loses outright. Applied to all eleven the
    21-round tour is **+8.31%** by the trace model; measured on the machine, one
    bank at a time, same process, same moment, control reproducing 87,431,352 to
    the tick:

        bank 5 (ring 442)  -1.437%      bank 3 (135)  +1.747%
        bank 2 (53)        -0.318%      bank 6 (59)   +3.948%
        bank 1 (53)        -0.082%      bank 7 (22)   +1.332%
        bank 0 (115)       -0.001%      all seven     +5.332%

    The four together are **-1.837%** (87,431,352 -> 85,824,944), which is the
    sum of the four singles to within a tick, and mean read latency goes
    100.685 -> 95.737 with the floor unmoved at 44 and the **max** 14,836 ->
    3,099 — the tail was bank 5's full-lap walk and it is gone.

    So the sign of every one of the eleven is a measured fact, and the entry is
    the four that are negative. Note what it is *not*: it is not "the big rings"
    (bank 3 at 135 slots is the largest loser) and it is not "the hot banks"
    (bank 5 takes 3,850 of 471,189 accesses). It is the banks whose accesses
    happen to move **forward** around their own ring.
    """
    from randomfun2026solvers.lm1 import machine

    key = ("deadman-3d_hires", "taped")
    rot = machine.TAPED_ROTATE_BANKS[key]
    assert rot == (0, 1, 2, 5)
    banks = machine.TAPED_BANKS["deadman-3d_hires"]
    # every named bank is a batch-2 ring: there is no narrow rotating body, and
    # the builder refuses rather than silently building the wrong one.
    threshold = machine.TAPED_JUMP_THRESHOLD["deadman-3d_hires"]
    assert machine.TAPED_SKIP_BATCH["deadman-3d_hires"] is None
    assert all(banks[k] + 1 >= threshold for k in rot)
    # ... and the three batch-2 rings left out are left out on their measured
    # regressions rather than on their size: bank 3 at 134 slots is bigger than
    # two of the four that are in.
    batched = {k for k, m in enumerate(banks) if m + 1 >= threshold}
    assert batched == {0, 1, 2, 3, 5, 6, 7}
    assert batched - set(rot) == {3, 6, 7}
    assert banks[3] > banks[1] and banks[3] > banks[2]
    # the head rides the packed wire's own forwarder, so both are load-bearing
    assert machine.TAPED_PROTOCOL[key] == "v5"
    assert key in machine.TAPED_FEED_TELEPORT
    # nothing else in the family rotates: `deadman-3d` is byte-pinned and
    # `matmul`/`sudoku` share the same two modules.
    assert list(machine.TAPED_ROTATE_BANKS) == [key]
