"""Store wiring + fragments validated on the reference engine."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from lmc.blockspec import E, BlockGraph, Instr, Pipe, W
from lmc.oracle import LM_PATH, run_grid
from lmc.router import render
from lmc.stores import CellStore, RingStore

Op = Instr

_HAVE_ORACLE = shutil.which("node") is not None and Path(LM_PATH).exists()
requires_oracle = pytest.mark.skipif(not _HAVE_ORACLE, reason="reference runner not available")


def _io_pipes():
    return [Pipe("in", "I", E, "CPU", W), Pipe("out", "CPU", E, "O", W)]


def _graph(store, trail):
    g = BlockGraph(cpu="CPU")
    g.rooms = {"CPU": "cpu", "I": "input", "O": "output", **store.rooms()}
    g.pipes = [*_io_pipes(), *store.pipes("CPU")]
    g.trail = trail
    return g


def test_store_pipes_shape():
    ring = RingStore("r")
    ps = ring.pipes("CPU")
    assert {p.id for p in ps} == {"r_up", "r_down"}
    assert ring.enqueue()[0].pipe == "r_up"
    assert ring.dequeue()[0].pipe == "r_down"


@requires_oracle
def test_ringstore_roundtrip_echo():
    """Push input into a North ring, pop it back, emit."""
    ring = RingStore("r")
    trail = [Op("@"), Op("r", "in"), *ring.enqueue(), *ring.dequeue(), Op("s", "out"), Op("H")]
    grid = render(_graph(ring, trail), ring_len=3)
    for v in (42, 7, -5, 0):
        assert run_grid(grid, [v], max_ticks=500).output == [v], (v, grid)


@requires_oracle
def test_cellstore_store_take_echo():
    """Spill a value to a South cell, take it back, emit."""
    cell = CellStore("sp")
    trail = [Op("@"), Op("r", "in"), *cell.store(), *cell.take(), Op("s", "out"), Op("H")]
    grid = render(_graph(cell, trail), spill_len=2)
    for v in (42, 7, -5, 0):
        assert run_grid(grid, [v], max_ticks=500).output == [v], (v, grid)


@requires_oracle
def test_cellstore_peek_keeps_value():
    """peek reads the cell without emptying it: read twice -> same value twice."""
    cell = CellStore("sp")
    trail = [
        Op("@"), Op("r", "in"), *cell.store(),
        *cell.peek(), Op("s", "out"),   # emit once (value kept)
        *cell.take(), Op("s", "out"),   # emit again
        Op("H"),
    ]
    grid = render(_graph(cell, trail), spill_len=2)
    assert run_grid(grid, [42], max_ticks=500).output == [42, 42], grid
