"""Throwaway: where is the worker standing?"""
import sys
from pathlib import Path

from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.subset_sum_mitm import public_cases

name, vals, t, _ = public_cases()[int(sys.argv[2]) if len(sys.argv) > 2 else 3]
inp = " ".join(map(str, [len(vals), *vals, t]))
d = Littleman().tick(Path("/tmp/ss_full.man"), int(sys.argv[1]), input=inp).model_dump()
for r in d["entities"]["runners"]:
    p, dd = r["pos"], r["dir"]
    wx, wy = p["x"] - 1, p["y"] - 21
    if 0 <= wx < 46 and 0 <= wy < 230:
        print(f"worker=({wx},{wy}) dir=({dd['x']},{dd['y']}) "
              f"A={r['a']} B={r['b']} BP={r['backpack']}")
print("out", d["output"])
