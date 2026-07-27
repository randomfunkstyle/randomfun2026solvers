#!/usr/bin/env python3
"""Assemble the whole bespoke matmul machine: MAIN, the ADDER, five rings, I/O.

What unblocked this over `matmul_asm2` is not a better router, it is three
placement facts, each of which was a failed route first:

* **an up-pipe and a down-pipe never interact.** A pipe leaving MAIN's east wall
  turns at its own column and then runs *away* from its row, so a down-pipe's
  vertical can only ever cross a row **below** its own and an up-pipe's only a row
  **above**. With every up row above every down row (`matmul_main`'s map already
  does this) the two families are independent and only the order *within* each
  family constrains the turn columns: of two down-pipes the upper one turns
  further east, of two up-pipes the lower one does.
* **the register relays have to hug MAIN's wall.** `matmul_asm2` pushed them nine
  columns clear so `prod` and `cmd` could descend past them; that made their six
  horizontal legs eight cells long and fenced the whole strip at six different
  rows, which is what stopped `prod`. Two-cell legs leave everything from
  `reg_x + 6` east open, and `prod` descends there.
* **`cmd` is the ADDER's top port** (`matmul_adder3`). `cmd` leaves MAIN below
  `prod`, so it descends *west* of it; its westward approach to the ADDER then
  has to arrive above where `prod`'s vertical stops.

Also: a bend whose backward cell lands on another room's wall parses as a **second
pipe out of that room** — same destination cell, and `r` picks it by reading order,
so the real pipe is never read. It loads, it analyses clean, and it deadlocks. The
pipe count is asserted against the expected count for exactly that reason.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

from . import matmul_adder3 as a3
from . import matmul_main as mm
from .lm1.machine import MachineError, _Grid
from .manroute2 import route

__all__ = ["PIPES", "build", "write"]

REPO = Path(__file__).resolve().parents[3]

#: A relay closes one ring: `r` a value off the incoming pipe, `s` it onto the
#: outgoing one. The cycle has to be **eight** cells, not six: `@` is a nop, so a
#: man who walks back onto his spawn cell keeps his heading and hits the wall
#: beyond it. Six cells is a 3x2 perimeter with all four corners turning, which
#: leaves nowhere for `@` — so the block is 4x2 and `@` rides a straight run.
RELAY = {(1, 1): ">", (2, 1): "@", (3, 1): "r", (4, 1): "v",
         (1, 2): "^", (2, 2): " ", (3, 2): "s", (4, 2): "<"}
RW, RH = 6, 4                      # relay room footprint
RC_GAP = 16                        # ring C's relay, pushed east for capacity

MY = 11                            # MAIN's north-west wall corner
AX = 2                             # the ADDER's west wall, tucked under MAIN
AY = MY + mm.IH + 3                # ... two rows clear of MAIN's south wall

PIPES = ("in", "a_fwd", "a_ret", "b_ret", "b_fwd", "prod", "cmd",
         "rn_fwd", "rn_ret", "rm_fwd", "rm_ret", "rk_fwd", "rk_ret",
         "cout", "cin", "out")


def serp(sx, sy, rise, foot, legs, step, lo, hi, endx, endy):
    """East to `rise`, vertical to the band's foot, then a boustrophedon.

    `step` is -1 for a band above MAIN and +1 for one below.
    """
    pts = [(sx, sy), (rise, sy), (rise, foot), (lo, foot)]
    west, row = True, foot
    for _ in range(legs):
        row += step
        pts.append((lo if west else hi, row))
        pts.append((hi if west else lo, row))
        west = not west
    pts.append((pts[-1][0], endy))
    pts.append((endx, endy))
    return pts


def build(*, legs=5, band_foot=7, verbose=False):
    g = _Grid()
    main, _ = mm.main_room()
    live = {k: v for k, v in main.cell.items() if v != " "}
    # MAIN's width is whatever its serpentine used, so every column downstream of it
    # is derived, not chosen.
    east = max(x for x, _ in main.cell) + 1
    reg_x = east + 3                   # register relays, two-cell legs
    c_in = c_cmd = east + 2            # the two pipes with nothing to clear
    c_prod = reg_x + RW                # ... east of the register rooms
    ae = AX + a3.IW + 1                # the ADDER's east wall
    assert ae + 1 < c_cmd, f"ADDER east wall {ae} fouls cmd's descent at {c_cmd}"
    g.room(0, MY, east, MY + mm.IH + 1)
    g.blit(0, MY, live)
    caps: dict[str, int] = {}

    def relay(x, y):
        g.room(x, y, x + RW - 1, y + RH - 1)
        g.blit(x, y, RELAY)

    # ── the three register rings: two-cell hops to relays against MAIN's wall ──
    for nm, ret, fwd in (("rn", mm.RN_RET, mm.RN_FWD), ("rm", mm.RM_RET, mm.RM_FWD),
                         ("rk", mm.RK_RET, mm.RK_FWD)):
        relay(reg_x, MY + ret - 1)
        caps[nm + "_fwd"] = g.draw_pipe([(east + 1, MY + fwd), (reg_x - 1, MY + fwd)])
        caps[nm + "_ret"] = g.draw_pipe([(reg_x - 1, MY + ret), (east + 1, MY + ret)])

    # ── the ADDER, and the two feeds that pinned its orientation ──────────────
    g.room(AX, AY, ae, AY + a3.IH + 1)
    g.blit(AX, AY, a3.cells())
    caps["prod"] = g.draw_pipe([(east + 1, MY + mm.PROD), (c_prod, MY + mm.PROD),
                                (c_prod, AY + a3.PROD), (ae + 1, AY + a3.PROD)])
    # `cmd` carries **3N words and MAIN sends them all before it fills a ring**, so its
    # capacity has to be at least 48 or MAIN blocks on `s(cmd)` while the ADDER waits
    # for products that cannot come — a deadlock that only shows up at N = 16. A
    # straight L-route holds 38, so it serpentines through the three free rows between
    # MAIN's south wall and `prod`'s westward leg.
    band = MY + mm.IH + 2
    caps["cmd"] = g.draw_pipe([(east + 1, MY + mm.CMD), (c_cmd, MY + mm.CMD),
                               (c_cmd, band), (ae + 3, band), (ae + 3, band + 1),
                               (c_cmd - 1, band + 1), (c_cmd - 1, AY + a3.CMD),
                               (ae + 1, AY + a3.CMD)])
    assert caps["cmd"] >= 50, f"cmd holds {caps['cmd']}, needs 48 at N=16"

    # ── ring C and the O room, in the pocket east of the ADDER ────────────────
    # `cmd` and `prod` cross that pocket at their own two rows and nowhere else,
    # so everything here sits below `prod`'s westward leg.
    # Ring C's capacity is `cout + cin`, and it has to hold **K** accumulators plus a
    # free source cell — up to 17. Pushing the relay well east is free length: the
    # pocket is wide and nothing else wants those columns.
    rcx, rcy = ae + RC_GAP, AY + a3.CIN
    relay(rcx, rcy)                                # interior rows rcy+1, rcy+2
    caps["cout"] = g.draw_pipe([(ae + 1, AY + a3.COUT), (rcx - 1, AY + a3.COUT)])
    caps["cin"] = g.draw_pipe([(rcx - 1, rcy + 1), (ae + 2, rcy + 1),
                               (ae + 2, AY + a3.CIN), (ae + 1, AY + a3.CIN)])
    ring_c = caps["cout"] + caps["cin"]
    assert ring_c >= 20, f"ring C holds {ring_c}, needs 17 for K=16"
    ox, oy = ae + 8, AY + a3.OUT + 2
    g.room(ox, oy, ox + 2, oy + 2)
    g.put(ox + 1, oy + 1, "O")
    caps["out"] = g.draw_pipe([(ae + 1, AY + a3.OUT), (ae + 2, AY + a3.OUT),
                               (ae + 2, oy + 1), (ox - 1, oy + 1)])

    # ── the I room, directly above MAIN, west of ring A's rise ────────────────
    iy = MY - 3
    g.room(c_in - 1, iy, c_in + 1, iy + 2)
    g.put(c_in, iy + 1, "I")
    caps["in"] = g.draw_pipe([(c_in, iy + 3), (c_in, MY + mm.IN), (east + 1, MY + mm.IN)])

    # ── the two storage rings ─────────────────────────────────────────────────
    lo, hi = 1, east - 1
    # Ring B's band starts one row under the O room and the relay sits one row under
    # the band's last leg — the original spacing left nine empty rows, and height is
    # the binding side of max(w, h).
    foot_b = oy + 4

    def ring(name, fwd_row, ret_row, foot, step, endy, wall_side):
        """Rise column and relay position are chosen together, with rollback.

        A ring's relay must sit **east of its own rise column**: the forward leg
        runs along the foot of the band out to the rise, so a relay west of it is
        fenced in by the ring's own pipe.
        """
        for rise in range(c_prod + 2, c_prod + 40, 1):
            for gap in (3, 4, 5, 6, 8, 10):
                probe = copy.deepcopy(g)
                rx = rise + gap
                try:
                    probe.room(rx, endy - 1, rx + RW - 1, endy + RH - 2)
                    probe.blit(rx, endy - 1, RELAY)
                    f = probe.draw_pipe(serp(east + 1, MY + fwd_row, rise, foot,
                                             legs, step, lo, hi, rx - 1, endy))
                    wall = endy + RH - 2 if wall_side > 0 else endy - 1
                    r = None
                    for col in range(rx + 1, rx + RW - 1):
                        trial = copy.deepcopy(probe)
                        try:
                            r = trial.draw_pipe([(col, wall + wall_side),
                                                 (col, MY + ret_row),
                                                 (east + 1, MY + ret_row)])
                        except MachineError:
                            continue
                        probe = trial
                        break
                    if r is None:
                        continue
                except MachineError:
                    continue
                g.c, g.drawn = probe.c, probe.drawn
                if verbose:
                    print(f"  ring {name}: rise={rise} relay={rx} fwd={f} ret={r}")
                return f, r
        raise MachineError(f"ring {name}: no rise/relay pair fits")

    caps["a_fwd"], caps["a_ret"] = ring("A", mm.A_FWD, mm.A_RET, band_foot, -1, 1, +1)
    caps["b_fwd"], caps["b_ret"] = ring("B", mm.B_FWD, mm.B_RET, foot_b, +1,
                                        foot_b + legs + 1, -1)
    return g, caps


def write(path, **kw):
    g, caps = build(**kw)
    rows = g.rows()
    Path(path).write_text("\n".join(rows) + "\n")
    return g, caps, max(len(r) for r in rows), len(rows)


if __name__ == "__main__":
    import sys

    out = Path(sys.argv[1] if len(sys.argv) > 1 else "matmul_v1.man")
    g, caps, w, h = write(out, verbose=True)
    print(f"grid {w}x{h}  footprint {max(w, h) ** 2:,}")
    print("ring A", caps["a_fwd"] + caps["a_ret"], " ring B", caps["b_fwd"] + caps["b_ret"])
    o = subprocess.run(["node", "lm.mjs", "analyze", str(out.resolve()), "--json"],
                       cwd=REPO / "littleman", capture_output=True, text=True)
    if o.returncode:
        print("LOAD FAILED:", o.stderr.strip()[:300])
        raise SystemExit(1)
    i = json.loads(o.stdout)
    orph = [k for k, p in enumerate(i["pipes"]) if p["src"] < 0 or p["dst"] < 0]
    print(f"loads: {len(i['rooms'])} rooms, {len(i['pipes'])} pipes "
          f"(expected {len(PIPES)}), orphans {orph}")
