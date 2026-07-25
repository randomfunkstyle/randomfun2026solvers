from dataclasses import FrozenInstanceError

import pytest

from littleman_tools.composer import Gate, Netlist, _plan_layout, _RoomRole
from littleman_tools.primitive_contracts import Side


def _dependent_netlist() -> Netlist:
    return Netlist(
        inputs=("a", "b"),
        gates=(
            Gate("and-gate.man", ("a", "b"), "product"),
            Gate("xor-gate.man", ("product", "b"), "result"),
        ),
        outputs=("result",),
    )


def test_layout_is_deterministic_and_orders_primitive_rooms_by_dependency_level() -> None:
    first = _plan_layout(_dependent_netlist())
    second = _plan_layout(_dependent_netlist())

    assert first == second
    assert [(room.name, room.origin) for room in first.placements] == [
        ("input", (4, 4)),
        ("fanout[b][0]", (31, 4)),
        ("gate[0].packer", (4, 22)),
        ("gate[0]", (25, 22)),
        ("gate[1].packer", (4, 40)),
        ("gate[1]", (25, 40)),
        ("output", (4, 58)),
    ]
    primitives = [room for room in first.placements if room.role is _RoomRole.PRIMITIVE]
    assert [(room.name, room.level) for room in primitives] == [
        ("gate[0]", 1),
        ("gate[1]", 2),
    ]
    assert primitives[0].origin[1] < primitives[1].origin[1]


def test_layout_uses_extracted_rooms_and_required_north_input_attachments() -> None:
    layout = _plan_layout(_dependent_netlist())
    primitives = [room for room in layout.placements if room.role is _RoomRole.PRIMITIVE]

    assert primitives[0].room.grid == ("+-----+", "|@>rMU|", "| ^s*<|", "+-----+")
    assert primitives[1].room.grid == ("+-----+", "|@>rMU|", "| ^s~<|", "+-----+")
    for placement in primitives:
        input_port = next(port for port in placement.ports if port.name == "input")
        assert input_port.side is Side.NORTH
        assert input_port.wall[1] == placement.bounds.top
        assert placement.bounds.left < input_port.wall[0] < placement.bounds.right


def test_every_port_has_a_non_corner_two_cell_exterior_stub_and_anchor_direction() -> None:
    layout = _plan_layout(_dependent_netlist())

    for placement in layout.placements:
        corners = {
            (placement.bounds.left, placement.bounds.top),
            (placement.bounds.right, placement.bounds.top),
            (placement.bounds.left, placement.bounds.bottom),
            (placement.bounds.right, placement.bounds.bottom),
        }
        for port in placement.ports:
            assert port.wall not in corners
            assert len(port.stub) == 2
            dx, dy = port.direction
            assert port.stub == (
                (port.wall[0] + dx, port.wall[1] + dy),
                (port.wall[0] + 2 * dx, port.wall[1] + 2 * dy),
            )
            assert port.anchor == port.stub[-1]
            assert port.escape == (
                (port.wall[0] + 3 * dx, port.wall[1] + 3 * dy),
                (port.wall[0] + 4 * dx, port.wall[1] + 4 * dy),
            )
            assert all(cell not in placement.bounds.cells for cell in port.stub)
            assert all(cell in placement.keepout for cell in port.escape)
            assert all(cell in placement.footprint.cells for cell in port.escape)


def test_padded_footprints_keepouts_and_level_aisles_do_not_overlap() -> None:
    layout = _plan_layout(_dependent_netlist())

    for index, placement in enumerate(layout.placements):
        assert placement.bounds.cells <= placement.keepout
        assert all(cell in placement.keepout for port in placement.ports for cell in port.stub)
        assert placement.keepout <= placement.footprint.cells
        reserved = placement.bounds.cells | frozenset(
            cell for port in placement.ports for cell in port.stub
        )
        assert all(
            (x + dx, y + dy) in placement.keepout
            for x, y in reserved
            for dx in range(-2, 3)
            for dy in range(-2, 3)
        )
        for other in layout.placements[index + 1 :]:
            assert placement.footprint.cells.isdisjoint(other.footprint.cells)

    all_footprints = frozenset().union(
        *(placement.footprint.cells for placement in layout.placements)
    )
    assert layout.routing_aisles
    assert all(aisle.width >= 1 and aisle.height >= 2 for aisle in layout.routing_aisles)
    assert all(aisle.cells.isdisjoint(all_footprints) for aisle in layout.routing_aisles)


def test_layout_records_generated_adapters_connections_and_is_immutable() -> None:
    layout = _plan_layout(_dependent_netlist())
    roles = {placement.role for placement in layout.placements}

    assert {
        _RoomRole.INPUT_DEMULTIPLEXER,
        _RoomRole.FANOUT,
        _RoomRole.PACKER,
        _RoomRole.PRIMITIVE,
        _RoomRole.OUTPUT_JOINER,
    } <= roles
    assert layout.connections
    port_refs = {
        (placement.name, port.name) for placement in layout.placements for port in placement.ports
    }
    assert all(
        (connection.source.room, connection.source.port) in port_refs
        and (connection.target.room, connection.target.port) in port_refs
        for connection in layout.connections
    )

    with pytest.raises(FrozenInstanceError):
        layout.placements[0].level = 99  # type: ignore[misc]


@pytest.mark.parametrize(
    ("netlist", "unused"),
    [
        (
            Netlist(
                inputs=("used", "unused"),
                gates=(Gate("not-gate.man", ("used",), "result"),),
                outputs=("result",),
            ),
            "unused",
        ),
        (
            Netlist(
                inputs=("a", "b"),
                gates=(
                    Gate("and-gate.man", ("a", "b"), "dead"),
                    Gate("not-gate.man", ("a",), "result"),
                ),
                outputs=("result",),
            ),
            "dead",
        ),
    ],
)
def test_layout_rejects_signal_sources_without_a_pipe_target(
    netlist: Netlist,
    unused: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=rf"Signal {unused!r} has no consumer or selected output",
    ):
        _plan_layout(netlist)
