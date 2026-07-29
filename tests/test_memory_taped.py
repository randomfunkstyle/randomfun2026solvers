"""The taped STORE tier: banked pipe tapes behind a chain of range gates.

The tier exists for the little-man census (a bank is two men, a gate one,
against the man-memory's ~two per slot), so the census is pinned here along
with the semantics: every address lands in the right bank, rebased right, and
comes back with the right value through the collector.

The probes stream requests, and the tier's ordering contract is the machine's
(the CPU blocks on every read; only one answer is ever in flight), so reads
are grouped per bank per run — two banks' rings answer at different speeds, so
*streamed* cross-bank reads can legally come home out of order. The machine
gate in ``test_deadman3d.py`` covers the serial cross-bank mix for real.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: E402
from randomfun2026solvers.memory_taped import (  # noqa: E402
    taped_plan,
    taped_store_block,
)

#: deadman-3d's shipped plan: hot high addresses in small rings.
PLAN = (128, 128, 40, 33)


def _standalone(block) -> FastLittleman:
    """The block as a complete program: an I room on the request stub, an O
    room on the answer stub — the same wrapper the men-v3 grid block test uses."""
    sx, sy = 6, 4
    grid = {(x + sx, y + sy): ch for (x, y), ch in block.cells.items()}
    ix, iy = block.in_cell[0] + sx, block.in_cell[1] + sy
    ox, oy = block.out_cell[0] + sx, block.out_cell[1] + sy
    for j, row in enumerate(("+-+", "|I|", "+-+")):
        for i, ch in enumerate(row):
            grid[(ix - 4 + i, iy - 1 + j)] = ch
    grid[(ix - 1, iy)] = ">"
    for j, row in enumerate(("+-+", "|O|", "+-+")):
        for i, ch in enumerate(row):
            grid[(ox - 1 + i, j)] = ch
    for y in range(3, oy):
        grid[(ox, y)] = "^"
    w = max(x for x, _ in grid) + 1
    h = max(y for _, y in grid) + 1
    return FastLittleman("\n".join("".join(grid.get((x, y), " ") for x in range(w)) for y in range(h)))


def test_the_plan_covers_the_tape_and_rejects_gaps() -> None:
    assert sum(taped_plan(330, PLAN)) >= 329
    assert taped_plan(330, 4) == [83, 83, 83, 80]
    with pytest.raises(ValueError):
        taped_plan(330, (100, 100))  # 129 addresses uncovered
    with pytest.raises(ValueError):
        taped_plan(330, 1)


def test_the_census_is_the_point() -> None:
    """Two men per bank plus one per gate — that is the tier's whole reason."""
    b = taped_store_block(330, PLAN, skip_batch=2)
    men = sum(1 for ch in b.cells.values() if ch == "@")
    assert men == 2 * len(PLAN) + (len(PLAN) - 1) + 1  # workers+relays, gates, collector
    assert b.pipes == 4 * len(PLAN) + (len(PLAN) - 2)


@pytest.mark.parametrize("skip_batch", [1, 2])
def test_every_address_reads_back_what_was_written(skip_batch: int) -> None:
    """All 329 slots, written through the whole chain then read one bank per
    run (see the module docstring for why reads are grouped per bank)."""
    engine = _standalone(taped_store_block(330, PLAN, skip_batch=skip_batch))
    writes = [x for a in range(1, 330) for x in (1, a, a * 13 + 7)]
    bounds = [1]
    for m in taped_plan(330, PLAN):
        bounds.append(bounds[-1] + m)
    for lo, hi in zip(bounds, bounds[1:], strict=False):
        hi = min(hi, 330)
        reads = [x for a in range(lo, hi) for x in (0, a)]
        want = [a * 13 + 7 for a in range(lo, hi)]
        res = engine.run(writes + reads, expected=want, max_ticks=60_000_000)
        assert res.fatal is None and res.output == want, (
            lo,
            hi,
            res.fatal or res.reason,
            res.output[:5],
        )


def test_fresh_slots_read_zero_and_extremes_survive() -> None:
    engine = _standalone(taped_store_block(330, PLAN, skip_batch=2))
    addrs = [1, 128, 129, 256, 257, 296, 297, 329]  # both sides of every seam
    fresh = engine.run(
        [x for a in addrs for x in (0, a)], expected=[0] * len(addrs), max_ticks=10_000_000
    )
    assert fresh.fatal is None and fresh.output == [0] * len(addrs)
    # extremes stay within one bank per pair — cross-bank reads race when
    # streamed (the short last ring answers first); the machine serializes
    edges = engine.run(
        [1, 1, -1000000, 0, 1, 1, 128, 1000000, 0, 128],
        expected=[-1000000, 1000000],
        max_ticks=10_000_000,
    )
    assert edges.fatal is None and edges.output == [-1000000, 1000000]
    top = engine.run(
        [1, 297, -1000000, 0, 297, 1, 329, 1000000, 0, 329],
        expected=[-1000000, 1000000],
        max_ticks=10_000_000,
    )
    assert top.fatal is None and top.output == [-1000000, 1000000]
