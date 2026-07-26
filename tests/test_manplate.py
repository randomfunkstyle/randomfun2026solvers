"""Tests for authoring a program as an AST and plating it (``manplate.py``).

The two things worth pinning are the ones that make composition safe rather than
lucky: a fragment's **ports** (there is a way in and a way out, and ``H`` has no
way out at all) and its **fixed internals** (a placer may move it, never rewrite
or split it).
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

from randomfun2026solvers.manast import render  # noqa: E402
from randomfun2026solvers.manplate import (  # noqa: E402
    HALT,
    Contract,
    Fragment,
    Port,
    best_plate,
    emit,
    hello_world,
    lit,
    pack,
    plate,
)

E, S = (1, 0), (0, 1)

LM_MJS = REPO / "littleman" / "lm.mjs"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)


# ── literals ─────────────────────────────────────────────────────────────────
def test_a_single_digit_needs_no_backticks_but_anything_longer_does() -> None:
    """``104`` unquoted is three one-digit loads and leaves 4, not 104."""
    assert lit(7) == "7"
    assert lit(32) == "`32`"
    assert lit(104) == "`104`"


def test_a_negative_literal_is_refused() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        lit(-1)


# ── ports ────────────────────────────────────────────────────────────────────
def test_a_plain_run_is_entered_left_and_left_right() -> None:
    f = emit(104)
    assert f.glyphs == "`104`s" and f.width == 6
    assert (f.in_port.dx, f.in_port.dy, f.in_port.heading) == (0, 0, E)
    assert len(f.out_ports) == 1
    assert (f.out_ports[0].dx, f.out_ports[0].heading) == (5, E)
    assert f.is_linear and not f.terminal


def test_halt_has_no_exit_at_all() -> None:
    """The reason exits are explicit: nothing may be routed out of a halt."""
    assert HALT.out_ports == ()
    assert HALT.terminal


def test_a_fragment_with_a_second_exit_is_not_linear() -> None:
    """A conditional turn needs a router, not a packer, so it must be refused."""
    fork = Fragment("fork", "d", exits=(Port(0, 0, E), Port(0, 0, S)), note="BP>0 turns south")
    assert not fork.is_linear
    with pytest.raises(ValueError, match="not linear"):
        pack([fork], 12)


def test_nothing_may_be_packed_after_a_terminal_fragment() -> None:
    with pytest.raises(ValueError, match="never returns"):
        pack([HALT, emit(1)], 12)


def test_a_contract_records_what_a_fragment_clobbers() -> None:
    """`emit` puts its literal in the main hand, so A does not survive it."""
    assert emit(5).contract.writes == frozenset({"A"})
    assert Contract().writes == frozenset()


# ── packing keeps internals fixed ────────────────────────────────────────────
def test_a_fragment_is_never_split_across_rows() -> None:
    """Fixed internals means atomic: it moves to the next row instead."""
    frags = [emit(104), emit(101), emit(108)]  # 6 cells each
    band = pack(frags, 14)  # capacity 12 -> two per row
    assert band is not None
    assert [[f.name for f in row] for row in band.rows] == [
        ["emit-104", "emit-101"],
        ["emit-108"],
    ]
    assert all(sum(f.width for f in row) <= 12 for row in band.rows)


def test_a_band_too_narrow_for_one_fragment_does_not_fit() -> None:
    assert pack([emit(104)], 6) is None  # capacity 4 < 6 cells


def test_a_corridor_row_sits_between_every_pair_of_code_rows() -> None:
    band = pack([emit(104), emit(101), emit(108)], 14)
    assert band is not None and len(band.rows) == 2
    assert band.height == 3, "two code rows plus one corridor between them"


# ── plating ──────────────────────────────────────────────────────────────────
def test_the_placer_searches_for_the_squarest_footprint() -> None:
    """Score is ``max(w,h)**2``, so a wide band and a tall one both lose."""
    frags = hello_world()
    best, width = best_plate(frags)
    for w in range(4, 41):
        other = plate(frags, w)
        if other is not None:
            assert best.geometry_factor <= other.geometry_factor, w
    assert plate(frags, width).geometry_factor == best.geometry_factor


def test_code_lands_inside_the_room_not_on_its_wall() -> None:
    """Children are placed at the *interior* origin, one in from the box."""
    ast = plate([emit(7), HALT], 8)
    assert ast is not None
    rows = render(ast)
    assert rows[0].startswith("+") and rows[0].endswith("+"), "row 0 is the top wall"
    assert "@" in rows[1], "the spawn is on the first interior row"
    assert "@" not in rows[0]


def test_the_output_pipe_is_two_cells_because_one_connects_nothing() -> None:
    """Measured: a single-cell pipe reports dst=-1 — it cannot be both ends."""
    ast = plate([emit(7), HALT], 8)
    assert ast is not None
    assert len(ast.pipes) == 1
    assert ast.pipes[0].capacity == 2


@node_required
def test_the_plated_hello_world_actually_runs() -> None:
    """End to end: authored as fragments, placed by the plate, run by the engine."""
    from randomfun2026solvers import optimize

    ast, _ = best_plate(hello_world())
    res = optimize.verify(render(ast), "hello-world", tick_cap=1_000_000)
    assert res.passed, [c.detail for c in res.cases if not c.passed]
    assert res.n_passed == len(res.cases)
