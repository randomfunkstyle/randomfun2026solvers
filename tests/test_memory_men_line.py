"""``n`` man-cells sharing ONE room, every resident born by ``Y``.

The fast tier pins the shape — that each site has a ``Y``, that the spawner does
not sit on one, and the measured cost model. The end-to-end proofs run on the
**reference** engine, because this design leans on split semantics (birth cells,
birth order, what a copy retains) where the fast validator agreeing is worth
checking rather than assuming.
"""

from __future__ import annotations

import random

import pytest
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.memory_men_line import (
    CELL_PITCH,
    FIELD_ROWS,
    build_field_line,
    field_rows,
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


def test_every_site_has_its_own_split():
    # The bug this pins: `@` placed on site 0's `Y` leaves cell 0 with no man, and
    # every test that never reads address 0 still passes.
    rows, cmd, ans = field_rows(16)
    assert len(rows) == FIELD_ROWS
    assert rows[1].count("Y") == 16, rows[1]
    assert rows[1][0] == "@"
    assert rows[1].count("H") == 1, "the last copy must stop, not walk into a wall"
    assert len(cmd) == len(ans) == 16


def test_tiles_keep_the_verified_cell_logic():
    rows, _, _ = field_rows(2)
    # the tile is memory_men.CELL with the spawn glyph gone: the man arrives by birth
    assert "rX" in rows[3] and "rW" in rows[4] and "W<s" in rows[5] and "M<" in rows[6]


def test_the_split_has_a_free_cell_on_both_sides():
    # A birth into a wall is fatal (reference/split.txt), so every `Y` needs a real
    # interior row above and below it, and the south birth cell must be a blank the
    # copy falls through onto the tile's `>`.
    rows, _, _ = field_rows(4)
    corridor = 1
    assert 0 < corridor < FIELD_ROWS - 1
    for x, glyph in enumerate(rows[corridor]):
        if glyph == "Y":
            assert rows[corridor - 1][x] == ">", "north copy needs its return lane"
            assert rows[corridor + 1][x] == " ", "south copy must fall through"
            assert rows[corridor + 2][x] == ">", "...and land on the tile's entry"


def test_field_is_squarer_than_one_room_per_value():
    from randomfun2026solvers.memory_men import build_line

    for n in (8, 16):
        field, walled = build_field_line(n), build_line(n)
        assert field.footprint < walled.footprint
        assert field.width < walled.width


def test_cost_model_is_ten_ticks_a_lane():
    src = build_field_line(8).source()

    def ticks(k: int, addr: int) -> int:
        result = FastLittleman(src).run(input=f"0 {addr} " * k, expected=[0] * k, max_ticks=200000)
        assert result.passed, (result.fatal, result.fatal_pos)
        return result.step

    per_op = [(ticks(12, a) - ticks(4, a)) / 8 for a in (0, 1, 7)]
    assert per_op == [22.0, 32.0, 92.0], per_op
    assert CELL_PITCH == 4


@pytest.mark.slow
def test_sixteen_cells_start_at_zero_on_the_reference_engine():
    src = build_field_line(16).source()
    stream = " ".join(f"0 {a}" for a in range(16))
    snap = Littleman().judge(src, input=stream, expected=[0] * 16, max_ticks=200000)
    assert snap.output == [0] * 16, snap.output


@pytest.mark.slow
def test_sixteen_cells_hold_sixteen_distinct_values_on_the_reference_engine():
    src = build_field_line(16).source()
    want = [100 + a for a in range(16)]
    stream = " ".join(f"1 {a} {100 + a}" for a in range(16))
    stream += " " + " ".join(f"0 {a}" for a in range(16))
    snap = Littleman().judge(src, input=stream, expected=want, max_ticks=200000)
    assert snap.output == want, snap.output


@pytest.mark.slow
@pytest.mark.parametrize("seed", [1, 2])
def test_sixteen_cells_answer_random_streams_on_the_reference_engine(seed: int) -> None:
    src = build_field_line(16).source()
    stream, want = _stream(16, 60, seed)
    snap = Littleman().judge(src, input=stream, expected=want, max_ticks=300000)
    assert snap.output == want, (snap.output, want)
