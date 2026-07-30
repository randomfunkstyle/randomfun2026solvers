"""Profile the hires men-v3 build with registry defaults.

Attribution is ``lm1.profile._region_of``'s: the **smallest** box containing a
cell owns it. Naively summing every box double-counts massively — ``cpu:drops``
is a 20x26 band laid across every lane row, so a box-sum reports 139% of the run.

usage: prof3d.py <rounds> [tag]
"""
import pickle
import os
import sys
import time

from common import setup, tour, SLUG
from randomfun2026solvers.fast_littleman import FastLittleman

INSTR = 880_332


def attribute(regions, heat, wait, prefix="cpu"):
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


def report(res, regions, tag=""):
    p = res.profile
    S = p.samples
    T = res.frame_ticks[-1]
    print(f"  {tag} step={res.step:,} last={T:,} passed={res.passed} fatal={res.fatal}")
    print(f"  t/instr = {T/INSTR:.3f}   samples={S:,}")
    own = attribute(regions, p.heat, p.wait)
    rows = [(n, h, w) for n, (h, w) in own.items() if n.startswith("cpu") and h]
    tot = sum(h for _, h, _ in rows)
    print(f"  cpu total {100*tot/S:.2f}% of run")
    print("  cpu region                          %run   %blocked    t/instr")
    for n, h, w in sorted(rows, key=lambda r: -r[1]):
        print(f"   {n:34s} {100*h/S:7.2f} {100*w/S:10.2f} {h/S*T/INSTR:10.2f}")
    return own


def main():
    d3, hires, M, prog = setup()
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 21
    tag = sys.argv[2] if len(sys.argv) > 2 else "base"
    inp, frames = tour(hires, rounds)
    t = time.time()
    m = M.build_for(SLUG, program=prog, store="men-v3")
    print(f"built {m.width}x{m.height} ({time.time()-t:.0f}s)", flush=True)
    t = time.time()
    res = FastLittleman("\n".join(m.rows)).run(
        inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000,
        profile=True, profile_stride=17)
    print(f"  ran in {time.time()-t:.0f}s", flush=True)
    with open(os.environ.get("HEATDIR", "/tmp") + f"/heat-{tag}-{rounds}.pkl", "wb") as fh:
        pickle.dump({"regions": m.regions, "heat": dict(res.profile.heat),
                     "wait": dict(res.profile.wait), "samples": res.profile.samples,
                     "step": res.step, "last": res.frame_ticks[-1],
                     "passed": res.passed, "fatal": res.fatal,
                     "size": (m.width, m.height)}, fh)
    report(res, m.regions, tag)


if __name__ == "__main__":
    main()
