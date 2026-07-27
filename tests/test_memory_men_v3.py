"""v3 man-memory: the router unrolled, the storage untouched.

The claim this file defends is the one the design rests on — **the router is the
whole per-access cost**, so unrolling its loop is the only change worth making.
Three things are checked against the engine: the bus is still v2's, an access is
still flat in the address, and the ops knob is a speed knob and never a
correctness one.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.memory_men_addr import CELL_TILE, DECODER_TILE, build_addr
from randomfun2026solvers.memory_men_v3 import BLOCK, MAIN, build_v3, router_rows

PROBLEM = Path(__file__).resolve().parents[1] / "tasks" / "problems" / "memory.json"


def _stream(n, ops, seed, wfrac=0.5):
    rng = random.Random(seed)
    mem, stream, want = [0] * n, [], []
    for _ in range(ops):
        addr = rng.randrange(n)
        if rng.random() < wfrac:
            value = rng.randint(-1000000, 1000000)
            stream += [1, addr, value]
            mem[addr] = value
        else:
            stream += [0, addr]
            want.append(mem[addr])
    return stream, want


def _marginal(engine, n, wfrac=0.5, lo=20, hi=220):
    """Ticks per operation, with the one-off ignition walk differenced out."""
    points = []
    for ops in (lo, hi):
        stream, want = _stream(n, ops, 11, wfrac)
        result = engine.run(stream, expected=want, max_ticks=3_000_000)
        assert result.ok and result.output == want, result.fatal or result.reason
        points.append(result.step)
    return (points[1] - points[0]) / (hi - lo)


# ── the design claim ────────────────────────────────────────────────────────


def test_the_storage_tiles_are_v2s_untouched():
    # v3 is a router change. If this ever fails the module's whole argument —
    # "the decoders have slack, the router has none" — has been quietly dropped.
    from randomfun2026solvers import memory_men_v3 as v3

    assert v3.DECODER_TILE is DECODER_TILE
    assert v3.CELL_TILE is CELL_TILE


def test_a_block_spends_no_tick_walking_home():
    # Eight glyphs of work, one pass-through and one rejoin: the ten columns an
    # operation costs. v2 ran the same eight round a seventeen-cell loop.
    assert MAIN == "rMrSWXSS"
    assert len(MAIN) == 8
    assert BLOCK == 10


def test_the_router_is_a_loop_so_ops_is_only_a_speed_knob():
    # A stream longer than the unrolled chain must still be answered — the last
    # block walks back to the first rather than halting.
    engine = FastLittleman(build_v3(8, ops=4, per_row=2).source())
    stream, want = _stream(8, 40, seed=3)
    result = engine.run(stream, expected=want, max_ticks=500_000)
    assert result.ok and result.output == want


# ── correctness ─────────────────────────────────────────────────────────────


def test_every_public_case_passes():
    cases = json.loads(PROBLEM.read_text())["publicTestData"]
    engine = FastLittleman(build_v3(100, ops=500, per_row=12).source())
    for case in cases:
        want = [int(v) for v in case["out"]]
        result = engine.run([int(v) for v in case["in"]], expected=want, max_ticks=2_000_000)
        assert result.output == want, f"{case['name']}: {result.output} != {want}"


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_random_streams_against_a_python_model(seed):
    engine = FastLittleman(build_v3(16, ops=200, per_row=10).source())
    stream, want = _stream(16, 120, seed)
    result = engine.run(stream, expected=want, max_ticks=1_000_000)
    assert result.ok and result.output == want


def test_the_ops_knob_never_changes_the_answer():
    stream, want = _stream(8, 30, seed=7)
    for ops, per_row in ((1, 1), (7, 3), (30, 8), (64, 12)):
        engine = FastLittleman(build_v3(8, ops=ops, per_row=per_row).source())
        result = engine.run(stream, expected=want, max_ticks=500_000)
        assert result.ok and result.output == want, f"ops={ops}"


# ── cost ────────────────────────────────────────────────────────────────────


def test_an_access_is_still_flat_in_the_address():
    # The broadcast is what makes the address free; unrolling the router must not
    # have reintroduced a walk. Low and high addresses within noise of each other.
    engine = FastLittleman(build_v3(100, ops=500, per_row=12).source())
    costs = []
    for addr in (0, 99):
        lo = engine.run([0, addr] * 20, expected=[0] * 20, max_ticks=500_000)
        hi = engine.run([0, addr] * 220, expected=[0] * 220, max_ticks=1_000_000)
        costs.append((hi.step - lo.step) / 200)
    assert abs(costs[0] - costs[1]) < 0.5, costs


def test_v3_beats_v2_on_ticks_per_operation():
    v2 = _marginal(FastLittleman(build_addr(100).source()), 100)
    v3 = _marginal(FastLittleman(build_v3(100, ops=500, per_row=12).source()), 100)
    # measured 16.79 -> 11.22 on a 50/50 mix; hold the win with room for engine drift
    assert v3 < v2 * 0.75, f"v2 {v2:.2f} -> v3 {v3:.2f} ticks/op"


def test_unrolling_does_not_cost_footprint():
    # The decoder room's 3n rows still set the square, so the router's width is
    # free — which is the only reason unrolling is affordable at all.
    assert build_v3(100, ops=500, per_row=12).footprint == build_addr(100).footprint


def test_router_rows_rejects_a_degenerate_chain():
    with pytest.raises(ValueError):
        router_rows(0)


# ── the grid: the shape the graded solution used ────────────────────────────


def test_the_unrolled_router_drops_into_the_grid_unchanged():
    # The strip only ever required a router owning one incoming pipe and no
    # outgoing pipe but the column stubs. v3 uses plain `r`, not `U`, so it does
    # not even care which wall the stream arrives on.
    from randomfun2026solvers.memory_men_grid import build_grid
    from randomfun2026solvers.memory_men_v3 import build_v3_grid

    cases = json.loads(PROBLEM.read_text())["publicTestData"]
    engine = FastLittleman(build_v3_grid(4, 25).source())
    for case in cases:
        want = [int(v) for v in case["out"]]
        result = engine.run([int(v) for v in case["in"]], expected=want, max_ticks=2_000_000)
        assert result.output == want, case["name"]

    v2 = _marginal(FastLittleman(build_grid(4, 25).source()), 100)
    v3 = _marginal(engine, 100)
    # measured 16.79 -> 11.28; the repeater's 10-tick lap is the new floor
    assert v3 < v2 * 0.75, f"grid v2 {v2:.2f} -> v3 {v3:.2f} ticks/op"


def test_the_grid_router_hook_defaults_to_v2():
    from randomfun2026solvers.memory_men_grid import build_grid

    assert build_grid(2, 4).rows == build_grid(2, 4, router=None).rows


def test_a_one_column_grid_really_gets_the_unrolled_router():
    # `build_grid` delegates one column to `build_addr`, which has no strip — so a
    # `router=` there used to be accepted and silently dropped, and a 1xN build
    # measured identical to v2. Caught by sweeping shapes in isolation.
    from randomfun2026solvers.memory_men_grid import build_grid
    from randomfun2026solvers.memory_men_v3 import build_v3_grid

    with pytest.raises(ValueError, match="one-column"):
        build_grid(1, 8, router=("@H",))

    v2 = _marginal(FastLittleman(build_grid(1, 25).source()), 25)
    v3 = _marginal(FastLittleman(build_v3_grid(1, 25).source()), 25)
    assert v3 < v2 * 0.75, f"1x25 v2 {v2:.2f} -> v3 {v3:.2f} ticks/op"


@pytest.mark.parametrize("shape", [(2, 8), (4, 25), (10, 10), (8, 13)])
def test_the_block_answers_at_every_shape(shape):
    # n = cols*rows cells whatever the aspect: every cell starts at zero, every
    # cell holds what was written to it, and the extremes of the value range survive.
    from randomfun2026solvers.memory_men_v3 import build_v3_grid

    cols, rows = shape
    n = cols * rows
    engine = FastLittleman(build_v3_grid(cols, rows).source())

    fresh = engine.run([x for a in range(n) for x in (0, a)], expected=[0] * n, max_ticks=3_000_000)
    assert fresh.ok and fresh.output == [0] * n

    written = [x for a in range(n) for x in (1, a, a * 7 - 3)]
    want = [a * 7 - 3 for a in range(n)]
    back = engine.run(
        written + [x for a in range(n) for x in (0, a)], expected=want, max_ticks=3_000_000
    )
    assert back.ok and back.output == want

    edges = engine.run(
        [1, 0, -1000000, 1, n - 1, 1000000, 0, 0, 0, n - 1],
        expected=[-1000000, 1000000],
        max_ticks=3_000_000,
    )
    assert edges.ok and edges.output == [-1000000, 1000000]
