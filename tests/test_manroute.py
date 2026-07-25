"""Tests for querying and rerouting the AST (``manroute.py``).

The parity rule is the headline. Any rectilinear path between two fixed cells has
length ``|dx| + |dy| + 2k`` — a detour must come back, so it adds cells in pairs.
A pipe pinned between two room walls therefore cannot be made *one* cell longer.
When a ring needs an odd capacity and the geometry offers even, the honest answer
is "move an endpoint", not "search harder", and a router that merely failed to
find a path would look like a bug instead of a fact.
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

from randomfun2026solvers.manast import Ast, Atom, PipeNode, RoomNode  # noqa: E402
from randomfun2026solvers.manroute import (  # noqa: E402
    Occupancy,
    Plan,
    Verdict,
    pad_to,
    shortest_path,
)

S, N = (0, 1), (0, -1)

LM_MJS = REPO / "littleman" / "lm.mjs"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)


def empty(w: int = 20, h: int = 20) -> Occupancy:
    return Occupancy(owner={})


# ── routing primitives ───────────────────────────────────────────────────────
def test_the_shortest_path_is_the_manhattan_distance() -> None:
    path = shortest_path((1, 1), (4, 3), empty())
    assert path is not None
    assert path[0] == (1, 1) and path[-1] == (4, 3)
    assert len(path) == 1 + abs(4 - 1) + abs(3 - 1)  # inclusive of both ends


def test_a_blocked_route_has_no_path() -> None:
    """The barrier has to *enclose*, because the plane is open.

    A single column of wall is not a barrier — the router will simply go around
    the end of it, which is correct behaviour and was my test being wrong rather
    than the code. So box the start in on all four sides.
    """
    occ = Occupancy(
        owner={
            c: "wall"
            for c in ((1, 0), (0, 1), (2, 1), (1, 2))  # the four neighbours of (1,1)
        }
    )
    assert shortest_path((1, 1), (6, 6), occ) is None
    # and with one side open it is routable again
    open_side = Occupancy(owner={c: "wall" for c in ((1, 0), (0, 1), (1, 2))})
    assert shortest_path((1, 1), (6, 6), open_side) is not None


def test_padding_adds_cells_only_in_pairs() -> None:
    """A detour leaves the route and rejoins it, so it costs two cells."""
    base = shortest_path((1, 1), (6, 1), empty())
    assert base is not None and len(base) == 6
    assert pad_to(base, 8, empty()) is not None
    assert len(pad_to(base, 8, empty())) == 8
    assert pad_to(base, 7, empty()) is None, "odd delta is unreachable"
    assert pad_to(base, 5, empty()) is None, "cannot pad shorter"


def test_a_padded_path_stays_contiguous_and_free() -> None:
    base = shortest_path((1, 1), (6, 1), empty())
    out = pad_to(base, 12, empty())
    assert out is not None and len(out) == 12
    assert len(set(out)) == 12, "no cell used twice"
    for a, b in zip(out, out[1:], strict=False):
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1, "steps stay adjacent"


# ── verdicts ─────────────────────────────────────────────────────────────────
def test_a_verdict_is_falsey_when_it_refuses_and_carries_a_reason() -> None:
    assert not Verdict(False, "because")
    assert Verdict(True)
    v = Verdict(True, "moved", factor_before=1024, factor_after=961)
    assert v.gain == pytest.approx(1024 / 961)
    assert "1,024" in str(v)


# ── queries on a small AST ───────────────────────────────────────────────────
def _two_rooms() -> Ast:
    top = RoomNode(id=0, x=0, y=0, w=1, h=1, children=[Atom(id=0, x=1, y=1, rows=["s"])])
    bot = RoomNode(id=1, x=0, y=8, w=1, h=1, children=[Atom(id=1, x=1, y=9, rows=["r"])])
    pipe = PipeNode(
        id=0,
        x=1,
        y=3,
        path=[(1, 3), (1, 4), (1, 5), (1, 6), (1, 7)],
        glyphs=["v", "|", "|", "|", "v"],
        src=0,
        dst=1,
        entry_dir=S,
        exit_dir=S,
        min_capacity=5,
    )
    return Ast(rooms=[top, bot], pipes=[pipe], source=[])


def test_an_undeclared_pipe_cannot_be_rerouted() -> None:
    ast = _two_rooms()
    ast.pipes[0].min_capacity = None
    assert not Plan(ast).can_reroute(0)
    assert "no declared capacity" in Plan(ast).can_reroute(0).reason


def test_parity_is_reported_as_a_reason_not_as_a_failure_to_route() -> None:
    """The distinguishing test: an even-length corridor cannot be made odd."""
    plan = Plan(_two_rooms())
    assert plan.can_reroute(0, min_capacity=7)  # 5 + 2
    odd = plan.can_reroute(0, min_capacity=8)  # 5 + 3
    assert not odd
    assert "parity" in odd.reason


def test_a_capacity_below_the_shortest_route_is_already_satisfied() -> None:
    v = Plan(_two_rooms()).can_reroute(0, min_capacity=3)
    assert v and "already above" in v.reason


def test_a_refused_move_leaves_the_tree_untouched() -> None:
    """Transactional: a search can try anything without corrupting the AST."""
    ast = _two_rooms()
    plan = Plan(ast)
    before = (ast.rooms[1].y, list(ast.pipes[0].path))
    v = plan.move_room(1, 0, -6)  # would land on top of the pipe/room above
    assert not v
    assert (plan.ast.rooms[1].y, plan.ast.pipes[0].path) == before


def test_moving_a_room_reroutes_its_pipe_and_keeps_capacity() -> None:
    plan = Plan(_two_rooms())
    v = plan.move_room(1, 0, 1)
    assert v, v.reason
    pipe = plan.ast.pipes[0]
    assert pipe.capacity >= pipe.min_capacity
    assert plan.ast.rooms[1].y == 9
    # the pipe still hands over into the room's top wall
    assert pipe.path[-1][1] + pipe.exit_dir[1] == plan.ast.rooms[1].y


def test_a_pinned_room_refuses_to_move() -> None:
    ast = _two_rooms()
    ast.rooms[1].pinned = True
    ast.rooms[1].note = "display resolution"
    v = Plan(ast).move_room(1, 0, 1)
    assert not v and "pinned" in v.reason


# ── the real grid ────────────────────────────────────────────────────────────
@node_required
def test_the_router_finds_the_memory_win_by_itself() -> None:
    """Sliding the relay up one is worth factor 1024 -> 961, and the query says so.

    This is the same win found by hand as ``drop_row(25)``; here it falls out of
    asking "can this room move?", which is the point of making the AST queryable.
    """
    from randomfun2026solvers.manast import Refine, parse_ast
    from randomfun2026solvers.manparse import parse_program

    prog = parse_program(REPO / "littleman" / "examples" / "memory2.man", bind=False)
    ast = parse_ast(prog, refine=Refine.BLOCKS, capacity={0: 1, 1: 1, 2: 1, 3: 1})
    plan = Plan(ast)

    up = plan.can_move_room(3, 0, -1)
    assert up, up.reason
    assert (up.factor_before, up.factor_after) == (1024, 961)

    # and the tape ring's parity is reported rather than silently unroutable
    odd = plan.can_reroute(2, min_capacity=101)
    assert not odd and "parity" in odd.reason
    assert plan.can_reroute(2, min_capacity=102)
