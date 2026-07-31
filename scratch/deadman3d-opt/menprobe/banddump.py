"""Dump the collector band across the whole CPU width, with region ownership.

For the "move the eastward routing run up onto SND's row" question: the row below
the collector is only free to merge if every cell it would occupy on SND's row is
one **no other man walks**. The four structured drops (JMPS/JMPF/BRZ/BRN) fall
*past* the collector into the slab band, so they cross SND's row as `.`, and an
eastbound glyph on one of those columns turns a southbound man east.

usage: banddump.py [men-v3|taped]
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/tmp/menprobe")
from common import setup, SLUG  # noqa: E402
from corridor import cpu_of  # noqa: E402


def main():
    d3, hires, M, prog = setup()
    store = sys.argv[1] if len(sys.argv) > 1 else "men-v3"
    m, cpu = cpu_of(M, prog, store)
    R = m.regions
    col_y = R["cpu:return:collector"][1]
    lanes = {n.split(":")[-1]: b for n, b in R.items() if n.startswith("cpu:lane:")}
    name_of = {b[1]: n for n, b in lanes.items()}
    tx, ty, tw, th = R["cpu:trie"]
    lane_x0 = tx + tw

    # every structured lane's drop column: it crosses the rows below its own lane
    drops = {}
    for n, (bx, by, bw, bh) in lanes.items():
        d = next((x for x in range(lane_x0, m.width) if m.rows[by][x] == "v"), None)
        if d is not None:
            drops[n] = (by, d)

    west, east = 8, min(m.width, 70)
    print(f"{store}: collector row {col_y}; drops "
          f"{ {k: v[1] for k, v in sorted(drops.items(), key=lambda kv: kv[1][0])} }")
    print()
    print(f"{'':>7}" + "".join(str((x // 10) % 10) for x in range(west, east)))
    print(f"{'':>7}" + "".join(str(x % 10) for x in range(west, east)))
    for y in range(col_y - 4, min(col_y + 8, m.height)):
        tag = name_of.get(y, "COLLECTOR" if y == col_y else "")
        print(f"{y:>5}  {m.rows[y][west:east]}  {tag}")

    # which columns on the row above the collector are crossed by a drop from above
    above = col_y - 1
    crossing = sorted(d for n, (r, d) in drops.items() if r < above)
    print(f"\nrow {above} is crossed from above by drop columns: {crossing}")
    print(f"row {above} cells at those columns: "
          f"{ {c: m.rows[above][c] for c in crossing} }")
    for y in range(col_y + 1, min(col_y + 4, m.height)):
        run = [x for x in range(west, east) if m.rows[y][x] not in " "]
        if run:
            print(f"row {y}: occupied x={min(run)}..{max(run)}  "
                  f"first-glyph x={next((x for x in run if m.rows[y][x] != '.'), None)}")


if __name__ == "__main__":
    main()
