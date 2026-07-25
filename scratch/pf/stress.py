"""Synthetic boards the public cases do not reach: the stated 64-move limit,
an open field (every tie-break exercised), and a one-cell corridor.

Expected frames come from an independent reference implementation of the rules
(plain BFS + the up/right/down/left tie-break), not from the machine.
"""
from __future__ import annotations

import random
from collections import deque

from randomfun2026solvers import pathfinder_prog as pf

W = 16


def reference(board, rx, ry, flags):
    """The problem statement, implemented directly."""
    buf = [[7 if board[y * W + x] else 0 for x in range(W)] for y in range(W)]
    buf[ry][rx] = 10
    frames = [["".join("%x" % c for c in row) for row in buf]]
    for fx, fy in flags:
        buf[fy][fx] = 9
        dist = [[-1] * W for _ in range(W)]
        dist[fy][fx] = 0
        q = deque([(fx, fy)])
        while q:
            x, y = q.popleft()
            for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < W and 0 <= ny < W and not board[ny * W + nx] \
                        and dist[ny][nx] < 0:
                    dist[ny][nx] = dist[y][x] + 1
                    q.append((nx, ny))
        d = dist[ry][rx]
        assert d > 0, "flag must be reachable and different"
        for _ in range(d):
            for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                nx, ny = rx + dx, ry + dy
                if dist[ny][nx] == dist[ry][rx] - 1:
                    break
            buf[ry][rx] = 0
            rx, ry = nx, ny
            buf[ry][rx] = 10
            frames.append(["".join("%x" % c for c in row) for row in buf])
    return frames


def machine(board, rx, ry, flags):
    ins = [*board, rx, ry]
    for fx, fy in flags:
        ins += [fx, fy]
    m = pf.Machine(pf.build(), ins)
    return m, m.run()


def border_board(fill=0):
    b = [fill] * 256
    for i in range(W):
        b[i] = b[240 + i] = b[i * W] = b[i * W + 15] = 1
    return b


def serpentine():
    """A comb maze: the only route is a long snake, so paths run to the cap."""
    b = border_board(1)
    for y in range(1, 15, 2):          # open corridors on the odd rows
        for x in range(1, 15):
            b[y * W + x] = 0
    for i, y in enumerate(range(2, 14, 2)):   # one connector per even row,
        b[y * W + (14 if i % 2 == 0 else 1)] = 0   # alternating ends
    return b


def open_field():
    return border_board(0)


def pillars(seed):
    rng = random.Random(seed)
    b = border_board(0)
    for y in range(2, 14):
        for x in range(2, 14):
            if rng.random() < 0.22:
                b[y * W + x] = 1
    return b
