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

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

EXAMPLES = REPO / "littleman" / "examples"


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


def test_the_commit_is_the_broadcast_and_a_tile_word_is_not() -> None:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers.lm1 import d3_router

    assert d3.commit_word(d3.GEOM64) == 7  # the bare unit code, no router
    assert d3.commit_word(d3.GEOM128) % 8 == d3_router.SEL["ALL"]
    for tile in range(4):
        assert d3.unit_word("CURS", 0, tile, d3.GEOM128) % 8 == d3_router.SEL[f"T{tile}"]


# ── the art ──────────────────────────────────────────────────────────────────
def test_the_hires_art_is_the_shape_the_screen_needs() -> None:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires

    art = d3.art_for(d3.GEOM128)
    assert len(art.title) == 96 and all(len(r) == 128 for r in art.title)
    assert len(art.hud_bg) == 16 and all(len(r) == 128 for r in art.hud_bg)
    # The wells double, so the divisors halve — a full clip fills its own well
    # and never overruns it.
    assert d3.div(d3.AMMO_START, art.ammo_per_px) <= art.ammo_cols[1] - art.ammo_cols[0]
    assert d3.div(d3.HEALTH_START, art.health_per_px) <= art.health_cols[1] - art.health_cols[0]
    assert hires.GEOM.tile_of(*art.face_box[:2]) == 2  # the mugshot starts on T2


def test_the_mugshot_straddles_the_seam_and_the_encoder_splits_it() -> None:
    """The one span in the frame that is genuinely two panels wide."""
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires  # registers the hires art

    g, art = deadman3d_hires.GEOM, d3.art_for(d3.GEOM128)
    col, row, w, _h = art.face_box
    assert col < g.tile_w < col + w, "the mugshot no longer crosses x = 64"
    words = d3.span_words(row, col, "1" * w, g)
    assert len({word % 8 for word in words}) == 2  # one selector per panel


def test_a_committed_span_is_one_cursor_and_its_runs() -> None:
    from randomfun2026solvers import deadman3d as d3

    words = d3.span_words(41, 0, "1111", d3.GEOM64)
    assert words == [d3.unit_word("CURS", 41 * 64, 0, d3.GEOM64),
                     d3.unit_word("RUN", 4 * 16 + 1, 0, d3.GEOM64)]


# ── the machine ──────────────────────────────────────────────────────────────
def test_the_machine_builds_with_four_display_rooms() -> None:
    from randomfun2026solvers.littleman import Littleman
    from randomfun2026solvers.lm1 import machine

    m = machine.build_for("deadman-3d_hires")
    info = Littleman().analyze("\n".join(m.rows))
    assert len(info.displays) == 4
    for panel in info.displays:
        lo, hi = panel["min"], panel["max"]
        assert (hi[0] - lo[0] - 1, hi[1] - lo[1] - 1) == (64, 48)


def test_the_checked_in_artifacts_match_their_builders() -> None:
    """The committed grid, assembly and input stream are all regenerable.

    The input stream especially: round 0 carries the title's RLE and the title
    is a different picture at 128x96, so this file is **not** the 64x48
    machine's and must never be copied from it.
    """
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine
    from randomfun2026solvers.lm1.programs import PROGRAM_DIR

    m = machine.build_for("deadman-3d_hires")
    man = EXAMPLES / "deadman-3d_hires.man"
    assert man.read_text().rstrip("\n").split("\n") == m.rows
    assert (PROGRAM_DIR / "deadman-3d_hires.asm").read_text() == hires.hires_source()
    stream = (EXAMPLES / "deadman-3d_hires.input.txt").read_text().split()
    assert [int(v) for v in stream] == hires.input_words(list(d3.WALK[:6]))
    assert stream != (EXAMPLES / "deadman-3d.input.txt").read_text().split()


def test_the_tape_covers_a_column_per_ray_and_the_tile_scalars() -> None:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers.lm1 import machine

    slots = d3.tape_slots(d3.GEOM128)
    assert slots["CMD"] == slots["ZBUF"] + 128  # one z-slot per rendered column
    assert set(d3._TILE_SCALARS) <= set(slots)
    assert machine.TAPE_SIZE["deadman-3d_hires"] == max(slots.values()) + 1
    # the committed layout is untouched by any of it
    assert max(d3.tape_slots().values()) + 1 == machine.TAPE_SIZE["deadman-3d"]


# ── the pixels, end to end ───────────────────────────────────────────────────
def test_the_machine_paints_what_the_model_says_across_the_seam() -> None:
    """The test that caught both real bugs in the port.

    One: the unit reseeds its banding mask per COMMAND, so a column split at the
    seam restarts its stripe phase on the lower panel and the model has to do
    the same.  Two: the floor-only panel's seed pixel is the wall loop's first
    lap, whose mask is 7 — and ``8 & 7 == 0``, so the floor colour is the one
    colour that cannot survive it.  Both showed up here as a wrong row 48 and
    nowhere else.
    """
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import display
    from randomfun2026solvers.lm1.asm import assemble
    from randomfun2026solvers.lm1.emulator import Emulator, Round

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


def test_composition_refuses_a_frame_stitched_from_mismatched_halves() -> None:
    """The invariant the broadcast COMMIT exists to keep, made checkable."""
    from randomfun2026solvers.lm1 import display
    from randomfun2026solvers.lm1.store import DoomWall

    writes: list[tuple[int, int, int]] = []
    wall = DoomWall(lambda t, p, v: writes.append((t, p, v)))
    wall.send(8 * (8 * 0 + wall.units[0].CODES["COMMIT"]) + wall.SEL["T0"])
    with pytest.raises(ValueError, match="broadcast"):
        display.tiled_frames_from_writes(writes)
