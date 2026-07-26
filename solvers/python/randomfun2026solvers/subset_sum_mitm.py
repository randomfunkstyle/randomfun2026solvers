#!/usr/bin/env python3
"""`subset-sum` as a meet-in-the-middle ring machine — the design, and its price.

The two builds that came before this one both lose to the *shape of the search*,
not to the machine:

* `lm1/programs/subset-sum.asm` (commit 14cfc15) is correct on every case and
  ~34x over the tick cap on the worst one, because an LM-1 instruction is 46 ticks.
* :mod:`subset_sum_ring` prices the lex DFS on a 21-cell value ring honestly and
  says so in its own docstring: a 400-input adversarial sweep drawn strictly
  inside the stated constraints needs **714,549 lex-DFS iterations** where the
  worst public case needs 112,018.  It clears the seven public cases and is not a
  general solver.

Measured here (:func:`worst_case_nodes`, and the C sweep quoted in the module
tests), the plain lex DFS reaches **811,418 iterations / 5,595,315 ring
rotations** on inputs inside the constraints.  No per-iteration cost a real grid
can hit makes that fit 15,000,000 ticks.  So the search has to stop being
exponential, and that is what meet-in-the-middle buys.

## The algorithm

Split the indices at `hL`: left `0..hL-1`, right `hL..n-1`, with `hR = 8` fixed
and `hL = n - 8` (so `2 <= hL <= 12`).  Fixing the right half is what keeps the
grid free of runtime geometry: `2^hR`, `2^hR - 1` and ring `B`'s pipe length are
all literals, and `hL` is the only thing the machine derives from `n`.

1. **Enumerate every right-half subset sum once** into a set `B` of `2^hR`
   words.  Biased by one (`s + 1`), so `0` never appears and a `-1` sentinel
   terminates a scan by sign alone.
2. **Walk the left masks in lexicographic order** and stop at the first one whose
   residual is in `B`.  Take-before-skip on the original index visits index sets
   in exactly lex order, and with bit `k` of the mask meaning "index `k` is
   taken", lex order on the index tuple is **descending order of the mask read
   with index 0 as the most significant bit**.  A plain counter `C` walked down
   from `2^hL - 1` gives that order if the mask fed to the peel is
   ``bitrev_hL(C)`` — which is why :func:`bitrev` exists and why the values can
   stay in the ring in **forward** order, appended as they arrive.
3. **Recover the right half** the same way: walk right masks down from
   `2^hR - 1` and take the first whose sum equals the residual.

Every candidate pair is examined at most once, so the cost is bounded by
`2^hL * (2^hR + 1) = 2^20 + 2^12` element comparisons **whatever the input** —
:func:`walk` reports it, and :func:`worst_case_ops` is the proof that the bound
is reached, not merely respected.

## Why the mask has to be bit-reversed

With the values in the ring in index order, the peel walks index `0, 1, ...` and
`x` (turn on BP's low bit) reads the mask from the bottom.  So peel step `k` sees
index `k` and mask bit `k`: "bit `k` = index `k`".  Lex order over index tuples is
descending order of the mask *read with index 0 at the top*, i.e. descending
`bitrev(m)`.  Enumerating `C` downward and peeling `bitrev(C)` gives exactly that,
and costs `hL + 1` glyph-steps a lap.

The alternative — storing the values reversed so the counter can be peeled
directly — needs a **prepend** into a FIFO ring, which is `O(k)` per value with a
count that changes every step, i.e. a nested loop with two live counters against
one backpack.  The bit reversal is the cheaper of the two by a wide margin, and
it leaves the output phase able to emit in increasing index order from the same
forward ring.

## The ring layout

One value ring `V` of `n + 8` words, one lap per candidate mask::

    [ ONE, C, CR, G, v_0 .. v_(hL-1), MB, v_hL .. v_(n-1), MT, MR, RR ]

    ONE = 1                 the additive constant a decrement needs
    C   = left mask counter, walked down from 2^hL
    CR  = right mask counter, walked down from 2^hR
    G   = 2^hL              the guard bit that makes bitrev count leading zeros
    MB  = -1                boundary: ends the left peel, starts the right one
    MT  = -(t + 1)          terminator, and the target in the same word
    MR  = totalR + 2        the "residual still reachable" bound, pre-biased
    RR  = r + 1             the residual the winning lap left behind

`C` holds *one more* than the mask counter it stands for: a lap reads `C`,
subtracts one, sends the difference back and uses it as the counter.  So the
exhaustion test is the same subtraction (`C - 1 < 0`) — and, more usefully, the
lap that wins leaves the ring holding exactly the winning counter, which is what
lets the output phase recompute the mask from the ring instead of finding a
register to keep it in.  `CR` works the same way.

`G` sits after `CR` and not before it because both `C` and `CR` need `B = 1` when
they are read, and both need their own guard immediately afterwards: the left
lap takes `G` from the ring, the right lap takes its `2^hR` from a literal.

`MB` and `MT` are the only negative words, so both loop exits are one `X` on the
word as it comes out of the ring — no counter, no second backpack.  `MT` carries
`-(t+1)` because the peel ends there anyway: `A = -(t+1)`, `B = sum` and one `+`
then one `N` is `r + 1`, already in the biased form the scan wants.

`MR = totalR + 2` makes the reachability test one subtraction: `MR - (r+1)` is
`totalR + 1 - r`, strictly positive exactly when `r <= totalR`.

## The scan, and why it is 5 ticks a word

Ring `B` holds `2^hR` biased sums and one `-1` sentinel.  The scan station keeps
the query `r + 1` in `B` (the only register a receive does not clobber) and walks

    r  s  ~  X

`~` is XOR, which **does not touch B**, and both operands are non-negative, so
`b ^ q` is `0` exactly on a hit and `> 0` otherwise — while the sentinel `-1`
gives `~q < 0`.  One `X` therefore separates all three outcomes:

    A == 0   straight   FOUND
    A  > 0   clockwise  keep scanning
    A  < 0   ccw        end of pass, not found

Because the sentinel is re-sent before the branch, a failed pass leaves the ring
exactly where it started, so the next lap's scan needs no realignment.

Laid out as a two-column clockwise loop, five cells a side with `X` at the two
turning corners, one lap is 10 cells and carries **two** words: 5 ticks a word,
the same bound :mod:`dataflow_relay` measures for a `counted_ring`.

## The cost

:func:`walk` counts ring words moved and branch steps and turns them into ticks
with :data:`TICKS`.  The bound that matters is the product
`2^hL * (2^hR + 1)`, which is `2^20 + 2^12` for `n = 20` however adversarial the
input; at 5 ticks a word that is 5.25M, and the per-lap overhead adds ~1.8M.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "HR",
    "TICKS",
    "Walk",
    "bitrev",
    "brute_force",
    "expected_output",
    "public_cases",
    "solve",
    "split",
    "walk",
]

#: The right half is **always** this many indices, which is what lets the grid
#: hold `2^HR`, `2^HR - 1` and the whole of ring `B`'s geometry as compile-time
#: constants: the only quantity the machine computes from `n` is `hL = n - 8`.
#: `n >= 10` is a stated constraint, so `hL >= 2` always.
#:
#: Raising it trades grid area for laps: the search cost `2^hL * (2^hR + 1)` is
#: flat at `2^n`, but the per-lap overhead scales with `2^hL` and ring `B`'s
#: pipe length with `2^hR`.
HR = 8

#: Tick charges the grid actually pays, per elementary step.  ``word`` is one
#: ring word received and re-sent by a two-per-lap station; the rest are glyph
#: counts on the branch lanes.  Calibrated against the engine in the tests.
TICKS: dict[str, int] = {
    "word": 5,      # one ring word moved through a two-per-lap station
    "peel": 12,     # one value word: receive, re-send, sign test, bit test, add
    "bitrev": 8,    # one bit of the reversal loop
    "scan": 5,      # one word of ring B compared
    "lane": 6,      # a branch lane's routing
}


@dataclass
class Walk:
    """What one run of the machine costs, in the units the grid charges."""

    answer: list[int] | None = None       #: chosen indices, or None for "no subset"
    laps: int = 0                         #: value-ring laps (candidate masks tried)
    scans: int = 0                        #: words of ring B compared
    peels: int = 0                        #: value words peeled
    words: int = 0                        #: ring words moved for any other reason
    bits: int = 0                         #: bit-reversal steps
    lanes: int = 0                        #: branch lanes taken

    @property
    def ticks(self) -> int:
        return (
            TICKS["word"] * self.words
            + TICKS["peel"] * self.peels
            + TICKS["bitrev"] * self.bits
            + TICKS["scan"] * self.scans
            + TICKS["lane"] * self.lanes
        )


def split(n: int) -> tuple[int, int]:
    """`(hL, hR)`: how many indices go to the enumerated and the tabulated half."""
    return n - HR, HR


def bitrev(value: int, width: int) -> int:
    """Reverse the low `width` bits of `value`.

    The machine computes this with a guard bit: peeling `value + 2^width` from the
    bottom until the backpack empties runs exactly `width + 1` steps whatever the
    leading zeros, and yields `bitrev(value) * 2 + 1`, so one arithmetic shift
    right recovers the answer.
    """
    out = 0
    for _ in range(width):
        out = out * 2 + (value & 1)
        value >>= 1
    return out


def _guarded_bitrev(value: int, width: int) -> int:
    """The machine's own route to :func:`bitrev` — asserted equal to it."""
    g = value + (1 << width)
    out = 0
    while g:
        out = out * 2 + (g & 1)
        g >>= 1
    return out >> 1


