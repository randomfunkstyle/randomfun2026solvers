#!/usr/bin/env python3
"""Compact control worker for the current-index one-pass MEMORY machine.

This builder intentionally contains no physical pipe placement yet.  Its sole
job is to make the control graph collision-checked before ring geometry is
introduced: the current-register commit happens in the shared top band, then
the read and write arms each consume exactly ``BP=delta`` values.
"""
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


def worker(size: int = 10) -> Circuit:
    if size <= 0:
        raise ValueError("size must be positive")
    c = Circuit(62, 32, strict_corridors=True)

    # All external input receives stay in the west band.  BP carries op until
    # the delta calculation branches into a concrete read or write arm.
    c.run(1, 15, "@rbrM")
    c.route((6, 15), E, [(8, 15), (8, 4)], (10, 4), E)
    c.run(10, 4, "1Ns")
    c.route((14, 4), E, [], (16, 4), E)
    delta = delta_ops(size)
    commit = commit_ops(size)
    delta_branch_x = 16 + 1 + len(delta)
    commit_after_x = 35 + 1 + len(commit)
    loop_x = commit_after_x + 1
    c.run(16, 4, "r" + delta + "d")

    # READ, BP==0: set BP=delta, commit current=address+1, then pass delta.
    c.run(delta_branch_x + 2, 4, "b")
    c.run(delta_branch_x + 3, 4, "1Ns")
    c.route((34, 4), E, [], (35, 4), E)
    c.run(35, 4, "r" + commit)
    c.route((commit_after_x, 4), E, [], (loop_x, 4), E)
    c.counted_loop(loop_x, 4, "rs")

    # WRITE, BP==1: d turns south.  Its own nearby commit is intentionally a
    # duplicate of the tiny read commit; no opcode needs to survive the pass.
    c.run(delta_branch_x, 5, "b", d=S)
    # Turn east one cell before the first commit opcode.  Otherwise the branch
    # enters `1Ns` southbound and simply falls through the worker floor.
    c.route((delta_branch_x, 6), S, [(29, 6)], (29, 9), E)
    c.run(30, 9, "1Ns")
    c.route((34, 9), E, [], (35, 9), E)
    c.run(35, 9, "r" + commit)
    c.route((commit_after_x, 9), E, [], (loop_x, 9), E)
    c.counted_loop(loop_x, 9, "rs")

    # READ: the head is the target after the delta loop.  The first explicit
    # send is placed next to the forward pipe; the second is placed next to O.
    c.route((loop_x + 2, 4), E, [(59, 4), (59, 8)], (58, 8), W)
    c.run(57, 8, "rs", d=W)
    # Output takes a separate south port below the worker, so neither the
    # index command nor the east-facing value ring can tie with it.
    c.route((55, 8), W, [(commit_after_x, 8), (commit_after_x, 29)], (59, 29), E)
    c.run(60, 29, "s")
    c.route((61, 29), E, [(61, 30), (0, 30)], (0, 15), E)

    # WRITE: consume the old target first, fetch the deferred value in the
    # same west-side input band, then send the replacement into the ring.
    c.route((loop_x + 2, 9), E, [(59, 9), (59, 14)], (58, 14), W)
    c.run(57, 14, "r", d=W)
    c.route((56, 14), W, [(56, 18), (4, 18)], (4, 18), W)
    c.run(3, 18, "r", d=W)
    c.route((2, 18), W, [(1, 18), (1, 25), (59, 25), (59, 17)], (59, 17), E)
    c.run(60, 17, "s")
    c.route((61, 17), E, [(61, 30), (0, 30)], (0, 15), E)

    return c


