#!/usr/bin/env python3
"""Coordinate-free dataflow plan for the compact one-pass MEMORY machine.

The plan is intentionally executable as a review artifact before a Littleman
glyph is placed.  It identifies which values may move with their consumer and
which values require a real storage container.  Geometry is introduced only
after this ledger is accepted.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable

from randomfun2026solvers.memory_blocks import DataFlow


@dataclass(frozen=True)
class Stage:
    name: str
    purpose: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    protocol: tuple[str, ...] = ()


@dataclass(frozen=True)
class OnePassPlan:
    """Named containers and dataflows, deliberately without canvas positions."""

    stages: tuple[Stage, ...]
    flows: tuple[DataFlow, ...]

    def movable(self) -> tuple[DataFlow, ...]:
        return tuple(flow for flow in self.flows if flow.movable)

    def stateful(self) -> tuple[DataFlow, ...]:
        return tuple(flow for flow in self.flows if not flow.movable)

    def required_long_flows(self) -> tuple[DataFlow, ...]:
        return tuple(flow for flow in self.flows if flow.lifetime == "ring")


@dataclass(frozen=True)
class SetupTrace:
    """Observable register-cell protocol for one operation setup."""

    next_address: int
    delta: int
    commands: tuple[tuple[str, str, int | None], ...]


@dataclass(frozen=True)
class ProtocolEvent:
    """One named dataflow operation in the no-timing protocol emulator."""

    flow: str
    action: str
    value: int


@dataclass
class RegisterCell:
    """The behavioural contract of the reusable Littleman register container."""

    value: int = 0

    def fetch(self) -> int:
        return self.value

    def store(self, value: int) -> None:
        self.value = value


class OnePassProtocolEmulator:
    """Presettable one-pass memory using the compact two-arm protocol.

    This intentionally has no tick counter.  The selected opcode arm owns its
    tiny pass loop, so BP can preserve delta while the index is updated before
    the data ring moves.  The data ring remains the only long movement.
    """

    def __init__(self, size: int, values: Iterable[int] | None = None, *, current: int = 0) -> None:
        if size <= 0:
            raise ValueError("size must be positive")
        initial = list(values) if values is not None else [0] * size
        if len(initial) != size:
            raise ValueError(f"expected {size} values")
        if not 0 <= current < size:
            raise ValueError("current outside memory size")
        self.size = size
        self.tape = deque(initial)
        self.index = RegisterCell(current)

    def access(self, op: int, address: int, value: int | None = None) -> tuple[int | None, tuple[ProtocolEvent, ...]]:
        if op not in (0, 1):
            raise ValueError("op must be 0 (read) or 1 (write)")
        if not 0 <= address < self.size:
            raise ValueError("address outside memory size")
        if op == 1 and value is None:
            raise ValueError("write requires value")

        events: list[ProtocolEvent] = []
        current = self.index.fetch()
        events.append(ProtocolEvent("current-fetch", "fetch-index", current))
        delta = (address - current) % self.size
        events.append(ProtocolEvent("delta", "calculate", delta))

        # BP preserves delta across this refetch/commit.  The opcode is carried
        # by the arm itself, so B is free for current + delta + 1.
        current_for_commit = self.index.fetch()
        events.append(ProtocolEvent("current-fetch", "refetch-index", current_for_commit))
        committed = (current_for_commit + delta + 1) % self.size
        self.index.store(committed)
        events.append(ProtocolEvent("current-commit", "store-index", committed))

        self.tape.rotate(-delta)
        events.append(ProtocolEvent("tape", "relative-pass", delta))
        old = self.tape.popleft()
        if op == 0:
            self.tape.append(old)
            result: int | None = old
            events.append(ProtocolEvent("target", "read-and-reappend", old))
        else:
            self.tape.append(value)
            result = None
            events.append(ProtocolEvent("target", "replace", value))

        return result, tuple(events)


def one_pass_plan() -> OnePassPlan:
    """Return the placement-independent plan for one relative tape pass.

    Both opcode arms own a tiny *code* loop, but either arm executes exactly
    one data pass.  This trades eight static loop cells for a temporary
    register: after calculating delta, BP preserves it while the arm re-fetches
    current and commits ``current + delta + 1`` before the tape moves.  The
    opcode is carried by the chosen runner path, not a value.
    """
    stages = (
        Stage("input", "receive operation and address", (), ("opcode", "address")),
        Stage(
            "shared-setup",
            "fetch current and calculate relative delta",
            ("opcode", "address", "current"),
            ("delta",),
            ("index.fetch()", "delta = (address - current) mod N", "branch by opcode"),
        ),
        Stage(
            "read-arm",
            "preserve delta in BP, commit index, then rotate/read",
            ("delta", "current", "tape"),
            ("result", "tape", "current"),
            ("BP = delta", "index.fetch()", "index.store(current + delta + 1)", "pass delta values"),
        ),
        Stage(
            "write-arm",
            "preserve delta in BP, commit index, then rotate/replace",
            ("delta", "current", "tape", "write-value"),
            ("tape", "current"),
            ("BP = delta", "index.fetch()", "index.store(current + delta + 1)", "pass delta values"),
        ),
        Stage("target", "read/reappend or write/replace target", ("target", "write-value"), ("result", "tape")),
        Stage("output", "emit read result", ("result",), ()),
    )
    flows = (
        DataFlow("opcode", "operation tag", "input.out", ("shared-setup.in",), "transient"),
        DataFlow("address", "logical address", "input.out", ("shared-setup.in",), "transient"),
        DataFlow("current-fetch", "persistent tape-head address", "index.out", ("shared-setup.in",), "persistent", "index register"),
        DataFlow("delta", "(address - current) mod N", "shared-setup.out", ("read-arm.in", "write-arm.in"), "transient", "carried in BP"),
        DataFlow("current-update", "current + delta + 1", "read-arm.out", ("index.in",), "persistent", "index register"),
        DataFlow("tape", "N memory values", "read-arm.out", ("read-arm.in", "write-arm.in", "target.in"), "ring", "the memory itself"),
        DataFlow("target", "value at logical address", "read-arm.out", ("target.in",), "transient"),
        DataFlow("write-value", "new value for write operations", "input.out", ("target.in",), "transient"),
        DataFlow("result", "read result", "target.out", ("output.in",), "transient"),
    )
    return OnePassPlan(stages, flows)


def setup_protocol(address: int, current: int, size: int) -> SetupTrace:
    """Model the command order independently of pipe travel time.

    The trace is FIFO protocol, not a tick schedule.  It proves that shared
    setup uses the old current before either opcode arm commits the new head.
    """
    if not 0 <= address < size or not 0 <= current < size:
        raise ValueError("address and current must be within the memory size")
    next_address = (address + 1) % size
    delta = (next_address - (current + 1)) % size
    return SetupTrace(
        next_address,
        delta,
        (("index", "fetch", None),),
    )


def _self_test() -> None:
    plan = one_pass_plan()
    assert [stage.name for stage in plan.stages] == [
        "input", "shared-setup", "read-arm", "write-arm", "target", "output"
    ]
    assert [flow.name for flow in plan.required_long_flows()] == ["tape"]
    assert {flow.name for flow in plan.movable()} == {"opcode", "address", "target", "write-value", "result"}
    assert {flow.name for flow in plan.stateful()} == {
        "current-fetch", "delta", "current-update", "tape"
    }
    for current in range(10):
        for address in range(10):
            trace = setup_protocol(address, current, 10)
            assert trace.next_address == (address + 1) % 10
            assert trace.delta == (address - current) % 10
            assert trace.commands == (("index", "fetch", None),)

    emu = OnePassProtocolEmulator(10, current=8)
    assert emu.access(1, 7, 42)[0] is None
    assert emu.index.value == 8
    assert emu.access(0, 7)[0] == 42
    assert emu.access(0, 0)[0] == 0
    assert emu.index.value == 1

    # The protocol machine agrees with direct indexed memory for a stateful
    # sequence that crosses the ring boundary several times.
    expected = [0] * 10
    current = 0
    reference_output: list[int] = []
    protocol = OnePassProtocolEmulator(10)
    operations = ((1, 7, 42), (1, 0, -3), (0, 7, None), (0, 0, None), (0, 9, None))
    for op, address, value in operations:
        if op:
            expected[address] = value  # type: ignore[index]
        else:
            reference_output.append(expected[address])
        got, events = protocol.access(op, address, value)
        if got is not None:
            assert got == reference_output[-1]
        assert ProtocolEvent("current-commit", "store-index", (address + 1) % 10) in events
        current = (address + 1) % 10
        assert protocol.index.value == current


if __name__ == "__main__":
    _self_test()
