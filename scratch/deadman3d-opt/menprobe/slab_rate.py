"""Executions, words and ticks per slab — the denominators, measured.

An inherited denominator is how the seek pool got framed around the wrong
number today, so every rate here is derived from cells this script finds in the
saved grid rather than from a frequency table.

* a branch's executions   = heat on its ``X``
* a taken branch / a jump = heat on the discard's entry glyph
* words discarded         = sum over the discard's ``r`` of ``heat - wait``

usage: slab_rate.py <pkl>
"""
import pickle
import sys

INSTR = 880_332


def main():
    with open(sys.argv[1], "rb") as fh:
        d = pickle.load(fh)
    heat, wait, S, T = d["heat"], d["wait"], d["samples"], d["last"]
    rows, regions = d["rows"], d["regions"]
    tick = T / S
    print(f"{sys.argv[1]}  last={T:,}  1 sample = {tick:.2f} ticks")

    def g(x, y):
        return rows[y][x] if y < len(rows) and x < len(rows[y]) else " "

    def box_cells(name, want):
        x, y, w, h = regions[name]
        return [(cx, cy) for cy in range(y, y + h) for cx in range(x, x + w)
                if g(cx, cy) in want]

    print("\n  slab   execs      taken/jumps    words     ticks   t/exec t/word")
    for m in ("JMPS", "JMPF", "BRZ", "BRN"):
        slab = f"cpu:slab:{m}"
        if slab not in regions:
            continue
        xs = box_cells(slab, "X")
        execs = sum(heat.get(c, 0) for c in xs) * tick if xs else 0
        disc = f"cpu:discard:{m}"
        src = disc if disc in regions else slab
        rs = box_cells(src, "r")
        words = sum(heat.get(c, 0) - wait.get(c, 0) for c in rs) * tick
        # the discard's own entry: an `a` (counted loop) or the `x` of the
        # ladder's first stage / the `]` an `even` ladder puts there instead.
        ent = box_cells(src, "a") or box_cells(src, "]")
        laps = sum(heat.get(c, 0) for c in ent) * tick if ent else 0
        # every cell inside any of the four boxes for this mnemonic
        tot = 0
        for pre in ("slab", "discard", "riser", "entry"):
            n = f"cpu:{pre}:{m}"
            if n not in regions:
                continue
            x, y, w, h = regions[n]
            tot += sum(v for (cx, cy), v in heat.items()
                       if x <= cx < x + w and y <= cy < y + h)
        tot *= tick
        print(f"  {m:5s} {execs:>10,.0f} {laps:>13,.0f} {words:>10,.0f} "
              f"{tot:>10,.0f} {tot/max(execs,1):>7.1f} "
              f"{tot/max(words,1):>6.2f}")
        if execs:
            print(f"        words/exec {words/execs:6.2f}   "
                  f"words/discard-entry {words/max(laps,1):6.2f}   "
                  f"{100*execs/INSTR:.2f}% of instructions")


if __name__ == "__main__":
    main()
