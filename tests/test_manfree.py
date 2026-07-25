"""Tests for freedom queries (``manfree.py``).

Two things are worth testing hard here, because both were wrong first.

The **circuit tracer** must find individual loops, not the recurrent region. A
strongly connected component of a real worker is the whole reachable body, and its
"latent" columns are the union over every loop in it — which is not a squash
candidate for any of them. The check that catches this is a grid with two loops of
different widths in one room: SCCs report one blob, minimal cycles report two.

The **tick model** is asserted against a hand-countable loop, because everything
the squash figures claim rests on "one cell of the lap is one tick".
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

from randomfun2026solvers.manast import (  # noqa: E402
    Ast,
    Atom,
    PipeNode,
    Refine,
    RoomNode,
    parse_ast,
    round_trip_ok,
)
from randomfun2026solvers.manfree import (  # noqa: E402
    Verdict,
    circuits,
    line_report,
    report,
    scan,
    squash_report,
)
from randomfun2026solvers.manmoves import try_squash  # noqa: E402

E, W, N, S = (1, 0), (-1, 0), (0, -1), (0, 1)

LM_MJS = REPO / "littleman" / "lm.mjs"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)

PLOTTER = (
    REPO.parent.parent / "worktrees" / "sort-numbers-ring" / "solutions" / "plotter"
    / "000000053693850_plotter.man"
)
plotter_required = pytest.mark.skipif(
    not PLOTTER.exists(), reason="plotter solution not present in this checkout"
)


def _room(rows: list[str], *, x: int = 0, y: int = 0, kind: str = "compute") -> Ast:
    """One room whose interior is `rows`, as an AST that renders exactly."""
    w = max(len(r) for r in rows)
    padded = [r.ljust(w) for r in rows]
    room = RoomNode(
        id=0,
        x=x,
        y=y,
        kind=kind,
        w=w,
        h=len(padded),
        children=[Atom(id=0, x=x + 1, y=y + 1, rows=padded)],
    )
    ast = Ast(rooms=[room])
    ast.source = [""]  # only render() is exercised; no round-trip claim made
    return ast


# ── is this line free? ───────────────────────────────────────────────────────
def test_a_blank_interior_row_is_free_and_shrinks_the_room():
    ast = _room(["ab", "  ", "cd"])
    rep = line_report(ast, "row", 2)
    assert rep.verdict is Verdict.FREE
    assert rep.removable
    assert rep.rooms_shrunk == [0]
    # the two side walls it crossed give way; nothing blocks
    assert rep.blockers == []


def test_a_row_of_glyphs_is_blocked_and_names_what_blocked_it():
    ast = _room(["ab", "cd"])
    rep = line_report(ast, "row", 1)
    assert rep.verdict is Verdict.BLOCKED
    assert not rep.removable
    blockers = [o for o in rep.blockers if "atom" in o.node]
    assert blockers, rep.occupants
    assert "ab" in blockers[0].why or "a" in blockers[0].why


def test_a_rooms_own_wall_is_refused_even_though_nothing_is_on_it():
    """*Free* and *removable* are different questions, which is why both exist."""
    ast = _room(["ab", "cd"])
    rep = line_report(ast, "row", 0)  # the top wall
    assert rep.verdict is Verdict.BLOCKED
    assert any(o.role == "own wall" for o in rep.occupants)


def test_a_pinned_display_blocks_its_own_blank_interior():
    ast = _room(["  ", "  "], kind="display")
    ast.rooms[0].pinned = True
    ast.rooms[0].note = "resolution"
    rep = line_report(ast, "row", 1)
    assert rep.verdict is Verdict.BLOCKED
    assert any("pinned" in o.role for o in rep.occupants)


def test_the_factor_delta_is_reported_because_the_short_axis_pays_nothing():
    # box 5x7: max is the height, so losing a *column* cannot lower max(w,h)**2.
    ast = _room(["a b", "   ", "c d", "   ", "e f"])
    col = line_report(ast, "col", 2)
    assert col.verdict is Verdict.FREE
    assert col.gain == 0, "a column cut on the short axis must report zero gain"
    row = line_report(ast, "row", 2)
    assert row.gain > 0


def test_scan_covers_every_line_and_sorts_paying_ones_first():
    ast = _room(["a b", "   ", "c d", "   ", "e f"])
    f = scan(ast)
    w, h = ast.bbox
    assert len(f.rows) == h and len(f.cols) == w
    paying = f.paying_lines()
    assert paying and all(r.gain > 0 for r in paying)
    assert paying == sorted(paying, key=lambda r: -r.gain)


def test_a_pipe_only_line_is_flagged_for_re_routing_not_for_cutting():
    """No instruction on the line, so it is pure geometry — a router's target."""
    pipe = PipeNode(
        id=0,
        x=0,
        y=0,
        path=[(x, 0) for x in range(5)],
        glyphs=[">", "-", "-", "-", "v"],
        pinned=True,
    )
    ast = Ast(pipes=[pipe])
    rep = line_report(ast, "row", 0)
    assert rep.pipe_only
    assert rep.pipes_here == ["pipe0"]
    assert not rep.removable  # deleting a whole leg is a re-route, not a cut


