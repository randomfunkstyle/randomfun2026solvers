#!/usr/bin/env python3
"""`sort-numbers` as a sorting network on a value ring.

The abstract model lives here so the network can be settled *before* any grid
exists.  A comparator network is exactly verifiable by the **0-1 principle**: it
sorts every input iff it sorts all 2^n zero-one inputs, so :func:`sorts_all_01`
is a total proof for n = 16 in 65,536 cheap checks.

Two networks are modelled:

``batcher_network``   Batcher odd-even merge -- 63 comparators, depth 10, but
                      its comparators span distances 8/4/2/1.  On a FIFO ring a
                      comparator at distance *d* costs *d* extra rotations, so
                      its 63 comparators cost far more ring traffic than the
                      120 adjacent ones below.  Kept only to be measured.

``transposition_network``  Odd-even transposition -- ``n`` passes of
                      ``floor(n/2)`` compare-exchanges, **every one adjacent**.
                      Adjacent means pop-two / push-two on a ring: no rotation
                      at all.  Fully data-independent, so the control flow is a
                      fixed counted loop with no branch on a comparison except
                      the swap itself.

Ring cost is what decides between them, so both are scored by
:func:`ring_traffic`, which counts the pipe operations a single man walking one
ring actually performs.
"""

from __future__ import annotations

__all__ = [
    "batcher_network",
    "transposition_network",
    "selection_network",
    "shrinking_network",
    "round_ticks",
    "model_score",
    "public_rounds",
    "apply_network",
    "sorts_all_01",
    "ring_traffic",
]

Comparator = tuple[int, int]


# ── the networks ─────────────────────────────────────────────────────────────
def transposition_network(n: int) -> list[Comparator]:
    """Odd-even transposition: `n` passes, every comparator on an adjacent pair."""
    net: list[Comparator] = []
    for p in range(n):
        for i in range(p % 2, n - 1, 2):
            net.append((i, i + 1))
    return net


def batcher_network(n: int) -> list[Comparator]:
    """Batcher's odd-even merge sort (Knuth 5.2.2 Algorithm M), for comparison."""
    net: list[Comparator] = []
    p = 1
    while p < n:
        k = p
        while k >= 1:
            for j in range(k % p, n - k, 2 * k):
                for i in range(min(k, n - j - k)):
                    if (i + j) // (2 * p) == (i + j + k) // (2 * p):
                        net.append((i + j, i + j + k))
            k //= 2
        p *= 2
    return net


# ── verification ─────────────────────────────────────────────────────────────
def apply_network(net: list[Comparator], values: list[int]) -> list[int]:
    out = list(values)
    for lo, hi in net:
        if out[lo] > out[hi]:
            out[lo], out[hi] = out[hi], out[lo]
    return out


def selection_network(n: int) -> list[Comparator]:
    """The *ring-adjacent* network: pass ``p`` is ``(p, p+1), (p, p+2), ... (p, n-1)``.

    Position ``p`` is not a ring slot -- it is the man's **carry register**, so
    every one of these comparators is between the carry and the ring head.  Like
    odd-even transposition it therefore needs zero rotations, but unlike it each
    comparator costs **one** pop and **one** push instead of two, and each pass
    is one element shorter than the last.  That is why it wins on a one-man ring
    (see :func:`ring_traffic`).
    """
    return [(p, i) for p in range(n - 1) for i in range(p + 1, n)]


def shrinking_network(n: int) -> list[Comparator]:
    """Alias kept for the report tables."""
    return selection_network(n)


def sorts_all_01(net: list[Comparator], n: int) -> bool:
    """The 0-1 principle: a comparator network sorts iff it sorts every 0/1 word."""
    for mask in range(1 << n):
        vals = [(mask >> i) & 1 for i in range(n)]
        out = apply_network(net, vals)
        if any(a > b for a, b in zip(out, out[1:], strict=False)):
            return False
    return True


# ── the cost model that picks the shape ──────────────────────────────────────
#
# Measured on the incumbent 25x25 ring machine (`sort-numbers_ring.man`):
# t(n) ticks for one round of n values is 99, 191, 307, ... 4289 for n = 1..16,
# i.e. ~15n^2 -- about **30 ticks per ring value-move**, because that machine's
# pass loop is a ~30-cell walk.  The tick cost of a ring machine is therefore
# (pipe ops) x (cells the man walks per pipe op), and the model below counts the
# first factor exactly.
GLYPHS_PER_MOVE = 6  # a tight `r - X` + `+ W s` lane, one value in and out


def ring_traffic(kind: str, n: int, workers: int = 1) -> int:
    """Pipe operations (pops + pushes) one *ring* performs to sort n values.

    ``kind`` is ``"transposition"`` or ``"selection"``.  ``workers`` divides the
    result: k comparators acting on disjoint pairs -- whether k pipelined rooms
    or k Y-split men chasing each other round one track -- retire k times the
    traffic per tick.  It never changes the *count*, only the wall clock, which
    is precisely why it is applied here and not inside the networks.
    """
    if kind == "transposition":
        # n passes; every pass is exactly one lap (n pops + n pushes), because
        # the odd passes spend their leftover slots rotating.
        ops = 2 * n * n
    elif kind == "selection":
        # pass p is one element shorter: (n-p) pops + (n-p) pushes, plus the
        # header word each pass reads back and rewrites.
        ops = sum(2 * (m + 1) for m in range(1, n + 1))
    else:
        raise ValueError(kind)
    return ops // max(workers, 1)


def round_ticks(kind: str, n: int, workers: int = 1, glyphs: int = GLYPHS_PER_MOVE) -> float:
    """Ticks for one round, load and emit included."""
    return ring_traffic(kind, n, workers) * glyphs / 2 + 2 * n * 3


def public_rounds() -> list[list[int]]:
    """Round lengths of the 7 public cases, in order."""
    return [[3, 4, 5], [1, 5, 4], [4, 7], [6, 2], [5, 8], [1, 16, 2], [16, 3, 12, 16]]


def model_score(kind: str, side: int, workers: int = 1) -> tuple[float, float]:
    """(avg_ticks, score) the model predicts for a `side` x `side` grid."""
    per_case = [
        sum(round_ticks(kind, n, workers) for n in rounds) for rounds in public_rounds()
    ]
    avg = sum(per_case) / len(per_case)
    return avg, side * side * avg
