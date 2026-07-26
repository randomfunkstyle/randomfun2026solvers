"""Sorted meet-in-the-middle for `subset-sum`: correctness, and the cost it buys.

The point of the module is that the old search pays `2^hL * 2^hR = 2^n` while
this one pays `2^hL + 2^hR`.  The cost tests pin that difference, because it is
the whole reason to prefer it — and the correctness tests pin the two things the
packing quietly assumes.
"""

from __future__ import annotations

import random

import pytest
from randomfun2026solvers import subset_sum_merge as M
from randomfun2026solvers.subset_sum_mitm import public_cases, walk


def test_every_public_case_matches():
    for name, values, target, want in public_cases():
        idx, _ = M.solve(values, target)
        got = [0] if not idx else [len(idx)] + [values[i] for i in idx]
        assert got == want, name


def test_fuzz_against_brute_force():
    M.fuzz(300, seed=13)


def test_the_split_is_balanced():
    """The cost is a sum now, not a product, so equal halves are cheapest.

    12/8 and 10/10 are identical for the old product search; for this one they are
    4,352 against 2,048.
    """
    assert M.split(20) == (10, 10)
    assert M.split(19) == (10, 9)
    assert M.split(10) == (5, 5)


def test_lex_smallest_is_numerically_largest_mask():
    """All three orderings the problem statement spells out, in mask arithmetic."""

    def mask(idx, n):
        return sum(1 << (n - 1 - i) for i in idx)

    assert mask([0, 4], 5) > mask([1, 3], 5)
    assert mask([1, 2, 4], 5) > mask([1, 3], 5)
    assert mask([1, 2, 4], 5) > mask([2, 3, 4], 5)


def test_the_lex_trick_needs_positive_values():
    """It fails outright when one set is a prefix of another — and cannot be.

    `{0}` is 100000 and `{0,5}` is 100001, so max-mask picks the longer set while
    lex wants the shorter. Two equal-sum sets can only be in that relation if the
    extra elements sum to zero, which `1 <= v` forbids. If the constraint ever
    changes, this is the assumption that breaks.
    """

    def mask(idx, n):
        return sum(1 << (n - 1 - i) for i in idx)

    assert mask([0, 5], 6) > mask([0], 6)  # the encoding really does invert
    assert M.prefix_pairs_are_impossible([1, 99999, 7])
    assert not M.prefix_pairs_are_impossible([1, 0, 7])


def test_no_subset_returns_none():
    values = [2, 4, 6, 8]
    idx, _ = M.solve(values, 21)  # odd target, all-even values
    assert idx is None
    assert M.expected_output(values, 21) == [0]


def test_dedupe_keeps_the_lex_winner():
    """Equal sums collapse to one entry, and it must be the larger mask.

    Without this the two-pointer would need group handling, which a rotating ring
    cannot do — it would have to back a pointer up.
    """
    values = [5, 3, 2, 4, 1]  # 3+2 == 5 and 4+1 == 5
    idx, _ = M.solve(values, 5)
    assert idx == [0], idx  # {0} beats {1,2} and {3,4}


@pytest.mark.parametrize("n", [10, 14, 20])
def test_cost_is_a_sum_not_a_product(n: int):
    """The generated entries must scale as `2^(n/2)`, not `2^n`."""
    rng = random.Random(5)
    values = [rng.randint(1, 99999) for _ in range(n)]
    target = int(sum(values) * 0.4)
    _, cost = M.solve(values, target)
    hl, hr = M.split(n)
    assert cost.generated <= (1 << hl) + (1 << hr)
    assert cost.compares < 4 * ((1 << hl) + (1 << hr))


def test_it_beats_the_old_search_at_n_20():
    """The measured reason to build it: same answer, an order of magnitude cheaper.

    The spread matters as much as the mean. The old search stops at the first
    lex-order hit, so a lucky instance costs it little and the margin narrows to
    ~5x; an unlucky one runs the full product and the margin passes 70x. This
    machine's cost barely moves, which is why the *worst* case is where the change
    pays — and the judged set is heavier than the public one.
    """
    rng = random.Random(11)
    ratios = []
    for _ in range(8):
        values = [rng.randint(1, 99999) for _ in range(20)]
        target = int(sum(values) * rng.uniform(0.10, 0.60))
        _, cost = M.solve(values, target)
        ratios.append(walk(values, target).ticks / cost.ticks)
    assert min(ratios) > 3.0, ratios
    assert sum(ratios) / len(ratios) > 12.0, ratios


def test_the_worst_case_clears_the_tick_cap_comfortably():
    """The old search runs 6.5M against a 15,000,000 cap — 2.3x. This is ~100x.

    That margin is the robustness half of the change: the old cost swings with the
    input because it is exponential, and the judged set is heavier than the public
    one.
    """
    rng = random.Random(23)
    worst = 0
    for _ in range(20):
        values = [rng.randint(1, 99999) for _ in range(20)]
        target = int(sum(values) * rng.uniform(0.10, 0.60))
        worst = max(worst, M.solve(values, target)[1].ticks)
    assert worst < 300_000, worst


def test_pruning_is_weak_at_the_stated_target_range():
    """A measured negative, so it is not attempted again.

    `t` is 10-60% of the value sum, so the upper cut (`s > t`) never fires — a
    half's total is under 50% — and the lower cut (`s < t - totalOther`) clips at
    most ~10% of the range. Both bounds are structurally loose *because* of the
    constraint, and pruning is not where any further win lives.
    """
    rng = random.Random(31)
    values = [rng.randint(1, 99999) for _ in range(20)]
    target = int(sum(values) * 0.60)  # the most favourable end
    _, cost = M.solve(values, target)
    assert cost.pruned < cost.generated // 10


def test_the_merge_never_backs_up_its_lead_pointer():
    """One ring plus a small delay carries the merge — not two full copies.

    Merging `L` with `L + v` reads the same sequence twice, and the `L` pointer
    can never fall behind: if it did, `L[a] < L[b] <= L[b] + v`, so the comparison
    would take from `L` and advance it. That is what lets a grid feed the
    comparator from one ring and park the passed-but-unconsumed words in a delay
    FIFO, which at `n = 20` stays far shorter than the list itself.
    """
    rng = random.Random(9)
    worst_gap = 0
    for _ in range(20):
        values = [rng.randint(1, 99999) for _ in range(10)]
        shift, out = 1 << 10, [0]
        for v in values:
            other = [p + v * shift for p in out]
            a = b = 0
            merged = []
            while a < len(out) or b < len(other):
                if b >= len(other) or (a < len(out) and out[a] <= other[b]):
                    merged.append(out[a])
                    a += 1
                else:
                    merged.append(other[b])
                    b += 1
                assert a >= b, "the lead pointer fell behind — a delay FIFO cannot work"
                worst_gap = max(worst_gap, a - b)
            out = merged
    assert worst_gap < 512, worst_gap
