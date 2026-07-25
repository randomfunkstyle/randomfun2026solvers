"""Tests for AST structural moves (``manmoves.py``).

Most of this file is about :func:`reglyph`, because redrawing a pipe from its path
is where the whole approach earns its keep *and* where it went wrong twice. Both
mistakes produced a grid that still loaded and still analysed as having pipes, so
neither was visible in the ASCII — only in the case results.
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
from randomfun2026solvers.manmoves import (  # noqa: E402
    MoveError,
    drop_row,
    reglyph,
    ring_capacity,
    try_drop,
)

E, W, N, S = (1, 0), (-1, 0), (0, -1), (0, 1)

LM_MJS = REPO / "littleman" / "lm.mjs"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)


# ── reglyph ──────────────────────────────────────────────────────────────────
def test_both_ends_are_arrowheads_never_straight_bodies() -> None:
    """A `-` on an end detaches the pipe from its room, silently.

    The first cell is where the engine picks the pipe up off the source wall and
    the last is where it hands the value over, so each must state a heading. Drawn
    as bodies, ``analyze`` reports src/dst of -1 — a pipe belonging to nobody —
    and the grid still loads.
    """
    path = [(1, 5), (2, 5), (3, 5), (4, 5)]
    assert reglyph(path, E, E) == [">", "-", "-", ">"]


def test_a_bend_terminus_shows_where_it_is_going_not_where_it_came_from() -> None:
    """The exit heading is not derivable from the path — the path stops there.

    Here the last cell is reached heading east but must hand the value over
    heading north. Inferring from the incoming heading writes ``>`` and the pipe
    runs along its own last leg instead of into the room.
    """
    path = [(6, 9), (7, 9), (8, 9)]
    assert reglyph(path, E, N) == [">", "-", "^"]
    assert reglyph(path, E, E) == [">", "-", ">"]  # straight terminus, for contrast


def test_a_bend_in_the_middle_shows_its_outgoing_heading() -> None:
    path = [(1, 1), (2, 1), (2, 2), (2, 3)]
    assert reglyph(path, E, S) == [">", "v", "|", "v"]


def test_straights_use_bodies_so_the_route_reads() -> None:
    vert = [(4, 1), (4, 2), (4, 3), (4, 4), (4, 5)]
    assert reglyph(vert, S, S) == ["v", "|", "|", "|", "v"]


def test_a_non_rectilinear_path_is_rejected() -> None:
    with pytest.raises(MoveError, match="heading"):
        reglyph([(0, 0), (1, 1)], E, E)


# ── drops ────────────────────────────────────────────────────────────────────
def _two_rooms_and_a_pipe() -> Ast:
    """A room at the top, a room lower down, one vertical pipe between them."""
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
        min_capacity=3,
    )
    return Ast(rooms=[top, bot], pipes=[pipe], source=[])


def test_a_drop_shortens_the_pipe_and_slides_what_is_below_it() -> None:
    ast = _two_rooms_and_a_pipe()
    rep = drop_row(ast, 5, capacity={(0,): 3})
    assert rep.pipes_shortened == {0: 1}
    assert ast.pipes[0].capacity == 4
    assert ast.pipes[0].glyphs == ["v", "|", "|", "v"]
    assert ast.rooms[1].y == 7, "the room below moved up by one"
    assert ast.rooms[0].y == 0, "the room above did not move"


def test_a_drop_is_refused_below_a_declared_minimum() -> None:
    """Length IS capacity, so this is the check that stops a ring deadlocking."""
    ast = _two_rooms_and_a_pipe()
    ast.pipes[0].min_capacity = 5
    with pytest.raises(MoveError, match="below its declared minimum"):
        drop_row(ast, 5)


def test_an_undeclared_pipe_blocks_the_drop() -> None:
    """Silence must never shorten a pipe."""
    ast = _two_rooms_and_a_pipe()
    ast.pipes[0].min_capacity = None
    with pytest.raises(MoveError, match="no declared capacity"):
        drop_row(ast, 5)


def test_a_group_minimum_is_checked_after_the_move() -> None:
    ast = _two_rooms_and_a_pipe()
    assert ring_capacity(ast, (0,)) == 5
    with pytest.raises(MoveError, match="against a need of"):
        drop_row(ast, 5, capacity={(0,): 5})


def test_a_live_glyph_refuses_the_drop() -> None:
    ast = _two_rooms_and_a_pipe()
    ast.rooms[0].h = 6  # stretch the room so row 3 is interior
    ast.rooms[0].children.append(Atom(id=9, x=1, y=3, rows=["M"]))
    with pytest.raises(MoveError, match="live glyph"):
        drop_row(ast, 3, capacity={(0,): 3})


def test_a_wall_refuses_the_drop() -> None:
    ast = _two_rooms_and_a_pipe()
    with pytest.raises(MoveError, match="wall"):
        drop_row(ast, 0, capacity={(0,): 3})


def test_try_drop_leaves_the_original_untouched_when_it_fails() -> None:
    """A refused move must not half-apply: the search depends on it."""
    ast = _two_rooms_and_a_pipe()
    before = (ast.pipes[0].capacity, ast.rooms[1].y, list(ast.pipes[0].glyphs))
    out, reason = try_drop(ast, "row", 0, capacity={(0,): 3})
    assert out is None and "wall" in reason
    assert (ast.pipes[0].capacity, ast.rooms[1].y, ast.pipes[0].glyphs) == before


def test_try_drop_returns_a_new_ast_and_does_not_mutate_the_old() -> None:
    ast = _two_rooms_and_a_pipe()
    out, rep = try_drop(ast, "row", 5, capacity={(0,): 3})
    assert out is not None and rep.pipes_shortened == {0: 1}
    assert out.pipes[0].capacity == 4
    assert ast.pipes[0].capacity == 5, "the original is untouched"


# ── the real grid ────────────────────────────────────────────────────────────
@node_required
def test_the_memory_drop_keeps_topology_and_every_binding() -> None:
    """The move that took memory from factor 1024 to 961.

    Row 25 is refused by ``mancompact`` because it holds a pipe arrowhead. On the
    AST the cell comes out and the glyphs are recomputed, and the result must have
    the same rooms, the same pipe connections, and the same bindings.
    """
    from randomfun2026solvers.manast import Refine, parse_ast, render, round_trip_ok
    from randomfun2026solvers.mancompact import _binding_signature
    from randomfun2026solvers.manparse import parse_program

    grid = REPO / "littleman" / "examples" / "memory2.man"
    prog = parse_program(grid, bind=False)
    ast = parse_ast(prog, refine=Refine.BLOCKS, capacity={0: 1, 1: 1, 2: 1, 3: 1})
    assert round_trip_ok(ast)
    assert ast.geometry_factor == 1024

    out, rep = try_drop(ast, "row", 25, capacity={(2, 3): 101})
    assert out is not None, rep
    assert out.geometry_factor == 961
    assert ring_capacity(out, (2, 3)) >= 101

    before = _binding_signature(parse_program(grid))
    after = _binding_signature(parse_program("\n".join(render(out)) + "\n"))
    assert after == before, "a shortened pipe must not re-bind any op"

    rebuilt = parse_program("\n".join(render(out)) + "\n")
    assert [(p.src, p.dst) for p in rebuilt.pipes] == [
        (p.src, p.dst) for p in parse_program(grid).pipes
    ], "every pipe must still connect the same two rooms"
