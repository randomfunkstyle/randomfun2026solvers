"""Validated model for ordered scalar Little Man primitive netlists."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from types import MappingProxyType

from .primitive_contracts import contracts_by_artifact

__all__ = ["ActiveRoom", "Gate", "Netlist", "extract_active_room"]


@dataclass(frozen=True)
class ActiveRoom:
    """A closed runner room, normalized to its own local grid."""

    text: str
    grid: tuple[str, ...]
    width: int
    height: int
    runner: tuple[int, int]


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
