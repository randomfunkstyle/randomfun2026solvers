from collections import defaultdict
from dataclasses import FrozenInstanceError

import pytest

from littleman_tools.composer import (
    ActiveRoom,
    Gate,
    Netlist,
    _AdapterFlow,
    _Connection,
    _Layout,
    _PortAnchor,
    _PortRef,
    _Rect,
    _render_raw_grid,
    _RoomPlacement,
    _RoomRole,
    _route_layout,
    _RoutedConnection,
    _routing_search_bounds,
    _UnroutableError,
)
from littleman_tools.primitive_contracts import Side
from littleman_tools.runner import Littleman


def _dependent_layout() -> _Layout:
    from littleman_tools.composer import _plan_layout

    return _plan_layout(
        Netlist(
            inputs=("a", "b"),
            gates=(
                Gate("and-gate.man", ("a", "b"), "product"),
                Gate("xor-gate.man", ("product", "b"), "result"),
            ),
            outputs=("result",),
        )
    )


def _bottleneck_layout() -> _Layout:
    def endpoint(
        name: str,
        flow: _AdapterFlow,
        wall: tuple[int, int],
        side: Side,
        direction: tuple[int, int],
    ) -> _RoomPlacement:
        stub = tuple(
            (wall[0] + distance * direction[0], wall[1] + distance * direction[1])
            for distance in (1, 2)
        )
        escape = tuple(
            (wall[0] + distance * direction[0], wall[1] + distance * direction[1])
            for distance in (3, 4)
        )
        port = _PortAnchor(
            name="pipe",
            flow=flow,
            side=side,
            wall=wall,
            stub=stub,
            anchor=stub[-1],
            direction=direction,
            escape=escape,
            instructions=(),
        )
        footprint = _Rect(
            left=min(wall[0], escape[-1][0]),
            top=min(wall[1], escape[-1][1]),
            width=abs(wall[0] - escape[-1][0]) + 1,
            height=abs(wall[1] - escape[-1][1]) + 1,
        )
        return _RoomPlacement(
            name=name,
            role=_RoomRole.PRIMITIVE,
            level=0,
            room=ActiveRoom(text="@", grid=("@",), width=1, height=1, runner=(0, 0)),
            origin=wall,
            bounds=_Rect(wall[0], wall[1], 1, 1),
            footprint=footprint,
            ports=(port,),
            keepout=footprint.cells,
        )

    placements = (
        endpoint("left", _AdapterFlow.OUTGOING, (-6, 0), Side.EAST, (1, 0)),
        endpoint("right", _AdapterFlow.INCOMING, (6, 0), Side.WEST, (-1, 0)),
        endpoint("top", _AdapterFlow.OUTGOING, (0, -6), Side.SOUTH, (0, 1)),
        endpoint("bottom", _AdapterFlow.INCOMING, (0, 6), Side.NORTH, (0, -1)),
    )
    connections = (
        _Connection("horizontal", _PortRef("left", "pipe"), _PortRef("right", "pipe")),
        _Connection("vertical", _PortRef("top", "pipe"), _PortRef("bottom", "pipe")),
    )
    unconstrained = _Layout(
        placements=placements,
        connections=connections,
        routing_aisles=(),
        keepout=frozenset(),
    )
    bounds = _routing_search_bounds(unconstrained)
    only_open_corridors = frozenset(
        {(coordinate, 0) for coordinate in range(-2, 3)}
        | {(0, coordinate) for coordinate in range(-2, 3)}
    )
    return _Layout(
        placements=placements,
        connections=connections,
        routing_aisles=(),
        keepout=bounds.cells - only_open_corridors,
    )


def test_routing_is_deterministic_directional_and_immutable() -> None:
    layout = _dependent_layout()

    first = _route_layout(layout)
    second = _route_layout(layout)

    assert first == second
    assert tuple(route.connection for route in first.routes) == layout.connections
    for route in first.routes:
        source, target = _route_ports(layout, route)
        assert route.path[:4] == source.stub + source.escape
        assert route.path[-4:] == tuple(reversed(target.escape)) + tuple(reversed(target.stub))
        assert all(
            abs(first_cell[0] - second_cell[0]) + abs(first_cell[1] - second_cell[1]) == 1
            for first_cell, second_cell in zip(route.path, route.path[1:], strict=False)
        )

    with pytest.raises(FrozenInstanceError):
        first.routes[0].path = ()  # type: ignore[misc]


