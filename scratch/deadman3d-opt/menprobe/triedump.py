"""Dump the decode band: fetch prologue, trie columns, lane starts.

Answers "which of the ``lane_x0 - fetch_x`` columns is doing what", which is the
only reducible part of the corridor big enough to matter.

usage: triedump.py [men-v3|taped]
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
    fx, fy, fw, fh = R["cpu:fetch"]
    tx, ty, tw, th = R["cpu:trie"]
    lane_x0 = tx + tw
    lanes = {n.split(":")[-1]: b for n, b in R.items() if n.startswith("cpu:lane:")}
    y0 = min(b[1] for b in lanes.values())
    y1 = max(b[1] for b in lanes.values())
    print(f"{store}: fetch x={fx}..{fx+fw-1} row={fy}  trie x={tx}..{tx+tw-1} "
          f"y={ty}..{ty+th-1}  lane_x0={lane_x0}")
    print(f"gap fetch_x -> lane_x0 = {lane_x0 - fx} columns\n")

    name_of = {b[1]: n for n, b in lanes.items()}
    west = fx - 2
    east = lane_x0 + 14
    hdr1 = "".join(str((x // 10) % 10) for x in range(west, east))
    hdr2 = "".join(str(x % 10) for x in range(west, east))
    print(f"{'':>7}{hdr1}")
    print(f"{'':>7}{hdr2}")
    for y in range(min(ty, y0) - 2, max(ty + th, y1) + 3):
        line = m.rows[y][west:east]
        mark = "*" if y == fy else ("h" if R.get("cpu:return:high") and y == R["cpu:return:high"][1] else " ")
        print(f"{y:>5}{mark} {line}  {name_of.get(y, '')}")

    # per-column occupancy in the gap, over the whole decode band
    print("\ncolumn census over the decode band (fetch_x .. lane_x0):")
    for x in range(fx, lane_x0 + 1):
        col = [m.rows[y][x] for y in range(ty, ty + th)]
        glyphs = sorted({c for c in col if c not in " ."})
        n = sum(1 for c in col if c not in " ")
        print(f"  x={x:>3}  cells={n:>3}  glyphs={''.join(glyphs)}")


if __name__ == "__main__":
    main()
