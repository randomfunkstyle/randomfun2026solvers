#!/usr/bin/env python3
"""Indexed one-pass READ probe: register transaction + physical value ring."""
from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, E, N, S, W
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.memory_blocks import register_cell
from randomfun2026solvers.memory_onepass_compact import commit_ops, delta_ops
from randomfun2026solvers.memory_onepass_relay import zero_fill_relay
from randomfun2026solvers.memory_tape import _draw_pipe


def _stamp(dst: Circuit, src: Circuit, at: tuple[int, int]) -> None:
    ox, oy = at
    for (x, y), ch in src.cell.items():
        dst.set(ox + x, oy + y, ch)


def _walls(c: Circuit, at: tuple[int, int], w: int, h: int) -> None:
    ox, oy = at
    for x in range(-1, w + 1):
        c.set(ox + x, oy - 1, "+" if x in (-1, w) else "-")
        c.set(ox + x, oy + h, "+" if x in (-1, w) else "-")
    for y in range(h):
        c.set(ox - 1, oy + y, "|")
        c.set(ox + w, oy + y, "|")


def _room(c: Circuit, x: int, y: int, name: str) -> None:
    for dy, row in enumerate(("+-+", f"|{name}|", "+-+")):
        for dx, ch in enumerate(row):
            c.set(x + dx, y + dy, ch)


def build_indexed_read_probe(size: int = 10) -> tuple[list[str], DebugMap]:
    """Input ``current address``; output the zero-valued target after delta pass."""
    if size <= 0:
        raise ValueError("size must be positive")
    worker = Circuit(36, 25, strict_corridors=True)
    # Input current stores the register; address is held in B for the first
    # fetch.  The selected commit leaves BP=delta for the physical tape pass.
    worker.run(1, 8, "@rM1sWs")
    worker.route((8, 8), E, [(9, 8), (9, 10)], (2, 10), W)
    worker.run(2, 10, "rM", d=W)
    worker.route((0, 10), W, [(0, 4)], (4, 4), E)
    worker.run(4, 4, "1Ns")
    worker.route((8, 4), E, [], (10, 4), E)
    worker.run(10, 4, "r" + delta_ops(size) + "b")
    worker.route((22, 4), E, [(24, 4), (24, 11), (3, 11), (3, 12)], (4, 12), E)
    worker.run(4, 12, "1Ns")
    worker.route((8, 12), E, [], (10, 12), E)
    worker.run(10, 12, "r")
    worker.route((11, 12), E, [(18, 12), (18, 15), (0, 15), (0, 14)], (1, 14), E)
    worker.run(1, 14, commit_ops(size))
    worker.route((18, 14), E, [(19, 14), (19, 17), (3, 17), (3, 18)], (4, 18), E)
    # FIFO acknowledgement is a real fetch, never a fixed-delay corridor.
    worker.run(4, 18, "1Ns")
    worker.route((8, 18), E, [], (10, 18), E)
    worker.run(10, 18, "r")
    worker.route((11, 18), E, [], (22, 18), E)
    worker.counted_loop(22, 18, "rs")
    worker.route((24, 18), E, [(28, 18), (28, 20)], (27, 20), W)
    worker.run(27, 20, "r", d=W)
    # Output and ring reappend are two explicit sends so index traffic is never
    # broadcast as it would be with S.
    # The output port is below the far side of the worker.  Keeping the send
    # here prevents the nearby index-transaction sends from selecting it.
    worker.route((26, 20), W, [(26, 22)], (30, 22), S)
    worker.run(30, 23, "s", d=S)
    worker.route((30, 24), S, [(32, 24), (32, 21)], (33, 21), E)
    worker.run(33, 21, "sH")

    grid = Circuit(100, 70)
    wx, wy = 15, 10
    _stamp(grid, worker, (wx, wy))
    _walls(grid, (wx, wy), worker.w, worker.h)
    debug = DebugMap(f"indexed one-pass read probe n={size}")
    debug.region_relative("worker", (wx, wy), 0, 0, worker.w, worker.h, note="index commit, delta pass, target read", color="#38bdf8")

    _room(grid, wx - 6, wy + 7, "I")
    input_pipe = [(wx - 3, wy + 8), (wx - 2, wy + 8)]
    _draw_pipe(grid, input_pipe)
    debug.region("input", wx - 6, wy + 7, 3, 3, note="current then address", color="#22c55e")
    debug.lane("input-pipe", input_pipe, kind="pipe", expect="current,address", color="#22c55e")

    output_col = wx + worker.w
    _room(grid, output_col - 1, wy + worker.h + 3, "O")
    output_pipe = [(output_col, wy + worker.h + 1), (output_col, wy + worker.h + 2)]
    _draw_pipe(grid, output_pipe)
    debug.region("output", output_col - 1, wy + worker.h + 3, 3, 3, note="target read", color="#a78bfa")
    debug.lane("output-pipe", output_pipe, kind="pipe", expect="target value", color="#a78bfa")

    index = register_cell("index", note="logical ring head", color="#facc15")
    ix, iy = wx + 2, 0
    _stamp(grid, index.circuit, (ix, iy))
    command = [(wx + 6, wy - 2), (wx + 6, wy - 3), (ix - 2, wy - 3), (ix - 2, iy + 3), (ix - 1, iy + 3)]
    response = [(ix + index.width, iy + 3), (wx + 10, iy + 3), (wx + 10, wy - 2)]
    _draw_pipe(grid, command)
    _draw_pipe(grid, response)
    debug.region("index", ix, iy, index.width, index.height, note=index.note, color=index.color)
    debug.lane("index-command", command, kind="pipe", expect="store/fetch index", color="#facc15")
    debug.lane("index-response", response, kind="pipe", expect="current index", color="#fde68a")

    relay = zero_fill_relay("relay", size, note="ring initialise and turnaround", color="#fb7185")
    rx, ry = 60, 38
    _stamp(grid, relay.circuit, (rx, ry))
    forward = [(wx + worker.w + 1, wy + 20), (70, wy + 20), (70, ry + 6), (rx + 9, ry + 6)]
    # The return pipe enters underneath the worker from the left.  This leaves
    # the compact lower-right output port exclusively available to target reads.
    returned = [(rx - 1, ry + 4), (rx - 2, ry + 4), (rx - 2, 55), (wx - 8, 55), (wx - 8, wy + worker.h + 2), (wx + 27, wy + worker.h + 2), (wx + 27, wy + worker.h + 1)]
    _draw_pipe(grid, forward)
    _draw_pipe(grid, returned)
    debug.region("relay", rx, ry, relay.width, relay.height, note=relay.note, color=relay.color)
    debug.lane("ring-forward", forward, kind="pipe", expect="pass/reappend to relay", color="#34d399")
    debug.lane("ring-return", returned, kind="pipe", expect="zeros and ring head", color="#10b981")
    debug.lane_relative("delta-pass", (wx, wy), [(22, 18), (23, 18), (23, 21), (22, 21), (22, 18)], kind="expected", expect="BP=delta controls real rs pass", color="#22c55e")

    return [row.rstrip() for row in grid.rows() if row.strip()], debug


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=10)
    parser.add_argument("--man", type=Path)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    rows, debug = build_indexed_read_probe(args.size)
    if args.man:
        args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    if args.html:
        debug.write_html(rows, args.html)
    if args.json:
        debug.write_json(args.json)
