#!/usr/bin/env python3
"""Assemble v1 by proposing routes and letting the grid reject them.

Currently draws MAIN, the three register rings and both storage rings: ten
pipes, all anchored to two rooms, ring A at 405 cells and ring B at 387
against the 257 each needs. Still to add: `prod`, `cmd`, `in`, the ADDER,
ring C's relay and the I/O rooms -- so it does not compute anything yet.

Every crossing rule in this build was found by reading a collision, not by deriving
it -- ring-vs-ring planarity, the port-wall question, and finally a ring crossing
itself. Deriving them one at a time and re-placing by hand took four attempts each.
So this proposes a wide candidate set per pipe and takes the first that `_Grid.put`
accepts. Pipes are routed longest-constrained first, because the two storage rings
carve up the space everything else has to fit around.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

from . import matmul_main as mm
from .lm1.machine import MachineError, _Grid

RELAY = {(1, 1): "@", (2, 1): "r", (3, 1): "v",
         (1, 2): "^", (2, 2): "s", (3, 2): "<"}
MX, MY = 0, 40                     # MAIN's north-west wall corner


def fit(g, options, name):
    fails = []
    for pts in options:
        probe = copy.deepcopy(g)
        try:
            n = probe.draw_pipe(pts)
        except MachineError as e:
            fails.append(str(e))
            continue
        g.c, g.drawn = probe.c, probe.drawn
        return n
    raise MachineError(f"{name}: {len(options)} candidates, e.g. {fails[:3]}")


def leaves_south(room_x, wall_y, tx, ty, jogs=()):
    """Routes leaving a room's south wall: the first leg must point *away* from it.

    A pipe's opening arrowhead has to have the wall as its backward cell, so a pipe
    starting below a south wall must run south first. Starting it eastward instead
    makes the loader report no source room at all — `analyze` gives `src: -1` — and
    the grid still parses, which is why this needs checking rather than assuming.
    """
    out = []
    for col in (room_x + 1, room_x + 2, room_x + 3):
        out.append([(col, wall_y + 1), (col, ty), (tx, ty)])
    for col in (room_x + 1, room_x + 2, room_x + 3):
        for m in jogs:
            out.append([(col, wall_y + 1), (col, m), (tx + 1, m), (tx + 1, ty), (tx, ty)])
    return out


def serp(sx, sy, rise, foot, legs, step, lo, hi, endx, endy):
    """East to `rise`, vertical to the band's foot, then a boustrophedon.

    `step` is -1 for a band above MAIN and +1 for one below; each leg alternates
    side, so the polyline stays rectilinear and every turn is a real bend.
    """
    pts = [(sx, sy), (rise, sy), (rise, foot), (lo, foot)]
    west = True
    row = foot
    for _ in range(legs):
        row += step
        pts.append((lo if west else hi, row))     # step onto the next row
        pts.append((hi if west else lo, row))     # traverse it
        west = not west
    pts.append((pts[-1][0], endy))
    pts.append((endx, endy))
    return pts


def build():
    g = _Grid()
    main, _ = mm.main_room()
    live = {k: v for k, v in main.cell.items() if v != " "}
    w = max(x for x, _ in live) + 1
    east = MX + w + 1
    g.room(MX, MY, east, MY + mm.IH + 1)
    g.blit(MX, MY, live)
    caps = {}

    def room(x, y):
        g.room(x, y, x + 4, y + 3)
        g.blit(x, y, RELAY)

    reg_x = east + 3
    for nm, ret, fwd in (("rn", mm.RN_RET, mm.RN_FWD), ("rm", mm.RM_RET, mm.RM_FWD),
                         ("rk", mm.RK_RET, mm.RK_FWD)):
        room(reg_x, MY + ret - 1)
        caps[nm + "_fwd"] = g.draw_pipe([(east + 1, MY + fwd), (reg_x - 1, MY + fwd)])
        caps[nm + "_ret"] = g.draw_pipe([(reg_x - 1, MY + ret), (east + 1, MY + ret)])

    lo, hi = 1, east - 1
    bot = MY + mm.IH + 1

    # A ring's relay has to sit *east of its own rise column*. The forward leg runs
    # along the foot of its band from column 1 out to the rise, and the return has to
    # get from the relay down past that row — so a relay west of the rise is fenced
    # in by its own pipe. Rise and relay are therefore chosen together, with rollback.
    def ring(name, fwd_row, ret_row, foot, step, endy, wall_side):
        for rise in range(reg_x + 12, reg_x + 30, 2):
            for gap in (4, 6, 8):
                probe = copy.deepcopy(g)
                rx = rise + gap
                try:
                    probe.room(rx, endy - 1, rx + 4, endy + 2)
                    probe.blit(rx, endy - 1, RELAY)
                    f = probe.draw_pipe(serp(east + 1, MY + fwd_row, rise, foot,
                                             5, step, lo, hi, rx - 1, endy))
                    wall = endy + 2 if wall_side > 0 else endy - 1
                    r = None
                    for col in (rx + 1, rx + 2, rx + 3):
                        try:
                            r = probe.draw_pipe([(col, wall + wall_side),
                                                 (col, MY + ret_row),
                                                 (east + 1, MY + ret_row)])
                            break
                        except MachineError:
                            probe = copy.deepcopy(probe)
                            continue
                    if r is None:
                        continue
                except MachineError:
                    continue
                g.c, g.drawn = probe.c, probe.drawn
                return f, r
        raise MachineError(f"ring {name}: no rise/relay pair fits")

    caps["a_fwd"], caps["a_ret"] = ring("A", mm.A_FWD, mm.A_RET, 8, -1, 2, +1)
    caps["b_fwd"], caps["b_ret"] = ring("B", mm.B_FWD, mm.B_RET, bot + 7, +1,
                                        bot + 13, -1)
    return g, caps, east, reg_x


if __name__ == "__main__":
    try:
        g, caps, east, reg_x = build()
        rows = g.rows()
        print(f"grid {max(len(r) for r in rows)}x{len(rows)}")
        print("ring A:", caps["a_fwd"] + caps["a_ret"], " ring B:", caps["b_fwd"] + caps["b_ret"])
        p = Path(__file__).with_name("asm3.man"); p.write_text("\n".join(rows) + "\n")
        o = subprocess.run(["node", "lm.mjs", "analyze", str(p), "--json"],
                           cwd=REPO / "littleman", capture_output=True, text=True)
        if o.returncode:
            print("LOAD FAILED:", o.stderr.strip()[:200])
        else:
            i = json.loads(o.stdout)
            print(f"loads: {len(i['rooms'])} rooms, {len(i['pipes'])} pipes")
    except MachineError as e:
        print("STUCK:", str(e)[:400])
