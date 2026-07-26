"""Throwaway: read ring B off the loadb-stage grid and check it against Python."""
import sys
from pathlib import Path

from randomfun2026solvers.littleman import Littleman

vals = [35598, 41872, 81980, 98583, 65116, 96540, 10035, 60706, 14417, 64505]
t = 248550
inp = " ".join(map(str, [len(vals)] + vals + [t]))
s = Littleman().tick(Path("/tmp/ss_loadb.man"), int(sys.argv[1]), input=inp)
d = s.model_dump()
got = d["output"]
want = [1]
for v in vals[len(vals) - 8:]:
    want = [x for y in want for x in (y, y + v)]
print("len", len(got), "sentinel", got[-1] if got else None)
print("match", got[:-1] == want, "want[:6]", want[:6], "got[:6]", got[:6])
for r in d["entities"]["runners"]:
    print("runner", r["id"], r["pos"], "halted", r["halted"])
