"""The decode trie's depth, and why 17 opcodes is worth exactly nothing.

The CPU's lane band is `2 * (1 << k) - 1` rows and `k` comes from the opcode
*count* alone. That makes the band a step function: shedding opcodes buys rows
only when the count crosses a power of two. `little-little-man` sits at 19, so the
first three removals are free of benefit and the fourth pays for all of them —
which is the trap this pins, because a partial fold looks like progress and
measures as none.

See LLM-DESIGN.md, "The sixteen-opcode cliff", for the measured prize (-26% of
`area2`) and the cost of each candidate removal.
"""

from __future__ import annotations

import pytest


def _k(n_opcodes: int) -> int:
    """`machine.build`'s rule, verbatim."""
    return max(1, (n_opcodes - 1).bit_length())


def _span(k: int) -> int:
    """`build_cpu`'s lane band, verbatim: leaves are spaced two rows apart."""
    return 2 * (1 << k) - 1


def test_the_band_is_sized_by_capacity_not_by_lanes_used():
    """19 opcodes occupy 19 lanes and are charged for 32 leaf slots."""
    assert _k(19) == 5
    assert (1 << _k(19)) == 32
    assert _span(_k(19)) == 63


@pytest.mark.parametrize("n", [17, 18, 19])
def test_shedding_opcodes_above_the_cliff_buys_no_rows(n: int):
    """The reason a partial fold is not partial credit."""
    assert _k(n) == _k(19) == 5
    assert _span(_k(n)) == 63


def test_sixteen_is_the_cliff():
    """16 is the first count that reaches depth 4, and it halves the band."""
    assert _k(16) == 4
    assert _span(_k(16)) == 31
    assert _span(_k(19)) - _span(_k(16)) == 32


def test_fifteen_is_no_better_than_sixteen():
    """Overshooting the cliff is free but pointless — the next step is at 8."""
    assert _k(15) == _k(16) == 4
    assert _k(9) == 4 and _k(8) == 3


def test_the_empty_leaves_cannot_be_reclaimed_without_changing_depth():
    """The trie's fan-out is `1 << (k - level)`, so leaf spacing *is* the depth.

    Recorded as arithmetic rather than as a comment: at depth 5 the band holds 32
    slots whatever the opcode count, so 13 stand empty at 19 opcodes and no
    allocator change reaches them.
    """
    k = _k(19)
    slots = 1 << k
    assert slots - 19 == 13
    # every level's fan-out doubles as you climb, which is what fixes the spacing
    assert [1 << (k - level) for level in range(1, k + 1)] == [16, 8, 4, 2, 1]
