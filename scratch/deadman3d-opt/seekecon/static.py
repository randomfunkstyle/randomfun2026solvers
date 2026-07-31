"""Static: how many instructions of each family clear each threshold.

`seek_split` decides statically. This is the registry-eye view, independent of
the tour, and it is what decides whether a family costs a lane and a slab at all.
"""
import sys
sys.path.insert(0, "/tmp/seekecon")
from common import setup

d3, hires, M, prog = setup()
from randomfun2026solvers.lm1.isa import SEEK_OF, TARGET_SEMS

instrs = sorted(prog.instrs, key=lambda i: i.pos)
n = len(instrs)
idx = {ins.pos: k for k, ins in enumerate(instrs)}
rows = []
for k, ins in enumerate(instrs):
    if ins.sem in TARGET_SEMS and ins.sem in SEEK_OF:
        skip = 2 * ((M._target_index(prog, instrs, idx, k) - k - 1) % n)
        rows.append((ins.mnemonic, skip))

print(f"{len(instrs)} instructions, {len(rows)} structured jumps")
for mn in ("JMPF", "BRZ", "BRN"):
    ss = sorted(s for m, s in rows if m == mn)
    print(f"\n{mn}: {len(ss)} sites, skips {ss[0]}..{ss[-1]}")
    for t in (64, 128, 256, 384, 448, 512, 600, 800, 1000, 2000):
        c = sum(1 for s in ss if s >= t)
        print(f"   >= {t:>5}: {c:>4} sites")
