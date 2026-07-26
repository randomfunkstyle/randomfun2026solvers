"""The dense layout's two load-bearing pieces: the bands and the loop rectangles.

Neither is checkable by eye.  A band that ties binds a pipe op to whichever pipe
reading order happens to pick, and a loop rectangle that is one cell too short
silently drops a glyph -- both produce a grid that loads, runs, and computes
something else.
"""

from __future__ import annotations

import pytest
from randomfun2026solvers import matmul_dense as D
from randomfun2026solvers.circuit import Collision
from randomfun2026solvers.matmul_grid import LAID, band_of, chains_of


@pytest.fixture(scope="module")
def b() -> D.Bands:
    return D.bands()


def test_no_attach_column_is_shared() -> None:
    assert len(set(D.ANCHORS.values())) == len(D.ANCHORS) == 14


def test_every_band_is_contiguous_and_tie_free(b: D.Bands) -> None:
    """A tie breaks by reading order over pipe segments; exclude them instead."""
    for sending in (False, True):
        cells = b.send if sending else b.recv
        for ring in ("q", "x", "io", "k", "b", "c", "s"):
            cols = sorted(x for x, r in cells.items() if r == ring)
            assert cols, f"{ring} owns no column for sending={sending}"
            assert cols == list(range(cols[0], cols[-1] + 1)), ring
        # the two margin columns carry no band, so every box has somewhere to turn
        for x in D.MARGIN_COLS:
            assert x not in cells


def test_every_pipe_op_has_a_column_to_stand_in(b: D.Bands) -> None:
    for name in LAID:
        for tok in LAID[name][0]:
            ring = band_of(tok)
            if ring is not None:
                assert b.span(ring, tok[0] == "s")


@pytest.mark.parametrize(
    ("name", "w", "form"),
    [("MAC", 8, "east"), ("LOADA_GO", 4, "west"), ("CFILL_GO", 5, "west")],
)
def test_self_loops_close_as_rectangles(b: D.Bands, name: str, w: int, form: str) -> None:
    """A `w`x2 rectangle costs `2w` ticks a lap and has `2w-3` glyph slots."""
    cells, x0, got_w, _first, got_form = D.rect_loop(name, b, 1, D.IW - 2)
    assert (got_w, got_form) == (w, form)
    glyphs = [g for tok in LAID[name][0] for _, g in D.items(tok)]
    # a perimeter of 2w carries 2w-3 glyph slots: three plain corners plus the
    # branch, which turns for itself
    assert len(glyphs) <= 2 * w - 3
    assert len(cells) <= 2 * w
    assert all(x0 <= x < x0 + w for x, _ in cells)


def test_mac_is_sixteen_ticks_a_lap(b: D.Bands) -> None:
    """The hot loop: 12 glyphs, four corners, one blank -- 16 cells a lap.

    :mod:`matmul_grid` walks 15 cells of body plus an 18-cell return corridor for
    the same twelve glyphs, and ``MAC`` is half of every tick the machine spends.
    """
    cells, x0, w, first, form = D.rect_loop("MAC", b, 1, D.IW - 2)
    assert (x0, w, form) == (14, 8, "east")
    # walk the rectangle from the first glyph and read off what it executes
    order = [(x, 1) for x in range(x0 + w - 2, x0, -1)] + \
            [(x, 0) for x in range(x0 + 1, x0 + w - 1)] + [(x0 + w - 1, 0)]
    walked = [cells[c] for c in order if c in cells]
    want = [g for tok in LAID["MAC"][0] for _, g in D.items(tok)]
    assert walked == want
    assert first == (x0 + w - 2, 1)


def test_mac_pipe_ops_bind_to_their_own_rings(b: D.Bands) -> None:
    cells, x0, w, _first, _form = D.rect_loop("MAC", b, 1, D.IW - 2)
    order = [(x, 1) for x in range(x0 + w - 2, x0, -1)] + \
            [(x, 0) for x in range(x0 + 1, x0 + w - 1)]
    toks = [t for t in LAID["MAC"][0] if band_of(t)]
    placed = [c for c in order if c in cells and cells[c] in "rs"]
    assert len(placed) == len(toks)
    for cell, tok in zip(placed, toks, strict=True):
        assert b.ring_at(cell[0], tok[0] == "s") == band_of(tok), (cell, tok)


def test_the_drawn_room_executes_the_cfg(b: D.Bands) -> None:
    """The whole worker, walked block by block against the CFG it compiles.

    Cells that are *walked* but hold no glyph are invisible to every other kind
    of check -- the grid loads, the pipes bind, and the machine computes
    something else -- so the layout is only believable once someone has followed
    the man's feet from every block's first cell.  This is `matmul_grid`'s own
    checker, pointed at the dense room.
    """
    from randomfun2026solvers import matmul_grid as G

    room = D.build_room(b)
    G.check_room(room)
    assert (room.iw, room.ih) == (52, 148)


def test_the_dense_room_is_a_third_faster_than_the_shipped_one(b: D.Bands) -> None:
    from randomfun2026solvers import matmul_grid as G

    room = D.build_room(b)
    traces = G.public_traces()
    ticks = sum(G.estimate_ticks(room, r, ln) for r, ln in traces) / len(traces)
    assert ticks < 25_000                       # matmul_grid measures 31,553


def test_the_relay_and_the_rectangle_want_the_anchors_apart_and_together(
    b: D.Bands,
) -> None:
    """The open conflict, pinned so the next change is aimed at the right thing.

    A turnaround room is six columns wide and every ring's pipe climbs straight
    up from its own attach column, so a relay may span no column but its own
    ring's two.  Eight anchors in eight consecutive columns is exactly what
    makes ``MAC`` an 8x2 rectangle, and it leaves no six-column window for `s`.
    """
    with pytest.raises(Collision, match="relay spans columns"):
        D.build_grid()


def test_the_cold_chains_all_lay_in_their_own_boxes(b: D.Bands) -> None:
    """Every chain but the hot one pours into a box no wider than its bands."""
    laid = 0
    for chain in chains_of():
        if chain.blocks[0] == "TBODY":
            with pytest.raises(Collision):
                D.lay_chain(chain.blocks, b)     # MAC needs an east-entry loop
            continue
        box = D.lay_chain(chain.blocks, b)
        assert box.cells
        assert all(0 <= x < D.IW for x, _ in box.cells)
        laid += 1
    assert laid == 16
