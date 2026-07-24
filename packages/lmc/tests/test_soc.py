"""Two-man SoC (CORE + DMA over a bus) validated on the reference engine."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from lmc.blockspec import Instr
from lmc.loopgen import forever_loop, if3, linear_block, seq_block
from lmc.oracle import LM_PATH, run_grid
from lmc.soc import render_soc

Op = Instr

_HAVE_ORACLE = shutil.which("node") is not None and Path(LM_PATH).exists()
requires_oracle = pytest.mark.skipif(not _HAVE_ORACLE, reason="reference runner not available")


def _echo_core():
    # loop { send IN(0,dummy); R resp -> value; send OUT(1,value) }
    return forever_loop(prologue=[Op("@")], body=seq_block([
        linear_block([Op("0"), Op("s", "req"), Op("0"), Op("s", "req")]),
        linear_block([Op("R", "resp"), Op("M")]),
        linear_block([Op("1"), Op("s", "req"), Op("W"), Op("s", "req")]),
    ]))


def _echo_dma():
    # loop { read cmd,value from req (both at top); IN: read input->resp; OUT: value->out }
    return forever_loop(prologue=[Op("@")], body=seq_block([
        linear_block([Op("r", "req"), Op("M"), Op("r", "req"), Op("W")]),  # A=cmd, B=value
        if3(neg=[], zero=[Op("r", "din"), Op("s", "resp")],
            pos=[Op("W"), Op("s", "dout")]),
    ]))


def test_soc_renders():
    grid = render_soc(_echo_core(), _echo_dma())
    assert "I" in grid and "O" in grid


@requires_oracle
@pytest.mark.parametrize("stream", [[42], [7, -5], [100, 200, 300], [0, -1000000, 1000000]])
def test_soc_echo(stream):
    grid = render_soc(_echo_core(), _echo_dma())
    res = run_grid(grid, stream, max_ticks=8000)
    assert res.output[: len(stream)] == stream, grid