def test_a_row_shared_by_pipe_and_code_is_not_a_re_route_candidate():
    ast = _room(["ab"])
    ast.pipes.append(
        PipeNode(id=0, x=0, y=1, path=[(5, 1), (6, 1)], glyphs=[">", ">"], pinned=True)
    )
    rep = line_report(ast, "row", 1)
    assert not rep.pipe_only


def test_an_unknown_axis_is_rejected_rather_than_guessed():
    with pytest.raises(ValueError):
        line_report(_room(["ab"]), "diagonal", 0)


# ── circuits ─────────────────────────────────────────────────────────────────
def test_a_rectangular_loop_costs_one_tick_per_cell():
    """The whole squash model rests on this, so it is asserted, not assumed.

    A 4x2 circuit: east along the top, south, west along the bottom, north. Eight
    cells, eight moves, so eight ticks — which is what the engine charges a
    ``counted_loop`` whose body is two glyphs.
    """
    ast = _room([">  v", "^  <"])
    (c,) = [c for c in circuits(ast) if c.ticks_per_lap == 8]
    assert c.ticks_per_lap == 8
    assert len(set(c.cells)) == 8


def test_a_wide_idle_loop_reports_its_latent_columns_and_counts_the_saving():
    # interior 8 wide, so the ring runs grid columns 1..8 and 2..7 are idle
    ast = _room([">      v", "^      <"])
    (c,) = [c for c in circuits(ast) if c.ticks_per_lap == 16]
    assert list(c.latent_cols) == [2, 3, 4, 5, 6, 7]
    assert c.squash == 12, "6 idle columns crossed twice = 12 ticks a lap"
    assert c.floor == 4, "what is left is the 2x2 ring: four ticks"


def test_a_loop_whose_columns_all_execute_has_nothing_to_squash():
    ast = _room([">rsv", "^sr<"])
    tight = [c for c in circuits(ast) if c.ticks_per_lap == 8]
    assert tight and all(c.squash == 0 for c in tight)


def test_two_loops_in_one_room_are_reported_separately():
    """The check that kills the SCC approach: one component, two different laps."""
    rows = [
        ">    v",
        "^    <",
        ">  v  ",
        "^  <  ",
    ]
    ast = _room(rows)
    laps = sorted({c.ticks_per_lap for c in circuits(ast)})
    assert 12 in laps and 8 in laps, laps


def test_a_branch_turns_only_the_ways_spec_allows():
    """``a`` turns counter-clockwise or goes straight — never clockwise.

    An over-approximating tracer ("a branch could go anywhere") invents laps the
    program cannot run, and every squash figure derived from them is fiction.
    """
    from randomfun2026solvers.manfree import _cell_map, _successors

    ast = _room(["  a  ", "     "])
    cells = _cell_map(ast)
    # standing west of the `a`, heading east: ccw of E is N, straight is E
    got = {h for _c, h in _successors(cells, ((2, 1), "E"))}
    assert got == {"N", "E"}, got


def test_a_halt_ends_the_walk_so_it_is_never_part_of_a_lap():
    ast = _room([">  H", "^  <"])
    assert all("H" not in str(c) for c in circuits(ast))
    # the ring is broken by the halt, so there is no 8-tick lap
    assert not [c for c in circuits(ast) if c.ticks_per_lap == 8]


# ── blocks ───────────────────────────────────────────────────────────────────
def test_squash_report_finds_a_rooms_free_interior_lines():
    ast = _room(["ab ", "   ", "cd "])
    (block,) = squash_report(ast)
    assert block.interior == (3, 3)
    assert block.free_cols == [3]  # absolute grid column of the blank interior col
    assert block.free_rows == [2]
    assert block.shrink == (1, 1)


def test_every_room_line_carries_a_verdict_that_was_actually_tried():
    """The point of the exercise: *can* it be removed, not *does it look empty*."""
    ast = _room(["ab ", "   ", "cd "])
    (block,) = squash_report(ast)
    assert {(ln.axis, ln.index) for ln in block.lines} == {("col", 3), ("row", 2)}
    assert all(ln.ok for ln in block.lines)


def test_squashing_a_room_narrows_it_without_moving_the_grid():
    ast = _room(["ab ", "cd "])
    before = ast.bbox
    trial, rep = try_squash(ast, 0, "col", 3)
    assert trial is not None, rep
    room = trial.rooms[0]
    assert (room.w, room.h) == (2, 2)
    # the box does not shrink: the freed column is blank space behind the new wall
    assert trial.bbox[1] == before[1]


def test_a_squash_grows_the_pipe_on_the_wall_that_moved():
    """The wall came in by one, so the pipe needs one more cell to still touch it."""
    ast = _room(["ab "])  # box 5x3, east wall at x=4
    pipe = PipeNode(
        id=0,
        x=5,
        y=1,
        path=[(7, 1), (6, 1), (5, 1)],
        glyphs=["<", "-", "<"],
        src=-1,
        dst=0,
        entry_dir=W,
        exit_dir=W,
        min_capacity=2,
        pinned=False,
    )
    ast.pipes.append(pipe)
    trial, rep = try_squash(ast, 0, "col", 3)
    assert trial is not None, rep
    grown = trial.pipes[0]
    assert len(grown.path) == 4, "the pipe must reach the wall's new position"
    assert grown.path[-1] == (4, 1)
    assert grown.glyphs[-1] == "<", "the terminal still hands the value west, into the wall"


