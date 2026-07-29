"""The router: draw a pipe between two touch cells, and price it.

The routing rules are read straight off ``SPEC.md`` § Pipes rather than invented:

* a pipe **starts** with an arrowhead whose backward cell is on the source
  room's border, pointing away from it — so the first step must leave along the
  port's own heading (§7.2: a port is *(cell, heading)*, never just a cell);
* it **ends** at the first arrowhead whose forward cell is on a room border — so
  a bend whose backward cell happens to sit on some other room's wall starts a
  *second* pipe, and a leg brushing a room corner is read as an extra pipe.
  That is the class of bug ``machine.py``'s ``_check_pipe_count`` exists to
  catch, so the router refuses to draw it;
* minimum length 2, and capacity == length.

``min_length`` is a first-class constraint, not a post-hoc pad: ``ARCH.md`` §7.4
records that the code ring deadlocks when its pipe is shorter than ``P``, and
that "every router optimises for short pipes, so a naive packing pass silently
breaks a looping program".
"""

from __future__ import annotations

from collections import deque

from .model import FORWARDER_CELLS, MIN_PIPE, Leg, Route

DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


class NoRoute(Exception):
    """No legal pipe exists between these two cells under these obstacles."""


def _border(rects: list[tuple[int, int, int, int]]) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for x0, y0, w, h in rects:
        for x in range(x0, x0 + w):
            out.add((x, y0))
            out.add((x, y0 + h - 1))
        for y in range(y0, y0 + h):
            out.add((x0, y))
            out.add((x0 + w - 1, y))
    return out


class Field:
    """Everything a route has to dodge: block cells, other pipes, the bounds."""

    def __init__(
        self,
        bounds: tuple[int, int],
        rects: list[tuple[int, int, int, int]],
        taken: set[tuple[int, int]] | None = None,
    ) -> None:
        self.bounds = bounds
        self.rects = rects
        self.solid: set[tuple[int, int]] = set()
        for x0, y0, w, h in rects:
            for x in range(x0, x0 + w):
                for y in range(y0, y0 + h):
                    self.solid.add((x, y))
        self.border = _border(rects)
        self.taken: set[tuple[int, int]] = set(taken or ())

    def plus(self, rect: tuple[int, int, int, int], taken=()) -> Field:
        """A field with one more rectangle, **sharing** this one's cell sets.

        Materialising the solid set again for every trial forwarder position is
        O(area) per attempt and dominates the whole search on a 250-row corridor.
        """
        sub = Field.__new__(Field)
        x0, y0, w, h = rect
        sub.bounds = self.bounds
        sub.rects = [*self.rects, rect]
        cells = {(x, y) for x in range(x0, x0 + w) for y in range(y0, y0 + h)}
        sub.solid = self.solid | cells
        sub.border = self.border | _border([rect])
        sub.taken = self.taken | set(taken)
        return sub

    def free(self, c: tuple[int, int]) -> bool:
        x, y = c
        bw, bh = self.bounds
        return 0 <= x < bw and 0 <= y < bh and c not in self.solid and c not in self.taken


def _add(c: tuple[int, int], d: tuple[int, int]) -> tuple[int, int]:
    return (c[0] + d[0], c[1] + d[1])


def _sub(c: tuple[int, int], d: tuple[int, int]) -> tuple[int, int]:
    return (c[0] - d[0], c[1] - d[1])


def _bend_ok(field: Field, cell: tuple[int, int], new_dir: tuple[int, int]) -> bool:
    """A bend is an arrowhead; its *backward* cell must not be on a room border.

    If it is, ``SPEC.md``'s parser reads a fresh pipe starting there.
    """
    return _sub(cell, new_dir) not in field.border


def route(
    field: Field,
    start: tuple[int, int],
    start_dir: tuple[int, int],
    goal: tuple[int, int],
    goal_dir: tuple[int, int],
    *,
    min_length: int = 2,
    max_length: int | None = None,
    node_cap: int = 400_000,
) -> tuple[tuple[int, int], ...]:
    """Shortest legal pipe from ``start`` to ``goal``, at least ``min_length`` cells.

    ``start_dir`` is the heading the pipe leaves the source wall on; ``goal_dir``
    is the heading it must be travelling when it arrives, so that its terminal
    arrowhead points into the destination wall.  ``max_length`` abandons the search
    once every frontier is longer than that — a forwarder's stub is short by
    definition, so bounding it is what keeps the room search affordable.
    """
    if not field.free(start) or not field.free(goal):
        raise NoRoute(f"{start} or {goal} is not free")
    short = _bfs(field, start, start_dir, goal, goal_dir, node_cap, max_length)
    if short is None:
        raise NoRoute(f"no path {start} -> {goal}")
    if len(short) >= min_length:
        return short
    want = min_length
    if (want - len(short)) % 2:
        want += 1  # a simple path's length keeps the shortest path's parity
    long = _exact(field, start, start_dir, goal, goal_dir, want, node_cap)
    if long is None:
        raise NoRoute(f"no {want}-cell path {start} -> {goal} (min_length={min_length})")
    return long


