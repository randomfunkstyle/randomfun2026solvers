"""The placer must guarantee, not merely allow, correct pipe targeting."""
from __future__ import annotations

import pytest
from randomfun2026solvers.asmlayout import (
    Anchors, Assembler, PlacementError, Serpentine, PIPE_OPS,
)
from randomfun2026solvers.circuit import Circuit

IW = IH = 26


def _anchors() -> Anchors:
    a = Anchors()
    for name, pos, inc in [
        ("IN", (4, IH), True), ("REGR", (10, IH), True), ("TAPERET", (IW, 13), True),
        ("REGF", (7, IH), False), ("OUT", (13, IH), False), ("TAPEFWD", (20, IH), False),
    ]:
        a.add(name, pos, incoming=inc)
    return a


def test_winner_matches_manhattan_and_reports_margin():
    a = _anchors()
    assert a.winner((4, 20), incoming=True)[0] == "IN"
    assert a.winner((10, 20), incoming=True)[0] == "REGR"
    assert a.winner((20, 20), incoming=False)[0] == "TAPEFWD"


def test_emit_pads_until_the_intended_pipe_wins():
    a, c = _anchors(), Circuit(IW, IH)
    s = Serpentine(c, a, 1, 0, IW - 3, 3)
    s.emit("r", pipe="REGR")
    cell = next(k for k, v in c.cell.items() if v == "r")
    assert a.winner(cell, incoming=True)[0] == "REGR"


def test_every_placed_pipe_op_resolves_as_declared():
    """The whole phase dance, then check each pipe op against the engine's rule."""
    a, c = _anchors(), Circuit(IW, IH)
    asm = Assembler(c, a, 1, IW - 3, gutter_down=0, gutter_up=IW - 1)
    asm.linear([("r", "REGR"), ("M1+", None), ("s", "REGF")])
    asm.branch("X", {"straight": [("1N", None)], "cw": [("1", None)]})
    asm.linear([("s", "REGF"), ("r", "IN"), ("-", None)])
    asm.branch("X", {"ccw": [("M`100`+", None)]})
    asm.linear([("M", None), ("r", "REGR"), ("+", None), ("s", "REGF"),
                ("r", "REGR"), ("Wb", None)])
    seen = 0
    for cell, ch in c.cell.items():
        need = PIPE_OPS.get(ch)
        if need is None:
            continue
        seen += 1
        name, margin = a.winner(cell, incoming=need)
        assert margin >= 2, f"{ch!r} at {cell} wins {name} by only {margin}"
    assert seen >= 7


def test_impossible_constraint_is_refused_not_mangled():
    a, c = _anchors(), Circuit(IW, IH)
    s = Serpentine(c, a, 1, 0, 3, 0)          # a lane nowhere near the tape anchors
    with pytest.raises(PlacementError):
        s.emit("r", pipe="TAPERET")


def test_band_height_is_derived_from_constraints():
    """A window already passed needs the serpentine's next row to come round again.

    REGF's window reaches column 9 (the midpoint to OUT at 13), so REGR-then-REGF
    does fit one row — the honest example is a window that must be revisited: an
    input read, then a register read east of it, then input again.
    """
    a, c = _anchors(), Circuit(IW, IH)
    asm = Assembler(c, a, 1, IW - 3, gutter_down=0, gutter_up=IW - 1)
    assert asm._fit_rows([("r", "REGR"), ("s", "REGF")], 0) == 1
    assert asm._fit_rows([("r", "IN"), ("r", "REGR"), ("r", "IN")], 0) >= 2
