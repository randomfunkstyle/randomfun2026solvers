"""The 128x96 tiled variant: four 64x48 LM-75s behind a 1-of-4 router.

``deadman-3d_hires`` is its own slug and its own program, so the 64x48 family is
untouched — the first test here is the one that says so, byte for byte.

What the rest pin is the tiling itself: the router's selectors read off its trie
(pinned against the emulator model exactly as the unit's arm codes are), the
seam at ``y = 48`` that every hi-res viewport column crosses, the per-tile-row
floor bound the COL arm bakes, and the broadcast COMMIT that is the only reason
four independently-swapping panels compose into one frame.
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
    """The whole point of a new slug: no existing machine moves a byte.

    The tiled wall reaches the generator through three additive edits — a new
    ``.unit`` name, a ``floor_row`` parameter on the DOOM block that defaults to
    what it always baked, and a widened ``unit in (...)`` test — and every one of
    them is supposed to be invisible here.
    """
    from randomfun2026solvers.lm1 import machine

    m = machine.build_for("deadman-3d", **kwargs)
    assert (EXAMPLES / artifact).read_text().rstrip("\n").split("\n") == m.rows


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


# ── the router ───────────────────────────────────────────────────────────────
def test_the_selectors_are_read_off_the_trie_and_pinned_to_the_model() -> None:
    from randomfun2026solvers.lm1 import d3_router
    from randomfun2026solvers.lm1.store import DoomWall

    assert d3_router.SEL == DoomWall.SEL
    assert d3_router.TILE_FLOOR_ROW == DoomWall.FLOOR_ROW
    # Five destinations on eight leaves, every selector distinct and in 0..7.
    assert sorted(d3_router.SEL.values()) == sorted(set(d3_router.SEL.values()))
    assert all(0 <= v < 8 for v in d3_router.SEL.values())


def test_only_the_broadcast_leaf_is_an_S() -> None:
    """``S`` is what makes a COMMIT all-or-nothing; a tile leaf must not have one."""
    from randomfun2026solvers.lm1 import d3_router

    r = d3_router.router_interior()
    sends = {dest: glyph for _x, _y, glyph, dest in r.glyphs if glyph in ("s", "S")}
    assert sends == {"T0": "s", "T1": "s", "T2": "s", "T3": "s", "ALL": "S"}


def test_every_tile_leaf_binds_its_own_outlet() -> None:
    """Nearest-pipe binding, recomputed here rather than trusted from the builder."""
    from randomfun2026solvers.lm1 import d3_router

    r = d3_router.router_interior()
    for gx, gy, glyph, dest in r.glyphs:
        if glyph != "s":
            continue
        dists = [abs(gx - col) + (d3_router.IH + 1 - gy) for col in r.south]
        assert dists.index(min(dists)) == d3_router.TILES.index(dest)
        assert sorted(dists)[1] > sorted(dists)[0]  # no reading-order tie


def test_the_wall_places_four_panels_and_four_legs() -> None:
    from randomfun2026solvers.lm1 import d3_router

    wall = d3_router.build_wall()
    assert len(wall.panels) == 4
    assert len(set(wall.panels)) == 4
    assert len(wall.legs) == 4
    assert wall.pipes == 4 + 4 * 7  # four fan-out legs plus each block's seven


# ── the tiling ───────────────────────────────────────────────────────────────
def test_the_tile_map_is_the_documented_one() -> None:
    from randomfun2026solvers.lm1 import display

    assert (display.tile_of(0, 0), display.tile_of(127, 0)) == (0, 1)
    assert (display.tile_of(0, 95), display.tile_of(127, 95)) == (2, 3)
    assert display.tile_of(63, 47) == 0 and display.tile_of(64, 48) == 3
    assert display.tile_addr(64, 48) == 0
    assert display.tile_addr(127, 95) == 47 * 64 + 63
    with pytest.raises(ValueError):
        display.tile_of(128, 0)


def test_a_viewport_column_splits_at_the_seam() -> None:
    """The one hot-loop primitive a hi-res raycaster needs, in its three cases."""
    from randomfun2026solvers import deadman3d_hires as hires

    # Wholly in the top tile: still two commands, because the bottom tile has to
    # be floored and the unit only floors after a wall run.
    assert len(hires.col_words(10, 4, 20, 3)) == 2
    # Straddling: one command per tile.
    assert len(hires.col_words(10, 30, 60, 3)) == 2
    # Wholly below the seam: the top tile is all *ceiling*, which COMMIT already
    # cleared to black, so it needs no command at all — one, not two.
    assert len(hires.col_words(10, 55, 70, 3)) == 1
    # Below the bottom tile's viewport bound there is nothing for the top tile
    # to paint beyond floor, and the bottom tile's run is clamped to row 31.
    for bad in ((10, -1, 5, 3), (10, 5, 80, 3), (128, 0, 5, 3), (10, 0, 5, 16)):
        with pytest.raises(ValueError):
            hires.col_words(*bad)


def test_the_model_paints_what_the_frames_say() -> None:
    """The wall model, the tile split and the composition agree end to end."""
    from randomfun2026solvers import deadman3d_hires as hires

    frames = hires.frames_for_words()
    assert len(frames) == 2
    assert all(len(f) == 96 and all(len(r) == 128 for r in f) for f in frames)
    assert frames[0] == hires.title_frame()
    assert frames[1] == hires.seam_frame()


def test_composition_refuses_a_frame_stitched_from_mismatched_halves() -> None:
    """The invariant the broadcast exists to keep, made checkable."""
    from randomfun2026solvers.lm1 import display
    from randomfun2026solvers.lm1.store import DoomWall

    writes: list[tuple[int, int, int]] = []
    wall = DoomWall(lambda t, p, v: writes.append((t, p, v)))
    wall.send(8 * (8 * 0 + wall.units[0].CODES["COMMIT"]) + wall.SEL["T0"])
    with pytest.raises(ValueError, match="broadcast"):
        display.tiled_frames_from_writes(writes)


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


def test_the_program_forwards_and_commits_on_the_broadcast() -> None:
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import display
    from randomfun2026solvers.lm1.emulator import Emulator, Round
    from randomfun2026solvers.lm1.programs import load

    prog = load("deadman-3d_hires")
    assert prog.unit == "doom4"
    res = Emulator(prog).run(
        [Round(input=tuple(hires.input_words()))], max_instructions=4_000_000
    )
    assert res.output == ()
    assert display.tiled_frames_from_writes(res.wall_writes) == hires.frames_for_words()
