"""Semantic placement contracts for the checked-in Little Man primitives.

The standalone ``.man`` files contain I/O harnesses that are useful for
testing, but a composer will extract their active rooms.  These contracts keep
the reusable facts about those rooms: one ordered serial input frame, their
port constraints, and the behaviour which depends on an incoming pipe's side.
They intentionally do not contain harness coordinates or pipe stubs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

__all__ = [
    "Direction",
    "PortContract",
    "PrimitiveContract",
    "Side",
    "contract_for",
    "contracts_by_artifact",
]


class Side(StrEnum):
    """A side of an active room, expressed independently of its harness."""

    NORTH = "north"
    EAST = "east"
    SOUTH = "south"
    WEST = "west"


class Direction(StrEnum):
    """How an active room's control flow reacts to its input side."""

    SIDE_INDEPENDENT = "side_independent"
    AWAY_FROM_INPUT = "away_from_input"


@dataclass(frozen=True)
class PortContract:
    """Constraints on one logical port of an active room."""

    required: bool
    multiplicity: int
    allowed_sides: frozenset[Side]
    corner_allowed: bool = False
    fanout_safe: bool = False


@dataclass(frozen=True)
class PrimitiveContract:
    """The serial and placement contract of a checked-in primitive artifact."""

    artifact: str
    operation: str
    input_order: tuple[str, ...]
    inputs: int
    outputs: int
    input_port: PortContract
    output_port: PortContract
    control_flow: Direction
    minimum_clearance: int


_ALL_SIDES: Final = frozenset(Side)
_NORTH_ONLY: Final = frozenset({Side.NORTH})
_WEST_ONLY: Final = frozenset({Side.WEST})
_INPUT_ANY_SIDE: Final = PortContract(True, 1, _ALL_SIDES)
_INPUT_NORTH: Final = PortContract(True, 1, _NORTH_ONLY)
_INPUT_WEST: Final = PortContract(True, 1, _WEST_ONLY)
_OUTPUT: Final = PortContract(True, 1, _ALL_SIDES)
_FANOUT_OUTPUT: Final = PortContract(True, 1, _ALL_SIDES, fanout_safe=True)


def _contract(
    artifact: str,
    operation: str,
    input_order: tuple[str, ...],
    *,
    input_port: PortContract = _INPUT_ANY_SIDE,
    output_port: PortContract = _OUTPUT,
    control_flow: Direction = Direction.SIDE_INDEPENDENT,
) -> PrimitiveContract:
    return PrimitiveContract(
        artifact=artifact,
        operation=operation,
        input_order=input_order,
        inputs=1,
        outputs=1,
        input_port=input_port,
        output_port=output_port,
        control_flow=control_flow,
        minimum_clearance=2,
    )


_CONTRACTS: Final[Mapping[str, PrimitiveContract]] = MappingProxyType(
    {
        # U turns the runner away from the incoming pipe, so these input frames
        # must enter through the north wall.  Their terminal `s` may become `S`
        # when a composed signal needs atomic fanout.
        "and-gate.man": _contract(
            "and-gate.man",
            "and",
            ("a", "b"),
            input_port=_INPUT_NORTH,
            output_port=_FANOUT_OUTPUT,
            control_flow=Direction.AWAY_FROM_INPUT,
        ),
        "xor-gate.man": _contract(
            "xor-gate.man",
            "xor",
            ("a", "b"),
            input_port=_INPUT_NORTH,
            output_port=_FANOUT_OUTPUT,
            control_flow=Direction.AWAY_FROM_INPUT,
        ),
        "and-gate-ten-tick.man": _contract("and-gate-ten-tick.man", "and", ("a", "b")),
        "or-gate.man": _contract(
            "or-gate.man",
            "or",
            ("a", "b"),
            input_port=_INPUT_NORTH,
            control_flow=Direction.AWAY_FROM_INPUT,
        ),
        "not-gate.man": _contract(
            "not-gate.man",
            "not",
            ("a",),
            input_port=_INPUT_NORTH,
            control_flow=Direction.AWAY_FROM_INPUT,
        ),
        "nand-gate.man": _contract(
            "nand-gate.man",
            "nand",
            ("a", "b"),
            input_port=_INPUT_NORTH,
            control_flow=Direction.AWAY_FROM_INPUT,
        ),
        "nand-gate-narrow.man": _contract(
            "nand-gate-narrow.man",
            "nand",
            ("a", "b"),
            input_port=_INPUT_NORTH,
            control_flow=Direction.AWAY_FROM_INPUT,
        ),
        "bit-register.man": _contract(
            "bit-register.man", "previous_bit_with_zero_reset", ("in",)
        ),
        "transistor.man": _contract("transistor.man", "controlled_forwarding", ("data", "enable")),
        "transistor-compact.man": _contract(
            "transistor-compact.man", "controlled_forwarding", ("data", "enable")
        ),
        "transistor-compact-narrow.man": _contract(
            "transistor-compact-narrow.man", "controlled_forwarding", ("data", "enable")
        ),
        # The canonical and compact MUX layouts have different entry sides.
        # Both use U/a control flow, so changing that side changes semantics.
        "mux-gate.man": _contract(
            "mux-gate.man",
            "select_first_mux",
            ("select", "i1", "i2"),
            input_port=_INPUT_WEST,
            control_flow=Direction.AWAY_FROM_INPUT,
        ),
        "mux-gate-select-first-user-u-compact.man": _contract(
            "mux-gate-select-first-user-u-compact.man",
            "select_first_mux",
            ("select", "i1", "i2"),
            input_port=_INPUT_NORTH,
            control_flow=Direction.AWAY_FROM_INPUT,
        ),
    }
)


def contracts_by_artifact() -> Mapping[str, PrimitiveContract]:
    """Return the immutable registry keyed by checked-in artifact filename."""

    return _CONTRACTS


def contract_for(artifact: str) -> PrimitiveContract:
    """Return the contract for an artifact filename, with a clear lookup error."""

    try:
        return _CONTRACTS[artifact]
    except KeyError as error:
        raise KeyError(f"No primitive contract registered for {artifact!r}") from error
