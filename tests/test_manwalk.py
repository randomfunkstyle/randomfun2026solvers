"""Tests for the walk tracer and the flow graph (``manwalk.py``).

The flow graph is what makes a sparse board readable, and its two derived sets
are only correct if direction is taken from *consecutive* positions of the same
runner — so that is what these pin, along with the stall accounting that stops a
blocked pipe from reading as hot code.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.manstruct import Kind  # noqa: E402
from randomfun2026solvers.manwalk import Step, Walk, flow_of  # noqa: E402


class FakeCell:
    """Minimal stand-in for a classified cell: flow_of only reads ``.kind``."""

    def __init__(self, kind: Kind) -> None:
        self.kind = kind


def walk_of(*steps: tuple[int, int, int, int]) -> Walk:
    """Build a Walk from (tick, runner, x, y) tuples."""
    w = Walk(ticks=max(s[0] for s in steps))
    for tick, runner, x, y in steps:
        s = Step(tick=tick, runner=runner, pos=(x, y))
        w.steps.append(s)
        w.first.setdefault(s.pos, tick)
        w.count[s.pos] += 1
        w.who.setdefault(s.pos, set()).add(runner)
    w.runners = len({s.runner for s in w.steps})
    return w


def test_direction_comes_from_consecutive_positions() -> None:
    f = flow_of(walk_of((0, 0, 1, 1), (1, 0, 2, 1), (2, 0, 2, 2)))
    assert f.edges[((1, 1), (2, 1), 0)] == 1
    assert f.edges[((2, 1), (2, 2), 0)] == 1
    assert f.out_dirs[(1, 1)] == {(1, 0)}  # east
    assert f.out_dirs[(2, 1)] == {(0, 1)}  # south
    assert f.in_dirs[(2, 2)] == {(0, 1)}


def test_a_blank_left_two_ways_is_a_crossing_not_a_fork() -> None:
    """Two lanes sharing one blank is not a branch, and calling it one is wrong.

    A blank is a nop in every heading, so an east-west lane and a north-south lane
    can pass through the same cell. That shows up as two exit headings, which is
    indistinguishable from a fork if you only look at the trace.
    """
    walk = walk_of(
        (0, 0, 5, 5), (1, 0, 6, 5),  # first pass: leaves east
        (2, 0, 5, 5), (3, 0, 5, 6),  # second pass: leaves south
    )
    cells = {(5, 5): FakeCell(Kind.FLOOR), (6, 5): FakeCell(Kind.FLOOR)}
    f = flow_of(walk, cells)
    assert f.out_dirs[(5, 5)] == {(1, 0), (0, 1)}
    assert (5, 5) in f.crossings
    assert (5, 5) not in f.forks, "a blank is never a branch"


def test_a_conditional_turn_is_a_fork_even_when_never_branched() -> None:
    """`X` forks on the main hand, so it is a fork by construction.

    Deriving forks from observed exits misses exactly this: a trace where the
    condition happened to go one way every time would report no fork at all, and
    an untaken arm is an untested branch — the thing most worth flagging.
    """
    walk = walk_of((0, 0, 3, 3), (1, 0, 4, 3))  # only ever leaves east
    f = flow_of(walk, {(3, 3): FakeCell(Kind.BRANCH)})
    assert (3, 3) in f.forks
    assert f.exercised((3, 3)) == {(1, 0)}
    assert (3, 3) in f.cold_forks, "one arm taken out of several is untested"
    assert (3, 3) not in f.crossings, "a fork is not also a crossing"


def test_a_fork_with_two_arms_taken_is_not_cold() -> None:
    walk = walk_of((0, 0, 3, 3), (1, 0, 4, 3), (2, 0, 3, 3), (3, 0, 3, 4))
    f = flow_of(walk, {(3, 3): FakeCell(Kind.BRANCH)})
    assert f.exercised((3, 3)) == {(1, 0), (0, 1)}
    assert (3, 3) not in f.cold_forks


def test_forks_need_the_grid_so_no_cells_means_no_forks() -> None:
    """Without the lattice the fork set is empty rather than guessed at."""
    f = flow_of(walk_of((0, 0, 3, 3), (1, 0, 4, 3)))
    assert f.forks == set()


def test_a_cell_entered_two_ways_is_a_join() -> None:
    """Where paths merge, the corridor is shared and therefore load bearing.

    ``in_dirs`` holds the heading the man was *travelling* when he arrived, not
    the side he came from: stepping down from (2,4) into (2,5) is south, (0,+1).
    """
    f = flow_of(
        walk_of((0, 0, 1, 5), (1, 0, 2, 5), (2, 0, 2, 4), (3, 0, 2, 5))
    )
    assert (2, 5) in f.joins
    assert f.in_dirs[(2, 5)] == {(1, 0), (0, 1)}  # arrived east, then south


def test_standing_still_is_a_stall_not_a_move() -> None:
    """An `r` on an empty pipe is waiting; counting it as traffic misreads it."""
    f = flow_of(walk_of((0, 0, 3, 3), (1, 0, 3, 3), (2, 0, 3, 3), (3, 0, 4, 3)))
    assert f.stalls[(3, 3)] == 2
    assert f.edges[((3, 3), (4, 3), 0)] == 1
    assert not f.crossings and not f.forks


def test_runners_never_share_an_edge() -> None:
    """Two men in different rooms must not be joined into one impossible jump."""
    f = flow_of(walk_of((0, 0, 1, 1), (0, 1, 9, 9), (1, 0, 2, 1), (1, 1, 9, 8)))
    assert f.runners == {0, 1}
    assert f.edges[((1, 1), (2, 1), 0)] == 1
    assert f.edges[((9, 9), (9, 8), 1)] == 1
    # no edge bridges the two runners' positions
    assert all(a != (1, 1) or b != (9, 9) for a, b, _ in f.edges)


def test_traffic_is_counted_per_traversal() -> None:
    f = flow_of(
        walk_of((0, 0, 1, 1), (1, 0, 2, 1), (2, 0, 1, 1), (3, 0, 2, 1))
    )
    assert f.edges[((1, 1), (2, 1), 0)] == 2
