"""Standalone DMA memory-controller validated on the reference engine."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from lmc.dma import render_dma_standalone
from lmc.oracle import LM_PATH, run_grid

_HAVE_ORACLE = shutil.which("node") is not None and Path(LM_PATH).exists()
requires_oracle = pytest.mark.skipif(not _HAVE_ORACLE, reason="reference runner not available")


def test_dma_renders():
    grid = render_dma_standalone()
    assert "I" in grid and "O" in grid


@requires_oracle
@pytest.mark.parametrize(
    "cmds,expected",
    [
        ([0, 10, 0, 20, 1, 0, 1, 0], [10, 20]),              # push 10,20; pop,pop
        ([0, 10, 0, 20, 1, 0, 1, 0, 0, 5, 1, 0], [10, 20, 5]),  # interleaved
        ([0, -7, 1, 0], [-7]),                                # negative value round-trips
        ([0, 1, 0, 2, 0, 3, 1, 0, 1, 0, 1, 0], [1, 2, 3]),   # FIFO order
    ],
)
def test_dma_push_pop(cmds, expected):
    grid = render_dma_standalone()
    res = run_grid(grid, cmds, max_ticks=8000)
    assert res.output[: len(expected)] == expected, grid
