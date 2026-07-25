#!/usr/bin/env python3
"""Two-bank implementation of the 100-cell ``memory`` problem.

The dispatcher maps addresses 0..49 to a low 50-cell full-lap tape and
addresses 50..99 to a high tape after subtracting 50.  Only one request is
outstanding: both banks acknowledge every operation through a merge relay, so
reads remain ordered and no timing delay is guessed.

This module is a deliberately roomy executable reference, not a submission
candidate.  It establishes the complete protocol and the exact-capacity bank
rings so a future shared-room/Y layout can be compared against measured data
rather than an optimistic rotation-only model.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, E, N, S, W
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.memory_tape import (
    COMPACT_RELAY,
    V2_ACK_OUT_ROW,
    V2_FWD_ROW,
    V2_IH,
    V2_IN_ROW,
    V2_IW,
    V2_RET_COL,
    _draw_pipe,
    worker_v2,
)

BANK_SIZE = 50
BANK_FWD_ROW = 16
DISPATCH_IW, DISPATCH_IH = 30, 24


def _stamp(dst: Circuit, src: Circuit, at: tuple[int, int]) -> None:
    ox, oy = at
    for (x, y), glyph in src.cell.items():
        dst.set(ox + x, oy + y, glyph)


def _walls(dst: Circuit, at: tuple[int, int], iw: int, ih: int) -> None:
    ox, oy = at
    for x in range(-1, iw + 1):
        dst.set(ox + x, oy - 1, "+" if x in (-1, iw) else "-")
        dst.set(ox + x, oy + ih, "+" if x in (-1, iw) else "-")
    for y in range(ih):
        dst.set(ox - 1, oy + y, "|")
        dst.set(ox + iw, oy + y, "|")


def _io_room(dst: Circuit, x: int, y: int, label: str) -> None:
    for dy, row in enumerate(("+-+", f"|{label}|", "+-+")):
        for dx, glyph in enumerate(row):
            dst.set(x + dx, y + dy, glyph)


def dispatcher() -> Circuit:
    """Parse one request, send it to one bank, and wait for completion."""
    c = Circuit(DISPATCH_IW, DISPATCH_IH)

    # Spawn once; MAIN is rX at (4,8).  READ stays east, WRITE turns south.
    c.run(1, 8, "@>>rX")

    # READ address selector.  A=addr-50 and B=50 at X.
    c.run(6, 8, "rM`50`W-X")

    # Low READ: add 50 back, send (0, addr) to the west command pipe.
    c.route((14, 7), N, [(14, 5)], (12, 5), W)
    c.run(12, 5, "+M0sWs", d=W)

    # High READ: zero continues east and positive turns south; merge at (16,9).
    c.route((15, 8), E, [(16, 8), (16, 9)], (16, 10), E)
    c.route((14, 9), S, [(14, 10), (16, 10)], (17, 10), E)
    c.run(17, 10, "M0sWs")

    # WRITE selector, reached from MAIN's clockwise branch.
    c.route((5, 9), S, [(5, 17)], (7, 17), E)
    c.run(7, 17, "rM`50`W-X")

    # Low WRITE sends (1, addr), then consumes/sends the deferred value beside I.
    c.route((15, 16), N, [(15, 14)], (12, 14), W)
    c.run(12, 14, "+M1sWs", d=W)
    c.route((6, 14), W, [(4, 14)], (4, 12), N)
    c.run(4, 12, "rs", d=N)

    # High WRITE: merge zero/positive, send (1, addr-50), fetch value near I,
    # then carry it back to the east command pipe.
    c.route((16, 17), E, [(17, 17), (17, 18)], (17, 19), E)
    c.route((15, 18), S, [(15, 19), (17, 19)], (18, 19), E)
    c.run(18, 19, "M1sWs")
    c.route((23, 19), E, [(27, 19), (27, 22), (4, 22)], (4, 20), N)
    c.run(4, 20, "r")
    c.route((4, 19), N, [(6, 19), (6, 21), (25, 21)], (25, 20), N)
    c.run(25, 20, "s", d=N)

    # Both READ paths wait for the selected bank's value, forward it to O, and
    # then re-enter MAIN.  Both WRITE paths wait for/discard their ack.
    # The generous proof layout keeps these joins outside the selector lanes.
    c.route((6, 5), W, [(1, 5), (1, 2)], (25, 2), E)
    c.route((22, 10), E, [(29, 10), (29, 1), (25, 1)], (25, 2), E)
    c.run(26, 2, "rs")
    c.route((28, 2), E, [(28, 0), (3, 0)], (3, 7), S)
    c.turn(3, 8, E)

    c.route((4, 10), N, [(0, 10), (0, 3)], (10, 3), E)
    c.route(
        (25, 19),
        N,
        [(25, 18), (24, 18), (24, 4), (10, 4)],
        (10, 3),
        E,
    )
    c.run(11, 3, "r")
    c.route((12, 3), E, [(17, 3), (17, 1), (2, 1)], (2, 7), S)
    c.turn(2, 8, E)
    return c


@dataclass(frozen=True)
class BankPorts:
    command: tuple[int, int]
    response: tuple[int, int]
    forward: tuple[int, int]
    returned: tuple[int, int]


def _place_bank(
    grid: Circuit,
    *,
    origin: tuple[int, int],
    relay_origin: tuple[int, int],
) -> BankPorts:
    """Place one acknowledged 50-cell worker and its private tape ring."""
    wx, wy = origin
    worker = worker_v2(BANK_SIZE, width=24, height=20, write_ack=True)
    _stamp(grid, worker, origin)
    _walls(grid, origin, worker.w, worker.h)

    rx, ry = relay_origin
    for dy, row in enumerate(COMPACT_RELAY):
        for dx, glyph in enumerate(row):
            if glyph != " ":
                grid.set(rx + dx, ry + dy, glyph)

    # Keep the ring at the exact 51-cell minimum.  A WRITE briefly holds the
    # replacement and displaced values simultaneously, so 50 cells would
    # deadlock; every extra pipe cell also adds avoidable phase latency.
    worker_right = wx + worker.w
    worker_bottom = wy + worker.h
    relay_right = rx + len(COMPACT_RELAY[0]) - 1
    fwd = [
        (worker_right + 1, wy + BANK_FWD_ROW),
        (worker_right + 11, wy + BANK_FWD_ROW),
        (worker_right + 11, ry + 1),
        (relay_right + 1, ry + 1),
    ]
    ret = [
        (relay_right + 1, ry + 2),
        (relay_right + 2, ry + 2),
        (relay_right + 2, ry + 4),
        (wx + V2_RET_COL, ry + 4),
        (wx + V2_RET_COL, worker_bottom + 1),
    ]
    slots = _draw_pipe(grid, fwd) + _draw_pipe(grid, ret)
    if slots != BANK_SIZE + 1:
        raise ValueError(f"bank ring has {slots} slots, expected {BANK_SIZE + 1}")

    return BankPorts(
        command=(wx - 2, wy + V2_IN_ROW),
        response=(wx - 2, wy + V2_ACK_OUT_ROW),
        forward=fwd[0],
        returned=ret[-1],
    )


def build() -> tuple[list[str], DebugMap]:
    """Assemble the complete, exact-capacity two-bank reference machine."""
    grid = Circuit(115, 80)
    debug = DebugMap("memory — two acknowledged 50-cell banks")

    dx, dy = 39, 6
    dispatch = dispatcher()
    _stamp(grid, dispatch, (dx, dy))
    _walls(grid, (dx, dy), dispatch.w, dispatch.h)
    debug.region("dispatcher", dx, dy, dispatch.w, dispatch.h, note="addr−50 bank select; one outstanding operation", color="#a855f7")

    low = _place_bank(grid, origin=(8, 39), relay_origin=(34, 61))
    high = _place_bank(grid, origin=(67, 39), relay_origin=(93, 61))
    debug.region("low-bank", 8, 39, 24, 20, note="addresses 0..49; exact 51-slot ring", color="#22c55e")
    debug.region("high-bank", 67, 39, 24, 20, note="addresses 50..99 after subtracting 50; exact 51-slot ring", color="#3b82f6")

    # Input enters the dispatcher's west wall beside MAIN.
    _io_room(grid, 31, dy + 7, "I")
    input_pipe = [(34, dy + 8), (37, dy + 8)]
    _draw_pipe(grid, input_pipe)
    debug.lane("input", input_pipe, kind="pipe", expect="op, addr, optional value", color="#22c55e")

    # Low/high command pipes leave opposite sides of the dispatcher.
    low_cmd = [(dx - 2, dy + 6), (4, dy + 6), (4, low.command[1]), low.command]
    high_cmd = [
        (dx + dispatch.w + 1, dy + 14),
        (105, dy + 14),
        (105, 35),
        (63, 35),
        (63, high.command[1]),
        high.command,
    ]
    _draw_pipe(grid, low_cmd)
    _draw_pipe(grid, high_cmd)
    debug.lane("low-command", low_cmd, kind="pipe", expect="selected requests for addresses 0..49", color="#22c55e")
    debug.lane("high-command", high_cmd, kind="pipe", expect="selected requests with local address addr−50", color="#3b82f6")

    # Response merge: one tiny room receives either bank completion and sends a
    # single FIFO acknowledgement to the dispatcher's north wall.
    collector_x, collector_y = 90, 8
    collector = (
        "+----+",
        "|@>Rv|",
        "| ^s<|",
        "+----+",
    )
    for yy, row in enumerate(collector):
        for xx, glyph in enumerate(row):
            if glyph != " ":
                grid.set(collector_x + xx, collector_y + yy, glyph)
    low_resp = [
        low.response,
        (2, low.response[1]),
        (2, 74),
        (114, 74),
        (114, collector_y + 1),
        (collector_x + len(collector[0]), collector_y + 1),
    ]
    high_resp = [
        high.response,
        (60, high.response[1]),
        (60, 72),
        (112, 72),
        (112, collector_y + 2),
        (collector_x + len(collector[0]), collector_y + 2),
    ]
    ack = [
        (collector_x + 3, collector_y - 1),
        (collector_x + 3, collector_y - 2),
        (collector_x + 3, 3),
        (dx + 11, 3),
        (dx + 11, dy - 2),
    ]
    _draw_pipe(grid, low_resp)
    _draw_pipe(grid, high_resp)
    _draw_pipe(grid, ack)
    debug.lane("low-completion", low_resp, kind="pipe", expect="read value or write acknowledgement", color="#22c55e")
    debug.lane("high-completion", high_resp, kind="pipe", expect="read value or write acknowledgement", color="#3b82f6")
    debug.lane("merged-completion", ack, kind="pipe", expect="one completion per dispatched request", color="#f59e0b")

    # Dispatcher forwards only READ completions to the real output.
    _io_room(grid, 83, 7, "O")
    output_pipe = [(dx + dispatch.w + 1, dy + 2), (82, dy + 2)]
    _draw_pipe(grid, output_pipe)
    debug.lane("output", output_pipe, kind="pipe", expect="READ values only, in request order", color="#ef4444")

    raw = grid.rows()
    used_y = [y for y, row in enumerate(raw) if row.strip()]
    top, bottom = min(used_y), max(used_y)
    left = min(x for row in raw[top : bottom + 1] for x, ch in enumerate(row) if not ch.isspace())
    right = max(x for row in raw[top : bottom + 1] for x, ch in enumerate(row) if not ch.isspace())
    rows = [row[left : right + 1].rstrip() for row in raw[top : bottom + 1]]
    return rows, debug.translated(-left, -top)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--man", type=Path, required=True)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    rows, debug = build()
    args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    debug.write_html(rows, args.html)
    debug.write_json(args.json)


if __name__ == "__main__":
    main()
