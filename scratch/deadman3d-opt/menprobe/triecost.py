"""Split ``cpu:trie``'s 17.73 t/instr into its horizontal and vertical halves.

The trie box is 7 columns wide and as tall as the lane band, so its heat is two
different things added together: the **eastward traverse** (one tick per column,
on every instruction -- the ``lane_x0`` term) and the **vertical descent** to the
lane's own row (the band-depth term). Only the first is what ``TIGHT_TRIE_COLS``
and ``lane_x0`` move; the second is opcode row order, which is dead.

Reads the pickle prof3d.py wrote.

usage: triecost.py [heat.pkl]
"""
from __future__ import annotations

import pickle
import sys

HEAT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/heat-base-21.pkl"
INSTR = 880_332


def main():
    with open(HEAT, "rb") as fh:
        pk = pickle.load(fh)
    heat, S, T = pk["heat"], pk["samples"], pk["last"]
    R = pk["regions"]
    tick = T / S
    tx, ty, tw, th = R["cpu:trie"]
    fx, fy, fw, fh = R["cpu:fetch"]
    hi = R.get("cpu:return:high")
    print(f"trie box x={tx}..{tx+tw-1} y={ty}..{ty+th-1}   fetch row {fy}   "
          f"hi_row {hi[1] if hi else None}")

    # _region_of gives a cell to the smallest box containing it, so only cells the
    # trie actually owns count. Rebuild that ownership here.
    boxes = sorted(((w * h, n, x, y, w, h) for n, (x, y, w, h) in R.items()), key=lambda t: t[0])

    def owner(cx, cy):
        for _a, n, x, y, w, h in boxes:
            if x <= cx < x + w and y <= cy < y + h:
                return n
        return None

    per_col = {}
    per_row = {}
    tot = 0.0
    for x in range(tx, tx + tw):
        for y in range(ty, ty + th):
            if owner(x, y) != "cpu:trie":
                continue
            v = heat.get((x, y), 0) * tick
            per_col[x] = per_col.get(x, 0.0) + v
            per_row[y] = per_row.get(y, 0.0) + v
            tot += v
    print(f"\ncpu:trie total {tot/INSTR:.3f} t/instr ({100*tot/T:.2f}% of run)")
    print("\nper column (the lane_x0 term -- one traverse tick each way per instr):")
    for x in sorted(per_col):
        print(f"  x={x:>3}  {per_col[x]/INSTR:7.3f} t/instr")
    print("\nper row (the band-depth term):")
    for y in sorted(per_row):
        if per_row[y]:
            print(f"  y={y:>3} (rel {y-fy:>+4})  {per_row[y]/INSTR:7.3f} t/instr")


if __name__ == "__main__":
    main()
