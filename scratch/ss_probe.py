"""Throwaway: step the subset-sum load-stage grid and print the man's state."""
import sys
from pathlib import Path

from randomfun2026solvers.littleman import Littleman

lm = Littleman()
vals = [35598, 41872, 81980, 98583, 65116, 96540, 10035, 60706, 14417, 64505]
t = 248550
inp = " ".join(map(str, [len(vals)] + vals + [t]))
n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
s = lm.tick(Path("/tmp/ss_load.man"), n, input=inp)
d = s.model_dump()
for r in d["entities"]["runners"]:
    p, dd = r["pos"], r["dir"]
    print(f"id{r['id']} grid=({p['x']},{p['y']}) worker=({p['x']-1},{p['y']-21}) "
          f"dir=({dd['x']},{dd['y']}) A={r['a']} B={r['b']} BP={r['backpack']}")
print("out", d["output"], "read", d["input_read"], "fatal", d["fatal"])
