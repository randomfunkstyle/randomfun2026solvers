"""Boards the seven public cases never reach.

AGENTS.md's rule about not sizing to the public cases applies to *behaviour* as
well as to capacities: `snake` ships 5 public cases and the server ran 17.  So
these drive the op-level machine against an independent implementation of the
problem statement (plain BFS plus the up/right/down/left tie-break) on

* a comb maze whose shortest paths run to 103 moves -- well past the stated
  64-move cap, so the machine is shown to have no length-dependent capacity;
* an open field, where every cell has four legal neighbours and so every arm of
  the tie-break is exercised on every step;
* eight random pillar fields with random reachable flags.

The measured pipe high-water marks are asserted to be *invariant* across all of
them: the ring is 18 words, the spill 6 and the scratch 7 whatever the board,
which is what lets the physical loops be sized once.
"""

from __future__ import annotations

import random
from collections import deque

import pytest
from randomfun2026solvers import pathfinder_prog as pf

W = 16
DIRS = ((0, -1), (1, 0), (0, 1), (-1, 0))  # up, right, down, left


def reference(board, rx, ry, flags):
    """The problem statement, implemented directly."""
    buf = [[7 if board[y * W + x] else 0 for x in range(W)] for y in range(W)]
    buf[ry][rx] = 10
    frames = [["".join(f"{c:x}" for c in row) for row in buf]]
    for fx, fy in flags:
        buf[fy][fx] = 9
        dist = _dists(board, fx, fy)
        d = dist[(rx, ry)]
        assert d > 0, "the flag must be reachable and different from the robot"
        for _ in range(d):
            nx, ny = next(
                (rx + dx, ry + dy)
                for dx, dy in DIRS
                if dist.get((rx + dx, ry + dy), -1) == dist[(rx, ry)] - 1
            )
            buf[ry][rx] = 0
            rx, ry = nx, ny
            buf[ry][rx] = 10
            frames.append(["".join(f"{c:x}" for c in row) for row in buf])
    return frames


def _dists(board, sx, sy):
    d = {(sx, sy): 0}
    q = deque([(sx, sy)])
    while q:
        x, y = q.popleft()
        for dx, dy in DIRS:
            n = (x + dx, y + dy)
            if 0 <= n[0] < W and 0 <= n[1] < W and not board[n[1] * W + n[0]] and n not in d:
                d[n] = d[(x, y)] + 1
                q.append(n)
    return d


def _border(fill=0):
    b = [fill] * 256
    for i in range(W):
        b[i] = b[240 + i] = b[i * W] = b[i * W + 15] = 1
    return b


def comb():
    """Odd rows are corridors, joined by one connector per even row."""
    b = _border(1)
    for y in range(1, 15, 2):
        for x in range(1, 15):
            b[y * W + x] = 0
    for i, y in enumerate(range(2, 14, 2)):
        b[y * W + (14 if i % 2 == 0 else 1)] = 0
    return b


def pillars(seed):
    rng = random.Random(seed)
    b = _border(0)
    for y in range(2, 14):
        for x in range(2, 14):
            if rng.random() < 0.22:
                b[y * W + x] = 1
    return b


def _boards():
    yield "comb", comb(), [(5, 9), (14, 1), (1, 13), (14, 13)]
    yield "open field", _border(0), [(14, 14), (1, 14), (14, 1), (8, 8), (1, 2)]
    rng = random.Random(7)
    for seed in range(1, 9):
        b = pillars(seed)
        reach = [k for k, v in _dists(b, 1, 1).items() if v > 0]
        yield f"pillars {seed}", b, rng.sample(reach, min(6, len(reach)))


BOARDS = list(_boards())


@pytest.mark.parametrize(("name", "board", "flags"), BOARDS, ids=[b[0] for b in BOARDS])
def test_matches_the_statement(name, board, flags):
    ins = [*board, 1, 1]
    for fx, fy in flags:
        ins += [fx, fy]
    m = pf.Machine(pf.build(), ins)
    assert m.run() == reference(board, 1, 1, flags)
    assert (m.maxring, m.maxfifo, m.maxscr) == (pf.RING_WORDS, pf.FIFO_WORDS, pf.SCRATCH_WORDS)


def test_comb_exceeds_the_stated_move_cap():
    """The machine must not depend on the 64-move guarantee."""
    assert max(_dists(comb(), 1, 1).values()) > 64
