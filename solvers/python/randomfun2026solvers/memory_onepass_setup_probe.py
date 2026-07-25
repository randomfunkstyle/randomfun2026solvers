#!/usr/bin/env python3
"""Executable FIFO/index setup probe for the compact one-pass memory worker.

Input is ``current address``.  The machine stores ``current`` in the real
register-cell, fetches it through the real pipes, computes
``(address-current) % size``, and emits the delta.  It is intentionally a
small integration gate before the tape and target arms are connected.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, E, W
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.memory_blocks import register_cell
from randomfun2026solvers.memory_onepass_compact import commit_ops, delta_ops
from randomfun2026solvers.memory_tape import _draw_pipe


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


def _room(circuit: Circuit, x: int, y: int, label: str) -> None:
    for dy, row in enumerate(("+-+", f"|{label}|", "+-+")):
        for dx, glyph in enumerate(row):
            circuit.set(x + dx, y + dy, glyph)


def build_setup_probe(size: int = 10, *, commit: bool = False) -> tuple[list[str], DebugMap]:
    """Build the real current/address setup transaction for a chosen size.

    ``commit=True`` carries the transaction through the index update used by
    each final arm and emits the committed index after verifying it by fetch.
    """
    if size <= 0:
        raise ValueError("size must be positive")

    worker_w, worker_h = 30, 22
    wx, wy = 10, 10
    grid = Circuit(60, 40)
    worker = Circuit(worker_w, worker_h, strict_corridors=True)
    debug = DebugMap(f"one-pass {'commit' if commit else 'setup'} probe n={size}")

    # Input current -> store in index.  This is a FIFO transaction, followed
    # by a routed address read and a separate index fetch.
    worker.run(1, 8, "@rM1sWs")
    worker.route((8, 8), E, [(9, 8), (9, 10)], (2, 10), W)
    worker.run(2, 10, "rM", d=W)
    worker.route((0, 10), W, [(0, 4)], (4, 4), E)
    worker.run(4, 4, "1Ns")
    worker.route((8, 4), E, [], (10, 4), E)
    worker.run(10, 4, "r" + delta_ops(size) + "s")
    if commit:
        # BP keeps delta while the arm refetches current and stores
        # current+delta+1. No timing loop is involved; each receive blocks.
        worker.run(22, 4, "b")
        worker.route((23, 4), E, [(24, 4), (24, 11), (3, 11), (3, 12)], (4, 12), E)
        worker.run(4, 12, "1Ns")
        worker.route((8, 12), E, [], (10, 12), E)
        worker.run(10, 12, "r")
        worker.route((11, 12), E, [(18, 12), (18, 15), (0, 15), (0, 14)], (1, 14), E)
        worker.run(1, 14, commit_ops(size))
        worker.route((18, 14), E, [(19, 14), (19, 17), (3, 17), (3, 18)], (4, 18), E)
        worker.run(4, 18, "1Ns")
        worker.route((8, 18), E, [], (10, 18), E)
        worker.run(10, 18, "r")
        worker.route((11, 18), E, [], (21, 18), E)
        worker.run(21, 18, "sH")
    else:
        worker.run(22, 4, "H")

    _stamp(grid, worker, (wx, wy))
    _walls(grid, (wx, wy), worker_w, worker_h)
    worker_origin = (wx, wy)
    debug.region_relative("worker", worker_origin, 0, 0, worker_w, worker_h, note="real setup transaction", color="#38bdf8")
    debug.region_relative("index-initialise", worker_origin, 1, 8, 7, 1, note="input current -> index store", color="#facc15")
    debug.region_relative("address-input", worker_origin, 0, 4, 10, 7, note="receive address while index store travels", color="#22c55e")
    debug.region_relative("index-fetch", worker_origin, 4, 4, 7, 1, note="-1 request and current response", color="#facc15")
    debug.region_relative("relative-delta", worker_origin, 11, 4, len(delta_ops(size)), 1, note="address-current modulo size", color="#60a5fa")

    # Input room and its single pipe on the worker's west wall.
    input_y = wy + 8
    _room(grid, wx - 6, input_y - 1, "I")
    input_pipe = [(wx - 3, input_y), (wx - 2, input_y)]
    _draw_pipe(grid, input_pipe)
    debug.region("input", wx - 6, input_y - 1, 3, 3, note="current then address", color="#22c55e")
    debug.lane("input-pipe", input_pipe, kind="pipe", expect="current, address", color="#22c55e")

    # The setup output sits beside its `s`. In commit mode it moves to the top
    # east wall so the index-store sends remain closer to the index command.
    output_y = wy + (0 if commit else 4)
    _room(grid, wx + worker_w + 3, output_y - 1, "O")
    output_pipe = [(wx + worker_w + 1, output_y), (wx + worker_w + 2, output_y)]
    _draw_pipe(grid, output_pipe)
    output_note = "committed current" if commit else "computed relative delta"
    debug.region("output", wx + worker_w + 3, output_y - 1, 3, 3, note=output_note, color="#a78bfa")
    debug.lane("output-pipe", output_pipe, kind="pipe", expect=output_note, color="#a78bfa")

    # The reusable index cell lives above the worker.  The command pipe exits
    # north first and only then bends, so it is a real worker endpoint.
    index = register_cell("index", note="persistent logical tape head", color="#facc15")
    rx, ry = wx + 2, 0
    _stamp(grid, index.circuit, (rx, ry))
    debug.region("index", rx, ry, index.width, index.height, note=index.note, color=index.color)
    command = [
        (wx + 6, wy - 2),
        (wx + 6, wy - 3), (rx - 2, wy - 3),
        (rx - 2, ry + 3), (rx - 1, ry + 3),
    ]
    response = [
        (rx + index.width, ry + 3),
        (wx + 10, ry + 3), (wx + 10, wy - 2),
    ]
    _draw_pipe(grid, command)
    _draw_pipe(grid, response)
    debug.lane("index-command-pipe", command, kind="pipe", expect="1,value store; -1 fetch", color="#facc15")
    debug.lane("index-response-pipe", response, kind="pipe", expect="stored current", color="#fde68a")
    debug.lane_relative("setup-flow", worker_origin, [(2, 8), (5, 8), (5, 4), (10, 4), (21, 4)], kind="expected", expect="store current; fetch it; calculate delta", color="#60a5fa")
    if commit:
        debug.region_relative("index-commit", worker_origin, 1, 12, 19, 7, note="BP keeps delta while current is refetched and stored", color="#fb923c")
        debug.lane_relative("commit-flow", worker_origin, [(22, 4), (22, 12), (11, 12), (18, 12), (18, 15), (0, 15), (0, 14), (18, 14), (19, 14), (19, 18), (4, 18), (21, 18)], kind="expected", expect="fetch current; store current+delta+1; verify by fetch", color="#fb923c")

    rows = [row.rstrip() for row in grid.rows() if row.strip()]
    return rows, debug


def _self_test() -> None:
    rows, debug = build_setup_probe(10)
    assert any("@rM1sWs" in row for row in rows)
    assert any(region.name == "index" for region in debug.regions)
    assert {lane.name for lane in debug.lanes} >= {"index-command-pipe", "index-response-pipe"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=10)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--man", type=Path)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    rows, debug = build_setup_probe(args.size, commit=args.commit)
    if args.man:
        args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    if args.html:
        debug.write_html(rows, args.html)
    if args.json:
        debug.write_json(args.json)
    if not (args.man or args.html or args.json):
        _self_test()
