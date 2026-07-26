"""The meet-in-the-middle search `subset-sum` needs, and the bound it respects.

The fast tier pins the two things that decide whether a grid built on this module
can exist at all: that the search is **correct** (lex-smallest, cross-checked
against enumeration), and that its cost is **bounded by the input size rather
than by the input**, which is exactly what the two earlier builds could not say.
"""

from __future__ import annotations

import itertools
import random

import pytest

from randomfun2026solvers.subset_sum_mitm import (
    HR,
    TICKS,
    bitrev,
    brute_force,
    expected_output,
    public_cases,
    solve,
    split,
    walk,
    worst_case_ops,
)

TICK_CAP = 15_000_000


def test_split_is_the_fixed_right_half() -> None:
    """Only `hL` varies, and the stated `n >= 10` keeps it at 2 or more."""
    for n in range(10, 21):
        hl, hr = split(n)
        assert hr == HR == 8
        assert hl == n - 8
        assert hl >= 2
        assert hl + hr == n


def test_bitrev_is_an_involution_and_orders_masks_lexicographically() -> None:
    """Descending counter + `bitrev` == lex order on the index tuples."""
    for width in (2, 5, 12):
        for value in (0, 1, 3, (1 << width) - 1):
            assert bitrev(bitrev(value, width), width) == value
    width = 6
    order = [
        tuple(k for k in range(width) if bitrev(c, width) >> k & 1)
        for c in range((1 << width) - 1, -1, -1)
    ]
    assert len(set(order)) == 1 << width, "every mask exactly once"
    # Two *solutions* can never stand in a prefix relation, because every value is
    # at least 1 and so no proper superset of a solution sums to the same target.
    # Outside that vacuous case, counting down visits index sets in lex order.
    for earlier, later in itertools.combinations(order, 2):
        if earlier[: len(later)] == later or later[: len(earlier)] == earlier:
            continue
        assert earlier < later, f"{earlier} was visited before {later}"


def test_guarded_bitrev_matches_the_plain_one() -> None:
    """The machine's guard-bit route runs `width + 1` steps whatever the zeros."""
    from randomfun2026solvers.subset_sum_mitm import _guarded_bitrev

    for width in (2, 3, 8, 12):
        for value in range(min(1 << width, 512)):
            assert _guarded_bitrev(value, width) == bitrev(value, width)


@pytest.mark.parametrize("case", public_cases(), ids=lambda c: c[0])
def test_public_cases(case: tuple[str, list[int], int, list[int]]) -> None:
    _name, values, target, want = case
    assert expected_output(values, target) == want


def test_lex_rule_prefers_the_smaller_index_even_when_longer() -> None:
    """`{1,2,4}` beats `{1,3}`: lex on the index tuple, not on the size."""
    pad = [99999] * 6                       # n >= 10 is a stated constraint
    # {1,3} and {2,4} both make 800; the smaller first index wins.
    assert solve([50000, 100, 200, 700, 600, 40000, *pad], 800) == [1, 3]
    # {1,2,4} and {1,3} both make 900; the longer set with the smaller second
    # index wins, which is the case the size-first reading gets wrong.
    values = [50000, 100, 200, 800, 600, 40000, *pad]
    assert solve(values, 900) == [1, 2, 4]
    assert brute_force(values, 900) == [1, 2, 4]


def test_no_solution_emits_a_lone_zero() -> None:
    values = [2] * 10 + [4] * 8            # every subset sum is even
    assert solve(values, 101) is None
    assert expected_output(values, 101) == [0]


def test_constraint_extremes() -> None:
    """`n`, `v` and `t` at both ends of the stated ranges."""
    assert solve([1] * 10, 101) is None            # t above the whole sum
    assert solve([99999] * 20, 99999 * 6) == list(range(6))
    assert solve([1] * 19 + [99999], 99999) == [19]
    assert solve([99999] + [1] * 19, 99999) == [0]


@pytest.mark.parametrize("seed", range(6))
def test_matches_enumeration_on_random_small_inputs(seed: int) -> None:
    """Independent oracle: every subset, lex-smallest by construction."""
    rng = random.Random(seed)
    for _ in range(25):
        n = rng.randint(10, 13)
        vmax = rng.choice([3, 12, 400, 99999])
        values = [rng.randint(1, vmax) for _ in range(n)]
        total = sum(values)
        lo, hi = max(101, total // 10), min(999999, total * 6 // 10)
        if lo >= hi:
            continue
        target = rng.randint(lo, hi)
        assert (solve(values, target) or []) == brute_force(values, target)


def test_the_answer_really_sums_to_the_target() -> None:
    """Cheap invariant that would catch a mask/index mix-up the oracle shares."""
    rng = random.Random(99)
    for _ in range(40):
        values = [rng.randint(1, 99999) for _ in range(20)]
        total = sum(values)
        target = rng.randint(max(101, total // 10), min(999999, total * 6 // 10))
        idx = solve(values, target)
        if idx is None:
            continue
        assert idx == sorted(set(idx))
        assert sum(values[i] for i in idx) == target


def test_cost_ceiling_is_input_independent_and_fits_the_cap() -> None:
    """The whole point of the rewrite: `2^hL * (2^hR + 1)` however adversarial.

    The plain lex DFS this replaces reaches 811,418 iterations and 5,595,315 ring
    rotations on inputs drawn strictly inside the constraints (C sweep, five
    value distributions x 3000 draws each), which no per-iteration cost a grid
    can hit fits into 15,000,000 ticks.
    """
    assert worst_case_ops(20) == (1 << 12) * ((1 << 8) + 1) == 1_052_672
    assert worst_case_ops(20) * TICKS["scan"] < TICK_CAP // 2


@pytest.mark.parametrize("seed", range(4))
def test_measured_cost_stays_under_the_cap_on_adversarial_inputs(seed: int) -> None:
    """Distributions chosen to defeat pruning: dense, coarse, near-equal, huge."""
    rng = random.Random(1000 + seed)
    draws = (
        lambda: rng.randint(1, 99999),
        lambda: rng.randint(90000, 99999),
        lambda: 1000 * rng.randint(1, 100),
        lambda: 50000 + rng.randint(0, 1),
    )
    draw = draws[seed]
    worst = 0
    for _ in range(30):
        values = [draw() for _ in range(20)]
        total = sum(values)
        target = rng.randint(max(101, total // 10), min(999999, total * 6 // 10))
        worst = max(worst, walk(values, target).ticks)
    assert worst < TICK_CAP, f"{worst} ticks on seed {seed}"
    assert worst < TICK_CAP // 2, "the design is meant to keep a 2x margin"


def test_walk_charges_every_scan_it_makes() -> None:
    """The tick model must not be able to under-report the dominant term."""
    values = [2] * 12 + [2] * 8            # every subset sum is even, t is odd
    w = walk(values, 21)
    assert w.answer is None
    assert w.laps == 1 << 12               # no early exit: the whole counter runs
    # A lap scans only when the residual is in range; every one that does pays for
    # the sentinel word as well, so scans is never a multiple of the ring alone.
    assert w.scans > 0
    assert w.ticks >= TICKS["scan"] * w.scans + TICKS["peel"] * w.peels


def test_brute_force_agrees_with_itself_on_the_lex_rule() -> None:
    """Guards the oracle: shorter prefix loses to a longer one that extends it."""
    combos = sorted(itertools.combinations(range(4), 2))
    assert combos[0] == (0, 1)
    assert brute_force([1, 2, 3, 4], 5) == [0, 3]
