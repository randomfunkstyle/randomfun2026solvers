"""Soft-CPU: fetch bytecode from the ROM block, decode, run microcode."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from lmc.cpu import render_rom_cpu
from lmc.oracle import LM_PATH, run_grid

_HAVE = shutil.which("node") is not None and Path(LM_PATH).exists()
requires_oracle = pytest.mark.skipif(not _HAVE, reason="reference runner not available")


def test_cpu_renders():
    assert "O" in render_rom_cpu([1, 42, 0])


@requires_oracle
@pytest.mark.parametrize(
    "bytecode,expected",
    [
        ([1, 42, 0], [42]),
        ([1, 42, 1, 7, 0], [42, 7]),
        ([1, -5, 1, 1000, 0], [-5, 1000]),
    ],
)
def test_cpu_runs_bytecode(bytecode, expected):
    """CPU fetches OUT_IMM/HALT opcodes from ROM and executes them."""
    grid = render_rom_cpu(bytecode)
    res = run_grid(grid, [], max_ticks=8000)
    assert res.output[: len(expected)] == expected, grid
