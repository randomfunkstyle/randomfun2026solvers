#!/usr/bin/env python3
"""One-ring relative MEMORY machine, built from marked access containers.

The tape head is the persistent ``current`` register.  Each access commits
``current = address + 1`` before moving the tape, then rotates exactly
``(address - current) mod size`` values.  Read consumes/reappends the target;
write consumes the target and appends the next input value.  There is no
restore pass and no acknowledgement/delay lane: register commands share one
FIFO pipe, so a later fetch is ordered after the preceding store.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, E, N, S, W
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.memory_blocks import register_cell
from randomfun2026solvers.memory_onepass_compact import commit_ops, delta_ops
from randomfun2026solvers.memory_onepass import compact_shared_setup_draft
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


def access_container(size: int, *, write: bool) -> Circuit:
    """Create one operation arm with entry from the east and exit east.

    The command/value routes are intentionally local to this container.  The
    assembly owns the physical pipes and is responsible for showing their
    named lanes in the debug sidecar.
    """
    c = Circuit(36, 26, strict_corridors=True)
    # Enter at (3, 10) heading west.  The address comes directly from input;
    # keeping it in B lets BP become the relative pass count.
    c.run(2, 10, "rM", d=W)
    c.route((0, 10), W, [(0, 4)], (4, 4), E)
    c.run(4, 4, "1Ns")
    c.route((8, 4), E, [], (10, 4), E)
    c.run(10, 4, "r" + delta_ops(size) + "b")

    # Re-fetch current while BP retains delta, then store address+1 before the
    # data ring moves.  FIFO ordering makes the later operation observe it.
    c.route((22, 4), E, [(24, 4), (24, 11), (3, 11), (3, 12)], (4, 12), E)
    c.run(4, 12, "1Ns")
    c.route((8, 12), E, [], (10, 12), E)
    c.run(10, 12, "r")
    c.route((11, 12), E, [(18, 12), (18, 15), (0, 15), (0, 14)], (1, 14), E)
    c.run(1, 14, commit_ops(size))
    c.route((18, 14), E, [(19, 14), (19, 17), (3, 17), (3, 18)], (4, 18), E)
    c.run(4, 18, "1Ns")
    c.route((8, 18), E, [], (10, 18), E)
    c.run(10, 18, "r")
    c.route((11, 18), E, [], (22, 18), E)
    c.counted_loop(22, 18, "rs")
    c.route((24, 18), E, [(28, 18), (28, 20)], (27, 20), W)
    c.run(27, 20, "r", d=W)  # consume the target after exactly delta passes

    if not write:
        # A stays live through the two sends.  The first is close only to O;
        # the second is close only to the forward ring pipe.
        c.route((26, 20), W, [(26, 22)], (30, 22), S)
        c.run(30, 23, "s", d=S)
        c.route((30, 24), S, [(32, 24), (32, 21)], (33, 21), E)
        c.run(33, 21, "s")
    else:
        # The old target was consumed above.  Pull the value lazily, only once
        # the target slot is available, then append it to the same ring.
        c.route((26, 20), W, [(26, 24), (3, 24)], (3, 23), N)
        c.run(3, 23, "r", d=N)
        c.route((3, 22), N, [(2, 22), (2, 25), (32, 25), (32, 21)], (32, 21), E)
        c.run(33, 21, "s")
    return c


def build_machine(size: int = 10) -> tuple[list[str], DebugMap]:
    """Build a persistent one-pass MEMORY machine for ``size`` cells."""
    if size <= 0:
        raise ValueError("size must be positive")

    read = access_container(size, write=False)
    write = access_container(size, write=True)
    worker = Circuit(38, 57, strict_corridors=True)
    _stamp(worker, read, (1, 0))
    _stamp(worker, write, (1, 31))

    # One runner is the operation dispatcher.  It enters a container from its
    # east edge, then both containers return to this same input instruction.
    worker.run(1, 28, "@rX")
    worker.route((4, 28), E, [(37, 28), (37, 10)], (4, 10), W)
    worker.turn(3, 29, E)
    worker.route((4, 29), E, [(37, 29), (37, 41)], (4, 41), W)
    worker.route((35, 21), E, [(36, 21), (36, 27), (0, 27)], (0, 28), E)
    worker.route((35, 52), E, [(36, 52), (36, 30), (0, 30)], (0, 28), E)

    # The ring is coiled in the short band directly below the worker.  Pipe
    # capacity is dense ``>^v`` storage, not a reason to enlarge the canvas.
    grid = Circuit(76, 80)
    wx, wy = 13, 10
    _stamp(grid, worker, (wx, wy))
    _walls(grid, (wx, wy), worker.w, worker.h)
    debug = DebugMap(f"one-pass persistent memory n={size}")
    debug.region_relative("worker", (wx, wy), 0, 0, worker.w, worker.h, note="opcode dispatch and one relative pass per operation", color="#38bdf8")
    debug.region_relative("read-access", (wx, wy), 1, 0, read.w, read.h, note="read: rotate delta, emit and reappend target", color="#a78bfa")
    debug.region_relative("write-access", (wx, wy), 1, 31, write.w, write.h, note="write: rotate delta, discard target, append input", color="#fb923c")
    debug.region_relative("main", (wx, wy), 0, 27, 5, 4, note="op 0 routes to read; op 1 routes to write", color="#60a5fa")

    # One incoming pipe passes the read address, write address, and deferred
    # write value in FIFO order.  It stays on the worker's west side.
    input_y = wy + 10
    _room(grid, wx - 6, input_y - 1, "I")
    input_pipe = [(wx - 3, input_y), (wx - 2, input_y), (wx - 2, wy + 41)]
    _draw_pipe(grid, input_pipe)
    debug.region("input", wx - 6, input_y - 1, 3, 3, note="operation stream", color="#22c55e")
    debug.lane("input-pipe", input_pipe, kind="pipe", expect="op, address, and deferred write value", color="#22c55e")

    # O sits beside the read-only target send.  It is intentionally a two-cell
    # pipe; the long pipes below are the memory ring, not control timing.
    output_y = wy + 23
    _room(grid, wx + worker.w + 3, output_y - 1, "O")
    output_pipe = [(wx + worker.w + 1, output_y), (wx + worker.w + 2, output_y)]
    _draw_pipe(grid, output_pipe)
    debug.region("output", wx + worker.w + 3, output_y - 1, 3, 3, note="read results", color="#a78bfa")
    debug.lane("output-pipe", output_pipe, kind="pipe", expect="read target only", color="#a78bfa")

    index = register_cell("current", note="logical address at ring head", color="#facc15")
    ix, iy = wx + 2, 0
    _stamp(grid, index.circuit, (ix, iy))
    command = [(wx + 6, wy - 2), (wx + 6, wy - 3), (ix - 2, wy - 3), (ix - 2, iy + 3), (ix - 1, iy + 3)]
    response = [(ix + index.width, iy + 3), (wx + 10, iy + 3), (wx + 10, wy - 2)]
    _draw_pipe(grid, command)
    _draw_pipe(grid, response)
    debug.region("current-index", ix, iy, index.width, index.height, note=index.note, color=index.color)
    debug.lane("index-command", command, kind="pipe", expect="fetch/store current in FIFO order", color="#facc15")
    debug.lane("index-response", response, kind="pipe", expect="current value", color="#fde68a")

    relay = zero_fill_relay("relay", size, note="initialise and turn the value ring", color="#fb7185")
    rx, ry = 58, 72
    _stamp(grid, relay.circuit, (rx, ry))
    # Forward and return are compact serpentine storage lanes.  The only long
    # straight portions sit beside the worker, where both access containers can
    # select their expected direction.  Every turn below is a real pipe cell.
    forward = [
        (wx + worker.w + 1, wy + 21), (75, wy + 21), (75, 68),
        (58, 68), (58, 69), (75, 69), (75, ry + 6), (rx + 9, ry + 6),
    ]
    returned = [
        (rx - 1, ry + 4), (13, ry + 4), (13, 70), (15, 70),
        (55, 70), (55, 71), (15, 71), (15, 72), (55, 72),
        (57, 72), (57, wy + 25),
    ]
    forward_slots = _draw_pipe(grid, forward)
    return_slots = _draw_pipe(grid, returned)
    if forward_slots + return_slots < size + 1:
        raise ValueError("compact ring does not have enough value capacity")
    debug.region("relay", rx, ry, relay.width, relay.height, note=relay.note, color=relay.color)
    debug.lane("ring-forward", forward, kind="pipe", expect="passed and appended values", color="#34d399")
    debug.lane("ring-return", returned, kind="pipe", expect="ring head and relay zero-fill", color="#10b981")
    debug.lane_relative("read-delta-pass", (wx, wy), [(23, 18), (24, 18), (24, 21), (23, 21), (23, 18)], kind="expected", expect="BP=delta rotates only the requested distance", color="#22c55e")
    debug.lane_relative("write-delta-pass", (wx, wy), [(23, 49), (24, 49), (24, 52), (23, 52), (23, 49)], kind="expected", expect="BP=delta rotates only the requested distance", color="#f97316")

    return [row.rstrip() for row in grid.rows() if row.strip()], debug


def build_compact_draft_machine(size: int = 10) -> tuple[list[str], DebugMap]:
    """Wire the single-input compact control draft to a real value ring.

    This is the integration checkpoint for the compact worker.  It deliberately
    retains the draft's read broadcast while routing is audited; the final
    builder replaces that one instruction with the two adjacent dedicated
    sends needed once the index command pipe is present.
    """
    inner, inner_debug = compact_shared_setup_draft(size)
    worker = Circuit(36, 35, strict_corridors=True)
    _stamp(worker, inner, (1, 1))
    # The draft's store sequence ends immediately east of local (29,29).
    # Return outside the inner box to its existing main input at (1,7).
    worker.route((30, 30), E, [(34, 30), (34, 33), (0, 33), (0, 8)], (1, 8), E)

    grid = Circuit(70, 66)
    wx, wy = 15, 10
    _stamp(grid, worker, (wx, wy))
    _walls(grid, (wx, wy), worker.w, worker.h)
    debug = DebugMap(f"compact one-pass draft integration n={size}")
    debug.region_relative("worker", (wx, wy), 0, 0, worker.w, worker.h, note="single input region, one relative pass", color="#38bdf8")
    translated_inner = inner_debug.translated(wx + 1, wy + 1)
    debug.regions.extend(translated_inner.regions)
    debug.lanes.extend(translated_inner.lanes)

    input_y = wy + 8
    _room(grid, wx - 6, input_y - 1, "I")
    input_pipe = [(wx - 3, input_y), (wx - 2, input_y)]
    _draw_pipe(grid, input_pipe)
    debug.region("input", wx - 6, input_y - 1, 3, 3, note="op, address, and write value", color="#22c55e")
    debug.lane("input-pipe", input_pipe, kind="pipe", expect="compact west-side input", color="#22c55e")

    # The broadcast read instruction is between the two ring sends.  These
    # initial anchors prove the compact geometry before the broadcast is split.
    _room(grid, 2, 25, "O")
    output_pipe = [(5, 26), (6, 26)]
    _draw_pipe(grid, output_pipe)
    debug.region("output", 2, 25, 3, 3, note="read results", color="#a78bfa")
    debug.lane("output-pipe", output_pipe, kind="pipe", expect="read target", color="#a78bfa")

    index = register_cell("current", note="logical ring head", color="#facc15")
    ix, iy = wx + 4, 0
    _stamp(grid, index.circuit, (ix, iy))
    command = [(wx + 9, wy - 2), (wx + 9, wy - 3), (ix - 2, wy - 3), (ix - 2, iy + 3), (ix - 1, iy + 3)]
    response = [(ix + index.width, iy + 3), (wx + 16, iy + 3), (wx + 16, wy - 2)]
    _draw_pipe(grid, command)
    _draw_pipe(grid, response)
    debug.region("current-index", ix, iy, index.width, index.height, note=index.note, color=index.color)
    debug.lane("index-command", command, kind="pipe", expect="fetch/store current", color="#facc15")
    debug.lane("index-response", response, kind="pipe", expect="current response", color="#fde68a")

    relay = zero_fill_relay("relay", size, note="initialise and turn ring", color="#fb7185")
    rx, ry = 2, 52
    _stamp(grid, relay.circuit, (rx, ry))
    # Both pipes coil in the 10-row band below the worker.  Their combined
    # capacity is independent of any control route.
    forward = [(wx + worker.w + 1, wy + 13), (62, wy + 13), (62, 48), (11, 48), (11, 58), (rx + 9, ry + 6)]
    returned = [(rx - 1, ry + 4), (1, 61), (63, 61), (63, wy + 12), (wx + worker.w + 1, wy + 12)]
    _draw_pipe(grid, forward)
    _draw_pipe(grid, returned)
    debug.region("relay", rx, ry, relay.width, relay.height, note=relay.note, color=relay.color)
    debug.lane("ring-forward", forward, kind="pipe", expect="pass and append values", color="#34d399")
    debug.lane("ring-return", returned, kind="pipe", expect="ring head values", color="#10b981")
    return [row.rstrip() for row in grid.rows() if row.strip()], debug


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=10)
    parser.add_argument("--man", type=Path)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    rows, debug = build_compact_draft_machine(args.size)
    if args.man:
        args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    if args.html:
        debug.write_html(rows, args.html)
    if args.json:
        debug.write_json(args.json)
