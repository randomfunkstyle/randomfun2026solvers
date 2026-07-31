"""SEEK_OPS re-sweep on today's machine (deliverable 2).

The JMPF-alone default is inherited from `deadman-3d`, pre-11-bank-cut. This
re-derives it on hires, both tiers, at the shipped threshold.

usage: ops.py <store> <rounds> <opset> [opset ...]     opset e.g. JMPF+BRZ
"""
import sys, time
sys.path.insert(0, "/tmp/seekecon")
from common import setup, tour, run, SLUG

d3, hires, M, prog = setup()
store = sys.argv[1]
rounds = int(sys.argv[2])
opsets = [tuple(a.split("+")) for a in sys.argv[3:]]
inp, frames = tour(hires, rounds)
print(f"tour {len(frames)} rounds, store={store}", flush=True)

for ops in opsets:
    M.SEEK_OPS_FOR[SLUG] = ops
    n = sum(1 for i in M.seek_split(prog, ops=ops).instrs if i.sem in M._SEEK_SEMS)
    t0 = time.time()
    tag = "+".join(ops)
    try:
        m = M.build_for(SLUG, program=prog, store=store)
    except Exception as exc:
        print(f"  {tag}: BUILD FAILED {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        continue
    print(f"  {tag}: {n} seek instrs, built {m.width}x{m.height} "
          f"({time.time()-t0:.0f}s)", flush=True)
    try:
        run(m, inp, frames, tag)
    except Exception as exc:
        print(f"  {tag}: RUN FAILED {type(exc).__name__}: {str(exc)[:160]}", flush=True)
M.SEEK_OPS_FOR.pop(SLUG, None)
