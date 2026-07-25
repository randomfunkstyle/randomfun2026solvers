"""``M`` columns of ``N`` cells: the same memory with its igniters run in parallel.

The claim to defend is the one that motivated the shape: the fixed cost of a
memory is one igniter's walk, so cutting it into columns cuts the fixed cost by
the number of columns. That is measured here against the reference engine, not
argued — along with the thing it must not break, which is that answers from
different columns come back in operation order.
"""

from __future__ import annotations

import random

import pytest
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.memory_men_addr import CELL_TILE, DECODER_TILE, band_room, build_addr
from randomfun2026solvers.memory_men_grid import REPEATER, build_grid


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


def _fixed_and_slope(src, lm, cap=900_000):
    """Ticks for 1 op and for 5, resolved into `fixed + slope * ops`."""
    t = []
    for k in (1, 5):
        snap = lm.judge(src, input="0 0 " * k, expected=[0] * k, max_ticks=cap)
        assert snap.output == [0] * k, snap.output
        t.append(snap.step)
    slope = (t[1] - t[0]) / 4
    return t[0] - slope, slope


def test_the_head_of_a_column_decodes_nothing():
    # The reason M and N are both free: the column's decoders hold *global*
    # addresses, so this only repeats. No compare, no shift, no arithmetic.
    joined = "".join(REPEATER)
    assert joined.count("r") == 3 and joined.count("S") == 3
    assert not set(joined) & set("~-%{}]bxad")


def test_a_column_carries_its_base_as_a_literal_and_counts_up():
    rows, mains = band_room(4, DECODER_TILE, increment=True, base=24)
    text = "\n".join(rows)
    # read walking south, so the digits appear one per row between the backticks
    assert [r.strip() for r in rows[1:8]] == ["`", "0", "2", "4", "`", "M", "1"]
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
    # per column: one feed, one answer, and three per cell; plus input and output
    assert len(analysis.pipes) == cols * (3 * rows + 2) + 2


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
    assert fixed == {1: 699.75, 4: 189.5, 10: 84.5, 25: 42.5}
    # Seven ticks a band and only ONE column's worth of it is on the clock, plus a
    # flat ~14 for a column's base literal and the two extra pipe hops.
    for cols, rows in [(1, 100), (4, 25), (10, 10), (25, 4)]:
        assert fixed[cols] == pytest.approx(7 * rows + (14.5 if cols > 1 else 0), abs=1)
    # the repeater also took 2.75 ticks off every op, because the router now waits
    # on M repeater pipes rather than on all hundred decoders
    assert slopes[1] == 16.25
    assert {slopes[c] for c in (4, 10, 25)} == {13.5}
    # and this model is what predicted the judged 1x100: 699.75 + 16.25 * 99 ops
    # = 2,308 against an observed avgTicks of 2,283, so ~99 ops a graded case
    assert fixed[1] + slopes[1] * 99 == pytest.approx(2283, rel=0.02)
