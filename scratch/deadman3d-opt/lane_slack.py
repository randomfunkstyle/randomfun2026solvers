#!/usr/bin/env python3
"""How far east of its last *operation* does each lane actually turn?

The lane band's return rule (``machine.py`` "drop columns") floors every drop at
the running suffix maximum of ``lane_end`` — the lane's **last cell**.  But a
lane's cells include the ``.`` run ``_flat_lane`` lays down while pushing a band's
first glyph out to its column, and a ``.`` has no direction: a southbound man
crosses it unchanged, exactly as the eastbound lane man does.

So this script asks the CPU room's own cells: for each lane row, where is the
last operation, where does the emitted ``v``/``^`` sit, and what is the westmost
column whose whole descent to the collector is free of *operations*?

Geometry only, no IWAD tables in the output.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

SLUG = "deadman-3d_hires"
KNOBS = dict(dda_acc_reload=False, dda_diff=True, dda_stepy_split=True, lap_via_jump=True)


def cpu_of():
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    hires.install_wad(Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD")
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    src = d3.deadman3d_source(d3.GEOM128, **KNOBS)
    program = assemble(src, name=SLUG)

    grabbed = {}
    orig = M.build_cpu

    def spy(*a, **k):
        cpu = orig(*a, **k)
        grabbed["cpu"] = cpu
        return cpu

    M.build_cpu = spy
    try:
        m = M.build_for(SLUG, program=program, store="taped")
    finally:
        M.build_cpu = orig
    return m, grabbed["cpu"]


def main(argv: list[str]) -> int:
    m, cpu = cpu_of()
    cells = cpu.cells
    print(f"grid {m.width}x{m.height}; cpu {cpu.width}x{cpu.height}; centre={cpu.centre}")
    for name, box in sorted(cpu.regions.items()):
        if "lane" not in name:
            print(f"  region {name}: {box}")

    def row_text(y, x1):
        return "".join(cells.get((x, y), " ") for x in range(0, x1))

    w = cpu.width
    # collector row: the row that is one long westbound `<` run
    collector = max(
        y
        for y in range(cpu.height)
        if sum(1 for x in range(w) if cells.get((x, y)) == "<") > w // 3
    )
    print(f"collector row = {collector}")
    print()
    for y in range(0, collector + 2):
        print(f"{y:>3} | {row_text(y, w).rstrip()}")
    print()

    print(f"{'row':>4} {'lastop':>6} {'turn':>5} {'walk':>5} {'free':>5} {'save':>5}")
    total = 0
    for y in range(0, collector):
        line = [cells.get((x, y)) for x in range(w)]
        turn = next((x for x in range(w) if line[x] in ("v", "^")), None)
        if turn is None:
            continue
        ops = [x for x in range(turn) if line[x] not in (None, ".")]
        if not ops:
            continue
        last = max(ops)
        # westmost column c > last whose descent y+1..collector-1 hits no operation
        free = None
        for c in range(last + 1, w):
            if all(cells.get((c, yy), ".") in (".", None) for yy in range(y + 1, collector)):
                free = c
                break
        walk = turn - last
        save = turn - free if free is not None else 0
        total += save
        print(f"{y:>4} {last:>6} {turn:>5} {walk:>5} {free!s:>5} {save:>5}")
    print(f"total columns recoverable if only operations blocked: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
