"""Anatomy of the slab band from a saved heat pickle.

The ``_region_of`` table says *which box*; this says *which glyph*, which is the
only reading that tells you whether a lever exists. A word discarded is
``heat - wait`` on an ``r``; a cell walked is ``heat`` on anything else.

usage: slab_anat.py /tmp/slab/heat-men-21.pkl
"""
import collections
import pickle
import sys

INSTR = 880_332


def main():
    with open(sys.argv[1], "rb") as fh:
        d = pickle.load(fh)
    heat, wait, S, T = d["heat"], d["wait"], d["samples"], d["last"]
    rows, regions = d["rows"], d["regions"]
    tick = T / S  # ticks per sample-unit
    print(f"{sys.argv[1]}  {d['size'][0]}x{d['size'][1]}  last={T:,} "
          f"passed={d['passed']} fatal={d['fatal']}  t/instr={T/INSTR:.3f}")
    print(f"  samples={S:,} stride={d['stride']}  one sample = {tick:.4f} ticks")

    def glyph(x, y):
        return rows[y][x] if y < len(rows) and x < len(rows[y]) else " "

    band = {n: b for n, b in regions.items()
            if n.split(":")[1:2] and n.split(":")[1] in
            ("slab", "discard", "riser", "entry")}
    print("\n  region boxes (x, y, w, h):")
    for n, b in sorted(band.items()):
        print(f"   {n:26s} {b}")

    # ---- pool total, by _region_of ---------------------------------------
    boxes = sorted(((w * h, n, x, y, w, h) for n, (x, y, w, h) in regions.items()),
                   key=lambda t: t[0])

    def owner(cx, cy):
        for _a, n, x, y, w, h in boxes:
            if x <= cx < x + w and y <= cy < y + h:
                return n
        return None

    pool_h = pool_w = 0
    per = collections.defaultdict(lambda: [0, 0])
    glyphs = collections.defaultdict(lambda: collections.Counter())
    for (cx, cy), v in heat.items():
        n = owner(cx, cy)
        if n in band:
            pool_h += v
            pool_w += wait.get((cx, cy), 0)
            per[n][0] += v
            per[n][1] += wait.get((cx, cy), 0)
            glyphs[n.split(":")[-1]][glyph(cx, cy)] += v
    print(f"\n  SLAB BAND POOL: {100*pool_h/S:.2f}% of run, "
          f"{100*pool_w/S:.2f}% blocked, {pool_h/S*T/INSTR:.3f} t/instr")
    print("  region                          %run   %blocked   t/instr   ticks")
    for n, (h, w) in sorted(per.items(), key=lambda kv: -kv[1][0]):
        print(f"   {n:28s} {100*h/S:7.2f} {100*w/S:9.2f} {h/S*T/INSTR:9.3f} "
              f"{h*tick:>12,.0f}")

    # ---- by glyph, per slab ----------------------------------------------
    print("\n  by slab (all four boxes merged), glyph -> ticks:")
    for m in sorted(glyphs):
        c = glyphs[m]
        tot = sum(c.values())
        top = ", ".join(f"{g!r}:{v*tick:,.0f}" for g, v in c.most_common(8))
        print(f"   {m:6s} {tot*tick:>12,.0f}  {top}")

    # ---- the reads: words discarded --------------------------------------
    print("\n  discard `r` cells — words = heat - wait:")
    tot_words = 0
    for n, (x, y, w, h) in sorted(band.items()):
        rs = [(cx, cy) for cy in range(y, y + h) for cx in range(x, x + w)
              if glyph(cx, cy) == "r"]
        if not rs:
            continue
        hh = sum(heat.get(c, 0) for c in rs)
        ww = sum(wait.get(c, 0) for c in rs)
        words = (hh - ww) * tick
        tot_words += words
        print(f"   {n:26s} {len(rs):2d} r  heat {hh*tick:>11,.0f}  "
              f"wait {ww*tick:>10,.0f}  words {words:>11,.0f}  "
              f"blocked {100*ww/max(hh,1):5.1f}%")
    print(f"   total words {tot_words:,.0f}")


if __name__ == "__main__":
    main()
