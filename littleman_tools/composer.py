"""Validated model for ordered scalar Little Man primitive netlists."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .primitive_contracts import contracts_by_artifact

__all__ = ["Gate", "Netlist"]


@dataclass(frozen=True)
class Gate:
    """One primitive invocation in an ordered scalar netlist."""

    kind: str
    inputs: tuple[str, ...]
    output: str


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
