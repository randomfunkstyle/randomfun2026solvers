"""The address-carrying man-memory wired as a CPU STORE (``store="grid"``).

The tier is *correct* and — measured on the engine — the wrong trade almost
everywhere, so what these tests pin is both halves of that. The correctness half
keeps the wiring from rotting; the geometry half records the reason it loses, so
the next person to reach for it reads the number instead of rebuilding it.

See ARCH.md §4.1 "The man-memory as STORE" for the measured table.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers import optimize  # noqa: E402
from randomfun2026solvers.lm1 import machine  # noqa: E402
from randomfun2026solvers.memory_men_addr import build_addr  # noqa: E402


def test_io_false_swaps_both_rooms_for_stubs_and_reports_them() -> None:
    """A program may hold at most one ``I`` and one ``O`` and the CPU owns both, so
    an embedded STORE cannot keep the standalone program's rooms."""
    standalone, block = build_addr(6), build_addr(6, io=False)

    assert "I" in standalone.source() and "O" in standalone.source()
    assert "I" not in block.source() and "O" not in block.source()
    assert standalone.in_cell is None and standalone.out_cell is None
    assert block.in_cell is not None and block.out_cell is not None


def test_the_request_stub_arrives_pointing_east_and_turns_south_inside() -> None:
    """The router reads its op with ``U`` — a receive *and* a turn in one glyph — so
    it is the arrival *heading* that the program depends on, not just the position.
    The stub therefore still enters the router's north wall; the turn is internal.
    """
    block = build_addr(6, io=False)
    assert block.in_cell is not None
    x, y = block.in_cell
    assert block.rows[y][x] == ">", "the host's request pipe must continue eastward"
    # somewhere along that row the pipe turns south, and nothing points back north
    assert "v" in block.rows[y]


def test_the_answer_leaves_north_and_stops_one_row_short_of_the_top() -> None:
    """``_assemble`` continues the response from one cell *above* ``out_cell``, so the
    block must leave that row free or the two pipes cannot join into one."""
    block = build_addr(9, io=False)
    assert block.out_cell is not None
    x, y = block.out_cell
    assert y == 1
    assert block.rows[y][x] == "^"
    assert len(block.rows[0].strip()) == 0, "row 0 belongs to the host's corridor"


@pytest.mark.parametrize("n", [4, 9, 16, 52])
def test_the_block_is_thirty_six_wide_whatever_n_is_and_three_rows_per_slot(n: int) -> None:
    """The whole trade in one assertion. Width is flat, so the cost of this tier is
    ``3n`` *rows* — and because the ROM already fills every row above the block,
    those rows are additive on the machine's longer side (ARCH.md §4.1).
    """
    block = build_addr(n, io=False)
    assert block.width == 36
    assert block.height == 3 * n + 9


def test_an_unknown_tier_names_the_ones_that_exist() -> None:
    with pytest.raises(machine.MachineError, match="unknown store tier"):
        machine.build_for("brackets", store="nonesuch")


@pytest.mark.slow
def test_snake_ring_is_the_one_program_the_swap_is_free_for() -> None:
    """``n=9`` is small enough that ``3n + 9`` fits inside the CPU's own height, so
    the block hides under a dimension already being paid for. This is the *only*
    measured program where the grid store costs no footprint at all, which is why
    it is also the only one where its tick win survives to the score.
    """
    tape = machine.build_for("snake-ring", store="tape")
    grid = machine.build_for("snake-ring", store="grid")

    def area2(m: machine.Machine) -> int:
        return max(max(len(r) for r in m.rows), len(m.rows)) ** 2

    # 18,225 while the machine was 121x135 and height-bound; 15,376 now that the
    # structures band packs its slab entry rows and ``build_cpu`` no longer counts the
    # past-the-end ``bottom`` as interior height. What this test asserts — that the
    # grid store costs snake-ring *nothing* — is unchanged: both builds are equal.
    assert area2(grid) == area2(tape) == 15376


@pytest.mark.slow
def test_grid_store_passes_brackets_public_cases() -> None:
    """Correctness, on the tier that swaps out the whole memory: nine cases of
    bracket matching, on a store whose every read is a broadcast rather than a walk.
    """
    candidate = machine.build_for("brackets", store="grid")
    result = optimize.verify(candidate.rows, "brackets")
    assert result.passed


@pytest.mark.slow
def test_the_tick_win_grows_with_n_and_the_footprint_cost_grows_faster() -> None:
    """The shape of the negative result, as an ordering rather than exact ticks.

    ``tcp`` (n=52) gains far more ticks from the swap than ``brackets`` (n=5) — it is
    44 % tape — and loses far more footprint. Ticks are compared through the engine;
    footprint is exact.
    """
    small = {s: machine.build_for("brackets", store=s) for s in ("tape", "grid")}
    large = {s: machine.build_for("tcp", store=s) for s in ("tape", "grid")}

    def area2(m: machine.Machine) -> int:
        return max(max(len(r) for r in m.rows), len(m.rows)) ** 2

    small_cost = area2(small["grid"]) / area2(small["tape"])
    large_cost = area2(large["grid"]) / area2(large["tape"])
    assert 1.0 < small_cost < 1.2, small_cost
    assert large_cost > 2.5, large_cost

    small_win = optimize.verify(small["grid"].rows, "brackets").avg_ticks / (
        optimize.verify(small["tape"].rows, "brackets").avg_ticks
    )
    large_win = optimize.verify(large["grid"].rows, "tcp").avg_ticks / (
        optimize.verify(large["tape"].rows, "tcp").avg_ticks
    )
    assert large_win < small_win < 1.0, (large_win, small_win)
