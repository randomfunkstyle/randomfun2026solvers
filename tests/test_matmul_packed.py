"""What `matmul_packed` has been measured to do, against `matmul_ring`.

The tuned machine is the same CFG, the same six rings and the same fourteen
pipes as the shipped one; only the band widths and the west margin differ.  So
every check here is a *comparison*: it fails if the tuning changed something it
was not supposed to change, or failed to change something it claimed to.

The fast tier asserts shapes and invariants and runs no engine.  The slow tier
runs all seven public cases on `FastLittleman`, and on the reference engine when
``LM_VALIDATOR=reference`` is set.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest

from randomfun2026solvers import matmul_cfg as cfg
from randomfun2026solvers import matmul_grid as mg
from randomfun2026solvers import matmul_packed as mp
from randomfun2026solvers import scoring

REPO = Path(__file__).resolve().parents[1]
GRID = REPO / "tasks" / "solutions" / "matmul_packed.man"
RING = REPO / "tasks" / "solutions" / "matmul_ring.man"
PROBLEM = REPO / "tasks" / "problems" / "matmul.json"

#: What the generator was measured at.  `matmul_ring.man` is 88x98 / 9,604 /
#: 31,553 / 3.030e8, so every one of these is an improvement and none is a
#: regression -- which is the whole claim being made.
SIZE = (85, 96)
AREA2 = 9216
TICKS = 31_089.714285714286
SCORE = 286_522_806.85714287


def _cases() -> list[tuple[str, list[int], list[int]]]:
    prob = json.loads(PROBLEM.read_text())
    out = []
    for case in prob["publicTestData"]:
        rnd = case["rounds"][0]
        out.append((case["name"], [int(v) for v in rnd["in"]],
                    [int(v) for v in rnd["out"]]))
    return out


def _case(n: int, m: int, k: int, seed: int) -> list[int]:
    rng = random.Random(seed)
    return [n, m, k] + [rng.randint(-99, 99) for _ in range(n * m + m * k)]


@pytest.fixture(scope="module")
def room() -> mg.Room:
    return mp.build_room()


# ── the fast tier: shapes and invariants ──────────────────────────────────────
def test_the_checked_in_grid_is_what_the_generator_emits():
    art, _dbg, _info = mp.build_grid()
    assert GRID.read_text() == "\n".join(art) + "\n"


def test_the_footprint_is_what_the_report_claims():
    w, h, area2 = scoring.footprint(GRID)
    assert (w, h, area2) == (*SIZE, AREA2)


def test_the_tuning_did_not_touch_the_shipped_ring_machine():
    """`matmul_ring.man` is a separate submission and must stay byte-identical.

    The trim that saves five columns here is off by default in
    `matmul_grid.build_room` for exactly this reason, and a default that drifts
    would rewrite a grid nobody asked to change.
    """
    art, _dbg, _info = mg.build_grid()
    assert RING.read_text() == "\n".join(art) + "\n"
    assert scoring.footprint(RING) == (88, 98, 9604)


def test_the_tuned_machine_is_smaller_on_both_axes_than_the_shipped_one():
    w, h, area2 = scoring.footprint(GRID)
    rw, rh, rarea2 = scoring.footprint(RING)
    assert (w, h) < (rw, rh)
    assert area2 < rarea2


def test_the_room_lost_the_channel_columns_the_router_never_reached(room):
    """Trimming is a translation, so the west column must now hold something."""
    assert any(room.circuit.get(0, y) != " " for y in range(room.ih))
    untrimmed = mg.build_room(mg.plan(mp.GEOMETRY))
    assert untrimmed.margin > room.margin
    assert untrimmed.iw - untrimmed.margin == room.iw - room.margin


def test_every_block_walks_its_own_tokens(room):
    mg.check_room(room)


def test_every_block_lands_where_the_cfg_says_it_should(room):
    """Walked from every block's first cell, and matched against the CFG.

    A cell that is walked but holds no glyph is invisible to every other check:
    the grid loads, the pipes bind, and the machine computes something else.
    """
    walked = mg.walk_blocks(room)
    for name in mg.LAID:
        ops, lanes = walked[name]
        assert [o for o in ops if o != " "] == mg._glyphs(name), name
        for lane, target in mg.successors(name).items():
            assert lanes.get(lane) == target, (name, lane)


def test_the_grid_holds_no_backtick():
    """Backticks pair on columns as well as rows; every literal is digits."""
    assert "`" not in GRID.read_text()


def test_the_grid_has_the_fourteen_pipes_and_nine_rooms():
    """A pipe whose first cell points into its room parses as nothing at all.

    Counting them is the only check that catches it: the grid still loads, the
    machine still runs, and it deadlocks on a ring that was never connected.
    """
    from randomfun2026solvers.littleman import Littleman

    got = Littleman().analyze(GRID)
    assert (len(got.rooms), len(got.pipes)) == (9, 14)


def test_each_ring_is_longer_than_the_words_it_carries():
    _art, _dbg, info = mp.build_grid()
    for ring, cells in info["rings"].items():
        assert cells >= mg.RING_WORDS[ring] + 1, (ring, cells)


def test_the_estimator_agrees_with_the_engine_on_the_full_size_case(room):
    """The geometry search prices thousands of layouts by walking them."""
    est = mg.estimate_ticks(room, *mg.public_traces()[3])
    assert abs(est - 130_644) < 0.02 * 130_644


def test_the_band_widths_are_the_ones_the_search_settled_on():
    assert mp.GEOMETRY.recv_order == ("q", "k", "io", "s", "b", "c", "x")
    assert mp.GEOMETRY.recv_w == {"q": 18, "k": 7, "io": 4, "s": 7,
                                  "b": 4, "c": 8, "x": 9}
    assert mp.GEOMETRY.recv_w == mp.GEOMETRY.send_w


# ── the slow tier: the engines ────────────────────────────────────────────────
@pytest.mark.slow
def test_every_public_case_passes_on_the_fast_engine():
    from randomfun2026solvers.fast_littleman import FastLittleman

    machine = FastLittleman(GRID)
    for name, inp, exp in _cases():
        res = machine.run(input=inp, expected=exp, max_ticks=2_000_000)
        assert list(res.output) == exp, name


@pytest.mark.slow
def test_the_shapes_the_public_cases_do_not_reach_also_pass():
    """The corners of the constraint box, where a short ring stalls silently."""
    from randomfun2026solvers.fast_littleman import FastLittleman

    machine = FastLittleman(GRID)
    for n, m, k in [(1, 1, 1), (2, 2, 1), (16, 16, 1), (16, 1, 16),
                    (2, 16, 2), (16, 2, 2), (3, 16, 16), (16, 16, 3)]:
        case = _case(n, m, k, seed=n * 1000 + m * 10 + k)
        exp = cfg.matmul_reference(case)
        res = machine.run(input=case, expected=exp, max_ticks=3_000_000)
        assert list(res.output) == exp, (n, m, k)


@pytest.mark.slow
def test_every_public_case_passes_on_the_reference_engine():
    if os.environ.get("LM_VALIDATOR", "").lower() != "reference":
        pytest.skip("set LM_VALIDATOR=reference to cross-check the wasm engine")
    from randomfun2026solvers.littleman import Littleman

    lm = Littleman()
    for name, inp, exp in _cases():
        snap = lm.judge(GRID, input=" ".join(map(str, inp)),
                        expected=" ".join(map(str, exp)), max_ticks=2_000_000)
        assert snap.fatal is None, (name, snap.fatal)
        assert list(snap.output) == exp, name


@pytest.mark.slow
def test_the_score_is_what_the_report_claims():
    got = scoring.score_program(GRID, PROBLEM)
    assert (got.width, got.height, got.area2) == (*SIZE, AREA2)
    assert got.avg_ticks == pytest.approx(TICKS, rel=1e-9)
    assert got.score == pytest.approx(SCORE, rel=1e-9)
    ring = scoring.score_program(RING, PROBLEM)
    assert got.score < ring.score
