#!/usr/bin/env python3
"""Assemble the v1 machine. **Incomplete**: MAIN and its three register rings only.

All thirteen of MAIN's ports sit on its **east** wall, which is what removes the
ring wraps: a ring is `MAIN -> relay -> MAIN` with both legs on the same wall, so
the relay sits just outside and each pipe is only as long as its capacity needs.

The three register rings are 2-cell hops to relay rooms stacked in a column beside
MAIN, each room spanning its own pair of rows (which is why the register rows are
spaced four apart). Ring A and ring B are the only long ones — 257 cells each —
and they take their length from a serpentine in the band above, where it is free.

What is here loads on the reference loader — 4 rooms, 6 pipes, every register hop
exactly 2 cells, which is the minimum a pipe may be and what the one-wall geometry
buys. Still to add: ring A, ring B, `prod`, `cmd`, `in`, the ADDER, ring C's relay
and the I/O rooms. Until all thirteen pipes exist the bindings cannot be checked —
`tools/route-check.mjs` on the partial grid resolves every `r` to the one nearest
pipe that happens to be present, which is correct behaviour and useless as a test.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

from . import matmul_main as mm
from .lm1.machine import _Grid

MAIN_X, MAIN_Y = 0, 30          # MAIN's north-west wall corner
REG_GAP = 3                     # >=3, so each register pipe is at least 2 cells


def relay_cells() -> dict[tuple[int, int], str]:
    """A 3x2 relay: read, carry, send, return. One incoming, one outgoing."""
    return {(1, 1): "@", (2, 1): "r", (3, 1): "v",
            (1, 2): "^", (2, 2): "s", (3, 2): "<"}


def build() -> tuple[str, dict[str, int]]:
    g = _Grid()
    mx, my = MAIN_X, MAIN_Y
    main, _info = mm.main_room()
    live = {k: v for k, v in main.cell.items() if v != " "}
    # Size the room to what MAIN actually uses, not to the module's scratch width:
    # the interior constant is a canvas bound, and drawing to it would make the
    # room 50 columns wider than its code.
    used_w = max(x for x, _ in live) + 1
    east = mx + used_w + 1                    # MAIN's east wall column
    reg_x = east + REG_GAP

    g.room(mx, my, east, my + mm.IH + 1)
    g.blit(mx, my, live)

    caps: dict[str, int] = {}

    # ── the three register rings: 2-cell hops to rooms stacked beside MAIN ────
    for name, ret_row, fwd_row in (("rn", mm.RN_RET, mm.RN_FWD),
                                   ("rm", mm.RM_RET, mm.RM_FWD),
                                   ("rk", mm.RK_RET, mm.RK_FWD)):
        top = my + ret_row - 1                # room spans both of its rows
        g.room(reg_x, top, reg_x + 4, top + 3)
        g.blit(reg_x, top, relay_cells())
        caps[f"{name}_fwd"] = g.draw_pipe(
            [(east + 1, my + fwd_row), (reg_x - 1, my + fwd_row)])
        caps[f"{name}_ret"] = g.draw_pipe(
            [(reg_x - 1, my + ret_row), (east + 1, my + ret_row)])

    rows = g.rows()
    return "\n".join(rows) + "\n", caps


def loads(text: str) -> tuple[bool, str]:
    path = Path(__file__).with_name("asm.man")
    path.write_text(text, encoding="utf-8")
    out = subprocess.run(["node", "lm.mjs", "analyze", str(path), "--json"],
                         cwd=REPO / "littleman", capture_output=True, text=True)
    if out.returncode:
        return False, out.stderr.strip()[:300]
    info = json.loads(out.stdout)
    return True, f"{len(info['rooms'])} rooms, {len(info['pipes'])} pipes"


if __name__ == "__main__":
    grid, caps = build()
    w = max(len(r) for r in grid.split("\n"))
    h = len(grid.rstrip("\n").split("\n"))
    print(f"partial machine {w}x{h}")
    print("pipe capacities:", caps)
    ok, msg = loads(grid)
    print("loads:" if ok else "LOAD FAILED:", msg)
