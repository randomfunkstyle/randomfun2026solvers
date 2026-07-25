#!/usr/bin/env python3
"""Relative-rotation variant for the Littleman MEMORY task.

This is intentionally a separate generator from ``memory_tape.py`` while the
architecture is experimental.  It keeps the same 100-value pipe tape, but stores
the logical address currently at the tape head in a 1-cell register room.  Each
operation rotates only ``delta = (addr - current) % 100`` cells, accesses the
target, then stores ``current = (current + delta + 1) % 100``.  Both operations
leave the tape head directly after the target: a read removes and appends the
same value, while a write drops the old target and appends its replacement.
"""
from __future__ import annotations

import sys

from randomfun2026solvers.circuit import Circuit, Collision, E, W, N, S, GLYPH
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.memory_tape import RELAY, _draw_pipe, lit


IW, IH = 72, 44
IN_ROW = 5
OUT_COL = 55
REG_OUT_COL = 8
REG_IN_COL = 18
TAPE_FWD_ROW = 25
TAPE_RET_COL = 58


def _reg_fetch(c: Circuit, send: tuple[int, int], recv: tuple[int, int], *, enter=E) -> tuple[int, int]:
    """Send -1 to the register and receive its stored value."""
    sx, sy = send
    rx, ry = recv
    c.run(sx, sy, "1Ns", d=enter)
    c.route((sx + 3 * enter[0], sy + 3 * enter[1]), enter, [(rx, sy)], (rx, ry), E)
    return c.run(rx, ry, "r")


def _reg_store(c: Circuit, send: tuple[int, int], *, enter=E) -> tuple[int, int]:
    """Store A into the register.  Leaves after sending command 1 and value."""
    sx, sy = send
    c.run(sx, sy, "M1sWs", d=enter)
    return sx + 5 * enter[0], sy + 5 * enter[1]


def _delta_setup(c: Circuit, n: int, y: int, calc_y: int, loop_y: int, via_x: int) -> tuple[int, int]:
    """Read addr, compute delta, and enter a counted tape pass loop.

    Entry: heading east, next instruction receives addr from input.
    Exit: east of the counted loop, with B=delta and BP exhausted to 0.
    """
    c.run(4, y, "rM")  # A=addr, B=addr
    c.route((6, y), E, [(6, calc_y)], (REG_OUT_COL, calc_y), E)
    after, _ = _reg_fetch(c, (REG_OUT_COL, calc_y), (REG_IN_COL, calc_y))
    # A=current, B=addr -> A=(addr-current)%n, B=delta, BP=delta
    calc_end, _ = c.run(after, calc_y, "W-M" + lit(n) + "W%Mb")
    c.route((calc_end, calc_y), E, [(via_x, calc_y), (via_x, loop_y)], (58, loop_y), W)
    c.turn(58, loop_y, S)
    c.turn(58, loop_y + 1, E)
    return c.counted_loop(59, loop_y + 1, "rs")


def _update_current(c: Circuit, n: int, current_bias: int, start: tuple[int, int], enter) -> tuple[int, int]:
    """Fetch old current, add preserved B=delta plus bias, mod n, store it."""
    sx, sy = start
    uy = 36
    c.route((sx, sy), enter, [(sx, 43), (0, 43), (0, uy)], (REG_OUT_COL, uy), E)
    after, _ = _reg_fetch(c, (REG_OUT_COL + 1, uy), (REG_IN_COL, uy))
    # A=old current, B=delta -> A=(old+delta+bias)%n
    bias_ops = "" if current_bias == 0 else lit(current_bias) + "W+M"
    calc_end, _ = c.run(after, uy, "+M" + bias_ops + lit(n) + "W%")
    c.route((calc_end, uy), E, [(34, uy), (34, uy + 2)], (REG_OUT_COL + 4, uy + 2), W)
    end = _reg_store(c, (REG_OUT_COL + 4, uy + 2), enter=W)
    # Acknowledge the store by fetching the register once more.  Without this,
    # the worker can reach the next operation before the store values traverse
    # the command pipe and the next fetch sees the old current.
    ack_y = uy + 4
    c.route(
        end,
        W,
        [(2, uy + 2), (2, uy + 6), (60, uy + 6), (60, ack_y)],
        (REG_OUT_COL, ack_y),
        E,
    )
    ack_end = _reg_fetch(c, (REG_OUT_COL + 1, ack_y), (REG_IN_COL, ack_y))
    c.route(ack_end, E, [(45, ack_y), (45, 4), (0, 4)], (0, IN_ROW), E)
    return (0, IN_ROW)


