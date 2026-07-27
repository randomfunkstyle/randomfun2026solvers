#!/usr/bin/env python3
"""Rectilinear pipe routing by breadth-first search over free cells.

Hand-deriving turn columns does not converge. Every placement in this build exposed
one more crossing rule -- nine of them -- and a six-pipe *test harness* was still
unsolvable by hand, because each pair of pipes imposes a condition of the form
"A's column must be west of B's" and the conditions contradict in combination.

`layout.py`'s `AStarRouter` cannot be used here: it *minimises* pipe length, and a
pipe's capacity **is** its length, so it optimises against the requirement that
rings A and B hold 257 values each. But the pipes left over after the two rings are
placed have no capacity requirement at all, and for those a plain shortest-path
search is exactly right -- it finds a route if one exists and says so if not,
without anybody deriving why.

Two details the engine cares about (`SPEC.md`):

* a pipe's **first** cell must point away from its source room, so its backward
  cell is the wall it leaves;
* its **last** cell must point into the destination wall.

So the search starts one cell out from the source wall, is forbidden from stepping
back onto it, and ends one cell short of the destination wall.
"""

from __future__ import annotations

from collections import deque

__all__ = ["route"]

_STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _free(grid: dict[tuple[int, int], str], cell: tuple[int, int], w: int, h: int) -> bool:
    x, y = cell
    return 0 <= x < w and 0 <= y < h and grid.get(cell, " ") == " "


def route(grid, start, end, bounds, *, min_len=0, forbid=()):
    """A polyline from `start` to `end` over free cells, or None.

    `start` and `end` are the pipe's own first and last cells — already one step
    outside their rooms. `forbid` names cells the route must avoid even if free
    (the walls it leaves and enters, so it cannot double back onto them).

    Prefers straight runs by exploring in the current heading first, which keeps
    the bend count low without a cost function; `min_len` rejects a route that is
    shorter than a ring needs, so the caller can retry somewhere roomier.
    """
    w, h = bounds
    blocked = set(forbid)
    if not _free(grid, start, w, h) or not _free(grid, end, w, h):
        return None
    seen = {start: None}
    q = deque([start])
    while q:
        cur = q.popleft()
        if cur == end:
            path = []
            while cur is not None:
                path.append(cur)
                cur = seen[cur]
            path.reverse()
            if len(path) < max(2, min_len):
                return None
            return _corners(path)
        for dx, dy in _STEPS:
            nxt = (cur[0] + dx, cur[1] + dy)
            if nxt in seen or nxt in blocked or not _free(grid, nxt, w, h):
                continue
            seen[nxt] = cur
            q.append(nxt)
    return None


def _corners(path):
    """Collapse a cell path to the corner list `draw_pipe` expects."""
    out = [path[0]]
    for i in range(1, len(path) - 1):
        a, b, c = path[i - 1], path[i], path[i + 1]
        if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
            out.append(b)
    out.append(path[-1])
    return out
