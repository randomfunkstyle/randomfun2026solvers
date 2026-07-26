"""Pure geometry tests for the LM-1 generator's constraint-based pipe router."""

from __future__ import annotations

import pytest
from randomfun2026solvers.lm1.routing import RouteBox, RouteError, constrained_route


def _assert_contiguous(path: list[tuple[int, int]]) -> None:
    assert len(path) == len(set(path))
    assert all(
        abs(ax - bx) + abs(ay - by) == 1
        for (ax, ay), (bx, by) in zip(path, path[1:], strict=False)
    )


def test_empty_box_returns_a_shortest_route_with_fixed_endpoint_headings() -> None:
    start, end = (1, 1), (6, 4)
    path = constrained_route(
        start,
        end,
        box=RouteBox(0, 0, 8, 6),
        start_direction=(1, 0),
        end_direction=(0, 1),
    )
    _assert_contiguous(path)
    assert path[0] == start and path[-1] == end
    assert (path[1][0] - start[0], path[1][1] - start[1]) == (1, 0)
    assert (end[0] - path[-2][0], end[1] - path[-2][1]) == (0, 1)
    assert len(path) == abs(end[0] - start[0]) + abs(end[1] - start[1]) + 1


def test_adjacent_endpoints_with_matching_headings_are_a_two_cell_pipe() -> None:
    path = constrained_route(
        (1, 1),
        (2, 1),
        box=RouteBox(0, 0, 3, 2),
        start_direction=(1, 0),
        end_direction=(1, 0),
    )
    assert path == [(1, 1), (2, 1)]


def test_obstacles_force_a_detour_that_stays_inside_the_box() -> None:
    box = RouteBox(0, 0, 6, 4)
    blocked = {(3, y) for y in range(4)}
    path = constrained_route((1, 2), (5, 2), box=box, blocked=blocked)
    _assert_contiguous(path)
    assert not set(path) & blocked
    assert all(box.contains(cell) for cell in path)


def test_minimum_capacity_is_met_with_the_endpoint_parity() -> None:
    path = constrained_route(
        (1, 2),
        (7, 2),
        box=RouteBox(0, 0, 8, 6),
        min_cells=12,
        start_direction=(1, 0),
        end_direction=(1, 0),
    )
    _assert_contiguous(path)
    assert len(path) >= 12
    shortest = abs(path[-1][0] - path[0][0]) + abs(path[-1][1] - path[0][1]) + 1
    assert (len(path) - shortest) % 2 == 0


def test_impossible_box_is_reported_as_a_constraint_failure() -> None:
    with pytest.raises(RouteError, match="no free route"):
        constrained_route(
            (0, 1),
            (4, 1),
            box=RouteBox(0, 0, 4, 2),
            blocked={(2, 0), (2, 1), (2, 2)},
        )


def test_a_fixed_endpoint_step_cannot_pass_through_an_occupied_cell() -> None:
    with pytest.raises(RouteError, match="fixed endpoint step is blocked"):
        constrained_route(
            (0, 1),
            (4, 1),
            box=RouteBox(0, 0, 4, 2),
            blocked={(1, 1)},
            start_direction=(1, 0),
        )
