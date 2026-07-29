"""Tests for room H's band model, and the known-answer regression.

The arithmetic tests are pure — the two constants they use (``y_b0 = 205``,
``floor_y = 194``) are *captured* values recorded here so a builder change that
moves them shows up as a failing test rather than as a silently wrong ceiling.

The last test is the real regression: it re-derives ``ROM_TOUCH_DROP``'s feasible
interval from a live build and asserts the endpoints. It needs the IWAD and takes
about a build, so it skips cleanly without one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .roomh import H_MIN_ROWS, BandModel

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"

#: captured from the shipped build (`python -m scratch.layout2.roomh`)
SHIPPED = BandModel(y_b0=205, floor_y=194, available0=12)


def test_ceiling_matches_the_built_grid():
    """``squash_grid.py`` built k=0..8 and refused k=9,10."""
    assert SHIPPED.k_max == 8


def test_room_h_height_matches_every_built_row():
    """The grid's H heights were 12-k; the model must reproduce them exactly."""
    assert [SHIPPED.height(k) for k in range(0, 8)] == [12, 11, 10, 9, 8, 7, 6, 5]


def test_refusal_is_by_the_four_row_minimum():
    assert SHIPPED.available(9) == 3 < H_MIN_ROWS
    assert "refused" in SHIPPED.explain(9)
    assert "builds" in SHIPPED.explain(8)


def test_moving_the_store_north_would_raise_the_ceiling_one_for_one():
    """The lever that would help is the store's floor, not room H's home.

    Stated as a test because it is the Phase 3 recommendation: each row the store
    moves north is one more row of squash, and ``store_offset`` dy is the registry
    that would do it (it does not route on hires today).
    """
    moved = BandModel(y_b0=205, floor_y=194 - 3, available0=12 + 3)
    assert moved.k_max == SHIPPED.k_max + 3


@pytest.mark.skipif(not WAD.exists(), reason="hires is WAD-derived; no IWAD present")
def test_rom_touch_drop_interval_is_rederived_from_a_live_build():
    """The known-answer case: drop 4 ties, 5..29 bind, 32 does not."""
    from .bindsolve import feasible
    from .capture import capture, rom_wanting

    cap = capture(lane_pitch=1, rom_touch_drop=0)
    assert len(rom_wanting(cap)) == 12, "the twelve r glyphs that want rom"
    fs, bounds = feasible(cap.glyphs, cap.touches, moving="rom", axis="y")
    assert len(fs.parts) == 1, f"a single interval, got {fs}"
    assert (fs.parts[0].lo, fs.parts[0].hi) == (5, 29), str(fs)
    for good in (5, 14, 22, 26, 29):
        assert good in fs
    for bad in (3, 4, 30, 32):
        assert bad not in fs
    lo = [b for b in bounds if b.side == "lo"][-1]
    hi = [b for b in bounds if b.side == "hi"][-1]
    assert lo.rival == "mem_resp" and lo.glyph_at == (43, 188)
    assert hi.rival == "in"
