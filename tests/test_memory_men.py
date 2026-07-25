"""The man-memory: the cell's semantics, and that the generators still emit them.

The heavy proofs (a generated grid answering random operation streams on the real
engine) are marked slow; the fast tier pins the *shape* — glyphs, geometry, the
measured cost model — so a change to a generator fails here rather than silently
producing a memory that loads and computes the wrong thing.
"""

from __future__ import annotations

import random

import pytest
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.memory_men import (
    CELL,
    CELL_READ_TICKS,
    CELL_WRITE_TICKS,
    build_line,
    build_tree,
    collector_rows,
    line_ticks,
    router_rows,
)

CELL_PROBE = "\n".join(
    [
        "     +----+",
        "+-+  |>rXv|",
        "|I|>>|  rW|",
        "+-+  |^W<s|>>+-+",
        "     |^@M<|  |O|",
        "     +----+  +-+",
    ]
)


def _stream(n: int, ops: int, seed: int) -> tuple[list[int], list[int]]:
    rng = random.Random(seed)
    mem = [0] * n
    stream: list[int] = []
    want: list[int] = []
    for _ in range(ops):
        addr = rng.randrange(n)
        if rng.random() < 0.5:
            stream += [0, addr]
            want.append(mem[addr])
        else:
            value = rng.randint(-1000000, 1000000)
            stream += [1, addr, value]
            mem[addr] = value
    return stream, want


def test_cell_is_four_by_four_with_the_restore():
    # The READ lane must end in `M`: without it the cell answers once and then
    # holds 0, which every single-write test still passes.
    assert len(CELL) == 4 and all(len(r) == 4 for r in CELL)
    assert "M" in CELL[3], "the READ lane's B restore is missing"
    assert CELL[0][2] == "X", "the op branch moved"


def test_cell_reads_write_and_restores():
    result = FastLittleman(CELL_PROBE).run(input="1 7 0 0", expected=[7, 7], max_ticks=4000)
    assert result.passed, result.fatal
    # a fresh cell answers 0 without any initialisation pass
    fresh = FastLittleman(CELL_PROBE).run(input="0", expected=[0], max_ticks=4000)
    assert fresh.passed, fresh.fatal


def test_cell_tick_costs_hold():
    def ticks(inp: str, want: list[int]) -> int:
        r = FastLittleman(CELL_PROBE).run(input=inp, expected=want, max_ticks=99999)
        assert r.passed, r.fatal
        return r.step

    reads = (ticks("0 " * 12, [0] * 12) - ticks("0 " * 4, [0] * 4)) / 8
    pairs = (ticks("1 5 0 " * 12, [5] * 12) - ticks("1 5 0 " * 4, [5] * 4)) / 8
    assert reads == CELL_READ_TICKS
    assert pairs - reads == CELL_WRITE_TICKS


def test_router_lane_port_sits_under_the_peel_dive():
    # Not cosmetic: with the port a column east the WRITE tail's `s` ties with the
    # western neighbour's pipe and ties break by reading order, so every WRITE
    # lands one cell too far west.
    rows, ports = router_rows(3)
    for x in ports:
        assert rows[0][x - 1] == "d"
        assert rows[0][x] == "v"


def test_mid_router_splits_the_address_with_one_glyph():
    rows, _ = router_rows(2, block=8)
    assert rows[0].startswith(">rM8W/bW"), rows[0]


def test_collector_needs_no_pipe_affinity():
    rows, ports = collector_rows(5)
    assert "R" in rows[0] and "s" in rows[1]
    assert len(ports) == 5


def test_line_cost_model_is_exact():
    assert line_ticks(0) == 22
    assert line_ticks(7) == 22 + 98


@pytest.mark.parametrize("n", [1, 3])
def test_line_geometry(n: int) -> None:
    line = build_line(n)
    assert line.width == 6 * n + 13
    assert line.height == 21


def test_tree_geometry() -> None:
    tree = build_tree(2, 3)
    assert tree.n == 6
    assert tree.rows[0].strip()


@pytest.mark.slow
@pytest.mark.parametrize("n", [1, 4, 8])
def test_line_answers_random_streams(n: int) -> None:
    stream, want = _stream(n, 40, seed=n)
    result = FastLittleman(build_line(n).source()).run(
        input=stream, expected=want, max_ticks=400000
    )
    assert result.passed, (result.fatal, result.fatal_pos, result.output)


@pytest.mark.slow
@pytest.mark.parametrize(("k1", "k2"), [(2, 2), (4, 4)])
def test_tree_answers_random_streams(k1: int, k2: int) -> None:
    tree = build_tree(k1, k2)
    stream, want = _stream(tree.n, 40, seed=k1 * 10 + k2)
    result = FastLittleman(tree.source()).run(input=stream, expected=want, max_ticks=500000)
    assert result.passed, (result.fatal, result.fatal_pos, result.output)
