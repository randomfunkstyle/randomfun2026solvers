#!/usr/bin/env python3
"""Routing: what a walk actually costs, and the floor no layout can beat.

Manhattan distance is the right cost for an **open, monotone** leg -- that is
measured, the man walks the full distance and takes no shortcuts.  It is the
wrong cost for anything that comes back, and almost every worker on this machine
comes back: it receives, works, answers, and returns to receive again.  A model
that prices the return leg as Manhattan distance from the send to the receive
will report a floor that is not reachable and will then spend its search budget
trying to reach it.

Two corrections live here.

1. The exact router
-------------------
:func:`walk_cost` is a BFS over ``(cell, heading)`` states under the engine's own
movement rule: execute the glyph (which may set any heading, including a
reversal), then step one cell along the new heading.  Turning is therefore free
in *ticks* -- the turn cell is also a step cell -- but it is not free in
*cells*, and it is emphatically not free in **revisits**.

2. The closed-loop floor
------------------------
This is the result that matters, and it is provable rather than searched.

A worker's man walks a **closed circuit**: he must end where he began or he
cannot serve a second request.  Every op glyph on that circuit must execute
exactly **once per lap** -- a cell visited twice is an op performed twice, which
is a different program, not a cheaper layout.  So the lap is a closed walk
visiting each op cell once.

A closed rectilinear circuit turns at least **four** times, and a turn happens
*at a cell*, consuming that cell's single glyph slot for a steer.  Hence::

    lap_floor = n_ops + 4 - n_turning_ops

where ``n_turning_ops`` counts ops that turn *by themselves* -- ``X``, ``d``,
``a``, ``x`` -- and so can double as a corner.  This is why skip loops are built
out of ``d``/``m`` and not out of ``>``/``v``: a branch glyph pays for its own
corner.

The floor is achieved by the minimum-perimeter axis-aligned rectangle with
enough perimeter cells, ``2(w + h) - 4 >= lap_floor``, and since a rectangle's
perimeter is even the floor rounds up to the next even number.

Checked against a shipped structure before being believed: the 31x31 memory
relay's worker room is ``>rv<s^`` -- **6 ticks per word, 4x2**.  Two ops, no
turning ops, so ``lap_floor = 2 + 4 = 6``, achieved on a 3x2 perimeter.  The
framework's floor and the shipped hand layout agree exactly, and the shipped
layout is therefore *proven* optimal rather than merely good.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

__all__ = [
    "DIRS",
    "walk_cost",
    "walk_path",
    "loop_floor",
    "min_loop_rect",
    "TURNING_OPS",
    "LoopFloor",
]

DIRS = {"E": (1, 0), "S": (0, 1), "W": (-1, 0), "N": (0, -1)}

#: Ops that turn by themselves and can therefore double as a circuit corner,
#: paying no extra cell for it.  ``X`` turns on sign(A); ``d``/``a`` turn on
#: BP > 0; ``x`` turns on BP's low bit and *always* turns.  This is exactly why
#: a skip loop is cheaper than it looks: its ``d`` is both the test and the
#: corner.
TURNING_OPS = set("Xdax")


# ── 1. the exact router ──────────────────────────────────────────────────────
def walk_cost(
    a: tuple[int, int],
    b: tuple[int, int],
    box: tuple[int, int, int, int],
    blocked: set | None = None,
    heading_in: str | None = None,
    heading_out: str | None = None,
) -> int | None:
    """Ticks to get from ``a`` to ``b``, both endpoints counted once.

    ``heading_in`` constrains the heading the man has on arriving at ``a``;
    ``heading_out`` the heading he must have when he leaves ``b``.  ``None``
    means free.  Returns ``None`` if unreachable.

    The cost counts **cells stood on**, which is the tick count, so a walk to an
    adjacent cell costs 2 (both cells) and the caller subtracts the shared
    endpoints when composing.  :func:`walk_path` returns the cells themselves.
    """
    p = walk_path(a, b, box, blocked, heading_in, heading_out)
    return None if p is None else len(p)


def walk_path(a, b, box, blocked=None, heading_in=None, heading_out=None):
    """The cells of a minimum walk from ``a`` to ``b``, inclusive."""
    blocked = blocked or set()
    x0, y0, x1, y1 = box
    starts = [heading_in] if heading_in else list(DIRS)
    q = deque()
    seen = {}
    for h in starts:
        st = (a[0], a[1], h)
        seen[st] = None
        q.append(st)
    goal = None
    while q:
        st = q.popleft()
        x, y, h = st
        if (x, y) == b and (heading_out is None or h == heading_out):
            goal = st
            break
        for nh, (dx, dy) in DIRS.items():
            nx, ny = x + dx, y + dy
            if not (x0 <= nx <= x1 and y0 <= ny <= y1):
                continue
            if (nx, ny) in blocked:
                continue
            ns = (nx, ny, nh)
            if ns in seen:
                continue
            seen[ns] = st
            q.append(ns)
    if goal is None:
        return None
    out = []
    st = goal
    while st is not None:
        out.append((st[0], st[1]))
        st = seen[st]
    return out[::-1]


# ── 2. the closed-loop floor ─────────────────────────────────────────────────
@dataclass(frozen=True)
class LoopFloor:
    """The provable minimum lap of a closed worker circuit."""

    n_ops: int
    n_turning_ops: int
    #: ops + 4 corners - corners paid for by turning ops
    raw: int
    #: rounded up to the next achievable rectangle perimeter (perimeters are even)
    ticks: int
    #: a rectangle that achieves it, as (w, h)
    rect: tuple[int, int]

    def explain(self) -> str:
        return (
            f"lap floor {self.ticks} ticks = {self.n_ops} ops + 4 corners"
            + (f" - {self.n_turning_ops} self-turning ops" if self.n_turning_ops else "")
            + (f" (raw {self.raw}, rounded to an even perimeter)" if self.ticks != self.raw else "")
            + f", achieved on {self.rect[0]}x{self.rect[1]}"
        )


def loop_floor(n_ops: int, n_turning_ops: int = 0) -> LoopFloor:
    """The minimum ticks per lap of a closed circuit carrying ``n_ops`` ops.

    A closed rectilinear circuit turns at least four times; a turn consumes a
    cell's single glyph slot unless the op there turns by itself.  Each op must
    execute exactly once per lap, so no op cell may be revisited.
    """
    raw = max(4, n_ops + max(0, 4 - n_turning_ops))
    ticks = raw + (raw % 2)  # rectangle perimeters are even
    return LoopFloor(n_ops, n_turning_ops, raw, ticks, min_loop_rect(ticks))


def min_loop_rect(perimeter: int) -> tuple[int, int]:
    """The squarest rectangle whose perimeter-cell count is ``perimeter``.

    Perimeter cells of a ``w x h`` rectangle number ``2(w + h) - 4``.  Squarest
    is chosen because the contest bills ``max(w, h)`` and slack in the smaller
    dimension is free -- so among equal-tick layouts, take the square one.
    """
    if perimeter < 4:
        return (2, 2)
    s = perimeter // 2 + 2  # w + h
    w = s // 2
    h = s - w
    return (max(2, w), max(2, h))