def solve(values: list[int], target: int) -> list[int] | None:
    """Lex-smallest index list summing to `target`, or None."""
    return walk(values, target).answer


def walk(values: list[int], target: int) -> Walk:  # noqa: PLR0915 - one machine
    """Run the machine word for word, charging every ring word it moves."""
    n = len(values)
    hl, hr = split(n)
    total_r = sum(values[hl:])
    w = Walk()

    # ── phase 1: ring B, every right-half subset sum, biased by one ──────────
    bring = [1]
    for v in values[hl:]:
        w.words += 2 * len(bring)          # each word read, re-sent, and doubled
        bring = [x for pair in ((y, y + v) for y in bring) for x in pair]
    bset = bring                            # sentinel -1 lives past the end

    # ── phase 2: left masks, lexicographic, first hit wins ──────────────────
    lap_words = n + 9
    found_mask = None
    residual = None
    for counter in range(pow(2, hl) - 1, -1, -1):
        w.laps += 1
        assert _guarded_bitrev(counter, hl) == bitrev(counter, hl)
        mask = bitrev(counter, hl)
        w.bits += hl + 1
        w.words += lap_words - hl          # everything the lap moves but the peel
        w.peels += hl
        w.lanes += 3
        s = sum(values[k] for k in range(hl) if mask >> k & 1)
        r = target - s
        if r < 0 or r > total_r:
            continue                        # both tests are pure economy: a
                                            # negative or oversized residual can
                                            # never be in B anyway
        w.scans += 1                        # the sentinel word
        hit = False
        for b in bset:
            w.scans += 1
            if b == r + 1:
                hit = True
                break
        if hit:
            found_mask, residual = mask, r
            break
    if found_mask is None:
        w.lanes += 2
        return w

    # ── phase 3: right masks, lexicographic, first hit wins ─────────────────
    right_mask = None
    for counter in range(pow(2, hr) - 1, -1, -1):
        w.laps += 1
        mask = bitrev(counter, hr)
        w.bits += hr + 1
        w.words += lap_words - hr
        w.peels += hr
        w.lanes += 3
        if sum(values[hl + k] for k in range(hr) if mask >> k & 1) == residual:
            right_mask = mask
            break
    assert right_mask is not None, "phase 2 proved the residual is a right-half sum"

    # ── phase 4: count the bits, then emit in increasing index order ────────
    chosen = [k for k in range(hl) if found_mask >> k & 1]
    chosen += [hl + k for k in range(hr) if right_mask >> k & 1]
    w.words += 4 * lap_words
    w.bits += 2 * n
    w.answer = chosen
    return w


