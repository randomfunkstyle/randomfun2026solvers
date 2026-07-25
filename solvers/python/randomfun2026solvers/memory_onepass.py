#!/usr/bin/env python3
"""Reference semantics for the one-pass, current-index MEMORY machine.

This is the executable contract for the next Littleman layout.  The tape is a
ring whose head has logical address ``current``.  An access rotates only to its
target; it never restores the head to zero.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import json
from typing import Iterable

from randomfun2026solvers.circuit import Circuit, E, N, S, W
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.memory_tape import _draw_pipe, lit


REGISTER_ART = (
    "+----+",
    "|vWs<|",
    "|v  W|",
    "|>@RX|",
    "|^  W|",
    "|^Wr<|",
    "+----+",
)

# Compact worker state contract.  This is deliberately phrased in terms of
# Littleman registers so the layout and its debug lanes can be checked against
# it while the worker is drawn:
#
#   MAIN: r(op); b; r(addr); M       BP=op, B=addr
#   FETCH: A=current, B=addr, BP=op
#   DELTA: A=B=(addr-current)%N
#   TAG: op=0 -> B=+delta; op=1 -> B=-delta; BP=delta
#   PASS: rotate delta values, then branch on sign(B) for read/write
#
# Keeping op in BP until TAG permits one fetch/calculation/pass corridor.  The
# former memory2 second-pass area is reserved for the subsequent current-store
# transaction instead of restoring the tape to zero.
COMPACT_FLOW = "shared relative-pass; current store replaces P2 restoration"


def compact_shared_setup_draft(size: int = 10) -> tuple[Circuit, DebugMap]:
    """First collision-checked compact worker fragment.

    This is intentionally only the common prefix through the relative pass. It
    establishes the packing constraints before target/update paths are allowed
    to consume the remaining cells.
    """
    # This draft does not need the old right gutter: P1 can descend directly
    # from its exit.  Keeping the room bounded here is part of the algorithm,
    # not a post-processing crop.
    c = Circuit(32, 31)
    debug = DebugMap(f"one-pass compact draft n={size}")

    # MAIN keeps op in BP while A/B carry the address through the register fetch.
    c.run(1, 7, "rbrM")
    c.route((5, 7), E, [(5, 3)], (5, 3), E)
    # Current fetch. The external command/response pipes attach at x=6 and x=15.
    c.run(6, 3, "1Ns")
    c.route((9, 3), E, [], (15, 3), E)
    c.run(15, 3, "r")
    # A=current, B=addr, BP=op -> A=B=delta; BP remains op for the tag branch.
    end, _ = c.run(16, 3, "W-M" + lit(size) + "W%M")
    c.run(end, 3, "d")

    # op==0 goes straight: B=+delta, BP=delta. op==1 turns south and negates B.
    c.run(end + 1, 3, "b")
    c.route((end + 2, 3), E, [(28, 3), (28, 10)], (28, 10), E)
    c.run(end, 4, "WNMNb", d=S)
    c.route((end, 10), S, [(28, 10)], (28, 10), E)
    c.counted_loop(29, 10, "rs")

    # P1 leaves B signed by the operation. Bring it to a small central
    # dispatch: +delta turns south into READ, -delta turns north into WRITE.
    c.route((31, 10), E, [(31, 16), (24, 16)], (24, 16), S)
    c.turn(24, 15, E)
    c.run(25, 15, "WX")

    # READ consumes the target, then performs two explicit sends: first back
    # to the value ring, then to output.  ``S`` would also broadcast the value
    # into the current-register command pipe once this draft is assembled.
    c.run(26, 16, "rs", d=S)
    c.route((26, 18), S, [], (3, 18), W)
    c.run(2, 18, "s", d=W)
    c.route((1, 18), W, [(0, 18), (0, 19)], (2, 19), E)
    c.run(2, 19, "WM1+M", d=S)  # B=delta+1 for the shared current-store.
    # WRITE consumes target, then will route through the input/value lane.
    c.turn(26, 14, W)
    c.horizontal(14, 26, 2)
    c.run(2, 14, "r", d=W)
    c.route((1, 14), W, [(1, 24)], (22, 24), E)
    c.run(22, 24, "srWNMNb")  # append new, drop old, then B=delta+1.

    # Both paths now carry B=delta+1. They join at one current-register fetch.
    c.route((2, 24), S, [(2, 25), (5, 25), (5, 28)], (5, 29), E)
    c.route((29, 24), E, [(30, 24), (30, 30), (5, 30)], (5, 29), E)
    c.run(6, 29, "1Ns")
    c.route((9, 29), E, [], (15, 29), E)
    c.run(15, 29, "r")
    update_end, _ = c.run(16, 29, "+M" + lit(size) + "W%")
    c.run(update_end, 29, "M1sWs")

    debug.region("target-dispatch", 24, 14, 4, 4, note="sign(B) chooses read or write target", color="#f59e0b")
    debug.region("read-target", 26, 16, 2, 2, note="read target, emit it, append it", color="#a78bfa")
    debug.region("write-target", 1, 14, 29, 11, note="write value enters before old target is discarded", color="#fb923c")
    debug.region("current-update", 5, 29, 29, 2, note="fetch current, add delta + 1, store modulo size", color="#eab308")

    debug.region("shared-setup", 1, 3, 28, 8, note="op -> current fetch -> delta -> operation tag", color="#60a5fa")
    debug.region("relative-pass", 29, 10, 2, 4, note="rotate delta values only", color="#22c55e")
    debug.lane("current-fetch", [(6, 3), (9, 3), (15, 3)], kind="expected", expect="-1 command out; current value returns", color="#facc15")
    debug.lane("read-tag", [(end, 3), (end + 1, 3), (28, 3), (28, 10)], kind="expected", expect="op=0 keeps B=+delta", color="#60a5fa")
    debug.lane("write-tag", [(end, 3), (end, 10), (28, 10)], kind="expected", expect="op=1 makes B=-delta", color="#fb923c")
    debug.lane("target-dispatch", [(31, 10), (32, 10), (32, 16), (24, 16), (24, 15), (26, 15)], kind="expected", expect="sign(B) selects target action", color="#f59e0b")
    debug.lane("read-current-advance", [(26, 18), (26, 23)], kind="expected", expect="B=delta becomes delta+1", color="#a78bfa")
    debug.lane("write-current-advance", [(1, 14), (1, 24), (29, 24)], kind="expected", expect="append new, discard old, B=-delta becomes delta+1", color="#fb923c")
    debug.lane("target-to-update", [(26, 23), (20, 23), (20, 28), (5, 28), (5, 29)], kind="expected", expect="read and write join after producing delta + 1", color="#eab308")
    debug.lane("current-fetch-store", [(6, 29), (9, 29), (15, 29), (29, 29)], kind="expected", expect="fetch current; add delta + 1 modulo size; command 1 stores it", color="#facc15")
    return c, debug


def assemble_compact_control_debug(size: int = 10) -> tuple[list[str], DebugMap]:
    """Place the control draft with a short, physically valid index protocol.

    The worker is still only a control draft: it has no I/O or tape ring.  Its
    index pipes are real, however.  Both leave/enter the worker's north wall,
    which makes their endpoints valid and lets every current ``s``/``r`` bind
    to the same two short pipes.  No acknowledgement or timing lane exists.
    """
    worker, debug = compact_shared_setup_draft(size)
    g = Circuit(70, 70)
    wx, wy = 8, 10
    for (x, y), char in worker.cell.items():
        g.set(wx + x, wy + y, char)
    for x in range(-1, worker.w + 1):
        g.set(wx + x, wy - 1, "+" if x in (-1, worker.w) else "-")
        g.set(wx + x, wy + worker.h, "+" if x in (-1, worker.w) else "-")
    for y in range(worker.h):
        g.set(wx - 1, wy + y, "|")
        g.set(wx + worker.w, wy + y, "|")

    debug = debug.translated(wx, wy)
    # Keep the persistent cell in the small north band.  A pipe must leave a
    # room perpendicular to its wall: the former draft began sideways below
    # the worker, which looked connected but had no worker endpoint.
    rx, ry = wx + 5, 0
    ports = place_current_register(g, debug, rx, ry)
    command = [
        (wx + 8, wy - 2),
        (wx + 8, wy - 3), (rx - 2, wy - 3),
        (rx - 2, ports["command"][1]), ports["command"],
    ]
    response = [
        ports["response"], (wx + 15, ports["response"][1]),
        (wx + 15, wy - 2),
    ]
    _draw_pipe(g, command)
    _draw_pipe(g, response)
    debug.region("worker", wx, wy, worker.w, worker.h, note="compact shared one-pass control", color="#38bdf8")
    debug.lane("register-command-pipe", command, kind="pipe", expect="-1 fetch or 1,value store; FIFO orders both", color="#facc15")
    debug.lane("register-response-pipe", response, kind="pipe", expect="current value returns to worker", color="#fde68a")

    rows = [row.rstrip() for row in g.rows() if row.strip()]
    return rows, debug


def place_current_register(circuit: Circuit, debug: DebugMap, x: int, y: int) -> dict[str, tuple[int, int]]:
    """Place the persistent index register and mark it as it is created.

    The returned ports are intentionally pipe anchors, not runner coordinates:
    callers must create the request/response lanes explicitly and mark those
    lanes alongside the routes they lay down.
    """
    for dy, row in enumerate(REGISTER_ART):
        for dx, char in enumerate(row):
            if char != " ":
                circuit.set(x + dx, y + dy, char)
    debug.region(
        "current-register",
        x + 1,
        y + 1,
        4,
        5,
        note="persistent logical address at the tape head",
        color="#facc15",
    )
    return {
        "command": (x - 1, y + 2),
        "response": (x + len(REGISTER_ART[0]), y + 2),
    }


@dataclass(frozen=True)
class PhaseEvent:
    phase: str
    tape_head: int
    current_register: int
    head_value: int
    tape: tuple[int, ...]


@dataclass(frozen=True)
class AccessEvent:
    op: str
    address: int
    value: int | None
    current_before: int
    delta: int
    consumed: int
    current_after: int
    tape_after: tuple[int, ...]
    phases: tuple[PhaseEvent, ...]


class OnePassMemory:
    """A ring-backed memory with a head that advances past every target."""

    def __init__(self, size: int, values: Iterable[int] | None = None) -> None:
        if size <= 0:
            raise ValueError("size must be positive")
        initial = list(values) if values is not None else [0] * size
        if len(initial) != size:
            raise ValueError(f"expected {size} initial values, got {len(initial)}")
        self.size = size
        self.current = 0
        self.tape: deque[int] = deque(initial)

    def access(self, address: int, value: int | None = None) -> tuple[int | None, AccessEvent]:
        """Read when ``value`` is ``None``; otherwise replace ``address``."""
        if not 0 <= address < self.size:
            raise ValueError(f"address {address} outside 0..{self.size - 1}")

        current_before = self.current
        delta = (address - current_before) % self.size
        next_current = (address + 1) % self.size
        phases = [
            PhaseEvent("before", current_before, current_before, self.tape[0], tuple(self.tape)),
        ]
        self.tape.rotate(-delta)
        phases.append(PhaseEvent("relative-pass", address, current_before, self.tape[0], tuple(self.tape)))
        consumed = self.tape.popleft()
        self.tape.append(consumed if value is None else value)
        phases.append(PhaseEvent("consume-and-append", next_current, current_before, self.tape[0], tuple(self.tape)))
        self.current = next_current
        phases.append(PhaseEvent("current-store", self.current, self.current, self.tape[0], tuple(self.tape)))
        event = AccessEvent(
            op="read" if value is None else "write",
            address=address,
            value=value,
            current_before=current_before,
            delta=delta,
            consumed=consumed,
            current_after=self.current,
            tape_after=tuple(self.tape),
            phases=tuple(phases),
        )
        return (consumed if value is None else None), event


def run_operations(size: int, operations: Iterable[tuple[int, int, int | None]]) -> tuple[list[int], list[AccessEvent]]:
    """Run ``(op, address, value)`` tuples where op is 0 read or 1 write."""
    memory = OnePassMemory(size)
    output: list[int] = []
    events: list[AccessEvent] = []
    for op, address, value in operations:
        if op not in (0, 1):
            raise ValueError(f"unsupported operation {op}")
        if op == 1 and value is None:
            raise ValueError("write requires a value")
        result, event = memory.access(address, value if op else None)
        if result is not None:
            output.append(result)
        events.append(event)
    return output, events


def _self_test() -> None:
    layout = Circuit(12, 12)
    debug = DebugMap("one-pass layout test")
    ports = place_current_register(layout, debug, 2, 2)
    assert ports == {"command": (1, 4), "response": (8, 4)}
    assert layout.get(4, 5) == "@"
    assert debug.regions[0].name == "current-register"

    output, events = run_operations(10, [(1, 7, 42), (0, 7, None), (0, 0, None), (0, 9, None)])
    assert output == [42, 0, 0]
    assert [(event.current_before, event.delta, event.current_after) for event in events] == [
        (0, 7, 8),  # write address 7; the head advances to 8
        (8, 9, 8),  # wrap around to read address 7
        (8, 2, 1),
        (1, 8, 0),
    ]
    assert events[0].tape_after[-1] == 42
    assert events[1].consumed == 42


if __name__ == "__main__":
    _self_test()
    _, trace = run_operations(10, [(1, 7, 42), (0, 7, None), (0, 0, None), (0, 9, None)])
    print(json.dumps([asdict(event) for event in trace], indent=2))
