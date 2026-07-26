"""``M`` columns of ``N`` cells: the same memory with its igniters run in parallel.

The claim to defend is the one that motivated the shape: the fixed cost of a
memory is one igniter's walk, so cutting it into columns cuts the fixed cost by
the number of columns. That is measured here against the reference engine, not
argued — along with the thing it must not break, which is that answers from
different columns come back in operation order.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.memory_men_addr import (
    CELL_TILE,
    DECODER_TILE,
    ROUTER_ROWS,
    band_room,
    build_addr,
)
from randomfun2026solvers.memory_men_grid import REPEATER, ROUTER_FLAT, build_grid


def _stream(n, ops, seed):
    rng = random.Random(seed)
    mem, stream, want = [0] * n, [], []
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


def _fixed_and_slope(src, lm, lo=4, hi=20, cap=900_000):
    """Resolve a run into `fixed + slope * ops`.

    Fit well away from ``k=1``: the first op also pays the pipeline filling —
    router to repeater to decoder to cell to collector — and a 1-vs-5 fit charges
    that to the slope, which reads as a per-op cost that isn't there. It said 13.5
    where the truth is a flat 16.
    """
    t = []
    for k in (lo, hi):
        snap = lm.judge(src, input="0 0 " * k, expected=[0] * k, max_ticks=cap)
        assert snap.output == [0] * k, snap.output
        t.append(snap.step)
    slope = (t[1] - t[0]) / (hi - lo)
    return t[0] - lo * slope, slope


def test_the_head_of_a_column_decodes_nothing():
    # The reason M and N are both free: the column's decoders hold *global*
    # addresses, so this only repeats. No compare, no shift, no arithmetic.
    joined = "".join(REPEATER)
    assert joined.count("r") == 3 and joined.count("S") == 3
    assert not set(joined) & set("~-%{}]bxad")


def test_a_column_carries_its_base_as_a_literal_and_counts_up():
    rows, mains = band_room(4, DECODER_TILE, increment=True, base=24)
    text = "\n".join(rows)
    # Read walking *east* and snaked back west for the `1`: a room this wide has
    # the columns to spare, and rows are what a column costs. Two rows, not nine.
    assert rows[0] == "@1M`24`v"
    assert rows[1].endswith("<") and rows[1][1] == "v"
    assert text.count("Y") == 4 and text.count("+") == 3
    # a column is its own room, so it needs no ignition pipe and no master igniter
    assert "R" not in text and "U" not in text
    # ...and the cell room pads to the same height so both rooms band level
    _, cell_mains = band_room(4, CELL_TILE, increment=False, init_h=len(rows) - 3 * 4)
    assert mains == cell_mains


def test_one_column_is_the_previous_solution_untouched():
    # A repeater in front of a single column is a pass-through: one more pipeline
    # stage and two more pipes to reach exactly one place.
    assert build_grid(1, 16).source() == build_addr(16).source()


@pytest.mark.parametrize(("cols", "rows"), [(2, 2), (3, 2), (2, 5), (4, 4)])
def test_every_pipe_has_a_source_room(cols, rows):
    analysis = Littleman().analyze(build_grid(cols, rows).source())
    assert all(p.src >= 0 for p in analysis.pipes), [p.src for p in analysis.pipes]
    # Per column: one feed down, two per cell (repeater->decoder, decoder->cell),
    # and ONE answer out. The answer path needs no pipe per cell because at most
    # one cell sends per operation. Plus the program's input and output.
    assert len(analysis.pipes) == cols * (2 * rows + 2) + 2


@pytest.mark.parametrize(("cols", "rows"), [(2, 2), (5, 2), (2, 5), (4, 4), (7, 3)])
def test_a_grid_answers_random_streams_on_the_reference_engine(cols, rows):
    grid = build_grid(cols, rows)
    stream, want = _stream(cols * rows, 25, seed=cols * 31 + rows)
    snap = Littleman().judge(grid.source(), input=stream, expected=want, max_ticks=400_000)
    assert snap.output == want, (snap.output, want)


def test_answers_come_back_in_operation_order_across_columns():
    # The one thing the layout has to guarantee: a later op's answer must not
    # overtake an earlier one. Every column's answer pipe is the same length and
    # the collector strip is on the far side from the router, so alternating reads
    # between the first and last column must still come out in order.
    grid = build_grid(5, 2)
    writes = " ".join(f"1 {a} {100 + a}" for a in range(10))
    alternating = " ".join(f"0 {a}" for a in (0, 9, 1, 8, 2, 7, 3, 6, 4, 5))
    want = [100 + a for a in (0, 9, 1, 8, 2, 7, 3, 6, 4, 5)]
    snap = Littleman().judge(
        grid.source(), input=f"{writes} {alternating}", expected=want, max_ticks=400_000
    )
    assert snap.output == want, snap.output


@pytest.mark.slow
def test_a_hundred_addresses_are_exact_in_every_shape():
    lm = Littleman()
    for cols, rows in [(4, 25), (10, 10), (25, 4)]:
        stream, want = _stream(100, 120, seed=3)
        snap = lm.judge(
            build_grid(cols, rows).source(), input=stream, expected=want, max_ticks=900_000
        )
        assert snap.output == want, (cols, rows, snap.output[:8], want[:8])


@pytest.mark.slow
def test_the_fixed_cost_falls_with_the_number_of_columns():
    # The whole point. One igniter walks 7 ticks a band and the router cannot fire
    # until the last decoder exists; M igniters in M rooms walk at the same time.
    lm = Littleman()
    fixed, slopes = {}, {}
    for cols, rows in [(1, 100), (4, 25), (10, 10), (25, 4)]:
        fixed[cols], slopes[cols] = _fixed_and_slope(build_grid(cols, rows).source(), lm)
    assert fixed == {1: 516.0, 4: 131.0, 10: 56.0, 25: 26.0}
    # ~5 ticks a band, and only ONE column's worth of it is on the clock
    for cols, rows in [(1, 100), (4, 25), (10, 10), (25, 4)]:
        assert fixed[cols] == pytest.approx(5 * rows, abs=20), (cols, rows)
    # Splitting into columns buys the fixed cost and *only* the fixed cost: the
    # per-op price is the router's 16-cell lap and no layout change touches it.
    assert set(slopes.values()) == {16.0}
    # Which is the model both judged runs came out of, to 0.2%:
    #   1x100  701 + 16*99 = 2,285   judged 2,283  (before the single-`+` igniter)
    #   4x25   177 + 16*99 = 1,761   judged 1,758  (likewise)
    # and it is the same model that puts this igniter's 4x25 at 1,715.
    assert fixed[4] + 16 * 99 == pytest.approx(1715, rel=0.01)


def test_the_flat_router_is_the_same_program_lying_down():
    # Kept, not used. `ROUTER_FLAT` is 9x3 where `ROUTER_ROWS` is 3x8, with the
    # same laps (READ 16, WRITE 18) — but it needs its input on the *west* wall,
    # because `U` turns the man away from the pipe and the code runs east. The
    # strip spans every column, so its west wall is at x=0 and a west feed would
    # need x=-1; the whole grid shifts two columns east to make room. That is a
    # loss while the grid is width-bound (132 wide against 105 tall) and a win as
    # soon as it is not — a taller, narrower shape should switch to it.
    def code(rows):
        return sorted(g for r in rows for g in r if g not in " @<>^v")

    assert code(ROUTER_FLAT) == code(ROUTER_ROWS) == sorted("UMrSWSXUS")
    assert (max(len(r) for r in ROUTER_FLAT), len(ROUTER_FLAT)) == (9, 3)
    assert (max(len(r) for r in ROUTER_ROWS), len(ROUTER_ROWS)) == (3, 8)


def test_a_cell_room_needs_exactly_one_outgoing_pipe():
    # The answer path collapses to one pipe per *column*, not one per cell: a cell
    # only reaches its `s` if its decoder spoke to it, and exactly one decoder
    # speaks per operation, so at most one cell in the room ever sends. `s` binds
    # to the nearest outgoing pipe and there is only one, which is also why the
    # per-column collector room could go entirely — the strip's `R` takes the
    # answer straight off the pipe.
    cols, rows = 3, 4
    analysis = Littleman().analyze(build_grid(cols, rows).source())
    out = Counter(p.src for p in analysis.pipes)
    # input: 1, router strip: one per column, then per column the repeater and the
    # decoder fan out to every band while the cell room sends once; strip: 1
    assert sorted(out.values()) == sorted([1, cols, *([rows, rows, 1] * cols), 1])
