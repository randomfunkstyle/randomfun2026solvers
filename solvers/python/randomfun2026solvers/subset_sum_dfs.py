#!/usr/bin/env python3
"""The lex-order DFS that `subset-sum` needs, and what it costs a ring machine.

`DATAFLOW-SURVEY.md` §4 argues that `subset-sum` is reachable as a bespoke grid and
prices it from two numbers — iterations and ring rotations — that lived only in a
throwaway script. This module is that arithmetic, as code, so the design rests on
something a test can check. It computes answers too, so it doubles as the reference
model for a grid that does not exist yet.

**The algorithm.** Take-before-skip on the original index visits index sets in
exactly lexicographic order, because every `v >= 1` means no proper superset of a
solution is a solution — so the first hit is the answer, and there is no candidate
enumeration and no separate output construction:

    loop:  if r == 0        -> SUCCESS, the marked cells are the answer
           if r > suf       -> backtrack (covers p == n, since suf[n] == 0)
           if v[p] <= r     -> take: mark p, link p -> q, q = p, r -= v[p]
           suf -= v[p]; p += 1
    back:  if nothing taken -> FAIL, emit 0
           jump to q+1, reading q's cell on the way: r += v[q], q = link(q), unmark

**Why a pipe ring can host the stack for free.** A pipe is FIFO, so it cannot be a
LIFO, and the deepest-taken position `q` has to be recoverable. Threading a linked
list through the ring cells costs *zero* extra rotations: the rotation that carries
the head from `p` round to `q+1` necessarily reads `q`'s cell on the way, which is
where `v[q]` and the link live.

**Why that jump is never a degenerate full lap.** The cost of the jump is
`((q - p) mod L) + 1`, which is `L` — a whole wasted lap — exactly when `p == q+1`,
i.e. when a backtrack immediately follows a take. That cannot happen: a take
decreases `r` and `suf` by the same `v[p]`, so `suf - r` is *invariant* across a
take, and a take only ran because `r <= suf` held. `assert_no_backtrack_after_take`
checks it, and :func:`dfs` asserts it inline on every case.

`suf` is carried incrementally rather than stored: `suf[p+1] = suf[p] - v[p]`, and
giving the sentinel cell the value `-Total` makes `suf <- suf - v` correct across
the wrap too, because the cell values then sum to zero around the ring and `suf`
becomes a well-defined function of ring position.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Walk", "brute_force", "dfs", "expected_output", "public_cases"]


@dataclass
class Walk:
    """What one run of the DFS cost the machine that would execute it."""

    answer: list[int] | None          #: taken indices, or None for "no subset sums to t"
    iterations: int = 0
    rotations: int = 0
    kinds: dict[str, int] = field(default_factory=lambda: {"take": 0, "skip": 0, "back": 0})

    @property
    def rot_per_iter(self) -> float:
        return self.rotations / self.iterations if self.iterations else 0.0

    def ticks(self, a: float, b: float) -> float:
        """§4.4's model: `a` ticks of station logic per iteration, `b` per rotation.

        The two extra ring ops per iteration are §4.4's state-header traffic; on the
        corrected costing of §4.4 they are ordinary ring ops and cost `b` like any
        other.
        """
        return a * self.iterations + b * (self.rotations + 2 * self.iterations)


def dfs(values: list[int], target: int) -> Walk:
    """Run the lex DFS, counting what a ring machine would pay for it.

    The ring has `L = n+1` cells: cell `j < n` holds `v[j]`, cell `n` is the
    sentinel holding `-Total`. The head sits at `p`. A forward step reads cell `p`
    and re-sends it: one rotation. A backtrack carries the head from `p` round to
    `q+1`, reading `q` on the way: `((q - p) mod L) + 1` rotations.
    """
    n = len(values)
    lim = n + 1
    total = sum(values)
    link = [-1] * n
    mark = [0] * n
    p, r, q, suf = 0, target, -1, total
    w = Walk(answer=None)
    while True:
        w.iterations += 1
        if r == 0:
            w.answer = [i for i in range(n) if mark[i]]
            return w
        if r > suf:
            if q < 0:
                w.answer = None
                return w
            assert p != q + 1, "a backtrack cannot follow a take: suf - r survives one"
            w.kinds["back"] += 1
            steps = ((q - p) % lim) + 1
            w.rotations += steps
            pos = p
            for _ in range(steps):
                suf -= -total if pos == n else values[pos]
                pos = (pos + 1) % lim
            assert pos == (q + 1) % lim
            r += values[q]
            mark[q] = 0
            p, q = q + 1, link[q]
            continue
        assert p < n, "suf[n] == 0 < r, so the sentinel always prunes"
        w.rotations += 1
        if values[p] <= r:
            w.kinds["take"] += 1
            mark[p] = 1
            link[p] = q
            q = p
            r -= values[p]
        else:
            w.kinds["skip"] += 1
        suf -= values[p]
        p += 1


def brute_force(values: list[int], target: int) -> list[int]:
    """Lex-smallest index set summing to `target`, by enumeration.

    Lex order on the sorted index tuple is exactly the problem's rule, prefix
    included: `(0,) < (0, 1)` makes the shorter set win, which is what "the set
    `0, 4` beats the set `1, 3`" generalises to. Independent of :func:`dfs` — the
    point is to disagree if either is wrong.
    """
    best: tuple[int, ...] | None = None
    n = len(values)
    for size in range(n + 1):
        for combo in itertools.combinations(range(n), size):
            if sum(values[i] for i in combo) == target and (best is None or combo < best):
                best = combo
    return list(best) if best is not None else []


def expected_output(values: list[int], target: int) -> list[int]:
    """`k` then the k chosen values in index order, or a lone `0`."""
    idx = dfs(values, target).answer
    if not idx:
        return [0]
    return [len(idx)] + [values[i] for i in idx]


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


if __name__ == "__main__":
    print(f"{'case':32} {'iters':>8} {'rot':>9} {'rot/it':>7} "
          f"{'take':>7} {'skip':>7} {'back':>7}")
    tot_i = tot_r = 0
    for name, values, target, want in public_cases():
        w = dfs(values, target)
        got = [0] if not w.answer else [len(w.answer)] + [values[i] for i in w.answer]
        assert got == want, f"{name}: {got} != {want}"
        print(f"{name:32} {w.iterations:8d} {w.rotations:9d} {w.rot_per_iter:7.2f} "
              f"{w.kinds['take']:7d} {w.kinds['skip']:7d} {w.kinds['back']:7d}")
        tot_i += w.iterations
        tot_r += w.rotations
    print(f"{'TOTAL':32} {tot_i:8d} {tot_r:9d} {tot_r / tot_i:7.2f}")
