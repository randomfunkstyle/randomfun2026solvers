"""``LANE_ORDER``: which lane sits on which row is a tick cost, and a correctness risk.

Two halves, and the second is the one that earned its place. The row a lane sits on
sets its return walk, so weighting lanes by how often their opcode runs and searching
that is worth a few percent for free (§7.6). But the search's filter was "passes every
public case on the reference engine", and for ``matmul`` that filter *passed a grid
that computes the wrong product* on matrices the public data never reaches. So the
orders here are pinned, and any program whose public data is thin gets exercised past
it — that is what `test_brackets_lane_order_survives_inputs_the_public_cases_miss`
is for.
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
    [("brackets", 95**2), ("gradebook", 113**2), ("sudoku-validity", 83**2)],
)
def test_the_pinned_order_does_not_cost_footprint(slug: str, footprint: int) -> None:
    """The whole reason width is a *constraint* in the search and not a term: the lane
    order picks ``mem_pad``, which sets the memory lanes' length, which sets the CPU's
    width — and width is squared in the score, so a tick win that widens the machine is
    usually a loss. `gradebook` actually *gains* a column here (114 → 113).
    """
    assert machine.build_for(slug).footprint == footprint


def test_matmul_keeps_the_default_order_because_its_public_data_under_covers_it() -> None:
    """Pinned as a warning, not as a preference.

    The search's `matmul` candidate was 1.2% better and passed 7/7 public cases on the
    reference engine, then failed `test_lm1_matmul`'s stress matrices on the same
    engine. `matmul` is the one program on the *long* return path (`_LONG_RETURN`, for
    the STREAM wiring), whose drop-column rule the search's model does not describe.
    """
    assert "matmul" not in machine.LANE_ORDER


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
