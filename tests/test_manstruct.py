"""Tests for the structural model (``manstruct.py``) a compactor rewrites.

Two tiers: the passability algebra and the freedom rules are pure Python and pin
the reasoning a compactor relies on; the whole-grid read needs ``node`` because
room and pipe geometry come from the reference engine, never from our own guess.
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

from randomfun2026solvers.manstruct import (  # noqa: E402
    DIRS,
    CapacityHint,
    CellInfo,
    Freedom,
    Kind,
    PipeInfo,
    _exits_for,
    _mirror_safe,
    _shape_of,
    analyze_structure,
)

LM_MJS = REPO / "littleman" / "lm.mjs"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)


def cell(glyph: str, kind: Kind) -> CellInfo:
    return CellInfo(0, 0, glyph, kind, exits=_exits_for(kind, glyph))


# ── the passability algebra ──────────────────────────────────────────────────
def test_floor_is_the_only_shareable_cell() -> None:
    """Compaction lives entirely on this distinction.

    An op cell is *transparent* — the man keeps his heading crossing it — but it
    still executes, so a second lane may not be routed through it. Only bare
    floor is both transparent and shareable.
    """
    floor = cell(" ", Kind.FLOOR)
    op = cell("M", Kind.OP)
    assert floor.transparent and floor.shareable
    assert op.transparent and not op.shareable


def test_a_steer_glyph_is_crossable_only_along_its_own_heading() -> None:
    """``>    >`` is four crossable cells between two that are not.

    Routing a north-south corridor through the ``>`` would silently re-steer the
    man east: the grid still loads and still runs, which is what makes it the
    worst bug class in this language.
    """
    east = cell(">", Kind.STEER)
    assert east.crossable_by("E")
    for heading in ("N", "S", "W"):
        assert not east.crossable_by(heading), heading
    assert not east.shareable


def test_a_wall_is_enterable_from_no_direction() -> None:
    """Entering a wall is fatal, so its transit table is empty, not identity."""
    wall = cell("-", Kind.WALL)
    assert wall.exits == {}
    assert not wall.transparent and not wall.shareable
    assert all(not wall.crossable_by(d) for d in DIRS)


def test_a_branch_is_enterable_but_its_exit_is_unknown() -> None:
    """``X``/``a``/``d``/``x`` turn on machine state, so no static exit exists."""
    branch = cell("d", Kind.BRANCH)
    assert set(branch.exits) == set(DIRS)
    assert all(v is None for v in branch.exits.values())
    assert not branch.transparent


# ── freedom rules ────────────────────────────────────────────────────────────
def test_a_non_palindromic_literal_forbids_mirroring() -> None:
    """A literal loads at its closing backtick, so walking it west reverses it."""
    assert not _mirror_safe("rW-M`100`W%Mb")  # 100 walked backwards is 001
    assert _mirror_safe("r+M`101`W%")  # palindromic: same value either way
    assert _mirror_safe("1Ns")  # no backtick span at all


def test_a_counted_loop_is_never_a_straight_run() -> None:
    """Its two columns make it a gadget, so it may translate but never reflow."""
    loop = [(5, 1), (6, 1), (6, 2), (6, 3), (5, 3), (5, 2)]
    assert _shape_of(loop) == "gadget"
    assert _shape_of([(1, 1), (2, 1), (3, 1)]) == "run-h"
    assert _shape_of([(1, 1), (1, 2), (1, 3)]) == "run-v"
    assert _shape_of([(4, 4)]) == "cell"


def test_group_slack_is_counted_once_not_per_pipe() -> None:
    """A ring's capacity is a property of the ring, so 113+186 has one surplus.

    Reporting ``length - need`` per pipe would show +12 and +85, and summing
    those would claim 97 free cells when the real figure is 198.
    """
    have = 113 + 186
    a = PipeInfo(4, 113, 2, 4, Freedom.SHRINKABLE, need=101, group=(4, 5), group_have=have)
    b = PipeInfo(5, 186, 4, 2, Freedom.SHRINKABLE, need=101, group=(4, 5), group_have=have)
    assert a.slack == b.slack == 198


def test_an_undeclared_pipe_reports_no_slack() -> None:
    """Silence must never license shortening: capacity is program semantics."""
    p = PipeInfo(0, 14, 0, 2, Freedom.FROZEN)
    assert p.slack is None and p.freedom is Freedom.FROZEN


# ── whole-grid read (engine is the oracle) ───────────────────────────────────
#: Parsing is the expensive part: ``parse_program`` asks the engine to resolve
#: every send/recv binding, one node process per pipe-op cell. Both grid tests
#: share one parse of the lightest grid that still has real pipes (triangle has
#: 25 such cells against tcp's 93), keeping the default run inside its budget.
@pytest.fixture(scope="module")
def program():
    from randomfun2026solvers.manparse import parse_program

    return parse_program(REPO / "tasks" / "solutions" / "triangle_cpu.man")


@node_required
def test_the_structural_read_agrees_with_the_engine_on_a_real_grid(program) -> None:
    """Every live in-room glyph lands in exactly one block, and IO rooms freeze."""
    s = analyze_structure(program)
    assert s.blocks, "a real grid has blocks"
    assert s.geometry_factor == max(s.bbox) ** 2

    # partition: no glyph in two blocks, none missed
    owned = [c for b in s.blocks for c in b.cells]
    assert len(owned) == len(set(owned)), "a cell belongs to at most one block"
    dead = {Kind.FLOOR, Kind.WALL, Kind.PIPE, Kind.VOID}
    live = {c for c, i in s.cells.items() if i.room is not None and i.kind not in dead}
    assert set(owned) == live

    # an IO room's shape is SPEC's, not ours
    io_rooms = {r.id for r in s.program.rooms if r.kind in ("input", "output")}
    assert io_rooms, "the grid reads and writes"
    for b in s.blocks:
        if b.room in io_rooms:
            assert b.freedom is Freedom.FROZEN, b


@node_required
def test_capacity_hints_turn_a_frozen_pipe_shrinkable(program) -> None:
    """The only thing that unlocks a pipe is an explicit, declared minimum."""
    bare = analyze_structure(program)
    assert all(p.freedom is Freedom.FROZEN for p in bare.pipes)
    assert bare.pipe_slack() == 0

    longest = max(bare.pipes, key=lambda p: p.length)
    hinted = analyze_structure(
        program, capacity=[CapacityHint((longest.id,), 1, note="test hint")]
    )
    target = next(p for p in hinted.pipes if p.id == longest.id)
    assert target.freedom is Freedom.SHRINKABLE
    assert target.slack == longest.length - 1
