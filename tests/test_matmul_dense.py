"""The dense layout's load-bearing pieces: bands, loop rectangles, the room.

None of it is checkable by eye.  A band that ties binds a pipe op to whichever
pipe reading order happens to pick; a loop rectangle one cell too short silently
drops a glyph; a room with no `@` loads, turns its relay men, and emits nothing.
All three produce a grid that runs and computes something else.
"""

from __future__ import annotations

import pytest
from randomfun2026solvers import matmul_dense as D
from randomfun2026solvers.matmul_grid import LAID, band_of, chains_of


@pytest.fixture(scope="module")
def b() -> D.Bands:
    return D.bands()


def test_no_attach_column_is_shared() -> None:
    assert len(set(D.ANCHORS.values())) == len(D.ANCHORS) == 14


def test_every_ring_keeps_its_two_pipes_adjacent() -> None:
    """What lets the north band route at all.

    A ring's two pipes share one strip block, and the band is crossing-free only
    if a pipe's row is its rank by column -- which needs the two rows adjacent.
    Interleaving `b` and `c` (which is what made MAC an 8x2 rectangle) breaks it:
    `b`'s receive run crosses `c`'s riser.
    """
    for ring in ("q", "io", "x", "k", "s", "b", "c"):
        assert abs(D.ANCHORS[(ring, True)] - D.ANCHORS[(ring, False)]) == 1


def test_every_band_is_contiguous_and_tie_free(b: D.Bands) -> None:
    """A tie breaks by reading order over pipe segments; exclude them instead."""
    for sending in (False, True):
        cells = b.send if sending else b.recv
        for ring in ("q", "x", "io", "k", "b", "c", "s"):
            cols = sorted(x for x, r in cells.items() if r == ring)
            assert cols, f"{ring} owns no column for sending={sending}"
            assert cols == list(range(cols[0], cols[-1] + 1)), ring
        for x in D.MARGIN_COLS:
            assert x not in cells


def test_every_pipe_op_has_a_column_to_stand_in(b: D.Bands) -> None:
    for name in LAID:
        for tok in LAID[name][0]:
            ring = band_of(tok)
            if ring is not None:
                assert b.span(ring, tok[0] == "s")


@pytest.mark.parametrize(("name", "w"), [("MAC", 10), ("LOADA_GO", 5),
                                         ("CFILL_GO", 8)])
def test_self_loops_close_as_west_rectangles(b: D.Bands, name: str, w: int) -> None:
    """`2w` ticks a lap, `2w-3` glyph slots -- and west form, which the pen can
    walk into: the entry is one column west of the top row and the exit one west
    of the bottom, exactly where a pen going east and then wrapping already is."""
    cells, x0, got_w, _first, form = D.rect_loop(name, b, 1, D.IW - 2)
    assert (got_w, form) == (w, "west")
    glyphs = [g for tok in LAID[name][0] for _, g in D.items(tok)]
    assert len(glyphs) <= 2 * w - 3
    assert all(x0 <= x < x0 + w for x, _ in cells)


def test_mac_walks_its_own_tokens_round_the_rectangle(b: D.Bands) -> None:
    cells, x0, w, first, _form = D.rect_loop("MAC", b, 1, D.IW - 2)
    order = ([(x, 0) for x in range(x0 + 1, x0 + w - 1)]
             + [(x, 1) for x in range(x0 + w - 2, x0, -1)] + [(x0, 1)])
    walked = [cells[c] for c in order if c in cells]
    assert walked == [g for tok in LAID["MAC"][0] for _, g in D.items(tok)]
    assert first == (x0 + 1, 0)


def test_mac_pipe_ops_bind_to_their_own_rings(b: D.Bands) -> None:
    cells, x0, w, _first, _form = D.rect_loop("MAC", b, 1, D.IW - 2)
    order = ([(x, 0) for x in range(x0 + 1, x0 + w - 1)]
             + [(x, 1) for x in range(x0 + w - 2, x0, -1)])
    toks = [t for t in LAID["MAC"][0] if band_of(t)]
    placed = [c for c in order if c in cells and cells[c] in "rs"]
    assert len(placed) == len(toks)
    for cell, tok in zip(placed, toks, strict=True):
        assert b.ring_at(cell[0], tok[0] == "s") == band_of(tok), (cell, tok)


def test_a_second_column_of_boxes_cannot_bind(b: D.Bands) -> None:
    """Why the stack cannot be folded into two columns of half the height.

    Every pipe is on the north wall, so "nearest pipe" is nearest *column*: a box
    shifted east past the last anchor takes the easternmost anchor for every one
    of its ops, whatever the block wanted.
    """
    recv = {r: c for (r, s), c in D.ANCHORS.items() if not s}
    send = {r: c for (r, s), c in D.ANCHORS.items() if s}
    for x in (D.IW, D.IW + 6, 2 * D.IW, 3 * D.IW):
        assert min(recv, key=lambda r: (abs(x - recv[r]), r)) == "c"
        assert min(send, key=lambda r: (abs(x - send[r]), r)) == "c"


def test_the_drawn_room_executes_the_cfg(b: D.Bands) -> None:
    """The whole worker, walked block by block against the CFG it compiles.

    Cells that are *walked* but hold no glyph are invisible to every other kind
    of check, so the layout is only believable once someone has followed the
    man's feet from every block's first cell.
    """
    from randomfun2026solvers import matmul_grid as G

    room = D.build_room(b)
    G.check_room(room)
    assert (room.iw, room.ih) == (53, 147)


def test_the_room_has_exactly_one_spawn(b: D.Bands) -> None:
    """Without it the grid loads, the relays turn, and nothing comes out."""
    room = D.build_room(b)
    spawns = [c for c, ch in room.circuit.cell.items() if ch == "@"]
    assert len(spawns) == 1
    (sx, sy), = spawns
    (ex, ey), _ = room.heading("HEAD")
    assert (sx + 1, sy) == (ex, ey)


def test_every_chain_lays_in_its_own_box(b: D.Bands) -> None:
    laid = 0
    for chain in chains_of():
        box = D.lay_chain(chain.blocks, b)
        assert box.cells
        assert all(0 <= x < D.IW for x, _ in box.cells)
        laid += 1
    assert laid == 17