def worker_relative(n: int = 100, *, current_bias: int = 0) -> Circuit:
    c = Circuit(IW, IH)

    # Fill the tape with 100 zeros, then enter MAIN.
    init_end, _ = c.run(1, 0, "@" + lit(n) + "b")
    c.route((init_end, 0), E, [(62, 0), (62, 15)], (62, 15), E)
    fill, _ = c.counted_loop(62, 15, "0s")
    c.route((fill, 15), E, [(70, 15), (70, 4), (0, 4)], (0, IN_ROW), S)
    c.turn(0, IN_ROW, E)

    # MAIN: op==0 continues east into READ; op==1 turns clockwise into WRITE.
    c.run(1, IN_ROW, "rX")
    c.turn(2, IN_ROW + 1, E)
    c.turn(3, IN_ROW + 1, S)
    c.turn(3, IN_ROW + 2, E)

    # READ path.
    read_loop_exit, _ = _delta_setup(c, n, IN_ROW, 2, 22, 61)
    # A is clobbered by pass-through; B still holds delta.  Read target value,
    # emit it, then put the same value back on the tape.
    c.run(read_loop_exit, 23, "r")
    c.route((read_loop_exit + 1, 23), E, [(66, 23), (66, 1)], (OUT_COL, 1), W)
    out_end, _ = c.run(OUT_COL, 1, "s", d=W)
    c.route((out_end, 1), W, [(50, 1), (50, 27)], (66, 27), E)
    c.run(66, 27, "s")
    # A read consumes the target, emits it through the output pipe, then writes
    # it back after the head; that advances the logical current to target+1.
    c.route((67, 27), E, [(67, 35)], (58, 35), W)
    read_inc_x, read_inc_y = c.run(58, 35, "WM1+M", d=W)
    c.route((read_inc_x, read_inc_y), W, [(53, 34)], (68, 34), E)

    # WRITE path. Same relative rotation, then drop the target and append the
    # new input. The physical tape head is now target+1, just as after a read.
    write_loop_exit, _ = _delta_setup(c, n, IN_ROW + 2, 10, 28, 68)
    c.run(write_loop_exit, 29, "r")
    c.route((write_loop_exit + 1, 29), E, [(68, 29), (68, 37), (2, 37)], (2, 13), N)
    c.run(2, 13, "r", d=N)
    c.route((2, 12), N, [(69, 12), (69, 25)], (63, 25), W)
    c.run(63, 25, "s", d=W)
    # B still holds delta. Convert it to delta+1, then enter the shared update.
    c.route((62, 25), W, [], (62, 33), W)
    write_inc_x, write_inc_y = c.run(61, 33, "WM1+M", d=W)
    c.route((write_inc_x, write_inc_y), W, [(52, 33), (52, 34)], (68, 34), E)
    c.turn(69, 34, S)
    _update_current(c, n, current_bias, (69, 35), S)
    return c


