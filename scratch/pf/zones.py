"""How much does the nearest-pipe rule actually cost this program?"""
import sys
from collections import Counter

sys.path.insert(0, 'scratch/pf')
import prog  # noqa: E402

ZONE = {"rr": "R", "sr": "R", "rf": "F", "sf": "F",
        "rg": "G", "sg": "G", "ri": "I", "sp": "P"}
P = prog.build()

tokcount = Counter()
zonecount = Counter()
trans = Counter()
runs = []
for name, (toks, _) in P.items():
    cur = None
    run = 0
    for t in toks:
        tokcount[t] += 1
        z = ZONE.get(t)
        if z is None:
            continue
        zonecount[z] += 1
        if cur is None:
            cur = z
            run = 1
        elif z == cur:
            run += 1
        else:
            trans[(cur, z)] += 1
            runs.append((cur, run))
            cur, run = z, 1
    if cur:
        runs.append((cur, run))

print("pipe ops by zone:", dict(zonecount), "total", sum(zonecount.values()))
print("total tokens:", sum(tokcount.values()))
print("transitions:", sum(trans.values()))
for k, v in trans.most_common():
    print("   ", k, v)
lens = Counter(r for _, r in runs)
print("run lengths:", dict(sorted(lens.items())))
print("runs:", len(runs), "mean", sum(r for _, r in runs) / len(runs))
