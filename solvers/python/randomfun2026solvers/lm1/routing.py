"""Constraint-based rectilinear pipe routing for live LM-1 generator grids.

The CPU generator knows *what* must connect before it knows the best concrete
polyline.  This module keeps those two decisions separate: callers provide
endpoints, endpoint headings, an allowed box, occupied cells, and a minimum pipe
capacity; :func:`constrained_route` returns the shortest legal route satisfying
them.

A pipe's capacity is its cell count.  If the shortest route is too short, the
router folds two-cell U detours into free space until the requested minimum is
met.  Fixed endpoints imply fixed length parity, so the smallest reachable
capacity may be one cell above the requested floor.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import count

Cell = tuple[int, int]
Direction = tuple[int, int]
_DIRECTIONS: tuple[Direction, ...] = ((1, 0), (0, 1), (-1, 0), (0, -1))


class RouteError(ValueError):
    """No route satisfies the declared geometry and capacity constraints."""


@dataclass(frozen=True)
class RouteBox:
    """Inclusive rectangle in which every routed cell must remain."""

    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        if self.left > self.right or self.top > self.bottom:
            raise RouteError(f"invalid route box {self}")

    def contains(self, cell: Cell) -> bool:
        x, y = cell
        return self.left <= x <= self.right and self.top <= y <= self.bottom


def _add(cell: Cell, direction: Direction) -> Cell:
    return cell[0] + direction[0], cell[1] + direction[1]


def _sub(cell: Cell, direction: Direction) -> Cell:
    return cell[0] - direction[0], cell[1] - direction[1]


def _check_direction(direction: Direction | None, label: str) -> None:
    if direction is not None and direction not in _DIRECTIONS:
        raise RouteError(f"{label} must be one unit cardinal step, got {direction}")


def _shortest(
    start: Cell,
    end: Cell,
    *,
    box: RouteBox,
    blocked: frozenset[Cell],
    incoming: Direction | None,
) -> list[Cell] | None:
    """Shortest free path, breaking equal-length ties on number of turns."""

    serial = count()
    initial = (start, incoming)
    best: dict[tuple[Cell, Direction | None], tuple[int, int]] = {initial: (1, 0)}
    previous: dict[
        tuple[Cell, Direction | None], tuple[Cell, Direction | None] | None
    ] = {initial: None}
    heap: list[tuple[int, int, int, Cell, Direction | None]] = [
        (1, 0, next(serial), start, incoming)
    ]

    final: tuple[Cell, Direction | None] | None = None
    while heap:
        length, turns, _serial, cell, direction = heapq.heappop(heap)
        state = (cell, direction)
        if best.get(state) != (length, turns):
            continue
        if cell == end:
            final = state
            break
        for step in _DIRECTIONS:
            nxt = _add(cell, step)
            if not box.contains(nxt) or nxt in blocked:
                continue
            cost = (length + 1, turns + int(direction is not None and step != direction))
            nxt_state = (nxt, step)
            if cost >= best.get(nxt_state, (1 << 30, 1 << 30)):
                continue
            best[nxt_state] = cost
            previous[nxt_state] = state
            heapq.heappush(heap, (*cost, next(serial), nxt, step))

    if final is None:
        return None
    path: list[Cell] = []
    state: tuple[Cell, Direction | None] | None = final
    while state is not None:
        path.append(state[0])
        state = previous[state]
    return path[::-1]


def _pad_to(
    path: list[Cell],
    target: int,
    *,
    box: RouteBox,
    blocked: frozenset[Cell],
    protect_first: bool,
    protect_last: bool,
) -> list[Cell] | None:
    """Add two-cell U detours until ``path`` reaches ``target`` cells."""

    need = target - len(path)
    if need < 0 or need % 2:
        return None
    out = list(path)
    taken = set(path)
    while need:
        stop = len(out) - 2 if protect_last else len(out) - 1
        for i in range(1 if protect_first else 0, stop):
            a, b = out[i], out[i + 1]
            direction = (b[0] - a[0], b[1] - a[1])
            for side in (
                (-direction[1], direction[0]),
                (direction[1], -direction[0]),
            ):
                a2, b2 = _add(a, side), _add(b, side)
                if a2 in taken or b2 in taken or a2 in blocked or b2 in blocked:
                    continue
                if not box.contains(a2) or not box.contains(b2):
                    continue
                out[i + 1 : i + 1] = [a2, b2]
                taken.update((a2, b2))
                need -= 2
                break
            else:
                continue
            break
        else:
            return None
    return out


def constrained_route(
    start: Cell,
    end: Cell,
    *,
    box: RouteBox,
    blocked: Iterable[Cell] = (),
    min_cells: int = 2,
    start_direction: Direction | None = None,
    end_direction: Direction | None = None,
) -> list[Cell]:
    """Return the shortest legal route satisfying all declared constraints.

    ``start_direction`` is the first step away from the source room.
    ``end_direction`` is the final step into the destination room.  These make
    the endpoint arrowheads deterministic and preserve pipe attachment semantics.
    """

    _check_direction(start_direction, "start_direction")
    _check_direction(end_direction, "end_direction")
    if min_cells < 2:
        raise RouteError(f"a pipe needs at least two cells, got {min_cells}")
    if not box.contains(start) or not box.contains(end):
        raise RouteError(f"route endpoints {start}->{end} leave box {box}")

    search_start = _add(start, start_direction) if start_direction else start
    search_end = _sub(end, end_direction) if end_direction else end
    for label, cell in (("first step", search_start), ("last step", search_end)):
        if not box.contains(cell):
            raise RouteError(f"{label} {cell} leaves box {box}")

    occupied = frozenset(blocked) - {start, end}
    forced = {search_start, search_end} - {start, end}
    if conflict := forced & occupied:
        raise RouteError(f"fixed endpoint step is blocked: {min(conflict)}")
    reserved = occupied | ({start, end} - {search_start, search_end})
    if start_direction is not None and search_start == end:
        if end_direction != start_direction:
            raise RouteError(
                f"adjacent endpoints require matching headings, got "
                f"{start_direction} and {end_direction}"
            )
        path = [start, end]
    else:
        inner = _shortest(
            search_start,
            search_end,
            box=box,
            blocked=reserved,
            incoming=start_direction,
        )
        if inner is None:
            raise RouteError(f"no free route from {start} to {end} inside {box}")
        path = ([start] if start_direction else []) + inner + ([end] if end_direction else [])
    target = max(min_cells, len(path))
    if (target - len(path)) % 2:
        target += 1
    padded = _pad_to(
        path,
        target,
        box=box,
        blocked=occupied,
        protect_first=start_direction is not None,
        protect_last=end_direction is not None,
    )
    if padded is None:
        raise RouteError(
            f"shortest route has {len(path)} cells but cannot reach minimum "
            f"capacity {min_cells} inside {box}"
        )
    return padded
