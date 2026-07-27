"""The dense layout's load-bearing pieces: bands, loop rectangles, the room.

None of it is checkable by eye.  A band that ties binds a pipe op to whichever
pipe reading order happens to pick; a loop rectangle one cell too short silently
drops a glyph; a room with no `@` loads, turns its relay men, and emits nothing.
All three produce a grid that runs and computes something else.

A fourth was found the expensive way and is pinned below.  **Landing on a
block's first cell is only half of arriving at it**: a block is a run of glyphs
read in one direction, so a lane that delivers the man onto that cell facing any
other way executes the one glyph and then walks him straight out of the block.
Every structural check still passes -- the glyphs are all there, in order, and
the walk from the block's *own* heading finds them -- so the only symptom is a
machine that computes something else.  ``BL2 -pos-> BL2_R`` shipped that way for
three sessions; only ``K >= 3`` ever takes that lane, so 2x2x2 passed and every
larger case hung with its rings scrambled.

The lesson generalises past the one lane: a checker that walks each block in
isolation cannot see how the man *got there*, so the arrival heading is now
checked in ``matmul_grid.walk_blocks`` where every lane goes, and the drawn grid
is executed against ``matmul_reference`` here rather than merely inspected.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest
from randomfun2026solvers import matmul_cfg as cfg
from randomfun2026solvers import matmul_dense as D
from randomfun2026solvers.matmul_grid import LAID, band_of, chains_of

REPO = Path(__file__).resolve().parents[1]
GRID = REPO / "tasks" / "solutions" / "matmul_dense.man"
PROBLEM = REPO / "tasks" / "problems" / "matmul.json"


def _case(n: int, m: int, k: int, seed: int) -> list[int]:
    rng = random.Random(seed)
    return [n, m, k] + [rng.randint(-99, 99) for _ in range(n * m + m * k)]


def _cases() -> list[tuple[str, list[int], list[int]]]:
    prob = json.loads(PROBLEM.read_text())
    return [(case["name"], [int(v) for v in case["rounds"][0]["in"]],
             [int(v) for v in case["rounds"][0]["out"]])
            for case in prob["publicTestData"]]


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


def test_every_lane_arrives_facing_the_way_its_target_is_written(b: D.Bands) -> None:
    """The bug that hung every case with `K >= 3`, pinned at the room level.

    ``_route_edges`` skips drawing a corridor when the man already falls through
    to the block he is meant to reach -- and "reaches" used to mean *lands on
    the cell*, with no word about which way he was facing when he got there.
    ``BL2``'s `pos` lane dropped onto ``BL2_R``'s first glyph heading **south**
    against the eastward run it is written as, so the man read `rk` and then
    walked out of the block through the blanks below it and into ``HEAD``.
    """
    from randomfun2026solvers import matmul_grid as G

    room = D.build_room(b)
    starts, circuit = room.starts, room.circuit

    def follow(pos, d):
        for _ in range(4 * (room.iw + room.ih)):
            if pos in starts:
                return starts[pos], d
            ch = circuit.get(*pos)
            if ch in G._TURN:
                d = G._TURN[ch]
            elif ch != " ":
                return None, d
            pos = (pos[0] + d[0], pos[1] + d[1])
        return None, d

    for (name, lane), (pos, d) in D.lane_origins(room).items():
        if (name, lane) in G.DEAD_LANES:
            continue
        target, arrive = follow((pos[0] + d[0], pos[1] + d[1]), d)
        if target is None:
            continue
        assert arrive == room.heading(target)[1], (name, lane, target, arrive)


@pytest.mark.parametrize(("n", "m", "k"), [
    (2, 2, 2),      # one group: the shape that passed while the rest hung
    (2, 2, 3),      # two groups -- the first shape that takes `BL2 -> BL2_R`
    (4, 4, 4),
    (7, 5, 9),
    (16, 16, 1),    # `K = 1`: the `c` ring at its shortest
    (16, 1, 16),    # `M = 1`: the `t` loop turns over exactly once
    (3, 16, 16),
])
def test_the_drawn_grid_multiplies_matrices(n: int, m: int, k: int) -> None:
    """The whole artefact, executed -- not inspected.

    Every other check in this file reads the layout the pen produced and asks
    whether it looks like the plan.  This one runs the finished `.man` and asks
    whether it *is* matrix multiplication, which is the only question a silent
    layout fault cannot answer for itself.
    """
    from randomfun2026solvers.fast_littleman import FastLittleman

    case = _case(n, m, k, seed=n * 1000 + m * 10 + k)
    exp = cfg.matmul_reference(case)
    res = FastLittleman(GRID).run(input=case, expected=exp, max_ticks=5_000_000)
    assert list(res.output) == exp, (n, m, k, res.reason)


def test_one_row_less_north_band_draws_and_does_not_load(b: D.Bands) -> None:
    """Why the band fit is gated on the engine's parse and not on the pen.

    ``build_grid`` at `nb = 14` raises nothing: every cell it writes is free and
    the art looks exactly like the art that works.  It is the *language* that
    rejects it -- a return pipe reaches back into the turnaround room it left --
    and that is a load error, not a collision.  A fit that trusted the pen would
    have shipped it, and the only symptom is a grid that scores nothing.
    """
    from randomfun2026solvers.matmul_grid import grid_loads

    room = D.build_room(b)
    good, _dbg, _meta = D.build_grid(room)
    assert grid_loads(good)
    fitted = len(good) - room.ih - 2                 # the band the fit settled on
    bad, _dbg, _meta = D.build_grid(room, nb=fitted - 1)
    assert len(bad) < len(good)
    assert not grid_loads(bad)


def test_the_committed_grid_is_what_the_generator_emits() -> None:
    """Otherwise a fix to the pen never reaches the file that gets submitted."""
    art, _dbg, _meta = D.build_grid()
    assert GRID.read_text() == "\n".join(art) + "\n"


@pytest.mark.slow
def test_every_public_case_passes_on_the_reference_engine() -> None:
    if os.environ.get("LM_VALIDATOR", "").lower() != "reference":
        pytest.skip("set LM_VALIDATOR=reference to cross-check the wasm engine")
    from randomfun2026solvers.littleman import Littleman

    lm = Littleman()
    for name, inp, exp in _cases():
        snap = lm.judge(GRID, input=" ".join(map(str, inp)),
                        expected=" ".join(map(str, exp)), max_ticks=2_000_000)
        assert snap.fatal is None, (name, snap.fatal)
        assert list(snap.output) == exp, name


def test_every_chain_lays_in_its_own_box(b: D.Bands) -> None:
    laid = 0
    for chain in chains_of():
        box = D.lay_chain(chain.blocks, b)
        assert box.cells
        assert all(0 <= x < D.IW for x, _ in box.cells)
        laid += 1
    assert laid == 17