def assemble_relative_debug(n: int = 100, *, current_bias: int = 0) -> tuple[list[str], DebugMap]:
    wk = worker_relative(n, current_bias=current_bias)
    g = Circuit(220, 130)
    WX, WY = 10, 16
    dbg = DebugMap(f"memory-relative n={n}", offset=(WX, WY))
    dbg.region("worker", WX, WY, IW, IH, note="main control room and tape access logic", color="#38bdf8")
    dbg.region("main-dispatch", 0, IN_ROW, 8, 4, local=True, note="read op; op=0 east/read, op=1 south/write")
    dbg.region("read-setup", 4, 2, 66, 24, local=True, note="addr/current fetch, delta, read target")
    dbg.region("write-setup", 2, 10, 68, 28, local=True, note="addr/current fetch, delta, consume old/write new")
    dbg.region("current-update", 0, 34, 71, 9, local=True, note="shared current register update and ack")
    for (x, y), ch in wk.cell.items():
        g.set(WX + x, WY + y, ch)
    for x in range(-1, IW + 1):
        g.set(WX + x, WY - 1, "+" if x in (-1, IW) else "-")
        g.set(WX + x, WY + IH, "+" if x in (-1, IW) else "-")
    for y in range(IH):
        g.set(WX - 1, WY + y, "|")
        g.set(WX + IW, WY + y, "|")

    # Input room.
    iy = WY + IN_ROW
    dbg.region("input-room", WX - 6, iy - 1, 3, 3, note="operation stream input", color="#22c55e")
    for i, r in enumerate(["+-+", "|I|", "+-+"]):
        for j, ch in enumerate(r):
            g.set(WX - 6 + j, iy - 1 + i, ch)
    g.set(WX - 3, iy, ">")
    g.set(WX - 2, iy, ">")

    # Output room above the worker.
    ox = WX + OUT_COL
    dbg.region("output-room", ox - 1, WY - 8, 3, 3, note="read results output", color="#a78bfa")
    for i, r in enumerate(["+-+", "|O|", "+-+"]):
        for j, ch in enumerate(r):
            g.set(ox - 1 + j, WY - 8 + i, ch)
    _draw_pipe(g, [(ox, WY - 2), (ox, WY - 5)])
    dbg.lane(
        "output-pipe",
        [(ox, WY - 2), (ox, WY - 5)],
        kind="pipe",
        expect="read target value moves worker -> output room",
        color="#a78bfa",
    )

    # Register room above the worker, using the known-good 1-value register.
    reg = ["+----+", "|vWs<|", "|v  W|", "|>@RX|", "|^  W|", "|^Wr<|", "+----+"]
    RX, RY = WX + 10, 2
    dbg.region("current-register", RX, RY, len(reg[0]), len(reg), note="1-cell register storing logical current", color="#facc15")
    for i, r in enumerate(reg):
        for j, ch in enumerate(r):
            g.set(RX + j, RY + i, ch)
    # Worker -> register command/value pipe.
    reg_cmd = [
        (WX + REG_OUT_COL, WY - 2),
        (WX + REG_OUT_COL, 10),
        (RX - 4, 10),
        (RX - 4, RY + 3),
        (RX - 1, RY + 3),
    ]
    _draw_pipe(
        g,
        reg_cmd,
    )
    dbg.lane(
        "register-command-pipe",
        reg_cmd,
        kind="pipe",
        expect="-1 means fetch; 1,value means store",
        color="#facc15",
    )
    # Register -> worker value pipe.
    reg_src = RX + len(reg[0])
    reg_val = [
        (reg_src, RY + 3),
        (reg_src + 3, RY + 3),
        (reg_src + 3, WY - 4),
        (WX + REG_IN_COL, WY - 4),
        (WX + REG_IN_COL, WY - 2),
    ]
    _draw_pipe(
        g,
        reg_val,
    )
    dbg.lane(
        "register-value-pipe",
        reg_val,
        kind="pipe",
        expect="current value returns register -> worker",
        color="#fde68a",
    )

    # Tape relay and folded value ring.
    bottom_y = WY + IH
    wall_x = WX + IW
    fy = WY + TAPE_FWD_ROW
    ret_col = WX + TAPE_RET_COL
    east = wall_x + 5
    b_fwd = bottom_y + 9
    relay_y = bottom_y + 5
    dbg.region("relay", 1, relay_y, len(RELAY[0]), len(RELAY), note="tape turnaround room", color="#fb7185")
    for i, r in enumerate(RELAY):
        for j, ch in enumerate(r):
            g.set(1 + j, relay_y + i, ch)
    relay_wall = len(RELAY[0])
    adj = relay_wall + 1
    fwd = [(wall_x + 1, fy), (east, fy), (east, b_fwd), (adj, b_fwd)]
    ret = [
        (adj, relay_y + 2),
        (east - 1, relay_y + 2),
        (east - 1, bottom_y + 3),
        (adj + 20, bottom_y + 3),
        (adj + 20, bottom_y + 2),
        (ret_col, bottom_y + 2),
        (ret_col, bottom_y + 1),
    ]
    n_fwd = _draw_pipe(g, fwd)
    n_ret = _draw_pipe(g, ret)
    dbg.lane("tape-forward-pipe", fwd, kind="pipe", expect="worker sends values to relay", color="#34d399")
    dbg.lane("tape-return-pipe", ret, kind="pipe", expect="relay returns values to worker", color="#10b981")
    dbg.lane(
        "read-delta-loop",
        dbg.points([(58, 22), (59, 22), (59, 23), (59, 24), (58, 24), (58, 23), (58, 22), (60, 22)]),
        kind="expected",
        expect="read rotates exactly delta=(addr-current)%n cells, not all n cells",
        color="#38bdf8",
    )
    dbg.lane(
        "write-delta-loop",
        dbg.points([(58, 28), (59, 28), (59, 29), (59, 30), (58, 30), (58, 29), (58, 28), (60, 28)]),
        kind="expected",
        expect="write first rotates delta=(addr-current)%n cells to reach target",
        color="#fb923c",
    )
    dbg.lane(
        "read-current-fetch",
        dbg.points([(4, IN_ROW), (6, IN_ROW), (6, 2), (REG_OUT_COL, 2), (REG_IN_COL, 2)]),
        kind="expected",
        expect="read addr -> ask current register -> receive current",
        color="#60a5fa",
    )
    dbg.lane(
        "read-target-access",
        dbg.points([(59, 23), (61, 23), (66, 23), (66, 1), (OUT_COL, 1), (50, 1), (50, 27), (67, 27)]),
        kind="expected",
        expect="after delta loop: r target, output, write same value back",
        color="#818cf8",
    )
    dbg.lane(
        "write-target-access",
        dbg.points([(59, 29), (68, 29), (68, 37), (2, 37), (2, 13), (2, 12), (69, 12), (69, 25), (63, 25)]),
        kind="expected",
        expect="after delta loop: consume target, read new value, write new value to tape",
        color="#fb923c",
    )
    dbg.lane(
        "write-current-advance",
        dbg.points([(62, 25), (62, 33), (61, 33), (57, 33), (52, 33), (52, 34), (68, 34), (69, 34)]),
        kind="expected",
        expect="B=delta becomes delta+1; no rest-of-ring pass is needed",
        color="#f97316",
    )
    dbg.lane(
        "current-update-entry",
        dbg.points([(69, 35), (69, 43), (0, 43), (0, 36), (REG_OUT_COL, 36)]),
        kind="expected",
        expect="enter update; B carries delta+1 to add",
        color="#eab308",
    )
    dbg.lane(
        "current-fetch",
        dbg.points([(REG_OUT_COL + 1, 36), (REG_OUT_COL + 4, 36), (REG_IN_COL, 36)]),
        kind="expected",
        expect="send -1 to current register and wait for old current",
        color="#facc15",
    )
    dbg.lane(
        "current-calc",
        dbg.points([(REG_IN_COL + 1, 36), (34, 36)]),
        kind="expected",
        expect="A=(old+B)%n; B is delta+1 for both read and write",
        color="#f59e0b",
    )
    dbg.lane(
        "current-store",
        dbg.points([(34, 36), (34, 38), (REG_OUT_COL + 4, 38)]),
        kind="expected",
        expect="store computed A into current register as command 1,value",
        color="#f97316",
    )
    dbg.lane(
        "current-ack",
        dbg.points([(REG_OUT_COL - 1, 38), (2, 38), (2, 42), (60, 42), (60, 40), (REG_OUT_COL, 40), (REG_OUT_COL + 1, 40), (REG_IN_COL, 40)]),
        kind="expected",
        expect="delay, then fetch register once to wait for store commit",
        color="#fde68a",
    )
    dbg.scenario(
        "write-read-7",
        "1 7 42 0 7",
        900,
        2400,
        watch=[
            "write-delta-loop", "write-target-access", "write-current-advance",
            "current-update-entry", "current-calc", "current-store", "current-ack",
            "register-command-pipe", "register-value-pipe",
        ],
        note="write 42 at address 7, then read address 7",
    )
    if n_fwd + n_ret < n + 1:
        raise Collision(f"tape holds {n_fwd + n_ret}, need {n + 1}")
    raw_rows = g.rows()
    first_row = next((i for i, r in enumerate(raw_rows) if r.strip()), 0)
    return [r.rstrip() for r in raw_rows[first_row:] if r.strip()], dbg.translated(0, -first_row)


def assemble_relative(n: int = 100, *, current_bias: int = 0) -> list[str]:
    rows, _ = assemble_relative_debug(n, current_bias=current_bias)
    return rows


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--debug-")]
    n = int(args[0]) if len(args) > 0 else 100
    current_bias = int(args[1]) if len(args) > 1 else 0
    rows, dbg = assemble_relative_debug(n, current_bias=current_bias)
    for arg in sys.argv[1:]:
        if arg.startswith("--debug-json="):
            dbg.write_json(arg.split("=", 1)[1])
        if arg.startswith("--debug-html="):
            dbg.write_html(rows, arg.split("=", 1)[1])
    print("\n".join(rows))
