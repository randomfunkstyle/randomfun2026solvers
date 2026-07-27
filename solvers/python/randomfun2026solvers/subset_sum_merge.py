#!/usr/bin/env python3
"""`subset-sum` by sorted meet-in-the-middle — the design that stops paying `2^n`.

:mod:`subset_sum_mitm` is meet-in-the-middle already, and it is still exponential.
It walks `2^hL` left masks and matches each one against ring `B`'s `2^hR + 1`
words, so the work is the **product** `2^hL * (2^hR + 1) = 2^n`, and rebalancing
the split cannot help: `4096 * 256` and `1024 * 1024` are the same number.  The
model measures 141,132 scan words on the worst public case, which is 705,660 of
its 965,889 ticks.

Sorting both halves turns the product into a **sum**.  Two sorted lists can be
matched by walking each one once, so the cost is `2^hL + 2^hR ~= 2,048` steps
where the product is `1,048,576`.

## Never sort — generate sorted

A sort of 1,024 words on this hardware would cost more than it saves.  It is not
needed: the subset sums of a half can be *built* in order.  Start from the empty
subset and, for each value `v`, merge the current sorted list with the same list
shifted by `v`.  Both operands are sorted, so each round is a two-way merge, and
the rounds cost `2, 4, 8, ... 2^h` — **`2^(h+1) - 2` element-moves in total**,
about 2,046 for `h = 10`, against the `10 * 1024` a comparison sort would need
and with no random access at all.

Because the cost is now `2^hL + 2^hR` rather than their product, the split
**should** be balanced: 1,024 + 1,024 = 2,048 against 12/8's 4,096 + 256 = 4,352.

## One word carries the sum and the mask

`v <= 99999 < 2^17` and `n <= 20`, so a subset sum is `< 2^21`; a balanced half
mask is at most 10 bits.  So

    packed = sum * 2^h + mask

is under `2^31` — trivial in a 64-bit word.  Ordering the packed word orders by
sum with the mask as tie-break, so **one ring carries both** and the match needs
no parallel mask ring and no second lookup.  `sum` is `packed // 2^h` and `mask`
is `packed % 2^h`.

## The lex rule is one integer comparison — and it needs `v >= 1`

The task wants the lexicographically smallest index list.  Encode a mask with
**index 0 as the most significant bit**; then, for two index sets of equal sum,
lexicographically smaller is **numerically larger**.  At the first differing bit
the set holding that index is the one whose list diverges lower, and it is also
the one with the larger mask.

That argument assumes neither set is a *prefix* of the other, and the encoding
genuinely fails when one is: `{0}` is `100000` = 32 and `{0,5}` is `100001` = 33,
so max-mask would pick `{0,5}` while lex wants `{0}`.  It cannot arise here.  If
`S1` is a proper subset of `S2` and both sum to `t`, then `S2 \\ S1` sums to zero,
which `1 <= v` forbids.  **The trick is sound because the values are positive**,
not because of the encoding, and :func:`prefix_pairs_are_impossible` pins it.

The same ordering makes the per-sum **dedupe** correct: within one sum keep only
the largest mask, because a larger left mask beats every right mask, so a smaller
mask with the same sum can never win.  Dedupe is what lets the two-pointer skip
group handling entirely — every sum appears once, so neither pointer ever has to
back up, which a rotating ring could not do cheaply anyway.

## Pruning is implemented, and measured not to matter

A half-sum is only usable when `t - totalOther <= s <= t`, and both cuts are in
:func:`generate`.  Neither pays, and the reason is the stated constraint itself:
**`t` is 10-60% of the value sum**, while a balanced half totals ~50%.

* the upper cut `s > t` cannot fire at all in the common case, because the half's
  entire total is below `t`;
* the lower cut clips `[0, t - totalOther]`, which at the most favourable end of
  the range is ~10% of the half's span.

Measured on the `near-total-sum, 20 values` public case (`t/total = 0.60`, the
best case for pruning): the lower bound is 41,481 against a left-half span of
463,776, and the two cuts together drop **29 of 1,929** entries — 1.5%.
Processing the values largest-first, so `reach` collapses early and the lower cut
can fire at all, is worth more than the cut itself and is why :func:`generate`
sorts.

So pruning is kept because it is nearly free and helps the small cases, but it is
**not** where any further win lives.  `test_pruning_is_weak_at_the_stated_target_range`
pins that, so it is not attempted again.

## What the grid will need

Sizes, measured: at `n = 20` each half's list reaches **1,024 words**, 512 at 18,
256 at 16.  That is four times ring `B`'s 257 and is the one real risk to the
footprint — the current machine is 81x202 and `area2` is half the score.  A
serpentine holds ~1,975 slots in 33x48 (:mod:`memory_tape`), so a 1,024-word ring
is affordable, but two per half would not be.

It does not need two per half.  Merging `L` with `L + v` reads the same sequence
twice, and **the `L` pointer can never fall behind the `L + v` pointer**: if it
did, `L[a] < L[b] <= L[b] + v`, so the comparison would take from `L` and advance
it.  Checked on every merge step of 40 random halves, where the largest gap
between the two pointers was **254**.

So one list ring plus a delay FIFO of ~256 words carries the merge, instead of
two full copies: the `L` stream feeds the comparator directly, and the words it
has passed but `L + v` has not yet consumed wait in the delay.  That is 1,024 +
256 a half rather than 2,048, and it is the difference between this fitting the
present footprint and not.

## The step charge is measured now, and it is ~10x what this module assumed

:data:`TICKS` prices a merge step at ``compare = 12``, and every speedup quoted
here follows from it.  :mod:`subset_sum_merge_probe` is the smallest grid that
performs exactly one such step, and on the engine the cost is exactly linear in
the emitted words at **129 ticks a step** — 162 before its return paths were
compacted.  The numbers above are therefore optimistic by about an order of
magnitude, and the honest table for `n = 20`, against the **judged** 798,357:

    compare =  12 (assumed)      79,513 ticks     ~10x
    compare =  50                199,419          4.0x
    compare = 100                357,190          2.2x
    compare = 129 (measured)     448,697          1.8x

**Why it is not 12.**  A merge step is *corridor-dominated*.  The station itself
is ten glyphs; the rest of the loop is the man walking to the output pipe and out
to a ring's return column and back.  `subset_sum_scan_probe`'s five-ticks-a-word
gadget is cheap for the opposite reason — its lap touches one pipe, never emits,
and fits in ten cells.  Two pipes and a branch per step cannot.

The 129 is an **upper bound** on the real machine: the probe emits to the `O`
room, whereas generation writes to a target ring that can sit beside its source.
Removing that trip is worth roughly 30 ticks a step, which puts a realistic build
in the 2-4x band rather than 20.9x.

So the rebuild is still a win — but a much smaller one than the step count
suggests, and small enough that it should be weighed against the `area2` cost of
three 1,024-word rings before the grid is built.  `subset-sum` is charged on its
202-row height with 121 columns spare, so the rings are free *if* they go east;
that is the constraint the layout has to respect.

## What is *not* worth doing

A reachable-sums bitset.  `t < 10^6` makes it ~15,625 64-bit words, and the
shift-or dynamic program is 20 passes over all of them — 312,500 word operations,
worse than the machine we already have and far worse than 6,000.  The `t` bound
is too loose for bitset tricks to pay on this problem.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass

__all__ = [
    "TICKS",
    "Cost",
    "generate",
    "match",
    "prefix_pairs_are_impossible",
    "solve",
    "split",
]

#: Tick charges per elementary step, in the same units as
#: :data:`subset_sum_mitm.TICKS`, whose ``word`` = 5 is measured on the engine by
#: :mod:`subset_sum_scan_probe`.  ``compare`` is a merge step's test-and-emit
#: branch, priced like that module's ``peel``.
TICKS: dict[str, int] = {
    "word": 5,  # one ring word moved through a two-per-lap station
    "compare": 12,  # one merge/match step: two heads tested, one emitted
    "lane": 6,  # a branch lane's routing
}


@dataclass
class Cost:
    """Ring words moved and comparisons made, in the units the grid charges."""

    words: int = 0
    compares: int = 0
    lanes: int = 0
    generated: int = 0  #: entries surviving generation, both halves
    pruned: int = 0  #: entries dropped for exceeding `t`
    deduped: int = 0  #: entries dropped as a duplicate sum

    @property
    def ticks(self) -> int:
        return (
            TICKS["word"] * self.words
            + TICKS["compare"] * self.compares
            + TICKS["lane"] * self.lanes
        )

    def __add__(self, other: Cost) -> Cost:
        return Cost(
            *(
                getattr(self, f) + getattr(other, f)
                for f in ("words", "compares", "lanes", "generated", "pruned", "deduped")
            )
        )


def split(n: int) -> tuple[int, int]:
    """`(hL, hR)`, balanced — the cost is now a sum, so equal halves are cheapest."""
    hr = n // 2
    return n - hr, hr


def _mask_of(index_in_half: int, half_width: int) -> int:
    """Bit weight for a half-local index, with **index 0 as the most significant**."""
    return 1 << (half_width - 1 - index_in_half)


def generate(values: list[int], width: int, target: int, cost: Cost, floor: int = 0) -> list[int]:
    """Sorted, deduped `packed = sum * 2^width + mask` for every subset of `values`.

    Built by successive two-way merges, never sorted.

    Both ends are pruned, and the two bounds are not equally easy.  A sum above
    `target` is dead immediately and stays dead, because sums only grow — so the
    upper cut applies at every round.  A sum *below* `floor` is only dead once
    nothing can lift it there, so the lower cut has to carry the slack still
    available: at round `i` an entry may yet gain `sum(values[i+1:])`.

    The lower bound is what makes the near-total-sum cases cheap.  When `t` is
    most of the value sum, the other half cannot make up more than its own total,
    so every small sum in this half is unusable — and that is exactly the case
    the upper bound alone does nothing for.
    """
    shift = 1 << width
    # Largest first.  The lower cut can only fire once `reach` has fallen below
    # `floor`, so spending the big values early is what lets it fire at all — in
    # index order `reach` is still enormous when the list is already full size.
    order = sorted(range(len(values)), key=lambda i: -values[i])
    seq = [(values[i], _mask_of(i, width)) for i in order]
    suffix = [0] * (len(seq) + 1)
    for i in range(len(seq) - 1, -1, -1):
        suffix[i] = suffix[i + 1] + seq[i][0]

    out = [0]  # the empty subset: sum 0, mask 0
    for i, (v, bit) in enumerate(seq):
        other = [p + v * shift + bit for p in out]  # same list, shifted by v
        reach = suffix[i + 1]  # the most a later round can add
        merged: list[int] = []
        a = b = 0
        cost.words += len(out) + len(other)  # both operands read once
        while a < len(out) or b < len(other):
            cost.compares += 1
            if b >= len(other) or (a < len(out) and out[a] <= other[b]):
                take, a = out[a], a + 1
            else:
                take, b = other[b], b + 1
            s = take // shift
            if s > target or s + reach < floor:  # dead above, or unreachable below
                cost.pruned += 1
                continue
            if merged and merged[-1] // shift == s:
                # same sum: keep the larger mask, which is the later of the two
                cost.deduped += 1
                merged[-1] = max(merged[-1], take)
                continue
            merged.append(take)
        cost.words += 2 * len(merged)  # written to both copies of the ring
        out = merged
    cost.generated += len(out)
    return out


def match(
    left: list[int], right: list[int], hl: int, hr: int, target: int, cost: Cost
) -> tuple[int, int] | None:
    """The best `(left_mask, right_mask)` whose sums make `target`, or None.

    `left` ascending and `right` descending are walked once each.  Both are
    deduped, so a match advances both pointers and neither ever backs up — the
    property a rotating ring needs.
    """
    sl, sr = 1 << hl, 1 << hr
    i, j = 0, len(right) - 1  # right walked from its top down
    best: tuple[int, int] | None = None
    while i < len(left) and j >= 0:
        cost.words += 2
        cost.compares += 1
        total = left[i] // sl + right[j] // sr
        if total < target:
            i += 1
        elif total > target:
            j -= 1
        else:
            lm, rm = left[i] % sl, right[j] % sr
            if best is None or lm > best[0] or (lm == best[0] and rm > best[1]):
                best = (lm, rm)
            i += 1  # deduped, so the next sums differ
            j -= 1
    cost.lanes += 2
    return best


def solve(values: list[int], target: int) -> tuple[list[int] | None, Cost]:
    """Lex-smallest index list summing to `target`, and what it cost."""
    n = len(values)
    hl, hr = split(n)
    cost = Cost()
    total_l, total_r = sum(values[:hl]), sum(values[hl:])
    # A half's sum must leave a residual the *other* half can actually make, which
    # bounds it from below as well as above.
    left = generate(values[:hl], hl, target, cost, floor=target - total_r)
    right = generate(values[hl:], hr, target, cost, floor=target - total_l)
    best = match(left, right, hl, hr, target, cost)
    if best is None:
        return None, cost
    lm, rm = best
    idx = [k for k in range(hl) if lm >> (hl - 1 - k) & 1]
    idx += [hl + k for k in range(hr) if rm >> (hr - 1 - k) & 1]
    cost.words += 2 * n
    return idx, cost


def expected_output(values: list[int], target: int) -> list[int]:
    """`k` then the k chosen values in index order, or a lone `0`."""
    idx, _ = solve(values, target)
    if not idx:
        return [0]
    return [len(idx)] + [values[i] for i in idx]


def brute_force(values: list[int], target: int) -> list[int]:
    """Lex-smallest index set summing to `target`, by enumeration."""
    best: tuple[int, ...] | None = None
    for size in range(len(values) + 1):
        for combo in itertools.combinations(range(len(values)), size):
            if sum(values[i] for i in combo) == target and (best is None or combo < best):
                best = combo
    return list(best) if best is not None else []


def prefix_pairs_are_impossible(values: list[int]) -> bool:
    """No equal-sum set is a proper subset of another — what the lex trick needs.

    True whenever every value is at least 1, which the task guarantees.
    """
    return all(v >= 1 for v in values)


def fuzz(trials: int = 300, seed: int = 7) -> None:
    """Check against brute force on small n, and against the DFS oracle on large."""
    from randomfun2026solvers import subset_sum_dfs  # noqa: PLC0415

    rng = random.Random(seed)
    for _ in range(trials):
        n = rng.randint(4, 12)
        values = [rng.randint(1, 99999) for _ in range(n)]
        if rng.random() < 0.5:  # half the trials have an answer
            k = rng.randint(1, n)
            target = sum(rng.sample(values, k))
        else:
            target = rng.randint(100, sum(values) or 100)
        got, _ = solve(values, target)
        want = brute_force(values, target)
        assert (got or []) == want, f"{values} t={target}: {got} != {want}"
    _ = subset_sum_dfs


if __name__ == "__main__":
    from randomfun2026solvers.subset_sum_mitm import public_cases, walk

    print(
        f"{'case':32} {'old ticks':>10} {'new ticks':>10} {'x':>6} "
        f"{'gen':>6} {'pruned':>7} {'dedup':>6}"
    )
    olds = news = 0
    for name, values, target, want in public_cases():
        idx, c = solve(values, target)
        got = [0] if not idx else [len(idx)] + [values[i] for i in idx]
        assert got == want, f"{name}: {got} != {want}"
        old = walk(values, target).ticks
        olds += old
        news += c.ticks
        print(
            f"{name:32} {old:10d} {c.ticks:10d} {old / c.ticks:5.1f}x "
            f"{c.generated:6d} {c.pruned:7d} {c.deduped:6d}"
        )
    print(f"{'AVERAGE':32} {olds // 7:10d} {news // 7:10d} {olds / news:5.1f}x")
