"""Tests for dead-line elimination (``mancompact.py``).

The move looks trivial and is not: deleting a line is geometrically free but can
change *semantics* two ways, and both are pinned here.

* A pipe's length **is** its capacity, so shortening one can deadlock a ring.
* ``s``/``r`` bind to the **nearest** pipe, so pulling a room's walls in can hand
  an op to a different pipe — with no other symptom at all.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.mancompact import (  # noqa: E402
    Cut,
    _affordable,
    _factor,
    _ordered,
    apply_cuts,
)

LM_MJS = REPO / "littleman" / "lm.mjs"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)


# ── the cut mechanics ────────────────────────────────────────────────────────
def test_cuts_are_applied_in_original_coordinates() -> None:
    """Every index refers to the source grid, so order of removal cannot matter.

    Applying them one at a time with re-indexing is the classic off-by-one here:
    dropping column 2 then column 5 removes the *original* 6.
    """
    rows = ["abcdef", "ghijkl", "mnopqr"]
    got = apply_cuts(rows, [Cut("col", 2), Cut("col", 5), Cut("row", 1)])
    assert got == ["abde", "mnpq"]


def test_a_cut_never_leaves_trailing_blanks() -> None:
    """Trailing whitespace is inert but would inflate the measured width."""
    rows = ["ab  ", "cd  "]
    assert apply_cuts(rows, [Cut("col", 0)]) == ["b", "d"]


def test_factor_is_the_squared_longer_side() -> None:
    assert _factor(["ab", "cd"]) == 4
    assert _factor(["abcd"]) == 16  # 4 wide, 1 tall -> max is 4
    assert _factor(["a", "b", "c"]) == 9


# ── capacity budgeting ───────────────────────────────────────────────────────
def test_a_cut_through_a_pipe_needs_declared_slack() -> None:
    """An undeclared pipe has a budget of zero, so it blocks the cut."""
    cut = Cut("col", 3, pipes=(4,))
    assert not _affordable(cut, {4: 0})
    assert _affordable(cut, {4: 1})


def test_slack_is_spent_not_reused() -> None:
    """Two cuts through one ring must not both spend the same slot.

    A ring with one surplus slot can afford exactly one cut; without draw-down
    both cuts look affordable and the second one deadlocks it.
    """
    budget = {4: 1, 5: 1}
    first = Cut("col", 3, pipes=(4, 5))
    assert _affordable(first, budget)
    for p in first.pipes:
        budget[p] -= 1
    assert not _affordable(Cut("col", 7, pipes=(4,)), budget)


def test_a_cut_touching_no_pipe_is_always_affordable() -> None:
    assert _affordable(Cut("row", 9), {})


# ── ordering ─────────────────────────────────────────────────────────────────
def test_the_dominant_axis_is_cut_first() -> None:
    """Only the longer side divides ``max(w,h)**2``.

    On a 10-wide, 4-tall grid a column cut shrinks the factor and a row cut does
    nothing, so columns must come first or the search wastes its budget.
    """
    rows = ["x" * 10] * 4
    cuts = [Cut("row", 1), Cut("row", 2), Cut("col", 3), Cut("col", 4)]
    order = _ordered(cuts, rows)
    assert order[0].axis == "col"


def test_ordering_interleaves_once_the_sides_are_equal() -> None:
    """A square stays square: after one column cut the row side dominates."""
    rows = ["x" * 5] * 5
    cuts = [Cut("col", 0), Cut("col", 1), Cut("row", 0), Cut("row", 1)]
    axes = [c.axis for c in _ordered(cuts, rows)]
    assert axes == ["col", "row", "col", "row"]


def test_ordering_keeps_every_cut() -> None:
    cuts = [Cut("row", 1), Cut("col", 3), Cut("col", 4)]
    assert sorted(_ordered(cuts, ["xxxxx"] * 5), key=str) == sorted(cuts, key=str)


# ── end to end on a real grid ────────────────────────────────────────────────
@node_required
@pytest.mark.slow  # the greedy search spends several engine parses
def test_compaction_of_a_real_grid_never_breaks_a_binding() -> None:
    """Whatever it decides to cut, the bindings must be identical afterwards.

    This is the property that matters: the search is allowed to find nothing (a
    generated grid is often already tight), but it is never allowed to hand an op
    to a different pipe.
    """
    from randomfun2026solvers.mancompact import _binding_signature, compact
    from randomfun2026solvers.manparse import parse_program

    grid = REPO / "tasks" / "solutions" / "sort-numbers_ring.man"
    before = _binding_signature(parse_program(grid))
    res = compact(grid)
    assert res.after[0] <= res.before[0] and res.after[1] <= res.before[1]
    if res.cuts:
        after = _binding_signature(parse_program("\n".join(res.rows) + "\n"))
        assert after == before
