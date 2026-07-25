"""Validated model for ordered scalar Little Man primitive netlists."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from os import PathLike
from pathlib import Path
from types import MappingProxyType

from .primitive_contracts import Side, contracts_by_artifact

__all__ = ["ActiveRoom", "Gate", "Netlist", "extract_active_room"]


@dataclass(frozen=True)
class ActiveRoom:
    """A closed runner room, normalized to its own local grid."""

    text: str
    grid: tuple[str, ...]
    width: int
    height: int
    runner: tuple[int, int]


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
            (x, y) for y, row in enumerate(grid) for x, cell in enumerate(row) if cell == "@"
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
class Netlist:
    """An ordered scalar DAG with immutable derived signal indexes."""

    inputs: tuple[str, ...]
    gates: tuple[Gate, ...]
    outputs: tuple[str, ...]
    producers: Mapping[str, Gate | None] = field(init=False, repr=False)
    consumers: Mapping[str, tuple[Gate, ...]] = field(init=False, repr=False)
    levels: Mapping[str, int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "gates", tuple(self.gates))
        object.__setattr__(self, "outputs", tuple(self.outputs))

        producers: dict[str, Gate | None] = {}
        consumers: dict[str, list[Gate]] = {}
        levels: dict[str, int] = {}

        for signal in self.inputs:
            if signal in producers:
                raise ValueError(f"Duplicate input signal {signal!r}")
            producers[signal] = None
            consumers[signal] = []
            levels[signal] = 0

        contracts = contracts_by_artifact()
        for index, gate in enumerate(self.gates):
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

            producers[gate.output] = gate
            consumers[gate.output] = []
            levels[gate.output] = max(input_levels, default=0) + 1

        for signal in self.outputs:
            if signal not in producers:
                raise ValueError(f"Selected output {signal!r} is not defined")

        object.__setattr__(self, "producers", MappingProxyType(producers))
        object.__setattr__(
            self,
            "consumers",
            MappingProxyType({signal: tuple(gates) for signal, gates in consumers.items()}),
        )
        object.__setattr__(self, "levels", MappingProxyType(levels))
