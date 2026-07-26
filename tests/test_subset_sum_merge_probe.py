"""The merge step's real cost, and what it does to the sorted-MITM estimate.

`subset_sum_merge` prices a two-way merge step at ``compare = 12`` ticks and
concludes the rebuild is worth 20.9x at `n = 20`.  That figure was never
measured — this probe is the smallest grid that performs exactly the step, and on
the engine it costs an order of magnitude more.  The tests below pin both the
correctness of the gadget and the slope, because the slope is what decides
whether the grid is worth building at all.
"""

from __future__ import annotations

import pytest
from randomfun2026solvers.subset_sum_merge_probe import build, expected

SENTINEL = 10**6


def _input(p: list[int], d: list[int]) -> str:
    """`k+1, P.., S, k+1, D.., S, 2k` — see the module docstring on the contract.

    The sentinel is pushed by the fill loop itself (the count is `k + 1`), which
    is what lets the exhausted side always lose the comparison without any
    end-of-stream test in the loop.
    """
    return " ".join(
        map(str, [len(p) + 1] + p + [SENTINEL] + [len(d) + 1] + d + [SENTINEL] + [len(p) + len(d)])
    )


def test_the_room_is_within_its_declared_interior():
    rows = build()
    assert max(len(r) for r in rows) <= 96
    assert len(rows) <= 42


@pytest.mark.slow
def test_it_merges_two_sorted_streams():
    from randomfun2026solvers.littleman import Littleman

    src = "\n".join(build())
    lm = Littleman()
    for p, d in [
        ([1, 3, 5], [2, 4, 6]),  # strict interleave, both sides win alternately
        ([1, 2, 3], [7, 8, 9]),  # one side drains first
        ([7, 8, 9], [1, 2, 3]),  # ...and the other way round
        ([2, 4, 6], [2, 4, 6]),  # ties: `A == 0` takes the P lane
    ]:
        want = expected(p, d)
        snap = lm.judge(src, input=_input(p, d), expected=want, max_ticks=200_000)
        assert snap.output == want, (p, d, snap.output)


@pytest.mark.slow
def test_a_merge_step_costs_129_ticks_not_the_12_the_model_assumes():
    """The measurement the rebuild's economics turn on.

    Cost is exactly linear in the number of emitted words, and the slope is the
    per-step charge.  It is ~129 rather than 12 because **a merge step is
    corridor-dominated**: the station is ten glyphs, and everything else is the
    man walking to the output pipe and out to a ring's return column and back.
    `subset_sum_scan_probe`'s 5-ticks-a-word gadget is cheap precisely because its
    lap touches one pipe and never emits.
    """
    from randomfun2026solvers.littleman import Littleman

    src = "\n".join(build())
    lm = Littleman()
    ticks = {}
    for k in (4, 12, 20):
        p = list(range(1, 2 * k, 2))
        d = list(range(2, 2 * k + 1, 2))
        want = expected(p, d)
        snap = lm.judge(src, input=_input(p, d), expected=want, max_ticks=400_000)
        assert snap.output == want, k
        ticks[2 * k] = snap.step

    slopes = {
        (b - a) / (nb - na)
        for (na, a), (nb, b) in zip(sorted(ticks.items()), sorted(ticks.items())[1:], strict=False)
    }
    assert slopes == {129.0}, ticks
