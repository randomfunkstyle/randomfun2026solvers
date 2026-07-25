#!/usr/bin/env python3
"""Current-index, one-pass MEMORY machine with compact debug-marked layouts.

The current-register commit happens before each relative access, then the read
and write arms consume exactly ``BP=delta`` values from the persistent ring.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, E, N, S, W
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.memory_blocks import register_cell
from randomfun2026solvers.memory_onepass_compact import commit_ops, delta_ops
from randomfun2026solvers.memory_onepass_relay import zero_fill_relay, zero_fill_relay_rot180
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


def _compact_split(ops: str, desired: int) -> int:
    """Return the nearest split to ``desired`` that is outside a literal."""
    legal: list[int] = []
    in_literal = False
    for index, ch in enumerate(ops, start=1):
        if ch == "`":
            in_literal = not in_literal
        if not in_literal and index < len(ops):
            legal.append(index)
    if in_literal:
        raise ValueError("unmatched backtick in compact instruction run")
    if not legal:
        raise ValueError("compact instruction run has no legal fold")
    return min(legal, key=lambda split: (abs(split - desired), split))


def compact_run(
    c: Circuit,
    x: int,
    y: int,
    ops: str,
    east_weight: int,
    *,
    fold=S,
) -> tuple[int, int]:
    """Execute ``ops`` east, then west on the row below.

    Walking west makes the suffix appear reversed in the rendered row, while
    the man still encounters the opcodes in their original order. Returns the
    first cell after the suffix while still heading west.
    """
    east_count = _compact_split(ops, east_weight)
    east = ops[:east_count]
    west = ops[east_count:]
    if fold not in (N, S):
        raise ValueError("snake fold must be north or south")
    suffix_y = y + fold[1]
    turn_x, _ = c.run(x, y, east)
    c.turn(turn_x, y, fold)
    c.turn(turn_x, suffix_y, W)
    return c.run(turn_x - 1, suffix_y, west, d=W)


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


def worker_snake(size: int = 10) -> Circuit:
    """Narrow worker with both current-index commits folded into snakes."""
    if size <= 0:
        raise ValueError("size must be positive")
    c = Circuit(46, 32, strict_corridors=True)

    c.run(1, 15, "@rbrM")
    c.route((6, 15), E, [(8, 15), (8, 4)], (10, 4), E)
    c.run(10, 4, "1Ns")
    c.route((14, 4), E, [], (16, 4), E)
    delta = delta_ops(size)
    commit = "r" + commit_ops(size)
    delta_branch_x = 16 + 1 + len(delta)
    c.run(16, 4, "r" + delta + "d")

    # READ: short fetch command east, commit suffix west, then a nearby pass.
    c.run(delta_branch_x + 2, 4, "b1Ns")
    read_after, read_y = compact_run(
        c,
        delta_branch_x + 6,
        4,
        commit,
        east_weight=6,
        fold=N,
    )
    c.route(
        (read_after, read_y),
        W,
        [(read_after - 1, read_y), (read_after - 1, 0), (41, 0), (41, 6)],
        (42, 6),
        E,
    )
    read_loop_x = 42
    read_exit, _ = c.counted_loop(read_loop_x, 6, "rs")

    # WRITE: the opcode branch drops below the read loop, then folds the same
    # commit sequence back west before entering its own pass.
    c.run(delta_branch_x, 5, "b", d=S)
    c.route((delta_branch_x, 6), S, [(27, 6), (27, 11)], (27, 11), E)
    c.run(28, 11, "1Ns")
    write_after, write_y = compact_run(
        c,
        31,
        11,
        commit,
        east_weight=6,
    )
    c.route(
        (write_after, write_y),
        W,
        [(write_after, write_y + 1)],
        (39, write_y + 1),
        E,
    )
    write_loop_x = 40
    write_exit, _ = c.counted_loop(write_loop_x, write_y + 1, "rs")

    # READ target, reappend, then emit from the south-side output port.
    c.route(
        (read_exit, 6),
        E,
        [(45, 6), (45, 10), (41, 10), (41, 11)],
        (42, 11),
        E,
    )
    c.run(43, 11, "rs")
    c.route((45, 11), E, [(45, 27)], (7, 27), W)
    c.run(6, 27, "s", d=W)
    c.route((5, 27), W, [(4, 27), (4, 30), (0, 30)], (0, 15), E)

    # WRITE target, deferred input fetch, replacement, then main.
    c.route((write_exit, write_y + 1), E, [(44, write_y + 1), (44, 18)], (43, 18), W)
    c.run(42, 18, "r", d=W)
    c.route((41, 18), W, [], (6, 18), W)
    c.run(5, 18, "r", d=W)
    c.route((4, 18), W, [(3, 18), (3, 24), (36, 24)], (36, 24), E)
    c.run(37, 24, "s")
    c.route((38, 24), E, [(38, 30), (0, 30)], (0, 15), E)
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


def build_tight(size: int = 10) -> tuple[list[str], DebugMap]:
    """Assemble the same protocol with a 104-slot, locally folded ring.

    The input moves to the top edge, freeing the worker's west edge to start
    at column zero.  The relay is rotated 180 degrees so the worker-to-relay
    pipe is two cells; the return pipe supplies the required ring capacity in
    a compact right-side snake rather than stretching the canvas.
    """
    c = worker(size)
    loop_x = 35 + 1 + len(commit_ops(size)) + 1
    grid = Circuit(78, 50)
    wx, wy = 1, 10
    _stamp(grid, c, (wx, wy))
    _walls(grid, (wx, wy), c.w, c.h)
    debug = DebugMap(f"one-pass tight n={size}")
    debug.region_relative("worker", (wx, wy), 0, 0, c.w, c.h, note="shared delta, per-arm commit, and one relative pass", color="#38bdf8")
    debug.region_relative("input-band", (wx, wy), 0, 13, 8, 7, note="op, addresses, and deferred write value", color="#22c55e")
    debug.region_relative("read-arm", (wx, wy), 29, 4, 33, 11, note="commit current then rotate/read/reappend", color="#a78bfa")
    debug.region_relative("write-arm", (wx, wy), 28, 5, 34, 21, note="commit current then rotate/replace", color="#fb923c")

    _room(grid, 1, 2, "I")
    input_pipe = [(4, 3), (5, 3), (5, 7), (3, 7), (3, 8)]
    _draw_pipe(grid, input_pipe)
    debug.region("input", 1, 2, 3, 3, note="operation stream", color="#22c55e")
    debug.lane("input-pipe", input_pipe, kind="pipe", expect="op, address, deferred write value", color="#22c55e")

    _room(grid, 60, wy + c.h + 3, "O")
    output_pipe = [(61, wy + c.h + 1), (61, wy + c.h + 2)]
    _draw_pipe(grid, output_pipe)
    debug.region("output", 60, wy + c.h + 3, 3, 3, note="read results", color="#a78bfa")
    debug.lane("output-pipe", output_pipe, kind="pipe", expect="read value only", color="#a78bfa")

    index = register_cell("current", note="logical ring head", color="#facc15")
    ix, iy = 54, 2
    _stamp(grid, index.circuit, (ix, iy))
    command = [(50, wy - 2), (50, wy - 3), (52, wy - 3), (52, iy + 3), (53, iy + 3)]
    response = [(ix + index.width, iy + 3), (61, iy + 3), (61, 1), (30, 1), (30, wy - 2)]
    _draw_pipe(grid, command)
    _draw_pipe(grid, response)
    debug.region("current-index", ix, iy, index.width, index.height, note=index.note, color=index.color)
    debug.lane("index-command", command, kind="pipe", expect="fetch/store current in FIFO order", color="#facc15")
    debug.lane("index-response", response, kind="pipe", expect="current response", color="#fde68a")

    relay = zero_fill_relay_rot180("relay", size, note="zero fill and ring turnaround", color="#fb7185")
    rx, ry = 66, 15
    _stamp(grid, relay.circuit, (rx, ry))
    forward = [(64, wy + 6), (65, wy + 6)]
    returned = [
        (rx + relay.width, ry + 3), (76, ry + 3), (76, 35),
        (65, 35), (65, 36), (75, 36), (75, 37),
        (65, 37), (65, 38), (77, 38), (77, wy + 4), (64, wy + 4),
    ]
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


def build_snake(size: int = 10) -> tuple[list[str], DebugMap]:
    """Assemble the folded worker with adjacent, spacer-free pipe folds."""
    c = worker_snake(size)
    grid = Circuit(56, 53)
    wx, wy = 6, 10
    _stamp(grid, c, (wx, wy))
    _walls(grid, (wx, wy), c.w, c.h)
    debug = DebugMap(f"one-pass snake n={size}")
    debug.region_relative("worker", (wx, wy), 0, 0, c.w, c.h, note="folded delta/commit rails and one relative pass", color="#38bdf8")
    debug.region_relative("shared-setup", (wx, wy), 8, 3, 22, 13, note="input, current fetch, delta, opcode split", color="#60a5fa")
    debug.region_relative("read-arm", (wx, wy), 28, 3, 18, 25, note="folded commit, pass, target, output", color="#a78bfa")
    debug.region_relative("write-arm", (wx, wy), 24, 11, 22, 20, note="folded commit, pass, replace", color="#fb923c")

    _room(grid, 0, wy + 14, "I")
    input_pipe = [(3, wy + 15), (4, wy + 15)]
    _draw_pipe(grid, input_pipe)
    debug.region("input", 0, wy + 14, 3, 3, note="operation stream", color="#22c55e")
    debug.lane("input-pipe", input_pipe, kind="pipe", expect="op, address, deferred write value", color="#22c55e")

    _room(grid, 0, wy + 26, "O")
    output_pipe = [(4, wy + 27), (3, wy + 27)]
    _draw_pipe(grid, output_pipe)
    debug.region("output", 0, wy + 26, 3, 3, note="read results", color="#a78bfa")
    debug.lane("output-pipe", output_pipe, kind="pipe", expect="read value only", color="#a78bfa")

    index = register_cell("current", note="logical ring head", color="#facc15")
    ix, iy = 40, 2
    _stamp(grid, index.circuit, (ix, iy))
    command = [(wx + 28, wy - 2), (wx + 28, wy - 3), (38, wy - 3), (38, iy + 3), (39, iy + 3)]
    response = [(ix + index.width, iy + 3), (47, iy + 3), (47, 1), (wx + 26, 1), (wx + 26, wy - 2)]
    _draw_pipe(grid, command)
    _draw_pipe(grid, response)
    debug.region("current-index", ix, iy, index.width, index.height, note=index.note, color=index.color)
    debug.lane("index-command", command, kind="pipe", expect="fetch/store current in FIFO order", color="#facc15")
    debug.lane("index-response", response, kind="pipe", expect="current response", color="#fde68a")

    relay = zero_fill_relay("relay", size, note="zero fill and ring turnaround", color="#fb7185")
    rx, ry = 2, 43
    _stamp(grid, relay.circuit, (rx, ry))
    forward = [(wx + c.w + 1, wy + 16), (54, wy + 16), (54, 46), (12, 46), (12, ry + 6), (11, ry + 6)]
    returned = [
        (rx - 1, ry + 4), (0, ry + 4), (0, 51),
        (55, 51), (55, wy + 14), (wx + c.w + 1, wy + 14),
    ]
    forward_slots = _draw_pipe(grid, forward)
    return_slots = _draw_pipe(grid, returned)
    if forward_slots + return_slots < size + 1:
        raise ValueError("ring capacity is too small")
    debug.region("relay", rx, ry, relay.width, relay.height, note=relay.note, color=relay.color)
    debug.lane("ring-forward", forward, kind="pipe", expect="passed and appended values", color="#34d399")
    debug.lane("ring-return", returned, kind="pipe", expect="ring head values", color="#10b981")
    debug.lane_relative("read-pass", (wx, wy), [(42, 6), (43, 6), (43, 9), (42, 9), (42, 6)], kind="expected", expect="BP=delta", color="#22c55e")
    debug.lane_relative("write-pass", (wx, wy), [(40, 13), (41, 13), (41, 16), (40, 16), (40, 13)], kind="expected", expect="BP=delta", color="#f97316")
    return [row.rstrip() for row in grid.rows() if row.strip()], debug


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=10)
    parser.add_argument("--man", type=Path)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--layout", choices=("wide", "tight", "snake"), default="snake")
    args = parser.parse_args()
    builders = {"wide": build, "tight": build_tight, "snake": build_snake}
    rows, debug = builders[args.layout](args.size)
    if args.man:
        args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    if args.html:
        debug.write_html(rows, args.html)
    if args.json:
        debug.write_json(args.json)
    if not (args.man or args.html or args.json):
        print("\n".join(rows))
