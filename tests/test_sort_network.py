"""Which comparator network `sort-numbers` should run, settled without the engine.

The **0-1 principle** (a comparator network sorts every input iff it sorts all
2^n zero-one inputs) makes these tests total for n = 16 at a cost of
milliseconds, so the choice of network is decided here and the grid tests in
`test_sort_grid.py` only have to check that the chosen one was built.
"""

from __future__ import annotations

import pytest
from randomfun2026solvers.sort_network import (
    apply_network,
    batcher_network,
    ring_traffic,
    selection_network,
    sorts_all_01,
    transposition_network,
)

N = 16  # the problem's maximum list length


# ── the networks ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "builder", [transposition_network, batcher_network, selection_network]
)
def test_every_modelled_network_sorts_every_zero_one_word(builder) -> None:
    """65,536 cases settles n = 16 completely, independent of the public data."""
    assert sorts_all_01(builder(N), N)


def test_a_broken_network_is_caught_by_the_zero_one_principle() -> None:
    """The proof has to be able to fail, or it proves nothing."""
    assert not sorts_all_01(transposition_network(N)[:-1], N)


def test_transposition_comparators_are_all_adjacent() -> None:
    """That is the whole reason it beats Batcher on a FIFO ring: no rotations."""
    assert all(hi - lo == 1 for lo, hi in transposition_network(N))
    assert max(hi - lo for lo, hi in batcher_network(N)) == N // 2


def test_selection_network_moves_less_ring_traffic_than_transposition() -> None:
    """Why the bank-of-one shape was built and the Y-split one was not.

    Both networks are rotation-free on a ring, but a transposition comparator
    pops and pushes *two* values where a carry comparator pops and pushes one,
    and every transposition pass is a full lap where selection passes shrink.
    A Y-split transposition therefore needs two men just to draw level.
    """
    assert ring_traffic("selection", N) < ring_traffic("transposition", N)
    assert ring_traffic("selection", N) >= ring_traffic("transposition", N, workers=2)


def test_padding_sinks_to_the_end_so_short_lists_reuse_the_full_network() -> None:
    """`n < 16` is handled by a sentinel above the value range."""
    sentinel = 10_001
    values = [7, -3, 10_000, -10_000, 0]
    padded = values + [sentinel] * (N - len(values))
    assert apply_network(selection_network(N), padded)[: len(values)] == sorted(values)
