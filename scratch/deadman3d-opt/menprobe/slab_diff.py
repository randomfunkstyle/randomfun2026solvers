"""Region-by-region delta between two heat pickles, in t/instr.

The point of the slab band is that its levers are not local: a deeper drain
block pushes the risers, the seek tail and the machine's whole south end down
with it. Only a full-machine diff shows whether a box's saving survived.

usage: slab_diff.py <before.pkl> <after.pkl>
"""
import pickle
import sys

INSTR = 880_332


def own(d):
    heat, wait, regions = d["heat"], d["wait"], d["regions"]
    boxes = sorted(((w * h, n, x, y, w, h) for n, (x, y, w, h) in regions.items()),
                   key=lambda t: t[0])
    out = {}
    for (cx, cy), v in heat.items():
        for _a, n, x, y, w, h in boxes:
            if x <= cx < x + w and y <= cy < y + h:
                hh, ww = out.get(n, (0, 0))
                out[n] = (hh + v, ww + wait.get((cx, cy), 0))
                break
    return {n: (h * d["last"] / d["samples"], w * d["last"] / d["samples"])
            for n, (h, w) in out.items()}


def main():
    a = pickle.load(open(sys.argv[1], "rb"))
    b = pickle.load(open(sys.argv[2], "rb"))
    oa, ob = own(a), own(b)
    print(f"  before {a['last']:,} ({a['size'][0]}x{a['size'][1]})  "
          f"after {b['last']:,} ({b['size'][0]}x{b['size'][1]})  "
          f"delta {b['last']-a['last']:+,} "
          f"({100*(b['last']-a['last'])/a['last']:+.3f}%)")
    rows = []
    for n in set(oa) | set(ob):
        ha = oa.get(n, (0, 0))[0]
        hb = ob.get(n, (0, 0))[0]
        if abs(hb - ha) > 5_000:
            rows.append((n, ha, hb, hb - ha))
    print("  region                          before      after       delta   d t/instr")
    for n, ha, hb, dd in sorted(rows, key=lambda r: r[3]):
        print(f"   {n:28s} {ha:>11,.0f} {hb:>11,.0f} {dd:>+11,.0f} "
              f"{dd/INSTR:>+9.3f}")
    tot = sum(r[3] for r in rows)
    print(f"   {'(sum of moved regions)':28s} {'':>11} {'':>11} {tot:>+11,.0f} "
          f"{tot/INSTR:>+9.3f}")


if __name__ == "__main__":
    main()
