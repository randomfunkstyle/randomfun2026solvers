"""``LANE_ORDER``: which lane sits on which row is a tick cost, and a correctness risk.

The row a lane sits on sets its return walk, so weighting lanes by how often their
opcode runs and searching that is worth a few percent for free (§7.6). These tests pin
the orders, pin that they cost no footprint — the constraint that makes the search
honest — and guard the two ways this work misled us:

* a candidate that passes the public cases can still be wrong, so `brackets` is
  exercised on inputs its nine public cases never reach, on the *reference* engine;
* a candidate that *fails* a pinned-tick test can still be right, which is what
  happened to `matmul` — see
  `test_a_pinned_tick_test_failing_does_not_mean_the_grid_is_wrong`.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers import optimize  # noqa: E402
from randomfun2026solvers.littleman import Littleman  # noqa: E402
from randomfun2026solvers.lm1 import machine, programs  # noqa: E402

OPENERS = {40: 41, 91: 93, 123: 125}
CLOSERS = {41: 40, 93: 91, 125: 123}


def _expected(codes: list[int]) -> int:
    """The problem's own rule: 0, else the 1-based first offending position."""
    stack: list[int] = []
    for i, c in enumerate(codes, start=1):
        if c in OPENERS:
            stack.append(c)
        else:
            if not stack or stack[-1] != CLOSERS[c]:
                return i
            stack.pop()
    return len(codes) + 1 if stack else 0


def _random_case(rng: random.Random, n: int) -> list[int]:
    return [rng.choice([40, 41, 91, 93, 123, 125]) for _ in range(n)]


def test_every_pinned_order_is_a_permutation_of_that_programs_unpinned_lanes() -> None:
    """A typo here would silently be a different machine, so it is checked rather than
    trusted: ``plan`` raises on a non-permutation, and this is where that surfaces."""
    for slug, order in machine.LANE_ORDER.items():
        p = machine.plan(programs.load(slug), middle_order=order)
        rows = sorted(p.row.values())
        assert len(rows) == len(set(rows)), f"{slug}: two lanes on one row"


@pytest.mark.parametrize(
    ("slug", "footprint"),
    [("brackets", 90**2), ("gradebook", 108**2), ("sudoku-validity", 80**2)],
)
def test_the_pinned_order_does_not_cost_footprint(slug: str, footprint: int) -> None:
    """The whole reason width is a *constraint* in the search and not a term: the lane
    order picks ``mem_pad``, which sets the memory lanes' length, which sets the CPU's
    width — and width is squared in the score, so a tick win that widens the machine is
    usually a loss. `gradebook` actually *gains* a column here (109 → 108).

    All three shed five more columns when ``ADAPTER_TAPE_GAP`` went 6 → 1, which is a
    corridor width and nothing to do with the lane order; the pinned numbers moved with
    it (95/113/83 → 90/108/80) but what this test asserts did not.
    """
    assert machine.build_for(slug).footprint == footprint


def test_a_pinned_tick_test_failing_does_not_mean_the_grid_is_wrong() -> None:
    """The trap that cost a wrong conclusion, written down so it cannot cost another.

    ``test_lm1_matmul`` pins each case's *exact* settle tick — "the recorded tick is
    enough, and one tick fewer is not" — so a **faster** grid fails it, and the failure
    looks identical to a wrong answer. `matmul` was removed from ``LANE_ORDER`` on that
    evidence and restored once the outputs were checked directly: correct on all seven
    public cases, on the reference engine, at every case's new lower tick.
    """
    assert machine.LANE_ORDER["matmul"] == ("MUL", "BRN", "SUB", "ADDI", "ST", "LD")
    grid = machine.build_for("matmul").rows
    result = optimize.verify(grid, "matmul", lm=Littleman())
    assert result.passed, "matmul's reordered grid must be right, not merely fast"
    assert all(c.passed for c in result.cases)


@pytest.mark.slow
def test_brackets_lane_order_survives_inputs_the_public_cases_miss() -> None:
    """The check `matmul` taught us to write, on the reference engine.

    Nine public cases is not much cover for a stack machine, and the reordered lanes
    are a *different grid* — same ISA, different geometry — so the risk is entirely in
    the hardware. These are random strings, biased short so plenty of them are
    unbalanced early, plus the boundary cases (empty, all openers, all closers).
    """
    rng = random.Random(20260726)
    codes = [[], [40], [41], [40, 40, 40], [41, 41, 41], [40, 91, 41, 93]]
    codes += [_random_case(rng, rng.randint(1, 24)) for _ in range(24)]

    cases = [
        {
            "name": f"stress {i}",
            "in": [str(len(c)), *[str(v) for v in c]],
            "out": [str(_expected(c))],
        }
        for i, c in enumerate(codes)
    ]
    problem = {"slug": "brackets", "scoring": "footprint-tick", "publicTestData": cases}

    # The *reference* engine, explicitly. `verify` defaults to the fast in-memory one,
    # and that is the validator which passed `matmul`'s broken reorder — so on the one
    # test whose job is to catch that class of bug, the default is the wrong choice.
    grid = machine.build_for("brackets").rows
    result = optimize.verify(grid, problem, lm=Littleman())
    assert result.passed, "the reordered brackets grid is wrong on non-public input"
