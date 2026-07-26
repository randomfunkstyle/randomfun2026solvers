"""`subset-sum`'s design arithmetic, as assertions rather than as a paragraph.

`DATAFLOW-SURVEY.md` §4 is the case that `subset-sum` — the one graded problem the
repo does not solve at all — is reachable as a bespoke grid. The whole case rests on
two counts per public case (iterations and ring rotations), and on a claim about the
linked-list stack being free. None of it was checked anywhere, and the script that
produced it is not in the repo.

So this module pins it: the walk answers every public case, agrees with brute force
on small inputs, reproduces §4.4's table to the rotation, and the invariant that
makes the stack free is asserted rather than argued.

No engine here on purpose — this is the *model*, and it is the thing a future grid
gets checked against.
"""

from __future__ import annotations

import itertools
import random
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.subset_sum_dfs import (  # noqa: E402
    brute_force,
    dfs,
    expected_output,
    public_cases,
)

#: §4.4's table: `case name -> (iterations, rotations)`.  Pinned exactly — these are
#: the numbers the design is costed on, so a change is either a better formulation
#: worth recording or a bug worth failing on.
SURVEY_44 = {
    "tiny warm up": (93, 285),
    "multiple solutions, lex pin": (3, 2),
    "no solution": (2653, 12293),
    "single-element subset": (2, 1),
    "last-index-required": (6143, 26623),
    "duplicate values": (3, 2),
    "near-total-sum, 20 values": (112018, 804803),
}


# ── the walk is correct ───────────────────────────────────────────────────────
@pytest.mark.parametrize("name", list(SURVEY_44))
def test_every_public_case_gets_the_published_answer(name: str) -> None:
    values, target, want = next((v, t, w) for n, v, t, w in public_cases() if n == name)
    assert expected_output(values, target) == want


def test_the_walk_agrees_with_brute_force_exhaustively_on_small_inputs() -> None:
    """All 4- and 5-value lists over 1..5, at every reachable target.

    Exhaustive rather than sampled because the tie-break is the whole difficulty of
    this problem: lex-smallest over *indices* is not lex-smallest over values, and a
    sampled test would miss the cases where several subsets hit the target.
    """
    for n in (4, 5):
        for values in itertools.product(range(1, 6), repeat=n):
            vals = list(values)
            for target in range(1, sum(vals) + 1):
                assert dfs(vals, target).answer == (brute_force(vals, target) or None), (
                    vals,
                    target,
                )


def test_the_walk_agrees_with_brute_force_on_random_wide_value_inputs() -> None:
    """Values spread over the constraint range, where `suf` pruning actually bites."""
    rng = random.Random(20260725)
    for _ in range(300):
        n = rng.randint(10, 14)
        vals = [rng.randint(1, 99999) for _ in range(n)]
        # a target that is reachable half the time, per the problem's 10-60% note
        target = rng.randint(1, sum(vals) * 6 // 10)
        assert dfs(vals, target).answer == (brute_force(vals, target) or None), (vals, target)


def test_the_constraint_corners() -> None:
    """`v = 1`, `v = 99999`, target equal to the whole sum, and target unreachable."""
    assert dfs([1] * 10, 5).answer == [0, 1, 2, 3, 4]
    assert dfs([99999] * 10, 99999 * 10).answer == list(range(10))
    assert dfs([99999] * 10, 99999 * 10 + 1).answer is None
    assert dfs([2] * 10, 5).answer is None  # parity makes it unreachable
    assert dfs([1, 99999] * 5, 99999).answer == [1]


# ── the arithmetic the design is costed on ────────────────────────────────────
@pytest.mark.parametrize("name", list(SURVEY_44))
def test_the_survey_rotation_counts_reproduce(name: str) -> None:
    values, target, _ = next((v, t, w) for n, v, t, w in public_cases() if n == name)
    w = dfs(values, target)
    assert (w.iterations, w.rotations) == SURVEY_44[name]


def test_the_totals_are_what_the_survey_quotes() -> None:
    walks = [dfs(v, t) for _, v, t, _ in public_cases()]
    assert sum(w.iterations for w in walks) == 120_915
    assert sum(w.rotations for w in walks) == 844_009


def test_a_backtrack_never_follows_a_take() -> None:
    """The invariant that keeps the linked-list stack free of a wasted lap.

    The jump from `p` to `q+1` costs `((q - p) mod L) + 1`, which degenerates to a
    whole lap `L` exactly when `p == q+1`. It never happens, because a take
    decreases `r` and `suf` by the same `v[p]` — so `suf - r` is invariant across a
    take, and the take only ran because `r <= suf` held. :func:`dfs` asserts this
    inline, so every walk in this module is already a witness; this states it.
    """
    rng = random.Random(1)
    for _ in range(200):
        vals = [rng.randint(1, 40) for _ in range(rng.randint(8, 12))]
        dfs(vals, rng.randint(1, sum(vals)))  # the inline assert is the test
    for _, values, target, _ in public_cases():
        dfs(values, target)


def test_the_corrected_budget_is_what_the_survey_now_claims() -> None:
    """§4.4 as corrected: b = 6.0 is the shipped relay, b = 3.2 needs the fat one.

    The worst public case is the only one that matters — the other six are under
    27k rotations — and the point of pinning it is that the margin is ~1.2-1.5x,
    not the 2x first written down.
    """
    worst = max((dfs(v, t) for _, v, t, _ in public_cases()), key=lambda w: w.rotations)
    cap = 15_000_000
    # value_ring.RELAY, measured at 6.0 ticks/rotation
    assert worst.ticks(a=60, b=6.0) == pytest.approx(12_894_114)
    assert worst.ticks(a=60, b=6.0) / cap == pytest.approx(0.860, abs=5e-4)
    assert worst.ticks(a=80, b=6.0) > cap, "at b=6.0 the design needs a <= 79"
    # dataflow_relay.relay(6, 4) behind an m=3 worker loop, measured at 3.2
    assert worst.ticks(a=60, b=3.2) / cap == pytest.approx(0.668, abs=5e-4)
    assert worst.ticks(a=80, b=3.2) / cap == pytest.approx(0.817, abs=5e-4)
    assert worst.ticks(a=105, b=3.2) > cap, "even on the fat relay, a <= 104"
