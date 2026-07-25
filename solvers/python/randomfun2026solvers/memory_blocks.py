#!/usr/bin/env python3
"""Composable, marked building blocks for Littleman memory machines.

This module deliberately owns geometry.  A memory algorithm should connect
named ports such as ``delta.out`` and ``pass.in``; it must not know where those
ports live on the final canvas.  Every placement and route also emits its debug
region/lane, so the visualisation cannot drift away from the machine layout.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

from randomfun2026solvers.circuit import Circuit, E, GLYPH
from randomfun2026solvers.man_debug import DebugMap

Cell = tuple[int, int]
Direction = tuple[int, int]


@dataclass(frozen=True)
class Port:
    """A block-local runner handoff point and its expected heading."""

    x: int
    y: int
    heading: Direction
    note: str = ""


@dataclass(frozen=True)
class PipePort:
    """A device-local pipe endpoint, one cell outside its room wall."""

    x: int
    y: int
    heading: Direction
    role: Literal["in", "out"]
    note: str = ""


@dataclass(frozen=True)
class Block:
    """A collision-checked local program fragment with named handoff ports."""

    name: str
    circuit: Circuit
    ports: dict[str, Port]
    note: str
    color: str

    @property
    def width(self) -> int:
        return self.circuit.w

    @property
    def height(self) -> int:
        return self.circuit.h


@dataclass(frozen=True)
class Device:
    """A persistent container with named pipe endpoints rather than runner ports."""

    name: str
    circuit: Circuit
    ports: dict[str, PipePort]
    note: str
    color: str

    @property
    def width(self) -> int:
        return self.circuit.w

    @property
    def height(self) -> int:
        return self.circuit.h


@dataclass(frozen=True)
class PlacedBlock:
    """A block after placement in an :class:`Assembly`."""

    block: Block
    origin: Cell

    def port(self, name: str) -> Port:
        try:
            local = self.block.ports[name]
        except KeyError as exc:
            raise KeyError(f"{self.block.name!r} has no port {name!r}") from exc
        return Port(self.origin[0] + local.x, self.origin[1] + local.y, local.heading, local.note)


@dataclass(frozen=True)
class PlacedDevice:
    """A device after placement, retaining its pipe port semantics."""

    device: Device
    origin: Cell

    def port(self, name: str) -> PipePort:
        try:
            local = self.device.ports[name]
        except KeyError as exc:
            raise KeyError(f"{self.device.name!r} has no port {name!r}") from exc
        return PipePort(
            self.origin[0] + local.x,
            self.origin[1] + local.y,
            local.heading,
            local.role,
            local.note,
        )


@dataclass(frozen=True)
class DataFlow:
    """A value contract reviewed before its producing/consuming blocks are fixed.

    ``transient`` values should normally be co-located or carried in registers.
    ``persistent`` values need storage.  ``ring`` is the one intentionally long
    flow: the memory tape itself.  This makes accidental long control pipes
    visible in the plan before a coordinate is selected.
    """

    name: str
    value: str
    producer: str
    consumers: tuple[str, ...]
    lifetime: Literal["transient", "persistent", "ring"]
    pin_reason: str = ""

    @property
    def movable(self) -> bool:
        return self.lifetime == "transient" and not self.pin_reason


@dataclass
class Assembly:
    """A named block canvas that owns both code and debug metadata."""

    width: int
    height: int
    title: str
    circuit: Circuit = field(init=False)
    debug: DebugMap = field(init=False)
    blocks: dict[str, PlacedBlock] = field(default_factory=dict, init=False)
    devices: dict[str, PlacedDevice] = field(default_factory=dict, init=False)
    flows: dict[str, DataFlow] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.circuit = Circuit(self.width, self.height)
        self.debug = DebugMap(self.title)

    def place(self, alias: str, block: Block, at: Cell) -> PlacedBlock:
        """Stamp a block and expose it under a stable, algorithmic name."""
        if alias in self.blocks:
            raise ValueError(f"duplicate block alias {alias!r}")
        ox, oy = at
        for (x, y), glyph in block.circuit.cell.items():
            self.circuit.set(ox + x, oy + y, glyph)
        placed = PlacedBlock(block, at)
        self.blocks[alias] = placed
        self.debug.region(alias, ox, oy, block.width, block.height, note=block.note, color=block.color)
        return placed

    def place_device(self, alias: str, device: Device, at: Cell) -> PlacedDevice:
        """Stamp a stateful device, keeping its pipe interface named and local."""
        if alias in self.devices or alias in self.blocks:
            raise ValueError(f"duplicate container alias {alias!r}")
        ox, oy = at
        for (x, y), glyph in device.circuit.cell.items():
            self.circuit.set(ox + x, oy + y, glyph)
        placed = PlacedDevice(device, at)
        self.devices[alias] = placed
        self.debug.region(alias, ox, oy, device.width, device.height, note=device.note, color=device.color)
        return placed

    def device_port(self, ref: str) -> PipePort:
        """Resolve a device ``block.port`` reference to a global pipe endpoint."""
        alias, sep, port = ref.partition(".")
        if not sep:
            raise ValueError(f"pipe port reference must be device.port, got {ref!r}")
        try:
            return self.devices[alias].port(port)
        except KeyError as exc:
            raise KeyError(f"unknown device in pipe port reference {ref!r}") from exc

    def endpoint(self, ref: str) -> Port:
        """Resolve ``block.port`` to its placed global port."""
        alias, sep, port = ref.partition(".")
        if not sep:
            raise ValueError(f"port reference must be block.port, got {ref!r}")
        try:
            return self.blocks[alias].port(port)
        except KeyError as exc:
            raise KeyError(f"unknown block in port reference {ref!r}") from exc

    def connect(
        self,
        name: str,
        source: str,
        target: str,
        *,
        via: Iterable[Cell] = (),
        note: str,
        color: str,
    ) -> None:
        """Route and mark a runner lane between two named ports.

        The source heading and target heading are carried by the blocks.  A
        collision here is a layout error, never a silently-crossing corridor.
        """
        start = self.endpoint(source)
        end = self.endpoint(target)
        points = [(start.x, start.y), *via, (end.x, end.y)]
        self.circuit.route(points[0], start.heading, points[1:-1], points[-1], end.heading)
        self.debug.lane(name, points, kind="expected", expect=note, color=color)

    def declare_flow(self, flow: DataFlow) -> None:
        """Record a dataflow before committing it to a physical route."""
        if flow.name in self.flows:
            raise ValueError(f"duplicate dataflow {flow.name!r}")
        self.endpoint(flow.producer)
        for consumer in flow.consumers:
            self.endpoint(consumer)
        self.flows[flow.name] = flow

    def movable_flows(self) -> list[DataFlow]:
        """Transient flows that must be reconsidered before adding a pipe."""
        return [flow for flow in self.flows.values() if flow.movable]

    def rows(self) -> list[str]:
        return [row.rstrip() for row in self.circuit.rows() if row.strip()]


def vertical_rail(
    name: str,
    ops: str,
    *,
    note: str,
    color: str,
) -> Block:
    """A one-column operation rail entered from above and left at its bottom."""
    circuit = Circuit(1, len(ops) + 1)
    circuit.run(0, 0, ops, d=(0, 1))
    circuit.set(0, len(ops), " ")
    return Block(
        name,
        circuit,
        {
            "in": Port(0, 0, (0, 1), "enter from the north"),
            "out": Port(0, len(ops), (0, 1), "leave below the rail"),
        },
        note,
        color,
    )


def horizontal_rail(
    name: str,
    ops: str,
    *,
    note: str,
    color: str,
) -> Block:
    """A one-row operation rail entered from the west and left at its east."""
    circuit = Circuit(len(ops) + 1, 1)
    circuit.run(0, 0, ops, d=E)
    circuit.set(len(ops), 0, " ")
    return Block(
        name,
        circuit,
        {
            "in": Port(0, 0, E, "enter from the west"),
            "out": Port(len(ops), 0, E, "leave east of the rail"),
        },
        note,
        color,
    )


def counted_pass(
    name: str,
    body: str = "rs",
    *,
    note: str,
    color: str,
) -> Block:
    """A reusable BP-counted loop, normally used to rotate tape values.

    The body is intentionally explicit.  ``rs`` is a pass-through rotation;
    a different two-operation body can be substituted without duplicating any
    geometry in the memory algorithm.
    """
    if not body:
        raise ValueError("counted pass body cannot be empty")
    circuit = Circuit(3, len(body) + 2)
    circuit.counted_loop(0, 0, body)
    circuit.set(2, 0, " ")
    return Block(
        name,
        circuit,
        {
            "in": Port(0, 0, E, "enter with BP=count"),
            "out": Port(2, 0, E, "leave after exactly BP passes"),
        },
        note,
        color,
    )


def east_fork(name: str, *, note: str, color: str) -> Block:
    """A ``Y`` fan-out entered from the west with north/south continuations.

    This is used by read target handling when an index command pipe is also
    outgoing from the worker: unlike ``S``, the fork can send one target copy
    to output and one to the tape without broadcasting to the index device.
    """
    circuit = Circuit(1, 3)
    circuit.set(0, 1, "Y")
    return Block(
        name,
        circuit,
        {
            "in": Port(0, 1, E, "target arrives from the west"),
            "north": Port(0, 0, (0, -1), "copy for output"),
            "south": Port(0, 2, (0, 1), "copy for tape"),
        },
        note,
        color,
    )


def backpack_branch_east(name: str, *, note: str, color: str) -> Block:
    """An east-entered ``d`` branch with zero/positive named exits."""
    circuit = Circuit(2, 2)
    circuit.set(0, 0, "d")
    circuit.set(1, 0, " ")
    circuit.set(0, 1, " ")
    return Block(
        name,
        circuit,
        {
            "in": Port(0, 0, E, "BP selects the arm"),
            "zero": Port(1, 0, E, "BP=0 continues east"),
            "positive": Port(0, 1, (0, 1), "BP>0 turns south"),
        },
        note,
        color,
    )


def fetch_transaction(name: str, *, gap: int, note: str, color: str) -> Block:
    """Send the ``-1`` fetch command, then receive its response.

    The interior gap is part of the block contract.  It is a FIFO transport
    window, never a timing loop: the runner blocks at ``r`` until the value
    arrives.  The caller chooses the device by placing the command/response
    pipes nearest to their respective instructions.
    """
    if gap < 0:
        raise ValueError("fetch gap must be non-negative")
    width = 3 + gap + 1 + 1
    circuit = Circuit(width, 1)
    circuit.run(0, 0, "1Ns")
    circuit.blanks(3, 0, gap)
    circuit.run(3 + gap, 0, "r")
    circuit.set(width - 1, 0, " ")
    return Block(
        name,
        circuit,
        {
            "in": Port(0, 0, E, "enter fetch transaction"),
            "out": Port(width - 1, 0, E, "response is now in A"),
            "command": Port(2, 0, E, "send -1 to the selected cell"),
            "response": Port(3 + gap, 0, E, "receive selected cell value"),
        },
        note,
        color,
    )


_REGISTER_ART = (
    "+----+",
    "|vWs<|",
    "|v  W|",
    "|>@RX|",
    "|^  W|",
    "|^Wr<|",
    "+----+",
)


def register_cell(name: str, *, note: str, color: str) -> Device:
    """A reusable one-value FIFO register device.

    Its protocol is ``-1 -> current`` for a fetch and ``1, value`` for a
    store.  The endpoints are part of the device contract, so callers never
    duplicate the room art or guess where a pipe must terminate.
    """
    circuit = Circuit(len(_REGISTER_ART[0]), len(_REGISTER_ART))
    for y, row in enumerate(_REGISTER_ART):
        for x, glyph in enumerate(row):
            if glyph != " ":
                circuit.set(x, y, glyph)
    return Device(
        name,
        circuit,
        {
            "command": PipePort(-1, 3, E, "in", "-1 fetch; 1,value store"),
            "value": PipePort(len(_REGISTER_ART[0]), 3, E, "out", "fetch response"),
        },
        note,
        color,
    )


def turn_port(name: str, heading: Direction, *, note: str, color: str) -> Block:
    """A named single-cell handoff used when a branch owns no arithmetic."""
    circuit = Circuit(1, 1)
    circuit.set(0, 0, GLYPH[heading])
    return Block(
        name,
        circuit,
        {"in": Port(0, 0, heading), "out": Port(0, 0, heading)},
        note,
        color,
    )


def _self_test() -> None:
    """Exercise ports, loops, collision-safe routes, and integrated markers."""
    setup = vertical_rail("delta", "W-M`10`W%M", note="relative delta", color="#60a5fa")
    rotate = counted_pass("pass", note="rotate delta tape values", color="#22c55e")
    flow = Assembly(12, 20, "block fabric test")
    flow.place("delta", setup, (2, 1))
    flow.place("pass", rotate, (7, 14))
    index = register_cell("index", note="persistent head address", color="#facc15")
    split = east_fork("target-split", note="send target to output and tape", color="#a78bfa")
    branch = backpack_branch_east("opcode", note="select read/write arm", color="#f59e0b")
    fetch = fetch_transaction("index-fetch", gap=2, note="fetch index", color="#facc15")
    flow.place_device("index", index, (6, 1))
    flow.place("target-split", split, (11, 10))
    flow.place("opcode", branch, (0, 9))
    flow.place("index-fetch", fetch, (0, 18))
    flow.connect(
        "delta-to-pass",
        "delta.out",
        "pass.in",
        via=[(2, 13), (7, 13)],
        note="delta becomes the pass count",
        color="#38bdf8",
    )
    flow.declare_flow(
        DataFlow(
            "delta-count",
            "delta in BP",
            "delta.out",
            ("pass.in",),
            "transient",
        )
    )
    assert flow.endpoint("pass.out").x == 9
    assert [region.name for region in flow.debug.regions] == [
        "delta", "pass", "index", "target-split", "opcode", "index-fetch"
    ]
    assert flow.debug.lanes[0].name == "delta-to-pass"
    assert [candidate.name for candidate in flow.movable_flows()] == ["delta-count"]
    assert flow.device_port("index.command").role == "in"
    assert flow.device_port("index.value").x == 12
    assert flow.endpoint("target-split.north").heading == (0, -1)
    assert flow.endpoint("opcode.positive").heading == (0, 1)
    assert flow.endpoint("index-fetch.command").x == 2


if __name__ == "__main__":
    _self_test()
