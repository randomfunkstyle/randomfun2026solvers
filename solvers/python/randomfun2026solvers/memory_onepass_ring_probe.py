#!/usr/bin/env python3
"""Executable one-pass ring probe using the compact zero-fill relay room.

This is the integration gate between the independently validated index worker
and a complete memory machine.  It proves the physical ring invariant: values
come from the return pipe, each ``rs`` pair returns one value through the
forward pipe, and the next value remains available at the worker head.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, E, N, S, W
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.memory_onepass_relay import zero_fill_relay
from randomfun2026solvers.memory_tape import _draw_pipe, lit


def _stamp(target: Circuit, source: Circuit, at: tuple[int, int]) -> None:
    ox, oy = at
    for (x, y), glyph in source.cell.items():
        target.set(ox + x, oy + y, glyph)


def _walls(circuit: Circuit, origin: tuple[int, int], width: int, height: int) -> None:
    ox, oy = origin
    for x in range(-1, width + 1):
        circuit.set(ox + x, oy - 1, "+" if x in (-1, width) else "-")
        circuit.set(ox + x, oy + height, "+" if x in (-1, width) else "-")
    for y in range(height):
        circuit.set(ox - 1, oy + y, "|")
        circuit.set(ox + width, oy + y, "|")


def build_ring_probe(size: int = 10) -> tuple[list[str], DebugMap]:
    """Build a ring which passes ``size`` values and emits the next one."""
    if size <= 0:
        raise ValueError("size must be positive")

    worker = Circuit(12, 6, strict_corridors=True)
    after, _ = worker.run(0, 0, "@" + lit(size) + "b")
    worker.route((after, 0), E, [], (7, 0), E)
    worker.counted_loop(7, 0, "rs")
    # At this point exactly ``size`` relay-filled values have completed one
    # physical pass.  Read the following head and send it only to output.
    worker.route((9, 0), E, [(10, 0), (10, 4)], (8, 4), W)
    worker.run(8, 4, "r", d=W)
    worker.route((7, 4), W, [], (1, 4), W)
    worker.run(1, 4, "s", d=W)
    worker.turn(0, 4, N)
    worker.run(0, 3, "H", d=N)

    grid = Circuit(64, 40)
    wx, wy = 15, 8
    _stamp(grid, worker, (wx, wy))
    _walls(grid, (wx, wy), worker.w, worker.h)
    debug = DebugMap(f"one-pass physical ring probe n={size}")
    debug.region_relative("worker", (wx, wy), 0, 0, worker.w, worker.h, note="one counted ring pass then head read", color="#38bdf8")

    # Output has its own west port, far from the worker's east-facing tape
    # sends.  The worker's final ``s`` is the only send near this pipe.
    for dy, row in enumerate(("+-+", "|O|", "+-+")):
        for dx, glyph in enumerate(row):
            grid.set(wx - 6 + dx, wy + 3 + dy, glyph)
    output = [(wx - 2, wy + 4), (wx - 3, wy + 4)]
    _draw_pipe(grid, output)
    debug.region("output", wx - 6, wy + 3, 3, 3, note="head value after one physical pass", color="#a78bfa")
    debug.lane("output-pipe", output, kind="pipe", expect="probe result", color="#a78bfa")

    relay = zero_fill_relay("relay", size, note="initialise and turn the ring", color="#fb7185")
    rx, ry = 35, 20
    _stamp(grid, relay.circuit, (rx, ry))
    debug.region("relay", rx, ry, relay.width, relay.height, note=relay.note, color=relay.color)

    # Worker forward leaves east; relay input is on its east side.  The return
    # leaves the relay west side and enters the worker bottom wall.  Capacity is
    # intentionally more than N so the fill and the worker can overlap safely.
    forward = [(wx + worker.w + 1, wy + 2), (45, wy + 2), (45, ry + 6), (rx + 9, ry + 6)]
    returned = [(rx - 1, ry + 4), (rx - 2, ry + 4), (rx - 2, 31), (wx + worker.w, 31), (wx + worker.w, wy + worker.h + 2), (wx + 8, wy + worker.h + 2), (wx + 8, wy + worker.h + 1)]
    forward_slots = _draw_pipe(grid, forward)
    return_slots = _draw_pipe(grid, returned)
    if forward_slots + return_slots < size + 1:
        raise ValueError("ring capacity is too small")
    debug.lane("ring-forward", forward, kind="pipe", expect="worker pass-through values", color="#34d399")
    debug.lane("ring-return", returned, kind="pipe", expect="relay zeros and returned values", color="#10b981")
    debug.lane_relative("counted-pass", (wx, wy), [(7, 0), (8, 0), (8, 3), (7, 3), (7, 0)], kind="expected", expect="exactly N rs pairs", color="#22c55e")
    debug.lane_relative("head-read", (wx, wy), [(9, 0), (10, 0), (10, 4), (8, 4), (1, 4), (1, 0)], kind="expected", expect="next ring value is the head", color="#a78bfa")

    return [row.rstrip() for row in grid.rows() if row.strip()], debug


def _self_test() -> None:
    rows, debug = build_ring_probe(10)
    assert any("@`10`b" in row for row in rows)
    assert {lane.name for lane in debug.lanes} >= {"ring-forward", "ring-return"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=10)
    parser.add_argument("--man", type=Path)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    rows, debug = build_ring_probe(args.size)
    if args.man:
        args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    if args.html:
        debug.write_html(rows, args.html)
    if args.json:
        debug.write_json(args.json)
    if not (args.man or args.html or args.json):
        _self_test()
