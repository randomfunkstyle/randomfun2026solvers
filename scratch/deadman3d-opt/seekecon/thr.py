"""SEEK_THRESHOLD sweep on the real machine.

`build_for` does not thread `seek_threshold` (build()'s default is bound at def
time), so the threshold is forced by wrapping `machine.seek_split` — the same
call `build` makes at line 4629, with one argument overridden. Nothing else moves.

usage: thr.py <store> <rounds> <thr> [thr ...]
"""
import sys, time
sys.path.insert(0, "/tmp/seekecon")
from common import setup, tour, run, SLUG

d3, hires, M, prog = setup()
store = sys.argv[1]
rounds = int(sys.argv[2])
thrs = [int(t) for t in sys.argv[3:]]
inp, frames = tour(hires, rounds)
print(f"tour {len(frames)} rounds, store={store}", flush=True)

_orig = M.seek_split
FORCE = {"t": None}


def patched(program, *, threshold=None, ops=("JMPF",)):
    return _orig(program, threshold=FORCE["t"], ops=ops)


M.seek_split = patched

for t in thrs:
    FORCE["t"] = t
    n = sum(1 for i in patched(prog).instrs if i.sem in M._SEEK_SEMS)
    t0 = time.time()
    try:
        m = M.build_for(SLUG, program=prog, store=store)
    except Exception as exc:
        print(f"  thr={t}: BUILD FAILED {type(exc).__name__}: {str(exc)[:160]}", flush=True)
        continue
    print(f"  thr={t}: {n} seek instrs, built {m.width}x{m.height} "
          f"({time.time()-t0:.0f}s)", flush=True)
    try:
        run(m, inp, frames, f"thr={t}")
    except Exception as exc:
        # The packing hazard SEEK_TIER_LAYOUT records: a re-packed literal whose
        # reverse reading overflows signed 64 bits. A threshold change re-packs.
        print(f"  thr={t}: RUN FAILED {type(exc).__name__}: {str(exc)[:160]}", flush=True)
