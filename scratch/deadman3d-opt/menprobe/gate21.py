"""Gate a candidate at the full 21 rounds on whichever tiers are named.

Registry defaults only -- ``men_run.py`` carries a stale ``STORE_SHAPE`` and
``SEEK_TIER_LAYOUT`` override that builds 496x662 instead of the shipped
496x674, which is a different machine and not a baseline.

usage: gate21.py [men-v3|taped ...]        -- env KNOB=1 to flip a registry flag
"""
import os
import sys
import time

sys.path.insert(0, "/tmp/menprobe")
from common import setup, tour, run, SLUG  # noqa: E402

INSTR = 880_332


def main():
    d3, hires, M, prog = setup()
    tiers = [a for a in sys.argv[1:] if not a.startswith("-")] or ["men-v3", "taped"]
    rounds = int(os.environ.get("ROUNDS", "21"))
    inp, frames = tour(hires, rounds)
    print(f"tour {rounds} rounds, tape={M.TAPE_SIZE[SLUG]}", flush=True)
    for store in tiers:
        t = time.time()
        m = M.build_for(SLUG, program=prog, store=store)
        print(f"built {store} {m.width}x{m.height} ({time.time()-t:.0f}s)", flush=True)
        res = run(m, inp, frames, store)
        if rounds == 21:
            print(f"    t/instr = {res.frame_ticks[-1]/INSTR:.4f}", flush=True)


if __name__ == "__main__":
    main()
