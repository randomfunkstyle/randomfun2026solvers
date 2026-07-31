"""JMPF+BRN on men-v3, with the store moved to make it bind.

The plain build refuses with "the store's request wall is on row 156 and the
adapter's request leaves on row 157: a straight leg needs them level
(TIER_LAYOUT's store_offset dy)" — the error names its own fix, and the user has
licensed store movement. So this walks store_offset dy around the shipped 10
until the BRN slab binds, and prices the family against a JMPF build carrying the
*same* dy, so the store move is a constant and only the family is being measured.

usage: brn.py <rounds> <dy> [dy ...]
"""
import sys, time
sys.path.insert(0, "/tmp/seekecon")
from common import setup, tour, run, SLUG

d3, hires, M, prog = setup()
rounds = int(sys.argv[1])
dys = [int(v) for v in sys.argv[2:]]
inp, frames = tour(hires, rounds)
KEY = (SLUG, "men-v3")
base_off = M.TIER_LAYOUT[KEY]["store_offset"]
print(f"tour {len(frames)} rounds; shipped store_offset {base_off}", flush=True)

for dy in dys:
    M.TIER_LAYOUT[KEY] = {"store_offset": (base_off[0], dy)}
    for ops in (("JMPF", "BRN"), ("JMPF",)):
        M.SEEK_OPS_FOR[SLUG] = ops
        tag = f"dy={dy} {'+'.join(ops)}"
        t0 = time.time()
        try:
            m = M.build_for(SLUG, program=prog, store="men-v3")
        except Exception as exc:
            print(f"  {tag}: BUILD FAILED {str(exc)[:150]}", flush=True)
            continue
        print(f"  {tag}: built {m.width}x{m.height} ({time.time()-t0:.0f}s)", flush=True)
        try:
            run(m, inp, frames, tag)
        except Exception as exc:
            print(f"  {tag}: RUN FAILED {type(exc).__name__}: {str(exc)[:140]}", flush=True)
