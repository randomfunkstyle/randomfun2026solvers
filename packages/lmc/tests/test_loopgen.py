"""Control-combinator primitives (loopgen) validated on the reference engine."""

from __future__ import annotations

import pytest
from lmc.blockspec import BlockGraph, E, Instr, Pipe, W
from lmc.loopgen import if3, linear_block, seq_block
from lmc.router import render

Op = Instr

try:
    import os

    from lmc.oracle import LM_PATH, run_grid

    _HAVE_ORACLE = os.path.exists(LM_PATH)
except Exception:  # pragma: no cover
    _HAVE_ORACLE = False

_NO_ORACLE = "reference runner (node + lm.mjs) not available"
oracle = pytest.mark.skipif(not _HAVE_ORACLE, reason=_NO_ORACLE)


def _max_graph() -> tuple[BlockGraph, object]:
    """max(a,b): read a,b; A=b-a, B=a; if3 sets A=max; emit.

    pos (b>a): max=b=(b-a)+a=A+B -> '+' ; neg/zero (b<=a): max=a=B -> 'W'.
    """
    pre = linear_block([Op("@"), Op("r", "in"), Op("M"), Op("r", "in"), Op("-")])
    branch = if3(neg=[Op("W")], zero=[Op("W")], pos=[Op("+")])
    post = linear_block([Op("s", "out"), Op("H")])
    trail = seq_block([pre, branch, post])

    g = BlockGraph(cpu="CPU")
    g.rooms = {"CPU": "cpu", "I": "input", "O": "output"}
    g.pipes = [Pipe("in", "I", E, "CPU", W), Pipe("out", "CPU", E, "O", W)]
    return g, trail


@oracle
@pytest.mark.parametrize(
    "a,b",
    [(3, 5), (5, 3), (4, 4), (-2, 7), (7, -2), (-5, -5), (0, 0), (10, -10), (-10, 10)],
)
def test_if3_max(a, b):
    g, trail = _max_graph()
    grid = render(g, trail)
    res = run_grid(grid, [a, b], max_ticks=300)
    assert res.output == [max(a, b)], grid


def test_if3_shape_is_three_rows():
    # two-way collapse (neg==zero) still builds a well-formed 3-row block
    branch = if3(neg=[Op("W")], zero=[Op("W")], pos=[Op("+")])
    assert branch.height == 3
    assert branch.spawn == (0, 0)
    ys = {c.y for c in branch.cells}
    assert ys == {0, 1, 2}
