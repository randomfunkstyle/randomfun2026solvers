"""Profile a tier and break the slab band down cell by cell.

``_region_of``'s attribution (smallest box wins) for the pool table, plus the
raw ``r``-cell word counts (``heat - wait``) and the riser columns' heat, which
is what actually decides whether the drain or the collector is worth attacking.

usage: slab_prof.py <store> [rounds] [tag]
"""
import pickle
import sys
import time

from common import setup, tour, SLUG
from randomfun2026solvers.fast_littleman import FastLittleman

INSTR = 880_332


def attribute(regions, heat, wait):
    boxes = sorted(
        ((w * h, n, x, y, w, h) for n, (x, y, w, h) in regions.items()),
        key=lambda t: t[0],
    )
    out = {}
    for (cx, cy), v in heat.items():
        for _a, n, x, y, w, h in boxes:
            if x <= cx < x + w and y <= cy < y + h:
                hh, ww = out.get(n, (0, 0))
                out[n] = (hh + v, ww + wait.get((cx, cy), 0))
                break
    return out


def main():
    store = sys.argv[1]
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 21
    tag = sys.argv[3] if len(sys.argv) > 3 else store
    d3, hires, M, prog = setup()
    inp, frames = tour(hires, rounds)
    t = time.time()
    m = M.build_for(SLUG, program=prog, store=store)
    print(f"built {m.width}x{m.height} ({time.time()-t:.0f}s)", flush=True)
    t = time.time()
    res = FastLittleman("\n".join(m.rows)).run(
        inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000,
        profile=True, profile_stride=17)
    print(f"  ran in {time.time()-t:.0f}s", flush=True)
    p = res.profile
    with open(f"/tmp/slab/heat-{tag}-{rounds}.pkl", "wb") as fh:
        pickle.dump({"regions": m.regions, "heat": dict(p.heat),
                     "wait": dict(p.wait), "samples": p.samples,
                     "stride": p.stride, "step": res.step,
                     "last": res.frame_ticks[-1], "rows": m.rows,
                     "passed": res.passed, "fatal": res.fatal,
                     "size": (m.width, m.height)}, fh)
    S = p.samples
    T = res.frame_ticks[-1]
    print(f"  {tag} step={res.step:,} last={T:,} passed={res.passed} "
          f"fatal={res.fatal}  t/instr={T/INSTR:.3f}", flush=True)
    own = attribute(m.regions, p.heat, p.wait)
    rows = [(n, h, w) for n, (h, w) in own.items() if n.startswith("cpu") and h]
    tot = sum(h for _, h, _ in rows)
    print(f"  cpu total {100*tot/S:.2f}% of run")
    print("  cpu region                          %run   %blocked    t/instr")
    for n, h, w in sorted(rows, key=lambda r: -r[1]):
        print(f"   {n:34s} {100*h/S:7.2f} {100*w/S:10.2f} {h/S*T/INSTR:10.2f}")


if __name__ == "__main__":
    main()