def test_routes_do_not_cross_overlap_or_enter_unowned_keepouts() -> None:
    layout = _dependent_layout()
    routed = _route_layout(layout)

    occupied: set[tuple[int, int]] = set()
    for route in routed.routes:
        source, target = _route_ports(layout, route)
        path = set(route.path)
        assert len(route.path) == len(path)
        assert occupied.isdisjoint(path)
        assert path & layout.keepout == set(
            source.stub + source.escape + target.stub + target.escape
        )
        occupied.update(path)


def test_each_route_owns_only_its_endpoint_escape_cells() -> None:
    layout = _dependent_layout()
    routed = _route_layout(layout)
    ports = {
        (placement.name, port.name): port
        for placement in layout.placements
        for port in placement.ports
    }

    for route in routed.routes:
        endpoints = {
            (route.connection.source.room, route.connection.source.port),
            (route.connection.target.room, route.connection.target.port),
        }
        path = set(route.path)
        for reference, port in ports.items():
            if reference in endpoints:
                assert set(port.escape) <= path
            else:
                assert path.isdisjoint(port.escape)


def test_routing_reports_a_clear_no_crossover_failure() -> None:
    layout = _dependent_layout()
    first_connection = layout.connections[0]
    ports = _ports_by_reference(layout)
    source = ports[(first_connection.source.room, first_connection.source.port)]
    far_x, far_y = source.escape[-1]
    sealed_keepout = layout.keepout | frozenset(
        {
            (far_x - 1, far_y),
            (far_x + 1, far_y),
            (far_x, far_y - 1),
        }
    )
    sealed = _Layout(
        placements=layout.placements,
        connections=layout.connections,
        routing_aisles=layout.routing_aisles,
        keepout=sealed_keepout,
    )

    with pytest.raises(
        _UnroutableError,
        match=r"Unroutable .*no-crossover",
    ):
        _route_layout(sealed)


def test_routing_exhausts_negotiated_retries_for_an_unavoidable_bottleneck() -> None:
    with pytest.raises(
        _UnroutableError,
        match=r"Unroutable .*no-crossover.*96 deterministic conflict retries",
    ):
        _route_layout(_bottleneck_layout())


def test_rendered_raw_grid_preserves_every_nearest_pipe_binding() -> None:
    layout = _dependent_layout()
    routed = _route_layout(layout)
    raw = _render_raw_grid(routed)
    littleman = Littleman()
    routes_by_instruction: dict[
        tuple[int, int],
        list[tuple[tuple[int, int], ...]],
    ] = defaultdict(list)

    for route in routed.routes:
        source, target = _route_ports(layout, route)
        expected = tuple(raw.to_grid(cell) for cell in route.path)
        for instruction in source.instructions + target.instructions:
            routes_by_instruction[instruction].append(expected)

    for instruction, expected_paths in routes_by_instruction.items():
        actual = tuple(
            cell.as_tuple() for cell in littleman.route(raw.source, *raw.to_grid(instruction))
        )
        assert set(actual) == set().union(*(set(path) for path in expected_paths)), (
            instruction,
            raw.source,
        )
        if len(expected_paths) == 1:
            assert actual == expected_paths[0]


def _route_ports(
    layout: _Layout,
    route: _RoutedConnection,
) -> tuple[_PortAnchor, _PortAnchor]:
    ports = _ports_by_reference(layout)
    return (
        ports[(route.connection.source.room, route.connection.source.port)],
        ports[(route.connection.target.room, route.connection.target.port)],
    )


def _ports_by_reference(
    layout: _Layout,
) -> dict[tuple[str, str], _PortAnchor]:
    return {
        (placement.name, port.name): port
        for placement in layout.placements
        for port in placement.ports
    }
