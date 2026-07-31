"""slab_prof, but for a variant. usage: slab_prof2.py <store> <rounds> <spec> <tag>"""
import pickle
import sys
import time

from common import setup, tour, SLUG
from randomfun2026solvers.fast_littleman import FastLittleman
from slab_try import apply

INSTR = 880_332


def main():
    store, rounds, spec, tag = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
    d3, hires, M, prog = setup()
    apply(M, (SLUG, store), spec)
    inp, frames = tour(hires, rounds)
    t = time.time()
    m = M.build_for(SLUG, program=prog, store=store)
    print(f"built {m.width}x{m.height} pad={m.mem_pad} ({time.time()-t:.0f}s)", flush=True)
    res = FastLittleman("\n".join(m.rows)).run(
        inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000,
        profile=True, profile_stride=17)
    p = res.profile
    with open(f"/tmp/slab/heat-{tag}-{rounds}.pkl", "wb") as fh:
        pickle.dump({"regions": m.regions, "heat": dict(p.heat),
                     "wait": dict(p.wait), "samples": p.samples,
                     "stride": p.stride, "step": res.step,
                     "last": res.frame_ticks[-1], "rows": m.rows,
                     "passed": res.passed, "fatal": res.fatal,
                     "size": (m.width, m.height)}, fh)
    print(f"  {tag}: last={res.frame_ticks[-1]:,} "
          f"t/instr={res.frame_ticks[-1]/INSTR:.3f} passed={res.passed} "
          f"fatal={res.fatal}", flush=True)


if __name__ == "__main__":
    main()
