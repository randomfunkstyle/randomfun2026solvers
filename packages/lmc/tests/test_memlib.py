"""memlib ring fragments: the memory/list building block, oracle-locked.

reverse_list is rebuilt entirely from `memlib` fragments, so if these tests pass
the fragments (append-run, rotate-to-index, pop-emit) are correct by construction.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from lmc import memlib
from lmc.blockspec import BlockGraph, E, N, Pipe, S, W
from lmc.loopgen import forever_loop
from lmc.oracle import LM_PATH, run_grid
from lmc.router import render

_HAVE_ORACLE = shutil.which("node") is not None and Path(LM_PATH).exists()
requires_oracle = pytest.mark.skipif(
    not _HAVE_ORACLE, reason="reference runner (node + lm.mjs) not available"
)


def _fragment_chars(instrs):
    return "".join(i.char for i in instrs)


def test_ring_fragments_shapes():
    """The element primitives are the exact glyphs the ring convention promises."""
    assert _fragment_chars(memlib.read_from("in")) == "r"
    assert _fragment_chars(memlib.enqueue("up")) == "s"
    assert _fragment_chars(memlib.dequeue("down")) == "r"
    assert _fragment_chars(memlib.rotate_once("down", "up")) == "rs"
    assert _fragment_chars(memlib.pop_emit("down", "out")) == "rs"
    assert _fragment_chars(memlib.length_to_bp("down")) == "q"
    # each counted loop is a BP `d`-branch (row 0) over a body that ends in `m`
    for blk, body_glyphs in (
        (memlib.load_run("in", "up"), "rsm"),
        (memlib.rotate_run("down", "up"), "rsm"),
        (memlib.drain_run("down", "out"), "rsm"),
    ):
        row0 = "".join(c.char for c in sorted(blk.cells, key=lambda c: c.x) if c.y == 0)
        assert "d" in row0
        assert body_glyphs in "".join(
            c.char for c in sorted(blk.cells, key=lambda c: c.x) if c.y == 1
        )


def _reverse_grid():
    program = forever_loop(prologue=[], body=memlib.reverse_round("in", "out", "down", "up"))
    g = BlockGraph(cpu="CPU")
    g.rooms = {"CPU": "cpu", "I": "input", "O": "output", "BUF": "buf"}
    g.pipes = [
        Pipe("in", "I", E, "CPU", W),
        Pipe("out", "CPU", E, "O", W),
        Pipe("up", "CPU", N, "BUF", S),
        Pipe("down", "BUF", S, "CPU", N),
    ]
    return render(g, program, ring_len=9)


def _trail_cells(program):
    return sorted((c.x, c.y, c.char, c.pipe) for c in program.cells)


def test_memlib_rebuilds_reverse_trail():
    """memlib.reverse_round reproduces demos.reverse_program's trail exactly.

    (The rendered grid can't be compared byte-for-byte: the Z3 router's pipe
    placement is unseeded, so identical trails render to different valid grids.
    The trail itself is deterministic, which is what memlib is responsible for.)
    """
    from lmc.demos import reverse_program

    _, program = reverse_program()
    rebuilt = forever_loop(prologue=[], body=memlib.reverse_round("in", "out", "down", "up"))
    assert _trail_cells(program) == _trail_cells(rebuilt)


@requires_oracle
def test_memlib_reverse_multi_round():
    """The memlib-built ring passes a multi-round reverse stream on the engine."""
    grid = _reverse_grid()
    stream = [1, 42, 2, 100, -100, 3, 10, 20, 30]
    expected = [42, -100, 100, 30, 20, 10]
    assert run_grid(grid, stream, max_ticks=500000).output == expected
