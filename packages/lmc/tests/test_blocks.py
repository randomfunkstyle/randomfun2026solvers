"""Regression-lock the validated primitive blocks against the reference engine."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from lmc.oracle import LM_PATH, run_grid, tick_grid

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
_HAVE_ORACLE = shutil.which("node") is not None and Path(LM_PATH).exists()
requires_oracle = pytest.mark.skipif(
    not _HAVE_ORACLE, reason="reference runner (node + lm.mjs) not available"
)


@requires_oracle
def test_chain_forwards_value():
    grid = (EXAMPLES / "chain.man").read_text()
    for v in (42, 7, -5):
        assert run_grid(grid, [v]).output == [v]


@requires_oracle
def test_roundtrip_one_word_memory():
    grid = (EXAMPLES / "roundtrip.man").read_text()
    r = run_grid(grid)
    assert r.halted and r.fatal is None
    # CPU sent 42 up, forwarder bounced it back down, CPU received it into A
    snap = tick_grid(grid, 30)
    cpu = max(snap["entities"]["runners"], key=lambda r: r["pos"][1])  # lower room
    assert cpu["a"] == 42 and cpu["halted"]


@requires_oracle
def test_forwarder_echoes_stream():
    grid = (EXAMPLES / "forwarder.man").read_text()
    snap = tick_grid(grid, 21, [1, 2, 3])
    assert snap.get("output") == [1, 2, 3]


@requires_oracle
def test_ring_cycles_stored_values():
    """3-word circulating store: seeded [10,20,30] cycle past the CPU tap."""
    grid = (EXAMPLES / "ring.man").read_text()
    for tick, want in [(18, 10), (26, 20), (34, 30), (42, 10), (50, 20), (58, 30)]:
        snap = tick_grid(grid, tick)
        cpu = max(snap["entities"]["runners"], key=lambda r: r["pos"][1])
        assert cpu["a"] == want, (tick, cpu["a"], want)
