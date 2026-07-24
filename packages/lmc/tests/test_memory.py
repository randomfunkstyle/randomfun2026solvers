"""memory sub-blocks. Block 1: DMA-mem (ADVANCE/PEEK/REPLACE), on the reference engine."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from lmc.memory import (
    render_memdma_standalone,
    render_memory,
    render_memory_read,
    render_read_driver_standalone,
)
from lmc.oracle import LM_PATH, run_grid

_HAVE_ORACLE = shutil.which("node") is not None and Path(LM_PATH).exists()
requires_oracle = pytest.mark.skipif(not _HAVE_ORACLE, reason="reference runner not available")

SEED = [10, 20, 30, 40, 50]


def test_memdma_renders():
    grid = render_memdma_standalone(SEED)
    assert "I" in grid and "O" in grid


@requires_oracle
@pytest.mark.parametrize(
    "cmds,expected",
    [
        # PEEK, ADVANCE, PEEK, ADVANCE, PEEK -> cells 0,2,4
        ([1, 0, 0, 0, 1, 0, 0, 0, 1, 0], [10, 30, 50]),
        # PEEK all 5 in order
        ([1, 0, 1, 0, 1, 0, 1, 0, 1, 0], [10, 20, 30, 40, 50]),
        # REPLACE head with 99, then PEEK all 5 -> head consumed, 99 at tail
        ([-1, 99, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0], [20, 30, 40, 50, 99]),
    ],
)
def test_memdma_commands(cmds, expected):
    grid = render_memdma_standalone(SEED)
    res = run_grid(grid, cmds, max_ticks=14000)
    assert res.output[: len(expected)] == expected, grid


def test_read_driver_renders():
    assert "I" in render_read_driver_standalone(5)


@requires_oracle
@pytest.mark.parametrize(
    "op_stream,cmds",
    [
        # READ addr=2 over N=5 -> ADVANCE,ADVANCE, PEEK, ADVANCE,ADVANCE
        ([0, 2], [0, 0, 0, 0, 1, 0, 0, 0, 0, 0]),
        ([0, 0], [1, 0, 0, 0, 0, 0, 0, 0, 0, 0]),
        ([0, 4], [0, 0, 0, 0, 0, 0, 0, 0, 1, 0]),
    ],
)
def test_read_driver_command_stream(op_stream, cmds):
    grid = render_read_driver_standalone(5)
    res = run_grid(grid, op_stream, max_ticks=8000)
    assert res.output[: len(cmds)] == cmds, grid


def test_memory_read_renders():
    assert "I" in render_memory_read([0, 0, 0, 0, 0])


@requires_oracle
@pytest.mark.parametrize(
    "op_stream,expected",
    [
        ([0, 2, 0, 0, 0, 4], [30, 10, 50]),   # READ cells 2,0,4 of seeded ring
        ([0, 1, 0, 3], [20, 40]),
    ],
)
def test_memory_read_end_to_end(op_stream, expected):
    """I -> Driver -> DMA-mem -> O, over a ring seeded [10,20,30,40,50]."""
    grid = render_memory_read([10, 20, 30, 40, 50])
    res = run_grid(grid, op_stream, max_ticks=30000)
    assert res.output[: len(expected)] == expected, grid


def test_memory_full_renders():
    assert "I" in render_memory([0, 0, 0, 0, 0])


@requires_oracle
@pytest.mark.parametrize(
    "op_stream,expected",
    [
        # WRITE 42->2; READ 2; WRITE 7->0; READ 0; READ 4 (untouched)
        ([1, 2, 42, 0, 2, 1, 0, 7, 0, 0, 0, 4], [42, 7, 0]),
        # overwrite: WRITE 1->3; READ 3; WRITE 9->3; READ 3
        ([1, 3, 1, 0, 3, 1, 3, 9, 0, 3], [1, 9]),
        # fresh cells read 0
        ([0, 0, 0, 1, 0, 4], [0, 0, 0]),
    ],
)
def test_memory_read_write(op_stream, expected):
    """Full memory (READ+WRITE) over a 5-cell RAM (starts at 0)."""
    grid = render_memory([0, 0, 0, 0, 0])
    res = run_grid(grid, op_stream, max_ticks=80000)
    assert res.output[: len(expected)] == expected, grid


@requires_oracle
@pytest.mark.parametrize(
    "op_stream,expected",
    [
        ([0, 3], [0]),                                              # fresh cell reads 0
        ([1, 7, 42, 0, 7], [42]),                                  # write then read
        ([1, 5, 1, 1, 5, 2, 0, 5, 1, 5, -5, 0, 5], [2, -5]),       # overwrite
        ([1, 0, -1000000, 1, 99, 1000000, 0, 0, 0, 99, 0, 50],
         [-1000000, 1000000, 0]),                                  # boundary addr/value
    ],
)
def test_memory_100_cell_public_cases(op_stream, expected):
    """The real `memory` problem: a 100-cell RAM (semester 1)."""
    grid = render_memory([0] * 100, ram_len=55)
    res = run_grid(grid, op_stream, max_ticks=600000)
    assert res.output[: len(expected)] == expected


def _parse_memory_md_cases():
    md = Path(__file__).resolve().parents[3] / "task_docs/problem_sets/semester1/memory.md"
    if not md.exists():
        return []
    pairs = re.findall(r"\nin:\s*(.*?)\nout:\s*(.*?)\n", md.read_text(), re.S)
    return [([int(x) for x in i.split()], [int(x) for x in o.split()]) for i, o in pairs]


@requires_oracle
@pytest.mark.parametrize("op_stream,expected", _parse_memory_md_cases())
def test_memory_all_public_cases_from_md(op_stream, expected):
    """Every case in memory.md, run against the 100-cell grid on the reference engine.

    Output must match EXACTLY (length included) -- a truncated/over-long output is a fail,
    which is what the contest reports as wrong-output.
    """
    grid = render_memory([0] * 100, ram_len=55)
    res = run_grid(grid, op_stream, max_ticks=600000)
    assert res.output == expected
