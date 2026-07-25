#!/usr/bin/env python3
"""Persistent relay room for the compact one-pass memory ring.

The worker must keep A/B/BP across its relative access, so it remains one room.
Initial zero filling and steady-state pipe turnaround have no such state and
belong in this separate room.  The relay sends ``size`` zeros before entering a
two-pair forwarding cycle.
"""
from __future__ import annotations

from randomfun2026solvers.circuit import Circuit, E, GLYPH, N, S, W
from randomfun2026solvers.memory_blocks import Device, PipePort
from randomfun2026solvers.memory_tape import lit


def zero_fill_relay(name: str, size: int, *, note: str, color: str) -> Device:
    """Return a self-contained relay with west output and east input ports.

    The two external ports deliberately live on opposite walls.  This makes
    every relay ``s`` select the ring-return pipe and every relay ``r`` select
    the ring-forward pipe without depending on pipe length.
    """
    if size <= 0:
        raise ValueError("size must be positive")

    inner = Circuit(7, 6, strict_corridors=True)
    end, _ = inner.run(0, 0, "@" + lit(size))
    inner.route((end, 0), E, [], (6, 0), S)
    inner.turn(6, 1, W)
    inner.run(5, 1, "b", d=W)
    inner.horizontal(1, 5, 0)
    inner.turn(0, 1, S)
    inner.turn(0, 2, E)
    fill, _ = inner.counted_loop(1, 2, "0s")
    if fill != 3:
        raise AssertionError("unexpected zero-fill loop exit")
    inner.turn(3, 2, S)
    inner.vertical(3, 2, 4)
    inner.run(3, 4, ">rsv")
    inner.run(6, 5, "<rs^", d=W)

    room = Circuit(9, 8, strict_corridors=True)
    for x in range(9):
        room.set(x, 0, "+" if x in (0, 8) else "-")
        room.set(x, 7, "+" if x in (0, 8) else "-")
    for y in range(1, 7):
        room.set(0, y, "|")
        room.set(8, y, "|")
    for (x, y), glyph in inner.cell.items():
        room.set(x + 1, y + 1, glyph)

    return Device(
        name,
        room,
        {
            "ring-return": PipePort(-1, 4, W, "out", "zeros and forwarded values"),
            "ring-forward": PipePort(9, 6, E, "in", "values from the worker"),
        },
        note,
        color,
    )


def zero_fill_relay_rot180(name: str, size: int, *, note: str, color: str) -> Device:
    """Return :func:`zero_fill_relay` rotated 180 degrees.

    A half-turn preserves clockwise control flow, unlike a mirror.  It places
    the forward input on the west side and the ring return on the east side,
    which is useful when both ports face a worker's east wall.
    """
    original = zero_fill_relay(name, size, note=note, color=color)
    transformed = Circuit(original.width, original.height, strict_corridors=True)
    opposite = {E: W, W: E, N: S, S: N}
    glyphs = {GLYPH[d]: GLYPH[opposite[d]] for d in (E, W, N, S)}
    for (x, y), ch in original.circuit.cell.items():
        transformed.set(original.width - 1 - x, original.height - 1 - y, glyphs.get(ch, ch))

    # A man always spawns east, even when the room is rotated. Extend the room
    # by two rows and feed a fresh east-facing spawn into the westbound startup.
    rotated = Circuit(original.width, original.height + 2, strict_corridors=True)
    for (x, y), ch in transformed.cell.items():
        if y == original.height - 1:
            continue
        rotated.set(x, y, "<" if ch == "@" else ch)
    for y in (original.height - 1, original.height):
        rotated.set(0, y, "|")
        rotated.set(original.width - 1, y, "|")
    for x in range(original.width):
        rotated.set(
            x,
            original.height + 1,
            "+" if x in (0, original.width - 1) else "-",
        )
    rotated.set(1, original.height, "@")
    rotated.set(original.width - 2, original.height, "^")
    rotated.set(original.width - 2, original.height - 1, "^")
    return Device(
        name,
        rotated,
        {
            "ring-return": PipePort(9, 3, E, "out", "zeros and forwarded values"),
            "ring-forward": PipePort(-1, 1, W, "in", "values from the worker"),
        },
        note,
        color,
    )


def _self_test() -> None:
    relay = zero_fill_relay("relay", 10, note="ring owner", color="#fb7185")
    assert relay.width == 9 and relay.height == 8
    assert relay.ports["ring-return"].role == "out"
    assert relay.ports["ring-forward"].role == "in"
    assert relay.circuit.get(1, 1) == "@"
    rotated = zero_fill_relay_rot180("relay", 10, note="ring owner", color="#fb7185")
    assert rotated.width == 9 and rotated.height == 10
    assert rotated.ports["ring-return"].role == "out"
    assert rotated.ports["ring-forward"].role == "in"
    assert rotated.circuit.get(1, 8) == "@"
    assert rotated.circuit.get(7, 6) == "<"


if __name__ == "__main__":
    _self_test()
