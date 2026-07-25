"""Validated model for ordered scalar Little Man primitive netlists."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from heapq import heappop, heappush
from os import PathLike
from pathlib import Path
from types import MappingProxyType

from .primitive_contracts import (
    PortContract,
    PrimitiveContract,
    Side,
    contract_for,
    contracts_by_artifact,
)

__all__ = ["ActiveRoom", "FanOut", "Gate", "Netlist", "compose", "extract_active_room", "write"]


@dataclass(frozen=True)
class ActiveRoom:
    """A closed runner room, normalized to its own local grid."""

    text: str
    grid: tuple[str, ...]
    width: int
    height: int
    runner: tuple[int, int] | None


class _AdapterFlow(StrEnum):
    """The direction values move through one generated adapter port."""

    INCOMING = "incoming"
    OUTGOING = "outgoing"


@dataclass(frozen=True)
class _AdapterPort:
    """One local pipe attachment and every instruction cell bound to that pipe."""

    name: str
    flow: _AdapterFlow
    side: Side
    wall: tuple[int, int]
    instructions: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _GeneratedAdapter:
    """An independently placeable generated room with explicit local ports."""

    room: ActiveRoom
    ports: tuple[_AdapterPort, ...]


def _make_input_demultiplexer(field_count: int) -> _GeneratedAdapter:
    """Split each ordered input frame across one scalar pipe per field."""

    room, receives, sends = _make_ordered_adapter_room(field_count)
    ports = [
        _AdapterPort(
            name="frame",
            flow=_AdapterFlow.INCOMING,
            side=Side.WEST,
            wall=(0, 1),
            instructions=receives,
        )
    ]
    ports.extend(
        _AdapterPort(
            name=f"field[{index}]",
            flow=_AdapterFlow.OUTGOING,
            side=Side.NORTH,
            wall=(send[0], 0),
            instructions=(send,),
        )
        for index, send in enumerate(sends)
    )
    return _GeneratedAdapter(room=room, ports=tuple(ports))


def _make_scalar_fanout(output_count: int) -> _GeneratedAdapter:
    """Broadcast one scalar atomically to the requested outgoing pipe ports."""

    if output_count < 2:
        raise ValueError("Scalar fanout requires at least two outputs")

    room = _room_from_grid(("+-----+", "|@>rSv|", "| ^  <|", "+-----+"))
    attachments = (
        (Side.EAST, (6, 1)),
        (Side.SOUTH, (4, 3)),
        (Side.WEST, (0, 2)),
        (Side.SOUTH, (2, 3)),
        (Side.NORTH, (5, 0)),
        (Side.EAST, (6, 2)),
        (Side.WEST, (0, 1)),
        (Side.SOUTH, (1, 3)),
        (Side.SOUTH, (3, 3)),
        (Side.SOUTH, (5, 3)),
        (Side.NORTH, (1, 0)),
        (Side.NORTH, (2, 0)),
        (Side.NORTH, (4, 0)),
    )
    if output_count > len(attachments):
        raise ValueError(
            f"Scalar fanout room supports at most {len(attachments)} direct outputs, "
            f"got {output_count}"
        )

    ports = [
        _AdapterPort(
            name="input",
            flow=_AdapterFlow.INCOMING,
            side=Side.NORTH,
            wall=(3, 0),
            instructions=((3, 1),),
        )
    ]
    ports.extend(
        _AdapterPort(
            name=f"copy[{index}]",
            flow=_AdapterFlow.OUTGOING,
            side=side,
            wall=wall,
            instructions=((4, 1),),
        )
        for index, (side, wall) in enumerate(attachments[:output_count])
    )
    return _GeneratedAdapter(room=room, ports=tuple(ports))


def _mirror_adapter_vertically(adapter: _GeneratedAdapter) -> _GeneratedAdapter:
    """Mirror a generated runner room while preserving its instruction flow."""

    height = adapter.room.height
    grid = tuple(
        row.translate(str.maketrans({"^": "v", "v": "^"})) for row in reversed(adapter.room.grid)
    )
    side_map = {
        Side.NORTH: Side.SOUTH,
        Side.EAST: Side.EAST,
        Side.SOUTH: Side.NORTH,
        Side.WEST: Side.WEST,
    }
    return _GeneratedAdapter(
        room=_room_from_grid(grid),
        ports=tuple(
            _AdapterPort(
                name=port.name,
                flow=port.flow,
                side=side_map[port.side],
                wall=(port.wall[0], height - 1 - port.wall[1]),
                instructions=tuple((x, height - 1 - y) for x, y in port.instructions),
            )
            for port in adapter.ports
        ),
    )


def _make_two_field_packer() -> _GeneratedAdapter:
    """Pack two scalar streams into one ordered serial frame."""

    room = _room_from_grid(("+-----+", "|@>rsv|", "|.^sr<|", "+-----+"))
    return _GeneratedAdapter(
        room=room,
        ports=(
            _AdapterPort(
                name="field[0]",
                flow=_AdapterFlow.INCOMING,
                side=Side.NORTH,
                wall=(3, 0),
                instructions=((3, 1),),
            ),
            _AdapterPort(
                name="field[1]",
                flow=_AdapterFlow.INCOMING,
                side=Side.SOUTH,
                wall=(4, 3),
                instructions=((4, 2),),
            ),
            _AdapterPort(
                name="frame",
                flow=_AdapterFlow.OUTGOING,
                side=Side.EAST,
                wall=(6, 1),
                instructions=((4, 1), (3, 2)),
            ),
        ),
    )


def _make_output_joiner(field_count: int) -> _GeneratedAdapter:
    """Join selected scalar outputs into their declared serial field order."""

    room, receives, sends = _make_ordered_adapter_room(field_count)
    ports = [
        _AdapterPort(
            name=f"field[{index}]",
            flow=_AdapterFlow.INCOMING,
            side=Side.NORTH,
            wall=(receive[0], 0),
            instructions=(receive,),
        )
        for index, receive in enumerate(receives)
    ]
    ports.append(
        _AdapterPort(
            name="frame",
            flow=_AdapterFlow.OUTGOING,
            side=Side.EAST,
            wall=(room.width - 1, 1),
            instructions=sends,
        )
    )
    return _GeneratedAdapter(room=room, ports=tuple(ports))


def _make_ordered_adapter_room(
    field_count: int,
) -> tuple[ActiveRoom, tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]:
    if field_count < 1:
        raise ValueError("Ordered adapter requires at least one field")

    interior_width = 4 * field_count + 3
    top = [" "] * interior_width
    top[0:2] = ("@", ">")
    receives: list[tuple[int, int]] = []
    sends: list[tuple[int, int]] = []
    for index in range(field_count):
        receive_x = 3 + 4 * index
        send_x = receive_x + 1
        top[receive_x - 1] = "r"
        top[send_x - 1] = "s"
        receives.append((receive_x, 1))
        sends.append((send_x, 1))
    top[-1] = "v"

    bottom = [" "] * interior_width
    bottom[0:2] = (".", "^")
    bottom[-1] = "<"
    wall = "+" + "-" * interior_width + "+"
    room = _room_from_grid((wall, "|" + "".join(top) + "|", "|" + "".join(bottom) + "|", wall))
    return room, tuple(receives), tuple(sends)


def _room_from_grid(grid: tuple[str, ...]) -> ActiveRoom:
    return ActiveRoom(
        text="\n".join(grid),
        grid=grid,
        width=len(grid[0]),
        height=len(grid),
        runner=next(
            ((x, y) for y, row in enumerate(grid) for x, cell in enumerate(row) if cell == "@"),
            None,
        ),
    )


def extract_active_room(source: str | PathLike[str]) -> ActiveRoom:
    """Extract the closed room containing the sole reusable ``@`` runner.

    ``source`` may be inline Little Man text or an existing artifact path.  The
    selection is based solely on the enclosing room walls, never on source
    order or a primitive-specific coordinate.
    """

    if isinstance(source, PathLike):
        text = Path(source).read_text(encoding="utf-8")
    elif "\n" not in source and Path(source).is_file():
        text = Path(source).read_text(encoding="utf-8")
    else:
        text = source

    rows = tuple(text.splitlines())
    runners = [(x, y) for y, row in enumerate(rows) for x, cell in enumerate(row) if cell == "@"]
    if not runners:
        raise ValueError("No runner '@' found in primitive artifact")

    rooms: dict[tuple[int, int, int, int], tuple[int, int]] = {}
    for runner_x, runner_y in runners:
        for top in range(runner_y):
            for bottom in range(runner_y + 1, len(rows)):
                for left in range(runner_x):
                    for right in range(runner_x + 1, max(len(row) for row in rows) if rows else 0):
                        if _is_closed_room(rows, left, top, right, bottom):
                            rooms[(left, top, right, bottom)] = (runner_x, runner_y)

    if not rooms:
        raise ValueError("Active room containing '@' is not closed")
    if len(rooms) != 1 or len(runners) != 1:
        raise ValueError("Multiple closed rooms contain '@' runners; active room is ambiguous")

    (left, top, right, bottom), (runner_x, runner_y) = next(iter(rooms.items()))
    grid = tuple(row[left : right + 1] for row in rows[top : bottom + 1])
    return ActiveRoom(
        text="\n".join(grid),
        grid=grid,
        width=right - left + 1,
        height=bottom - top + 1,
        runner=(runner_x - left, runner_y - top),
    )


def _is_closed_room(rows: tuple[str, ...], left: int, top: int, right: int, bottom: int) -> bool:
    """Return whether bounds are a Little Man room rectangle containing walls."""

    if right - left < 2 or bottom - top < 2:
        return False
    if any(len(rows[y]) <= right for y in range(top, bottom + 1)):
        return False
    if rows[top][left] != "+" or rows[top][right] != "+":
        return False
    if rows[bottom][left] != "+" or rows[bottom][right] != "+":
        return False
    if any(rows[top][x] != "-" for x in range(left + 1, right)):
        return False
    if any(rows[bottom][x] != "-" for x in range(left + 1, right)):
        return False
    return all(rows[y][left] == "|" and rows[y][right] == "|" for y in range(top + 1, bottom))


@dataclass(frozen=True)
class Gate:
    """One primitive invocation in an ordered scalar netlist."""

    kind: str
    inputs: tuple[str, ...]
    output: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "inputs", tuple(self.inputs))


@dataclass(frozen=True)
class FanOut:
    """Copies one ordered input frame into named ordered branch frames."""

    source: tuple[str, ...]
    branches: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", tuple(self.source))
        object.__setattr__(self, "branches", tuple(tuple(branch) for branch in self.branches))


@dataclass(frozen=True)
class Netlist:
    """An ordered scalar DAG with optional explicit input-frame fanout."""

    inputs: tuple[str, ...]
    gates: tuple[Gate, ...]
    outputs: tuple[str, ...]
    fanouts: tuple[FanOut, ...] = ()
    producers: Mapping[str, Gate | FanOut | None] = field(init=False, repr=False)
    consumers: Mapping[str, tuple[Gate, ...]] = field(init=False, repr=False)
    levels: Mapping[str, int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "gates", tuple(self.gates))
        object.__setattr__(self, "outputs", tuple(self.outputs))
        object.__setattr__(self, "fanouts", tuple(self.fanouts))

        producers: dict[str, Gate | FanOut | None] = {}
        consumers: dict[str, list[Gate]] = {}
        levels: dict[str, int] = {}

        for signal in self.inputs:
            if signal in producers:
                raise ValueError(f"Duplicate input signal {signal!r}")
            producers[signal] = None
            consumers[signal] = []
            levels[signal] = 0

        branch_owner: dict[str, tuple[FanOut, tuple[str, ...]]] = {}
        for fanout in self.fanouts:
            if not fanout.source:
                raise ValueError("FanOut source must not be empty")
            if len(set(fanout.source)) != len(fanout.source):
                raise ValueError("FanOut source contains duplicate signals")
            for signal in fanout.source:
                if signal not in producers:
                    raise ValueError(f"FanOut source signal {signal!r} is not a declared input")
            if len(fanout.branches) < 2:
                raise ValueError("FanOut requires at least two branches")
            for branch_index, branch in enumerate(fanout.branches):
                if len(branch) != len(fanout.source):
                    raise ValueError(
                        f"FanOut branch {branch_index} has width {len(branch)}, "
                        f"expected {len(fanout.source)}"
                    )
                for signal in branch:
                    if signal in branch_owner:
                        raise ValueError(f"Duplicate FanOut branch signal {signal!r}")
                    if signal in producers:
                        raise ValueError(
                            f"FanOut branch signal {signal!r} conflicts with a declared input"
                        )
                    branch_owner[signal] = (fanout, branch)
                    producers[signal] = fanout
                    consumers[signal] = []
                    levels[signal] = 0

        branch_consumers: dict[tuple[str, ...], Gate] = {}
        contracts = contracts_by_artifact()
        for index, gate in enumerate(self.gates):
            referenced_branches = {
                branch_owner[signal][1] for signal in gate.inputs if signal in branch_owner
            }
            if referenced_branches:
                branch = next(iter(referenced_branches))
                if len(referenced_branches) != 1 or gate.inputs != branch:
                    raise ValueError(f"Gate {index} must consume complete FanOut branch {branch!r}")
                if branch in branch_consumers:
                    raise ValueError(f"FanOut branch {branch!r} is consumed more than once")

            try:
                contract = contracts[gate.kind]
            except KeyError as error:
                raise ValueError(f"Unknown primitive kind {gate.kind!r}") from error

            expected_arity = len(contract.input_order)
            actual_arity = len(gate.inputs)
            if actual_arity != expected_arity:
                raise ValueError(
                    f"Primitive {gate.kind!r} expects {expected_arity} inputs, got {actual_arity}"
                )
            if gate.output in producers:
                if producers[gate.output] is None:
                    raise ValueError(
                        f"Produced signal {gate.output!r} conflicts with a declared input"
                    )
                if isinstance(producers[gate.output], FanOut):
                    raise ValueError(
                        f"Produced signal {gate.output!r} conflicts with a FanOut branch"
                    )
                raise ValueError(f"Duplicate produced signal {gate.output!r}")

            input_levels: list[int] = []
            for signal in gate.inputs:
                if signal not in producers:
                    raise ValueError(
                        f"Gate {index} input {signal!r} is not defined by a declared input "
                        "or prior gate"
                    )
                consumers[signal].append(gate)
                input_levels.append(levels[signal])

            if referenced_branches:
                branch_consumers[branch] = gate
            producers[gate.output] = gate
            consumers[gate.output] = []
            levels[gate.output] = max(input_levels, default=0) + 1

        for fanout in self.fanouts:
            for branch in fanout.branches:
                if branch not in branch_consumers:
                    raise ValueError(f"FanOut branch {branch!r} is not consumed by a gate")

        for signal in self.outputs:
            if signal in branch_owner:
                raise ValueError(f"Selected output {signal!r} is a FanOut branch signal")
            if signal not in producers:
                raise ValueError(f"Selected output {signal!r} is not defined")

        object.__setattr__(self, "producers", MappingProxyType(producers))
        object.__setattr__(
            self,
            "consumers",
            MappingProxyType({signal: tuple(gates) for signal, gates in consumers.items()}),
        )
        object.__setattr__(self, "levels", MappingProxyType(levels))


class _RoomRole(StrEnum):
    """Why a room exists in the lowered netlist layout."""

    INPUT_INTERFACE = "input_interface"
    INPUT_DEMULTIPLEXER = "input_demultiplexer"
    FANOUT = "fanout"
    PACKER = "packer"
    PRIMITIVE = "primitive"
    OUTPUT_JOINER = "output_joiner"
    OUTPUT_INTERFACE = "output_interface"


@dataclass(frozen=True)
class _Rect:
    """An inclusive integer-grid rectangle."""

    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width - 1

    @property
    def bottom(self) -> int:
        return self.top + self.height - 1

    @property
    def cells(self) -> frozenset[tuple[int, int]]:
        return frozenset(
            (x, y)
            for y in range(self.top, self.bottom + 1)
            for x in range(self.left, self.right + 1)
        )


@dataclass(frozen=True)
class _PortAnchor:
    """A placed port with its stub and reserved route-exit corridor."""

    name: str
    flow: _AdapterFlow
    side: Side
    wall: tuple[int, int]
    stub: tuple[tuple[int, int], tuple[int, int]]
    anchor: tuple[int, int]
    direction: tuple[int, int]
    escape: tuple[tuple[int, int], tuple[int, int]]
    instructions: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _RoomPlacement:
    """One active room inside a padded, non-overlapping reservation."""

    name: str
    role: _RoomRole
    level: int
    room: ActiveRoom
    origin: tuple[int, int]
    bounds: _Rect
    footprint: _Rect
    ports: tuple[_PortAnchor, ...]
    keepout: frozenset[tuple[int, int]]


@dataclass(frozen=True)
class _PortRef:
    """A stable reference to one named port before routing."""

    room: str
    port: str


@dataclass(frozen=True)
class _Connection:
    """An unrouted scalar connection between two exterior anchors."""

    signal: str
    source: _PortRef
    target: _PortRef


@dataclass(frozen=True)
class _Layout:
    """Immutable placement output consumed by the later routing task."""

    placements: tuple[_RoomPlacement, ...]
    connections: tuple[_Connection, ...]
    routing_aisles: tuple[_Rect, ...]
    keepout: frozenset[tuple[int, int]]


class _UnroutableError(ValueError):
    """A layout cannot be embedded by the planar no-crossover backend."""


@dataclass(frozen=True)
class _RoutedConnection:
    """One logical connection and its source-to-target pipe cells."""

    connection: _Connection
    path: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _RoutedLayout:
    """Immutable routing output consumed by the final rendering task."""

    layout: _Layout
    routes: tuple[_RoutedConnection, ...]


@dataclass(frozen=True)
class _RawGrid:
    """Internal unfinalized source plus its global-to-grid translation."""

    source: str
    origin: tuple[int, int]

    def to_grid(self, cell: tuple[int, int]) -> tuple[int, int]:
        return (cell[0] - self.origin[0], cell[1] - self.origin[1])


@dataclass(frozen=True)
class _RoomSpec:
    name: str
    role: _RoomRole
    level: int
    room: ActiveRoom
    ports: tuple[_AdapterPort, ...]


_STUB_LENGTH = 2
_DEFAULT_CLEARANCE = 2
_ROOM_AISLE = 6
_LEVEL_AISLE = 6
_ROUTING_RETRIES = 96
_SIDE_DIRECTIONS: Mapping[Side, tuple[int, int]] = MappingProxyType(
    {
        Side.NORTH: (0, -1),
        Side.EAST: (1, 0),
        Side.SOUTH: (0, 1),
        Side.WEST: (-1, 0),
    }
)
_SIDE_ORDER = (Side.NORTH, Side.EAST, Side.SOUTH, Side.WEST)
_ROLE_ORDER: Mapping[_RoomRole, int] = MappingProxyType(
    {
        _RoomRole.INPUT_INTERFACE: 0,
        _RoomRole.INPUT_DEMULTIPLEXER: 1,
        _RoomRole.FANOUT: 2,
        _RoomRole.PACKER: 3,
        _RoomRole.PRIMITIVE: 4,
        _RoomRole.OUTPUT_JOINER: 5,
        _RoomRole.OUTPUT_INTERFACE: 6,
    }
)


def _plan_layout(
    netlist: Netlist,
    primitive_directory: str | PathLike[str] | None = None,
) -> _Layout:
    """Lower and deterministically place a validated scalar netlist.

    This reserves room geometry and two-cell port stubs only.  Connections
    deliberately contain endpoint references rather than searched routes.
    """

    if not netlist.inputs:
        raise ValueError("Layout requires at least one declared input signal")
    if not netlist.outputs:
        raise ValueError("Layout requires at least one selected output signal")

    primitive_root = (
        Path(primitive_directory)
        if primitive_directory is not None
        else Path(__file__).parents[1] / "tasks" / "solutions" / "primitives"
    )
    specs, connections = _lower_layout_rooms(netlist, primitive_root)
    placements, aisles = _place_room_specs(specs)
    keepout = frozenset().union(*(placement.keepout for placement in placements))
    return _Layout(
        placements=placements,
        connections=connections,
        routing_aisles=aisles,
        keepout=keepout,
    )


def _lower_layout_rooms(
    netlist: Netlist,
    primitive_root: Path,
) -> tuple[tuple[_RoomSpec, ...], tuple[_Connection, ...]]:
    specs: list[_RoomSpec] = []
    connections: list[_Connection] = []
    signal_sources: dict[str, _PortRef] = {}
    signal_targets: dict[str, list[_PortRef]] = {signal: [] for signal in netlist.producers}
    fanout_orientation_counter = [0]
    gates_per_level = Counter(netlist.levels[gate.output] for gate in netlist.gates)
    gate_ordinal_by_level: Counter[int] = Counter()

    specs.append(_io_interface_spec("input.io", _RoomRole.INPUT_INTERFACE, 0, "I"))
    input_adapter = _make_input_demultiplexer(len(netlist.inputs))
    specs.append(_adapter_spec("input", _RoomRole.INPUT_DEMULTIPLEXER, 0, input_adapter))
    connections.append(
        _Connection(
            signal="input:frame",
            source=_PortRef("input.io", "frame"),
            target=_PortRef("input", "frame"),
        )
    )
    signal_sources.update(
        {
            signal: _PortRef("input", f"field[{index}]")
            for index, signal in enumerate(netlist.inputs)
        }
    )

    for index, gate in enumerate(netlist.gates):
        level = netlist.levels[gate.output]
        level_ordinal = gate_ordinal_by_level[level]
        gate_ordinal_by_level[level] += 1
        gate_name = f"gate[{index}]"
        room = extract_active_room(primitive_root / gate.kind)
        primitive_ports = _select_primitive_ports(room, contract_for(gate.kind))
        specs.append(
            _RoomSpec(
                name=gate_name,
                role=_RoomRole.PRIMITIVE,
                level=level,
                room=room,
                ports=primitive_ports,
            )
        )

        if len(gate.inputs) == 1:
            signal_targets[gate.inputs[0]].append(_PortRef(gate_name, "input"))
        else:
            packer_name = f"{gate_name}.packer"
            packer = (
                _make_two_field_packer()
                if len(gate.inputs) == 2
                else _make_output_joiner(len(gate.inputs))
            )
            if gates_per_level[level] > 1 and level_ordinal % 2 == 0:
                packer = _mirror_adapter_vertically(packer)
            specs.append(_adapter_spec(packer_name, _RoomRole.PACKER, level, packer))
            for field_index, signal in enumerate(gate.inputs):
                signal_targets[signal].append(_PortRef(packer_name, f"field[{field_index}]"))
            connections.append(
                _Connection(
                    signal=f"{gate.output}:input-frame",
                    source=_PortRef(packer_name, "frame"),
                    target=_PortRef(gate_name, "input"),
                )
            )

        signal_sources[gate.output] = _PortRef(gate_name, "output")

    output_level = max(netlist.levels[signal] for signal in netlist.outputs) + 1
    output_adapter = _make_output_joiner(len(netlist.outputs))
    specs.append(_adapter_spec("output", _RoomRole.OUTPUT_JOINER, output_level, output_adapter))
    specs.append(_io_interface_spec("output.io", _RoomRole.OUTPUT_INTERFACE, output_level, "O"))
    connections.append(
        _Connection(
            signal="output:frame",
            source=_PortRef("output", "frame"),
            target=_PortRef("output.io", "frame"),
        )
    )
    for index, signal in enumerate(netlist.outputs):
        signal_targets[signal].append(_PortRef("output", f"field[{index}]"))

    for signal in netlist.producers:
        _connect_signal_with_fanout(
            signal=signal,
            source=signal_sources[signal],
            targets=signal_targets[signal],
            level=netlist.levels[signal],
            specs=specs,
            connections=connections,
            counter=[0],
            orientation_counter=fanout_orientation_counter,
        )

    return tuple(specs), tuple(connections)


def _adapter_spec(
    name: str,
    role: _RoomRole,
    level: int,
    adapter: _GeneratedAdapter,
) -> _RoomSpec:
    _validate_adapter_ports(name, adapter)
    return _RoomSpec(name=name, role=role, level=level, room=adapter.room, ports=adapter.ports)


def _io_interface_spec(
    name: str,
    role: _RoomRole,
    level: int,
    label: str,
) -> _RoomSpec:
    """Create a structural one-pipe runtime input or output boundary room."""

    room = _room_from_grid(("+-+", f"|{label}|", "+-+"))
    if label == "I":
        port = _AdapterPort(
            name="frame",
            flow=_AdapterFlow.OUTGOING,
            side=Side.EAST,
            wall=(2, 1),
            instructions=(),
        )
    elif label == "O":
        port = _AdapterPort(
            name="frame",
            flow=_AdapterFlow.INCOMING,
            side=Side.WEST,
            wall=(0, 1),
            instructions=(),
        )
    else:
        raise ValueError(f"Unknown runtime I/O room label {label!r}")
    return _RoomSpec(name=name, role=role, level=level, room=room, ports=(port,))


def _connect_signal_with_fanout(
    *,
    signal: str,
    source: _PortRef,
    targets: Sequence[_PortRef],
    level: int,
    specs: list[_RoomSpec],
    connections: list[_Connection],
    counter: list[int],
    orientation_counter: list[int],
) -> None:
    if not targets:
        raise ValueError(f"Signal {signal!r} has no consumer or selected output")
    if len(targets) == 1:
        connections.append(_Connection(signal, source, targets[0]))
        return

    branch_count = min(13, len(targets))
    fanout_name = f"fanout[{signal}][{counter[0]}]"
    counter[0] += 1
    fanout = _make_scalar_fanout(branch_count)
    if orientation_counter[0] % 2:
        fanout = _mirror_adapter_vertically(fanout)
    orientation_counter[0] += 1
    specs.append(_adapter_spec(fanout_name, _RoomRole.FANOUT, level, fanout))
    connections.append(_Connection(signal, source, _PortRef(fanout_name, "input")))

    groups = _balanced_groups(tuple(targets), branch_count)
    for index, group in enumerate(groups):
        _connect_signal_with_fanout(
            signal=signal,
            source=_PortRef(fanout_name, f"copy[{index}]"),
            targets=group,
            level=level,
            specs=specs,
            connections=connections,
            counter=counter,
            orientation_counter=orientation_counter,
        )


def _balanced_groups(
    values: tuple[_PortRef, ...],
    group_count: int,
) -> tuple[tuple[_PortRef, ...], ...]:
    short_size, longer_groups = divmod(len(values), group_count)
    groups: list[tuple[_PortRef, ...]] = []
    start = 0
    for index in range(group_count):
        size = short_size + (1 if index < longer_groups else 0)
        groups.append(values[start : start + size])
        start += size
    return tuple(groups)


def _select_primitive_ports(
    room: ActiveRoom,
    contract: PrimitiveContract,
) -> tuple[_AdapterPort, _AdapterPort]:
    incoming = _instruction_cells(room, frozenset("rRUq"))
    outgoing = _instruction_cells(room, frozenset("sS"))
    if not incoming:
        raise ValueError(f"Primitive {contract.artifact!r} active room has no receive instruction")
    if not outgoing:
        raise ValueError(f"Primitive {contract.artifact!r} active room has no send instruction")

    input_candidates = _ranked_wall_candidates(room, contract.input_port, incoming)
    output_candidates = _ranked_wall_candidates(room, contract.output_port, outgoing)
    for input_side, input_wall in input_candidates:
        input_stub = frozenset(_local_stub(input_side, input_wall))
        for output_side, output_wall in output_candidates:
            if input_wall == output_wall:
                continue
            if not input_stub.isdisjoint(_local_stub(output_side, output_wall)):
                continue
            return (
                _AdapterPort(
                    name="input",
                    flow=_AdapterFlow.INCOMING,
                    side=input_side,
                    wall=input_wall,
                    instructions=incoming,
                ),
                _AdapterPort(
                    name="output",
                    flow=_AdapterFlow.OUTGOING,
                    side=output_side,
                    wall=output_wall,
                    instructions=outgoing,
                ),
            )

    raise ValueError(
        f"Primitive {contract.artifact!r} cannot place distinct non-corner input/output stubs"
    )


def _instruction_cells(
    room: ActiveRoom,
    glyphs: frozenset[str],
) -> tuple[tuple[int, int], ...]:
    return tuple(
        (x, y) for y, row in enumerate(room.grid) for x, cell in enumerate(row) if cell in glyphs
    )


def _ranked_wall_candidates(
    room: ActiveRoom,
    port: PortContract,
    instructions: tuple[tuple[int, int], ...],
) -> tuple[tuple[Side, tuple[int, int]], ...]:
    candidates: list[tuple[tuple[int, int, int, int, int], Side, tuple[int, int]]] = []
    for side_index, side in enumerate(_SIDE_ORDER):
        if side not in port.allowed_sides:
            continue
        for wall in _side_wall_cells(room, side):
            distances = [
                abs(wall[0] - instruction[0]) + abs(wall[1] - instruction[1])
                for instruction in instructions
            ]
            candidates.append(
                (
                    (max(distances), sum(distances), side_index, wall[1], wall[0]),
                    side,
                    wall,
                )
            )
    if not candidates:
        raise ValueError("Port contract has no legal non-corner wall attachment")
    candidates.sort()
    return tuple((side, wall) for _, side, wall in candidates)


def _side_wall_cells(room: ActiveRoom, side: Side) -> tuple[tuple[int, int], ...]:
    if side is Side.NORTH:
        return tuple((x, 0) for x in range(1, room.width - 1))
    if side is Side.EAST:
        return tuple((room.width - 1, y) for y in range(1, room.height - 1))
    if side is Side.SOUTH:
        return tuple((x, room.height - 1) for x in range(1, room.width - 1))
    return tuple((0, y) for y in range(1, room.height - 1))


def _local_stub(
    side: Side,
    wall: tuple[int, int],
) -> tuple[tuple[int, int], tuple[int, int]]:
    dx, dy = _SIDE_DIRECTIONS[side]
    return (
        (wall[0] + dx, wall[1] + dy),
        (wall[0] + 2 * dx, wall[1] + 2 * dy),
    )


def _validate_adapter_ports(name: str, adapter: _GeneratedAdapter) -> None:
    for port in adapter.ports:
        if port.wall not in _side_wall_cells(adapter.room, port.side):
            raise ValueError(
                f"Adapter {name!r} port {port.name!r} is not on a non-corner "
                f"{port.side.value} wall cell"
            )
        if not port.instructions:
            raise ValueError(f"Adapter {name!r} port {port.name!r} has no instructions")


def _place_room_specs(
    specs: tuple[_RoomSpec, ...],
) -> tuple[tuple[_RoomPlacement, ...], tuple[_Rect, ...]]:
    indexed_specs = tuple(enumerate(specs))
    levels = sorted({spec.level for spec in specs})
    specs_by_level = {
        level: _order_level_specs(
            tuple((index, spec) for index, spec in indexed_specs if spec.level == level)
        )
        for level in levels
    }
    band_widths = {
        level: sum(
            spec.room.width + 2 * (_STUB_LENGTH + _DEFAULT_CLEARANCE)
            for _, spec in specs_by_level[level]
        )
        + _ROOM_AISLE * (len(specs_by_level[level]) - 1)
        for level in levels
    }
    max_band_width = max(band_widths.values())
    placements: list[_RoomPlacement] = []
    band_ranges: list[tuple[int, int]] = []
    band_top = 0

    for level in levels:
        level_specs = specs_by_level[level]
        band_height = max(
            spec.room.height + 2 * (_STUB_LENGTH + _DEFAULT_CLEARANCE) for _, spec in level_specs
        )
        left = (max_band_width - band_widths[level]) // 2
        for _, spec in level_specs:
            placement = _place_room_spec(spec, left, band_top)
            placements.append(placement)
            left = placement.footprint.right + 1 + _ROOM_AISLE
        band_ranges.append((band_top, band_top + band_height - 1))
        band_top += band_height + _LEVEL_AISLE

    max_right = max(placement.footprint.right for placement in placements)
    aisles = tuple(
        _Rect(
            left=0,
            top=upper_bottom + 1,
            width=max_right + 1,
            height=lower_top - upper_bottom - 1,
        )
        for (_, upper_bottom), (lower_top, _) in zip(band_ranges, band_ranges[1:], strict=False)
    )
    return tuple(placements), aisles


def _order_level_specs(
    indexed_specs: tuple[tuple[int, _RoomSpec], ...],
) -> tuple[tuple[int, _RoomSpec], ...]:
    """Reserve a deterministic planar order for generated sibling branches."""

    standard = tuple(sorted(indexed_specs, key=lambda item: (_ROLE_ORDER[item[1].role], item[0])))
    fanouts = tuple(item for item in standard if item[1].role is _RoomRole.FANOUT)
    input_core = tuple(
        item
        for item in standard
        if item[1].role in {_RoomRole.INPUT_INTERFACE, _RoomRole.INPUT_DEMULTIPLEXER}
    )
    if len(fanouts) >= 2 and len(fanouts) + len(input_core) == len(standard):
        left = tuple(reversed(fanouts[::2]))
        right = fanouts[1::2]
        return left + input_core + right

    packers = {
        spec.name.removesuffix(".packer"): (index, spec)
        for index, spec in standard
        if spec.role is _RoomRole.PACKER and spec.name.endswith(".packer")
    }
    pairs = [
        (packers[spec.name], (index, spec))
        for index, spec in standard
        if spec.role is _RoomRole.PRIMITIVE and spec.name in packers
    ]
    if len(pairs) >= 2 and 2 * len(pairs) == len(standard):
        left = tuple(
            item for packer, primitive in reversed(pairs[1::2]) for item in (packer, primitive)
        )
        right = tuple(item for packer, primitive in pairs[::2] for item in (primitive, packer))
        return left + right

    return standard


def _place_room_spec(spec: _RoomSpec, left: int, top: int) -> _RoomPlacement:
    margin = _STUB_LENGTH + _DEFAULT_CLEARANCE
    footprint = _Rect(
        left=left,
        top=top,
        width=spec.room.width + 2 * margin,
        height=spec.room.height + 2 * margin,
    )
    origin = (left + margin, top + margin)
    bounds = _Rect(origin[0], origin[1], spec.room.width, spec.room.height)
    ports = tuple(_place_port(port, origin) for port in spec.ports)
    occupied = bounds.cells | frozenset(cell for port in ports for cell in port.stub)
    keepout = _expand_cells(occupied, _DEFAULT_CLEARANCE)
    if not keepout <= footprint.cells:
        raise ValueError(f"Room {spec.name!r} keepout exceeds its padded footprint")
    return _RoomPlacement(
        name=spec.name,
        role=spec.role,
        level=spec.level,
        room=spec.room,
        origin=origin,
        bounds=bounds,
        footprint=footprint,
        ports=ports,
        keepout=keepout,
    )


def _place_port(
    port: _AdapterPort,
    origin: tuple[int, int],
) -> _PortAnchor:
    wall = (origin[0] + port.wall[0], origin[1] + port.wall[1])
    direction = _SIDE_DIRECTIONS[port.side]
    stub = (
        (wall[0] + direction[0], wall[1] + direction[1]),
        (wall[0] + 2 * direction[0], wall[1] + 2 * direction[1]),
    )
    escape = (
        (wall[0] + 3 * direction[0], wall[1] + 3 * direction[1]),
        (wall[0] + 4 * direction[0], wall[1] + 4 * direction[1]),
    )
    return _PortAnchor(
        name=port.name,
        flow=port.flow,
        side=port.side,
        wall=wall,
        stub=stub,
        anchor=stub[-1],
        direction=direction,
        escape=escape,
        instructions=tuple((origin[0] + x, origin[1] + y) for x, y in port.instructions),
    )


def _expand_cells(
    cells: frozenset[tuple[int, int]],
    clearance: int,
) -> frozenset[tuple[int, int]]:
    return frozenset(
        (x + dx, y + dy)
        for x, y in cells
        for dy in range(-clearance, clearance + 1)
        for dx in range(-clearance, clearance + 1)
    )


def _route_layout(layout: _Layout) -> _RoutedLayout:
    """Route every placed connection with deterministic negotiated congestion."""

    if not layout.connections:
        return _RoutedLayout(layout=layout, routes=())

    ports = _ports_by_reference(layout)
    bounds = _routing_search_bounds(layout)
    uncongested: dict[int, tuple[tuple[int, int], ...]] = {}
    for index, connection in enumerate(layout.connections):
        source, target = _connection_ports(connection, ports)
        path = _search_connection_path(
            layout,
            source,
            target,
            bounds,
            occupancy=Counter(),
            history=Counter(),
            present_penalty=0,
        )
        if path is None:
            raise _UnroutableError(
                "Unroutable scalar layout for no-crossover backend: "
                f"connection {connection.signal!r} cannot leave its reserved endpoints"
            )
        uncongested[index] = path

    order = tuple(
        sorted(
            range(len(layout.connections)),
            key=lambda index: (-len(uncongested[index]), index),
        )
    )
    routes: dict[int, tuple[tuple[int, int], ...]] = {}
    history: Counter[tuple[int, int]] = Counter()

    for retry in range(_ROUTING_RETRIES):
        occupancy = Counter(cell for path in routes.values() for cell in path)
        for index in order:
            previous = routes.get(index, ())
            occupancy.subtract(previous)
            path = _search_connection_path(
                layout,
                *_connection_ports(layout.connections[index], ports),
                bounds,
                occupancy=occupancy,
                history=history,
                present_penalty=100 * (retry + 1),
            )
            if path is None:
                raise _UnroutableError(
                    "Unroutable scalar layout for no-crossover backend: "
                    f"connection {layout.connections[index].signal!r} has no path"
                )
            routes[index] = path
            occupancy.update(path)

        conflicts = tuple(cell for cell, count in occupancy.items() if count > 1)
        if not conflicts:
            return _RoutedLayout(
                layout=layout,
                routes=tuple(
                    _RoutedConnection(connection, routes[index])
                    for index, connection in enumerate(layout.connections)
                ),
            )
        for cell in conflicts:
            history[cell] += occupancy[cell] - 1

    raise _UnroutableError(
        "Unroutable scalar layout for no-crossover backend after "
        f"{_ROUTING_RETRIES} deterministic conflict retries"
    )


def _ports_by_reference(
    layout: _Layout,
) -> Mapping[tuple[str, str], _PortAnchor]:
    ports: dict[tuple[str, str], _PortAnchor] = {}
    for placement in layout.placements:
        for port in placement.ports:
            reference = (placement.name, port.name)
            if reference in ports:
                raise ValueError(f"Duplicate placed port reference {reference!r}")
            ports[reference] = port
    return MappingProxyType(ports)


def _connection_ports(
    connection: _Connection,
    ports: Mapping[tuple[str, str], _PortAnchor],
) -> tuple[_PortAnchor, _PortAnchor]:
    try:
        source = ports[(connection.source.room, connection.source.port)]
        target = ports[(connection.target.room, connection.target.port)]
    except KeyError as error:
        raise ValueError(f"Connection {connection.signal!r} references an unknown port") from error
    if source.flow is not _AdapterFlow.OUTGOING:
        raise ValueError(f"Connection {connection.signal!r} source is not an outgoing port")
    if target.flow is not _AdapterFlow.INCOMING:
        raise ValueError(f"Connection {connection.signal!r} target is not an incoming port")
    return source, target


def _routing_search_bounds(layout: _Layout) -> _Rect:
    margin = max(12, 2 * len(layout.connections) + 4)
    left = min(placement.footprint.left for placement in layout.placements) - margin
    top = min(placement.footprint.top for placement in layout.placements) - margin
    right = max(placement.footprint.right for placement in layout.placements) + margin
    bottom = max(placement.footprint.bottom for placement in layout.placements) + margin
    return _Rect(left, top, right - left + 1, bottom - top + 1)


def _search_connection_path(
    layout: _Layout,
    source: _PortAnchor,
    target: _PortAnchor,
    bounds: _Rect,
    *,
    occupancy: Counter[tuple[int, int]],
    history: Counter[tuple[int, int]],
    present_penalty: int,
) -> tuple[tuple[int, int], ...] | None:
    start = source.escape[-1]
    goal = target.escape[-1]
    blocked = set(layout.keepout)
    blocked.difference_update((start, goal))
    blocked.update((source.escape[0], target.escape[0]))
    blocked.difference_update((start, goal))

    distance: dict[tuple[int, int], int] = {start: 0}
    previous: dict[tuple[int, int], tuple[int, int]] = {}
    frontier: list[tuple[int, int, int, int]] = [
        (_manhattan(start, goal) * 10, 0, start[1], start[0])
    ]
    directions = ((0, -1), (-1, 0), (1, 0), (0, 1))

    while frontier:
        _, cost, y, x = heappop(frontier)
        cell = (x, y)
        if cost != distance.get(cell):
            continue
        if cell == goal:
            break
        for dx, dy in directions:
            neighbor = (x + dx, y + dy)
            if (
                neighbor[0] < bounds.left
                or neighbor[0] > bounds.right
                or neighbor[1] < bounds.top
                or neighbor[1] > bounds.bottom
                or neighbor in blocked
            ):
                continue
            next_cost = cost + 10 + 100 * history[neighbor] + present_penalty * occupancy[neighbor]
            if next_cost >= distance.get(neighbor, 2**63 - 1):
                continue
            distance[neighbor] = next_cost
            previous[neighbor] = cell
            estimate = next_cost + 10 * _manhattan(neighbor, goal)
            heappush(frontier, (estimate, next_cost, neighbor[1], neighbor[0]))

    if goal not in distance:
        return None

    middle = [goal]
    while middle[-1] != start:
        middle.append(previous[middle[-1]])
    middle.reverse()
    return (
        source.stub
        + source.escape
        + tuple(middle[1:-1])
        + tuple(reversed(target.escape))
        + tuple(reversed(target.stub))
    )


def _manhattan(first: tuple[int, int], second: tuple[int, int]) -> int:
    return abs(first[0] - second[0]) + abs(first[1] - second[1])


def _render_raw_grid(routed: _RoutedLayout) -> _RawGrid:
    """Assemble an internal rectangular route-oracle fixture without final cropping."""

    canvas: dict[tuple[int, int], str] = {}
    for placement in routed.layout.placements:
        for local_y, row in enumerate(placement.room.grid):
            for local_x, glyph in enumerate(row):
                canvas[(placement.origin[0] + local_x, placement.origin[1] + local_y)] = glyph

    for route in routed.routes:
        for index, cell in enumerate(route.path):
            glyph = _route_glyph(route.path, index)
            existing = canvas.get(cell, " ")
            if existing != " ":
                raise ValueError(f"Routed pipe overlaps occupied grid cell {cell}")
            canvas[cell] = glyph

    if not canvas:
        return _RawGrid(source="", origin=(0, 0))

    min_x = min(x for x, _ in canvas) - 1
    max_x = max(x for x, _ in canvas) + 1
    min_y = min(y for _, y in canvas) - 1
    max_y = max(y for _, y in canvas) + 1
    source = "\n".join(
        "".join(canvas.get((x, y), " ") for x in range(min_x, max_x + 1))
        for y in range(min_y, max_y + 1)
    )
    return _RawGrid(source=source, origin=(min_x, min_y))


def compose(netlist: Netlist) -> str:
    """Compose a validated netlist into deterministic, cropped Little Man source."""

    raw = _render_raw_grid(_route_layout(_plan_layout(netlist)))
    return _crop_source(raw.source)


def write(netlist: Netlist, path: str | Path) -> Path:
    """Compose ``netlist`` and write its exact source to the requested path."""

    destination = Path(path)
    source = compose(netlist)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source, encoding="utf-8")
    return destination


def _crop_source(source: str) -> str:
    """Crop to the smallest non-whitespace rectangle and retain a final newline."""

    rows = source.splitlines()
    occupied = [
        (x, y) for y, row in enumerate(rows) for x, glyph in enumerate(row) if not glyph.isspace()
    ]
    if not occupied:
        return ""

    left = min(x for x, _ in occupied)
    right = max(x for x, _ in occupied)
    top = min(y for _, y in occupied)
    bottom = max(y for _, y in occupied)
    return "\n".join(row[left : right + 1] for row in rows[top : bottom + 1]) + "\n"


def _route_glyph(
    path: tuple[tuple[int, int], ...],
    index: int,
) -> str:
    if len(path) < 2:
        raise ValueError("Little Man pipes require at least two cells")
    if index < len(path) - 1:
        first, second = path[index], path[index + 1]
    else:
        first, second = path[index - 1], path[index]
    direction = (second[0] - first[0], second[1] - first[1])
    try:
        return {
            (0, -1): "^",
            (1, 0): ">",
            (0, 1): "v",
            (-1, 0): "<",
        }[direction]
    except KeyError as error:
        raise ValueError(f"Route contains non-adjacent cells {first} and {second}") from error
