"""Per-cell heat over the slab band, drawn as the grid itself.

The region table says which box; this says which *cell*, which is the only way
to separate a riser from the fan-out its bounding box swallowed.

usage: slab_cells.py <pkl> <x0> <y0> <w> <h>
"""
import pickle
import sys


def main():
    with open(sys.argv[1], "rb") as fh:
        d = pickle.load(fh)
    x0, y0, w, h = (int(v) for v in sys.argv[2:6])
    heat, wait, S, T = d["heat"], d["wait"], d["samples"], d["last"]
    rows = d["rows"]
    tick = T / S
    print(f"  x {x0}..{x0+w-1}, y {y0}..{y0+h-1}   1 sample = {tick:.2f} ticks")
    print("      " + "".join(str(x % 10) for x in range(x0, x0 + w)))
    for y in range(y0, y0 + h):
        line = "".join(rows[y][x] if x < len(rows[y]) else " "
                       for x in range(x0, x0 + w))
        print(f"  {y:4d} {line}")
    print("\n  cell     glyph      ticks       blocked")
    tot = 0
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            v = heat.get((x, y), 0)
            if not v:
                continue
            tot += v
            g = rows[y][x] if x < len(rows[y]) else " "
            print(f"  ({x:3d},{y:3d}) {g!r:>6} {v*tick:>12,.0f} "
                  f"{wait.get((x,y),0)*tick:>12,.0f}")
    print(f"  total {tot*tick:,.0f} ticks = {100*tot/S:.2f}% of run")


if __name__ == "__main__":
    main()