def build(size: int = 10) -> tuple[list[str], DebugMap]:
    """Assemble the compact worker with marked, folded physical pipes."""
    c = worker(size)
    loop_x = 35 + 1 + len(commit_ops(size)) + 1
    grid = Circuit(78, 66)
    wx, wy = 8, 10
    _stamp(grid, c, (wx, wy))
    _walls(grid, (wx, wy), c.w, c.h)
    debug = DebugMap(f"one-pass compact v2 n={size}")
    debug.region_relative("worker", (wx, wy), 0, 0, c.w, c.h, note="shared delta, per-arm commit, and one relative pass", color="#38bdf8")
    debug.region_relative("input-band", (wx, wy), 0, 13, 8, 7, note="op, addresses, and deferred write value", color="#22c55e")
    debug.region_relative("read-arm", (wx, wy), 29, 4, 33, 11, note="commit current then rotate/read/reappend", color="#a78bfa")
    debug.region_relative("write-arm", (wx, wy), 28, 5, 34, 21, note="commit current then rotate/replace", color="#fb923c")

    _room(grid, 2, wy + 17, "I")
    input_pipe = [(5, wy + 18), (6, wy + 18)]
    _draw_pipe(grid, input_pipe)
    debug.region("input", 2, wy + 17, 3, 3, note="operation stream", color="#22c55e")
    debug.lane("input-pipe", input_pipe, kind="pipe", expect="op, address, deferred write value", color="#22c55e")

    _room(grid, 67, wy + c.h + 3, "O")
    output_pipe = [(68, wy + c.h + 1), (68, wy + c.h + 2)]
    _draw_pipe(grid, output_pipe)
    debug.region("output", 67, wy + c.h + 3, 3, 3, note="read results", color="#a78bfa")
    debug.lane("output-pipe", output_pipe, kind="pipe", expect="read value only", color="#a78bfa")

    index = register_cell("current", note="logical ring head", color="#facc15")
    ix, iy = 61, 2
    _stamp(grid, index.circuit, (ix, iy))
    command = [(57, wy - 2), (57, wy - 3), (59, wy - 3), (59, iy + 3), (60, iy + 3)]
    # Central top endpoint: it owns both current-register commits, while the
    # ring-return endpoint remains nearer to every pass/target receive.
    response = [(ix + index.width, iy + 3), (68, iy + 3), (68, 1), (39, 1), (39, wy - 2)]
    _draw_pipe(grid, command)
    _draw_pipe(grid, response)
    debug.region("current-index", ix, iy, index.width, index.height, note=index.note, color=index.color)
    debug.lane("index-command", command, kind="pipe", expect="fetch/store current in FIFO order", color="#facc15")
    debug.lane("index-response", response, kind="pipe", expect="current response", color="#fde68a")

    relay = zero_fill_relay("relay", size, note="zero fill and ring turnaround", color="#fb7185")
    rx, ry = 2, 50
    _stamp(grid, relay.circuit, (rx, ry))
    # The source arrow must point away from the east wall before the pipe folds.
    # A vertical first leg is a valid pipe, but it belongs to no worker port.
    forward = [(71, wy + 6), (72, wy + 6), (72, 48), (20, 48), (20, 49), (72, 49), (72, 51), (12, 51), (12, ry + 6), (11, ry + 6)]
    # The return starts westward from the relay's west wall, then folds under
    # the board before arriving westward into the worker's east wall.
    returned = [(rx - 1, ry + 4), (0, ry + 4), (0, 60), (77, 60), (77, wy + 4), (71, wy + 4)]
    forward_slots = _draw_pipe(grid, forward)
    return_slots = _draw_pipe(grid, returned)
    if forward_slots + return_slots < size + 1:
        raise ValueError("ring capacity is too small")
    debug.region("relay", rx, ry, relay.width, relay.height, note=relay.note, color=relay.color)
    debug.lane("ring-forward", forward, kind="pipe", expect="passed and appended values", color="#34d399")
    debug.lane("ring-return", returned, kind="pipe", expect="ring head values", color="#10b981")
    debug.lane_relative("read-pass", (wx, wy), [(loop_x, 4), (loop_x + 1, 4), (loop_x + 1, 7), (loop_x, 7), (loop_x, 4)], kind="expected", expect="BP=delta", color="#22c55e")
    debug.lane_relative("write-pass", (wx, wy), [(loop_x, 9), (loop_x + 1, 9), (loop_x + 1, 12), (loop_x, 12), (loop_x, 9)], kind="expected", expect="BP=delta", color="#f97316")
    return [row.rstrip() for row in grid.rows() if row.strip()], debug


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=10)
    parser.add_argument("--man", type=Path)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    rows, debug = build(args.size)
    if args.man:
        args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    if args.html:
        debug.write_html(rows, args.html)
    if args.json:
        debug.write_json(args.json)
    if not (args.man or args.html or args.json):
        print("\n".join(rows))
