"""Z3 router: BlockGraph -> grid, validated against the reference engine."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from lmc.blockspec import BlockGraph, E, Instr, Pipe, W, ring_io_graph
from lmc.oracle import LM_PATH, run_grid
from lmc.router import render, solve_attachments
from lmc.trail import build_trail

_HAVE_ORACLE = shutil.which("node") is not None and Path(LM_PATH).exists()
requires_oracle = pytest.mark.skipif(
    not _HAVE_ORACLE, reason="reference runner (node + lm.mjs) not available"
)


def _manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def test_attachments_satisfy_nearest():
    """Recompute nearest in plain Python from the Z3-chosen attach cells."""
    g = ring_io_graph()
    trail = build_trail(g.trail)
    seg = solve_attachments(g, trail)
    for cell in trail.pipe_op_cells():
        tp = g.pipe(cell.pipe)
        direction = tp.cpu_dir(g.cpu)
        rivals = [
            p for p in g.pipes if p.cpu_dir(g.cpu) == direction and p.id != cell.pipe
        ]
        c = (cell.x, cell.y)
        dt = _manhattan(c, seg[cell.pipe])
        for q in rivals:
            dq = _manhattan(c, seg[q.id])
            # target must be strictly nearer, or tie broken by reading order
            key_t = (dt, seg[cell.pipe][1], seg[cell.pipe][0])
            key_q = (dq, seg[q.id][1], seg[q.id][0])
            assert key_t < key_q, (cell.char, cell.pipe, q.id, key_t, key_q)


@requires_oracle
def test_router_reproduces_ring_io():
    """R0: router-generated grid echoes input through the ring. BUF loops forever
    (memory server), so the CPU halts but the run hits the cap -- output is
    correct well before then."""
    grid = render(ring_io_graph())
    for v in (42, 7, -5, 1000000):
        assert run_grid(grid, [v], max_ticks=300).output == [v], (v, grid)


@requires_oracle
def test_generated_counted_loop():
    """Loop codegen: read n, emit 7 n times (do-while, n>=1)."""
    from lmc.loopgen import counted_loop_trail

    g = BlockGraph(cpu="CPU")
    g.rooms = {"CPU": "cpu", "I": "input", "O": "output"}
    g.pipes = [Pipe("in", "I", E, "CPU", W), Pipe("out", "CPU", E, "O", W)]
    trail = counted_loop_trail(
        prologue=[Instr("@"), Instr("r", "in"), Instr("b")],
        body=[Instr("7"), Instr("s", "out")],
        epilogue=[Instr("H")],
    )
    grid = render(g, trail)
    for n in (1, 3, 5, 8):
        assert run_grid(grid, [n]).output == [7] * n, (n, grid)


@requires_oracle
def test_generated_while_loop_zero_trip():
    """while_loop runs the body 0+ times: n=0 emits nothing."""
    from lmc.loopgen import linear_block, while_loop

    ii = Instr
    w = while_loop(
        prologue=[ii("@"), ii("r", "in"), ii("b")],  # A=n, BP=n
        test=[ii("d")],  # BP > 0 ?
        body=linear_block([ii("9"), ii("s", "out"), ii("m")]),  # emit 9, BP--
        epilogue=[ii("H")],
    )
    g = BlockGraph(cpu="CPU")
    g.rooms = {"CPU": "cpu", "I": "input", "O": "output"}
    g.pipes = [Pipe("in", "I", E, "CPU", W), Pipe("out", "CPU", E, "O", W)]
    grid = render(g, w)
    for n in (0, 1, 3, 5):
        assert run_grid(grid, [n]).output == [9] * n, (n, grid)


@requires_oracle
def test_generated_nested_loops():
    """Value-outer loop (counter in B) nesting a counted-inner loop: emit T(n)
    ones. Exercises nested loops + B surviving + no BP conflict."""
    from lmc.loopgen import linear_block, loop_wrap, seq_block

    ii = Instr
    inner = loop_wrap(
        prologue=[],
        body=linear_block([ii("1"), ii("s", "out")]),
        test=[ii("m"), ii("d")],
        epilogue=[],
    )
    outer = loop_wrap(
        prologue=[ii("@"), ii("r", "in"), ii("M")],
        body=seq_block(
            [
                linear_block([ii("b")]),
                inner,
                linear_block([ii("W"), ii("M"), ii("1"), ii("-"), ii("N"), ii("M")]),
            ]
        ),
        test=[ii("W"), ii("M"), ii("X")],
        epilogue=[ii("H")],
    )
    g = BlockGraph(cpu="CPU")
    g.rooms = {"CPU": "cpu", "I": "input", "O": "output"}
    g.pipes = [Pipe("in", "I", E, "CPU", W), Pipe("out", "CPU", E, "O", W)]
    grid = render(g, outer)
    for n in (1, 2, 3, 4, 6):
        exp = [1] * (n * (n + 1) // 2)
        assert run_grid(grid, [n], max_ticks=200000).output == exp, (n, grid)
