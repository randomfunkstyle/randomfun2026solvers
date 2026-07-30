#!/usr/bin/env python3
"""Where else in the box the 365x267 packed wall could physically sit.

The wall is hung *below everything already drawn* (``machine._coprocessor``'s
``by = max(by, max(y) + 3)``), so the screens' row is the CPU band's height plus
the wall's own internal stack.  This asks the first half of that: how much of the
box above the wall is actually occupied, per row and per column band.

    python scratch/deadman3d-opt/hires_slot.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"


def main() -> int:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)
    m = M.build_for(SLUG, program=prog, store="taped")
    rows = list(m.rows)
    h, w = len(rows), max(len(r) for r in rows)
    pad = [r.ljust(w) for r in rows]
    print(f"box {m.width}x{m.height}; grid {w}x{h}")

    WALL_Y, WALL_X, WALL_W, WALL_H = 197, 1, 365, 267

    def free(y: int, x0: int, x1: int) -> bool:
        return all(pad[y][x] == " " for x in range(x0, min(x1 + 1, w)))

    # 1. rows above the wall: is the wall's own column band free there?
    blocked = [y for y in range(WALL_Y) if not free(y, WALL_X, WALL_X + WALL_W - 1)]
    print(f"rows 0..{WALL_Y - 1} with anything in x{WALL_X}..{WALL_X + WALL_W - 1}: "
          f"{len(blocked)} of {WALL_Y}; lowest={max(blocked) if blocked else None}")

    # 2. per-row extent above the wall, coarsely
    print("row extents above the wall (every 8th row): y: minx..maxx")
    for y in range(0, WALL_Y, 8):
        occ = [x for x in range(w) if pad[y][x] != " "]
        print(f"   y{y:>4}: {occ[0] if occ else '-':>5}..{occ[-1] if occ else '-':<5} "
              f"({len(occ)} cells)")

    # 3. for each candidate x offset, the highest y the 365x267 wall could start at
    print("candidate slots: x offset -> highest legal wall_y (needs 365x267 clear, "
          "and must stay in the box)")
    for x0 in range(0, m.width - WALL_W + 1, 32):
        ok = None
        for y0 in range(0, WALL_Y + 1):
            if all(free(y, x0, x0 + WALL_W - 1) for y in range(y0, min(h, y0 + WALL_H))):
                ok = y0
                break
        print(f"   x{x0:>4}: {ok}")

    # 4. the wall's own column band, below the CPU: how far east is free?
    print("free width east of the wall, at the wall's rows:")
    for y in range(WALL_Y, h, 24):
        occ = [x for x in range(w) if pad[y][x] != " "]
        print(f"   y{y:>4}: maxx={occ[-1] if occ else '-'}")
    east = min((max((x for x in range(w) if pad[y][x] != " "), default=0)
                for y in range(WALL_Y, h)), default=0)
    print(f"   min over wall rows of maxx = {east}")
    allmax = max(max((x for x in range(w) if pad[y][x] != " "), default=0)
                 for y in range(WALL_Y, h))
    print(f"   max over wall rows of maxx = {allmax} (box width {m.width})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
