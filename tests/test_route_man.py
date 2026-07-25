"""Tests for routing the little MAN (``manroute.route_man``).

A pipe and a man are different problems, and conflating them is why hand-rewiring
kept breaking cases. A pipe owns every cell it occupies. The man owns only his
**bends**: a straight run over blank floor is shared freely, because a blank is a
nop in every heading, so two lanes may cross there. A turn glyph cannot be shared —
it forces a heading on everyone entering it, silently re-steering the other lane.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.manroute import route_man  # noqa: E402

E, W, N, S = (1, 0), (-1, 0), (0, -1), (0, 1)


def test_a_straight_walk_needs_no_turn_glyphs() -> None:
    p = route_man((1, 1), E, (5, 1), E, code=set())
    assert p is not None
    assert p.cells == [(1, 1), (2, 1), (3, 1), (4, 1), (5, 1)]
    assert p.turns == {}
    assert p.ticks == 5


def test_a_bend_costs_exactly_one_turn_glyph_placed_where_he_turns() -> None:
    """The glyph goes on the cell he is standing on when the heading changes.

    He executes his current cell and then steps, so a `v` one cell late leaves him
    walking east into whatever is there — which is how a corner becomes a crash.
    """
    p = route_man((1, 1), E, (1, 4), S, code=set())
    assert p is not None
    assert p.turns == {(1, 1): "v"}, p.turns
    assert (1, 1) not in p.blanks


def test_code_is_never_crossed_because_crossing_would_execute_it() -> None:
    """The one absolute: an op cell is transparent to movement but still runs."""
    wall = {(3, y) for y in range(0, 12)}
    assert route_man((1, 5), E, (5, 5), E, code=wall, bound_y=11) is None
    # with a gap it routes around
    wall.discard((3, 8))
    p = route_man((1, 5), E, (5, 5), E, code=wall, bound_y=11)
    assert p is not None and (3, 8) in p.cells


def test_a_reserved_blank_may_be_crossed_by_another_lane() -> None:
    """Two lanes sharing floor is legal and is what makes dense packing possible."""
    other = {(3, y) for y in range(0, 12)}  # a vertical lane's transit blanks
    p = route_man((1, 5), E, (5, 5), E, code=set(), blanks=other)
    assert p is not None
    assert p.cells == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]
    assert p.turns == {}


def test_a_bend_may_not_land_on_a_shared_blank() -> None:
    """Putting a turn glyph on another lane's transit re-steers that lane."""
    shared = {(3, 1)}
    p = route_man((1, 1), E, (3, 5), S, code=set(), blanks=shared)
    assert p is not None
    assert (3, 1) not in p.turns, "the bend must move off the shared cell"


def test_an_existing_turn_is_crossable_only_along_its_own_heading() -> None:
    """`>` entered heading east is a genuine merge; entered heading south it is not."""
    placed = {(3, 5): ">"}
    east = route_man((1, 5), E, (5, 5), E, code=set(), turns=placed)
    assert east is not None and (3, 5) in east.cells

    boxed = {(3, y): ">" for y in range(0, 12)}
    assert route_man((3, 0), S, (3, 11), S, code=set(), turns=boxed, bound_y=11) is None


def test_bends_are_minimised_before_length() -> None:
    """A bend costs an exclusive cell; extra straight cells cost only ticks."""
    p = route_man((1, 1), E, (9, 1), E, code=set())
    assert p is not None and p.turns == {}, "a clear straight line takes no bends"

    detour = route_man((1, 1), E, (9, 1), E, code={(5, 1)})
    assert detour is not None
    # Four, not two: step off the row, run along it, step back on, and resume east.
    # Arriving at the goal with the demanded heading is itself one of the bends.
    assert len(detour.turns) == 4, detour.turns


def test_arriving_with_the_wrong_heading_is_a_different_problem() -> None:
    """The exit heading is part of the goal: a block's port demands one."""
    east = route_man((1, 1), E, (5, 1), E, code=set())
    south = route_man((1, 1), E, (5, 1), S, code=set())
    assert east is not None and south is not None
    assert east.turns == {} and len(south.turns) >= 1