def test_a_squash_is_refused_when_it_would_slide_a_pipes_attach_cell():
    """Moving where a pipe lands is a re-route, and it is refused by name.

    This is the case that keeps ``plotter``'s worker from giving up column 46: a
    pipe attaches to its south wall east of that column, so narrowing the room
    moves the cell that pipe is aiming at.
    """
    ast = _room(["a  ", "b  "])  # box 5x4, south wall row 3
    ast.pipes.append(
        PipeNode(
            id=0,
            x=3,
            y=4,
            path=[(3, 6), (3, 5), (3, 4)],
            glyphs=["^", "|", "^"],
            src=-1,
            dst=0,
            entry_dir=N,
            exit_dir=N,
            min_capacity=2,
            pinned=False,
        )
    )
    trial, why = try_squash(ast, 0, "col", 2)
    assert trial is None
    assert "S wall" in str(why) and "re-route" in str(why)


def test_a_pinned_room_cannot_be_squashed_either():
    ast = _room(["   ", "   "], kind="display")
    ast.rooms[0].pinned = True
    ast.rooms[0].note = "resolution"
    trial, why = try_squash(ast, 0, "col", 2)
    assert trial is None
    assert "pinned" in str(why)


def test_a_squash_refuses_a_line_that_is_not_interior():
    ast = _room(["ab"])
    trial, why = try_squash(ast, 0, "col", 0)  # the west wall
    assert trial is None
    assert "not interior" in str(why)


def test_squash_report_refuses_to_shrink_a_pinned_display():
    ast = _room(["   ", "   "], kind="display")
    ast.rooms[0].pinned = True
    (block,) = squash_report(ast)
    assert block.pinned
    assert block.shrink == (0, 0), "a pinned block offers no shrink at all"


def test_a_pipe_crossing_a_room_holds_its_column_even_though_no_glyph_does():
    ast = _room(["a  ", "   "])
    ast.pipes.append(
        PipeNode(id=0, x=2, y=1, path=[(2, 1), (2, 2)], glyphs=["v", "v"], pinned=True)
    )
    (block,) = squash_report(ast)
    assert 2 not in block.free_cols


# ── the real grid ────────────────────────────────────────────────────────────
@node_required
@plotter_required
def test_the_plotter_round_trips_including_its_display_panel():
    """The bug this module found: a display was dropped and the gate said OK.

    ``Program.to_grid()`` re-renders from the rooms and pipes the analyser
    reported, and it reports a display as bare geometry rather than as a room — so
    the panel was missing from *both* sides of the round-trip comparison. 26 rows
    of ``plotter`` were invisible to every freedom query.
    """
    ast = parse_ast(PLOTTER, refine=Refine.BLOCKS)
    assert round_trip_ok(ast)
    displays = [r for r in ast.rooms if r.kind == "display"]
    assert len(displays) == 1
    assert displays[0].size == (34, 26)
    assert displays[0].pinned


@node_required
@plotter_required
def test_the_painter_loop_costs_the_14_ticks_its_author_documented():
    """An independent check on the tracer: the figure was written down elsewhere."""
    ast = parse_ast(PLOTTER, refine=Refine.BLOCKS)
    painter = next(r for r in ast.rooms if (r.w, r.h) == (3, 6))
    laps = [c.ticks_per_lap for c in circuits(ast) if c.room == painter.id]
    assert 14 in laps, laps


@node_required
@plotter_required
def test_a_row_feeding_a_display_wall_is_reported_forced_not_spare():
    """Row 0 holds one pipe and no instruction, and still cannot go.

    A pipe reaches a wall only from the line immediately outside it, and row 0 is
    the only row outside the display's top wall — its ADDR port. Calling that row
    slack because nothing executes on it is how a compactor promises a win it
    cannot deliver.
    """
    ast = parse_ast(PLOTTER, refine=Refine.BLOCKS)
    text = report(ast)
    body = text.split("── re-route candidates")[1].split("── nearest")[0]
    row0 = body.split("row 27")[0]
    assert "FORCED" in row0
    assert "display" in row0
    # so the honest headline is one row, not two
    assert "64 -> 63" in body


@node_required
@plotter_required
def test_the_plotter_has_no_free_row_so_the_report_says_squashing_is_the_lever():
    ast = parse_ast(PLOTTER, refine=Refine.BLOCKS)
    f = scan(ast)
    assert f.removable_rows() == []
    assert f.paying_lines() == [], "a column cut cannot help a grid taller than wide"
    text = report(ast)
    assert "Squashing is the only lever" in text
    # rows 0 and 63 carry one pipe each and nothing else
    solo = [r for r in f.pipe_only_lines() if r.axis == "row" and len(r.pipes_here) == 1]
    assert {r.index for r in solo} >= {0, 63}
