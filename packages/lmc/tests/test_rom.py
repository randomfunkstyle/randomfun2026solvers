"""ROM block: a separate program store that streams bytecode."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from lmc.oracle import LM_PATH, run_grid
from lmc.rom import render_rom_standalone

_HAVE = shutil.which("node") is not None and Path(LM_PATH).exists()
requires_oracle = pytest.mark.skipif(not _HAVE, reason="reference runner not available")


def test_rom_renders():
    assert "O" in render_rom_standalone([1, 2, 3])


@requires_oracle
@pytest.mark.parametrize("bytecode", [[11, 22, 33, 44], [5], [0, -7, 100, -100]])
def test_rom_streams_bytecode(bytecode):
    grid = render_rom_standalone(bytecode)
    res = run_grid(grid, [], max_ticks=8000)
    # ROM cycles forever; the first len(bytecode) outputs are the program in order
    assert res.output[: len(bytecode)] == bytecode, grid