def expected_output(values: list[int], target: int) -> list[int]:
    """`k` then the k chosen values in index order, or a lone `0`."""
    idx = solve(values, target)
    if not idx:
        return [0]
    return [len(idx)] + [values[i] for i in idx]


def brute_force(values: list[int], target: int) -> list[int]:
    """Lex-smallest index set summing to `target`, by enumeration."""
    best: tuple[int, ...] | None = None
    n = len(values)
    for size in range(n + 1):
        for combo in itertools.combinations(range(n), size):
            if sum(values[i] for i in combo) == target and (best is None or combo < best):
                best = combo
    return list(best) if best is not None else []


def public_cases(repo: Path | None = None) -> list[tuple[str, list[int], int, list[int]]]:
    """`(name, values, target, expected)` for each public case, in file order."""
    root = repo or Path(__file__).resolve().parents[3]
    spec = json.loads((root / "tasks" / "problems" / "subset-sum.json").read_text())
    out = []
    for case in spec["publicTestData"]:
        rnd = case["rounds"][0]
        out.append((
            case["name"],
            [int(x) for x in rnd["in"][1:-1]],
            int(rnd["in"][-1]),
            [int(x) for x in rnd["out"]],
        ))
    return out


def worst_case_ops(n: int = 20) -> int:
    """The cost ceiling: `2^hL` laps, each scanning `2^hR + 1` words of ring B."""
    hl, hr = split(n)
    return pow(2, hl) * (pow(2, hr) + 1)


if __name__ == "__main__":
    print(f"{'case':32} {'laps':>7} {'scans':>9} {'peels':>7} {'ticks':>10}")
    for name, values, target, want in public_cases():
        w = walk(values, target)
        got = [0] if not w.answer else [len(w.answer)] + [values[i] for i in w.answer]
        assert got == want, f"{name}: {got} != {want}"
        print(f"{name:32} {w.laps:7d} {w.scans:9d} {w.peels:7d} {w.ticks:10d}")
    print(f"\nceiling at n=20: {worst_case_ops()} scan words "
          f"= {worst_case_ops() * TICKS['scan']} ticks")
