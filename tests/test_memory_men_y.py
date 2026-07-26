"""The ``Y``-split bank selector, and the man-memory wired as an LM-1 STORE block.

Both are verified against the engine rather than by inspection: a selector whose
predicate is wrong still loads, and a STORE block with a mis-bound pipe still runs
— it just answers the wrong cell.
"""

from __future__ import annotations

import random

import pytest
from randomfun2026solvers import optimize
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine
from randomfun2026solvers.memory_men_store import MAX_LINE_N, men_block, men_ticks
from randomfun2026solvers.memory_men_y import build_tree_y, y_men_block, y_selector_rows


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


def test_selector_splits_once_and_halts_the_loser():
    sel = y_selector_rows(4)
    joined = "\n".join(sel.rows)
    assert joined.count("Y") == 1, "one split per request, not one per bank"
    # three halts: the high child's out-of-range case and both of the low child's
    assert joined.count("H") == 3
    # each child recomputes the predicate: Y copies registers, not conclusions
    assert joined.count("W-X") == 2
    assert sel.high_col != sel.low_col


def test_selector_ports_straddle_the_room():
    # The bank pipes leave on opposite walls so each child's `s` is strictly
    # nearest its own bank; a tie here breaks by reading order and silently sends
    # every request to one bank.
    sel = y_selector_rows(8)
    assert sel.low_col < sel.high_col


def test_store_block_shape_matches_the_tape_seam():
    blk = men_block(5)
    assert blk.cells and blk.width > 0 and blk.height > 0
    # the request stub is entered heading east, the response stub leaves north
    assert blk.cells[blk.in_cell] == ">"
    assert blk.cells[blk.out_cell] == "^"


def test_store_block_refuses_a_size_it_would_lose_at():
    with pytest.raises(ValueError):
        men_block(MAX_LINE_N + 1)
    with pytest.raises(ValueError):
        men_block(0)


def test_men_ticks_is_the_measured_line_cost():
    assert men_ticks(0) == 22
    assert men_ticks(4) == 78


def test_y_store_block_rounds_odd_capacity_and_matches_the_seam() -> None:
    blk = y_men_block(5)
    assert blk.capacity == 6
    assert blk.pipes == 15
    assert blk.cells[blk.in_cell] == ">"
    assert blk.cells[blk.out_cell] == "^"


@pytest.mark.slow
def test_y_store_backend_passes_brackets_public_cases() -> None:
    candidate = machine.build_for("brackets", store="men-y")
    result = optimize.verify(candidate.rows, "brackets")
    assert result.passed


@pytest.mark.slow
@pytest.mark.parametrize("bank", [2, 4])
def test_y_memory_answers_random_streams(bank: int) -> None:
    rows, _, _ = build_tree_y(bank)
    stream, want = _stream(2 * bank, 40, seed=bank)
    result = FastLittleman("\n".join(rows)).run(input=stream, expected=want, max_ticks=600000)
    assert result.passed, (result.fatal, result.fatal_pos, result.output)