def _bfs(
    field: Field,
    start: tuple[int, int],
    start_dir: tuple[int, int],
    goal: tuple[int, int],
    goal_dir: tuple[int, int],
    node_cap: int,
    max_length: int | None = None,
) -> tuple[tuple[int, int], ...] | None:
    # State is (cell, heading): the bend rule depends on which way we arrived.
    # Parent pointers, not carried paths — a 100x250 corridor makes the difference
    # between milliseconds and minutes.  A BFS shortest path on a unit-weight grid
    # is always simple, so no explicit self-avoidance is needed here.
    if start == goal:
        return None
    first = _add(start, start_dir)
    if not field.free(first) and first != goal:
        return None
    root = (start, start_dir)
    parent: dict[
        tuple[tuple[int, int], tuple[int, int]],
        tuple[tuple[int, int], tuple[int, int]] | None,
    ] = {root: None}
    q: deque[tuple[tuple[int, int], tuple[int, int], int]] = deque([(*root, 1)])
    n = 0
    while q:
        cell, d, depth = q.popleft()
        n += 1
        if n > node_cap:
            return None
        if max_length is not None and depth >= max_length:
            continue
        for nd in DIRS:
            if nd == (-d[0], -d[1]):
                continue  # a pipe never doubles back on itself
            if nd != d and not _bend_ok(field, cell, nd):
                continue
            nxt = _add(cell, nd)
            if nxt == goal:
                if nd != goal_dir:
                    continue
                out = [goal]
                node: tuple[tuple[int, int], tuple[int, int]] | None = (cell, d)
                while node is not None:
                    out.append(node[0])
                    node = parent[node]
                return tuple(reversed(out))
            if (nxt, nd) in parent or not field.free(nxt):
                continue
            parent[(nxt, nd)] = (cell, d)
            q.append((nxt, nd, depth + 1))
    return None


def _exact(
    field: Field,
    start: tuple[int, int],
    start_dir: tuple[int, int],
    goal: tuple[int, int],
    goal_dir: tuple[int, int],
    length: int,
    node_cap: int,
) -> tuple[tuple[int, int], ...] | None:
    """A simple path of exactly ``length`` cells, for ``min_length`` constraints."""
    budget = [node_cap]

    def walk(
        cell: tuple[int, int], d: tuple[int, int], path: tuple[tuple[int, int], ...]
    ) -> tuple[tuple[int, int], ...] | None:
        budget[0] -= 1
        if budget[0] < 0:
            return None
        left = length - len(path)
        if left == 0:
            return None
        for nd in DIRS:
            if nd == (-d[0], -d[1]):
                continue
            if nd != d and not _bend_ok(field, cell, nd):
                continue
            nxt = _add(cell, nd)
            gap = abs(nxt[0] - goal[0]) + abs(nxt[1] - goal[1])
            if nxt == goal:
                if left == 1 and nd == goal_dir:
                    return (*path, goal)
                continue
            if left - 1 < gap + 1 or (left - 1 - gap - 1) % 2:
                continue
            if not field.free(nxt) or nxt in path:
                continue
            got = walk(nxt, nd, (*path, nxt))
            if got is not None:
                return got
        return None

    return walk(start, start_dir, (start,))


# ── forwarders ───────────────────────────────────────────────────────────────
def room_route(
    field: Field,
    start: tuple[int, int],
    start_dir: tuple[int, int],
    goal: tuple[int, int],
    goal_dir: tuple[int, int],
    room: tuple[int, int, int, int],
    in_side: str,
    out_side: str,
    *,
    node_cap: int = 400_000,
    budget: int | None = None,
) -> tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]:
    """Two stubs into and out of a forwarder placed at ``room``.

    The room is crossed for :data:`model.FORWARDER_CELLS`, *whatever its size* —
    that is the property, and it is why the store's request teleport was a room
    hanging the whole height of a corridor rather than a short relay.
    """
    x0, y0, w, h = room
    inward = {"N": (0, 1), "S": (0, -1), "E": (-1, 0), "W": (1, 0)}
    outward = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}
    mid = {
        "N": (x0 + w // 2, y0 - 1),
        "S": (x0 + w // 2, y0 + h),
        "E": (x0 + w, y0 + h // 2),
        "W": (x0 - 1, y0 + h // 2),
    }
    cap = None if budget is None else max(MIN_PIPE, budget - MIN_PIPE)
    sub = field.plus(room)
    a = route(
        sub, start, start_dir, mid[in_side], inward[in_side],
        max_length=cap, node_cap=node_cap,
    )
    b = route(
        field.plus(room, a),
        mid[out_side],
        outward[out_side],
        goal,
        goal_dir,
        max_length=None if budget is None else max(MIN_PIPE, budget - len(a)),
        node_cap=node_cap,
    )
    return a, b


def price(route_: Route, weight: float) -> float:
    return route_.cells * weight


__all__ = ["FORWARDER_CELLS", "Field", "Leg", "NoRoute", "price", "room_route", "route"]
