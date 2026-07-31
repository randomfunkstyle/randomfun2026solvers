"""What is one ``lane_x0`` column actually worth, measured?

The fallback's whole value rests on "each column removed from ``lane_x0`` is
worth 2 t/instr". That is a model. ``TIGHT_TRIE_COLS`` is the one lever that
moves ``lane_x0`` by a known amount (12 <-> 4+2k = 14), so switching it off
measures d(ticks)/d(column) directly on today's geometry.

usage: colcost.py [store]
"""
import sys
import time

from common import setup, tour, run, SLUG

INSTR = 880_332


def main():
    d3, hires, M, prog = setup()
    store = sys.argv[1] if len(sys.argv) > 1 else "men-v3"
    inp, frames = tour(hires, 21)
    key = (SLUG, store)

    out = {}
    for tag, tight in (("tight (shipped)", True), ("wide (TIGHT_TRIE_COLS off)", False)):
        if tight:
            M.TIGHT_TRIE_COLS.add(key)
        else:
            M.TIGHT_TRIE_COLS.discard(key)
        t = time.time()
        m = M.build_for(SLUG, program=prog, store=store)
        lx0 = min(x for n, (x, y, w, h) in m.regions.items()
                  if n.startswith("cpu:lane:"))
        fx = m.regions["cpu:fetch"][0]
        print(f"\n{tag}: {m.width}x{m.height} lane_x0={lx0} (gap {lx0-fx}) "
              f"built in {time.time()-t:.0f}s", flush=True)
        res = run(m, inp, frames, tag)
        out[tag] = (lx0, res.frame_ticks[-1], res.passed, res.fatal)
    M.TIGHT_TRIE_COLS.add(key)

    (l1, t1, _, _), (l2, t2, _, _) = out["tight (shipped)"], out["wide (TIGHT_TRIE_COLS off)"]
    dcol = l2 - l1
    print(f"\n  lane_x0 {l1} -> {l2}  ({dcol:+d} columns)")
    print(f"  ticks   {t1:,} -> {t2:,}  ({t2-t1:+,}, {100*(t2-t1)/t1:+.3f}%)")
    if dcol:
        print(f"  => {(t2-t1)/dcol/INSTR:+.3f} t/instr per column "
              f"({(t2-t1)/dcol:+,.0f} ticks per column)")


if __name__ == "__main__":
    main()
