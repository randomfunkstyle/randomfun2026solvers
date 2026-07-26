"""Tests for the mutable grid AST (``manast.py``) and the pipe-packing facts.

Round-tripping is the gate every compaction move sits behind: if the AST cannot
rebuild its own input byte for byte, it has misunderstood the grid and no rewrite
built on it can be trusted.

The two ``analyze``-backed tests at the bottom pin *engine behaviour* the reflow
router depends on. They are measurements, not assumptions — the whole reason a
299-cell ring can become a ~110-cell one is that the engine tolerates a fully
dense serpentine and does not merge adjacent anti-parallel lanes.
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
    Joint,
    PaintError,
    Refine,
    Run,
    parse_ast,
    render,
    round_trip_ok,
)

LM_MJS = REPO / "littleman" / "lm.mjs"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)


# ── the two flags ────────────────────────────────────────────────────────────
def test_an_atom_is_rigid_but_still_mobile_by_default() -> None:
    """ "Do not touch this 2x2" is two claims, and only one is usually meant.

    An atom's *bytes* are sacred (``rigid_content``); its *address* is not. Being
    able to move a block we refuse to rewrite is most of what compaction does.
    """
    a = Atom(id=0, x=5, y=5, rows=["ab", "cd"])
    assert a.rigid_content and not a.pinned
    assert a.size == (2, 2)
    a.translate(-3, 2)
    assert (a.x, a.y) == (2, 7)
    assert a.paint() == {(2, 7): "a", (3, 7): "b", (2, 8): "c", (3, 8): "d"}


def test_a_pinned_node_refuses_to_move() -> None:
    """The real "do not touch at all": position itself is load bearing."""
    a = Atom(id=0, x=1, y=1, rows=["xy"], pinned=True, note="nearest-pipe tie-break")
    with pytest.raises(PaintError, match="pinned"):
        a.translate(1, 0)
    assert (a.x, a.y) == (1, 1)


def test_an_atom_keeps_its_blanks_because_shape_is_part_of_the_contract() -> None:
    """A blank inside a gadget may be a corridor the man walks; we do not guess."""
    a = Atom(id=0, x=0, y=0, rows=[">d", "m ", " s", "^<"])
    assert a.size == (2, 4)
    assert a.blank_rows() == []
    assert a.blank_cols() == []
    b = Atom(id=1, x=0, y=0, rows=["a b", "   ", "c d"])
    assert b.blank_rows() == [1]
    assert b.blank_cols() == [1]


# ── render ───────────────────────────────────────────────────────────────────
def test_render_refuses_to_let_two_nodes_disagree_on_a_cell() -> None:
    """Silent overwrite is how a compactor would produce a wrong-but-loading grid."""
    ast = Ast(
        rooms=[],
        pipes=[],
        strays=[Atom(id=0, x=2, y=0, rows=["M"]), Atom(id=1, x=2, y=0, rows=["W"])],
        source=[],
    )
    with pytest.raises(PaintError, match=r"cell \(2, 0\)"):
        render(ast)


def test_a_run_lays_glyphs_along_its_heading() -> None:
    east = Run(id=0, x=1, y=1, glyphs="1Ns", heading="E")
    south = Run(id=1, x=1, y=1, glyphs="1Ns", heading="S")
    assert east.size == (3, 1) and south.size == (1, 3)
    assert set(east.paint()) == {(1, 1), (2, 1), (3, 1)}
    assert set(south.paint()) == {(1, 1), (1, 2), (1, 3)}


def test_a_joint_is_one_cell() -> None:
    j = Joint(id=0, x=4, y=7, glyph="^")
    assert j.size == (1, 1) and j.paint() == {(4, 7): "^"}


# ── round-trip: the gate ─────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def program():
    """One parse shared by both levels: parsing costs a node process per pipe op."""
    from randomfun2026solvers.manparse import parse_program

    return parse_program(REPO / "tasks" / "solutions" / "triangle_cpu.man")


@node_required
@pytest.mark.parametrize("refine", [Refine.ROOMS, Refine.BLOCKS])
def test_the_ast_rebuilds_its_source_byte_for_byte(program, refine) -> None:
    """Both refinement levels are lossless; refining only changes granularity."""
    from randomfun2026solvers.manast import diff_source

    ast = parse_ast(program, refine=refine)
    assert round_trip_ok(ast), diff_source(ast)[:3]
    assert not ast.strays, "the engine should account for every live cell"


@node_required
def test_refining_splits_an_interior_without_changing_the_render(program) -> None:
    """The safety property that makes refinement free: same bytes, finer bodies.

    Child *count* is not the invariant — a room whose live glyphs form a single
    clump refines to one body, so a simple grid can have the same count at both
    levels. What always holds is that no unrefined interior survives, and that
    every body is now sized to its own content rather than to the whole room.
    """
    coarse = parse_ast(program, refine=Refine.ROOMS)
    fine = parse_ast(program, refine=Refine.BLOCKS)
    assert render(coarse) == render(fine)

    assert all("unrefined" in c.note for r in coarse.rooms for c in r.children)
    assert not any("unrefined" in c.note for r in fine.rooms for c in r.children)

    # every refined body fits inside its room's interior, and at least one is
    # strictly smaller than the interior it came from
    shrunk = False
    for room in fine.rooms:
        for child in room.children:
            cw, ch = child.size
            assert cw <= room.w and ch <= room.h, (child, room.w, room.h)
            if (cw, ch) != (room.w, room.h):
                shrunk = True
    assert shrunk, "refining should size bodies to their content"


@node_required
def test_an_undeclared_pipe_is_pinned(program) -> None:
    """Silence must never license shortening a ring."""
    ast = parse_ast(program)
    assert ast.pipes and all(p.pinned for p in ast.pipes)
    declared = parse_ast(program, capacity={0: 1})
    assert not declared.pipes[0].pinned
    assert declared.pipes[0].min_capacity == 1


# ── engine facts the reflow router is built on ───────────────────────────────
def _serpentine(width: int, height: int):
    """A room, a dense boustrophedon pipe filling width x height, and a room."""
    from randomfun2026solvers.circuit import Circuit
    from randomfun2026solvers.memory_tape import _draw_pipe

    g = Circuit(width + 24, height + 8)
    for dy, row in enumerate(["+--+", "|@s|", "+--+"]):
        for dx, ch in enumerate(row):
            g.set(dx, dy, ch)
    x0, y0, x1 = 4, 1, 4 + width - 1
    pts = [(x0, y0)]
    for i in range(height):
        end = x1 if i % 2 == 0 else x0
        pts.append((end, y0 + i))
        if i < height - 1:
            pts.append((end, y0 + i + 1))
    n = _draw_pipe(g, pts)
    lx, ly = pts[-1]
    for dy, row in enumerate(["+--+", "|r |", "+--+"]):
        for dx, ch in enumerate(row):
            g.set(lx + 1 + dx, ly - 1 + dy, ch)
    return "\n".join(r.rstrip() for r in g.rows()), n


@node_required
@pytest.mark.parametrize(("w", "h"), [(10, 11), (16, 7)])
def test_a_dense_serpentine_is_one_pipe_at_full_density(w: int, h: int) -> None:
    """Pipe capacity costs area at 100% efficiency, so a ring can be tiny.

    This is the licence for reflow: 101 slots need ~110 cells, i.e. an 11x10
    box — not the 299 cells sprawled over an 88x68 grid that the n=100 relative
    memory layout spends on them.
    """
    from randomfun2026solvers.littleman import Littleman

    text, drawn = _serpentine(w, h)
    assert drawn == w * h, "the boustrophedon should fill the box"
    info = Littleman().analyze(text)
    assert len(info.pipes) == 1, "a serpentine must not fragment"
    assert len(info.pipes[0].path) == drawn


@node_required
@pytest.mark.parametrize("gap", [0, 1])
def test_anti_parallel_pipes_do_not_merge_even_when_adjacent(gap: int) -> None:
    """A ring's forward and return lanes need no separator row.

    The engine traces a pipe by following flow direction, not by taking connected
    components of pipe glyphs, so ``>--->`` directly above ``<---<`` stays two
    pipes. Without this a folded ring would need a blank row per lane and lose a
    third of its density.
    """
    from randomfun2026solvers.circuit import Circuit
    from randomfun2026solvers.littleman import Littleman
    from randomfun2026solvers.memory_tape import _draw_pipe

    g = Circuit(26, 10 + gap)
    for dy, row in enumerate(["+-+", "|@|", "|s|", "|r|", "+-+"]):
        for dx, ch in enumerate(row):
            g.set(dx, dy, ch)
    bx = 20
    for dy, row in enumerate(["+-+", "|r|", "|s|", "+-+"]):
        for dx, ch in enumerate(row):
            g.set(bx + dx, 1 + dy, ch)
    n1 = _draw_pipe(g, [(3, 2), (bx - 1, 2)])
    n2 = _draw_pipe(g, [(bx - 1, 3 + gap), (3, 3 + gap)])

    info = Littleman().analyze("\n".join(r.rstrip() for r in g.rows()))
    assert len(info.pipes) == 2, f"gap={gap} merged the lanes"
    assert sorted(len(p.path) for p in info.pipes) == sorted([n1, n2])


@node_required
def test_an_io_room_is_movable_but_a_display_is_pinned(program) -> None:
    """`pinned` is "may not translate", which an IO room is not.

    SPEC fixes an IO room's *shape* — 3x3, one marker, at most one pipe — not its
    address, and its shape needs no flag: a cut through the interior lands on the
    `I`/`O` glyph and a cut at the edge lands on a wall, both already refused.
    Pinning it instead conflated the two flags and silently blocked every
    compaction that had to slide an output room by one row.

    A display is the genuine case for `pinned`: its interior size *is* the pixel
    resolution and its interior is legitimately blank, so nothing else would stop
    a cut from quietly shrinking the panel.
    """
    ast = parse_ast(program)
    io = [r for r in ast.rooms if r.kind in ("input", "output")]
    assert io, "the grid reads and writes"
    for room in io:
        assert not room.pinned, f"{room.kind} room should be movable"
        room.translate(0, 1)  # must not raise

    displays = [r for r in ast.rooms if r.kind == "display"]
    for room in displays:
        assert room.pinned
        with pytest.raises(PaintError, match="pinned"):
            room.translate(1, 0)
