"""Choose the 22 trie slots that minimise ROM opcode cells at a FIXED lane order.

With ``trim_dead`` on, a lane's row is ``y0 + 2*rank(slot)`` — the *rank* of its
slot among the used ones, not the slot itself.  So any increasing relabelling of
the used slots leaves every lane row (hence every measured lane tick) untouched
and only moves the opcode numbers, which are ``_bitrev(slot, 5)``.

DP over slots 0..31 x ranks 0..21.
"""
import collections

from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1 import programs

SLUG = "deadman-3d"
program = M.seek_split(programs.load(SLUG), threshold=M.SEEK_THRESHOLD, ops=M.SEEK_OPS)
mo = list(M.LANE_ORDER[SLUG])
used_m = {op.mnemonic for op in program.ops_used}
at = min((mo.index(c) for c in ("JMPF", "BRZ", "BRN") if c in mo), default=len(mo))
for new in ("JMPS", "BRZS", "BRNS"):
    if new in used_m and new not in mo:
        mo.insert(at, new)
        at += 1
p = M.plan(program, middle_order=mo)
instrs = sorted(program.instrs, key=lambda i: i.pos)
hist = collections.Counter(i.mnemonic for i in instrs)

by_rank = sorted(p.number, key=lambda m: p.row[m])
N = len(by_rank)
K = p.k
LANES = 1 << K
cnt = [hist[m] for m in by_rank]
print(f"{N} lanes, k={K}; rank -> lane(count):")
print("  " + "  ".join(f"{r}:{m}({c})" for r, (m, c) in enumerate(zip(by_rank, cnt))))

# cells for a code: 2 if one digit else 5 (all codes < 100)
def cells(code):
    return 2 if code < 10 else 5

cur = [M._bitrev(p.row[m] // 2, K) for m in by_rank]
cur_cost = sum(c * cells(code) for c, code in zip(cnt, cur))
print(f"\ncurrent slots {[p.row[m]//2 for m in by_rank]}")
print(f"current codes {cur}")
print(f"current opcode cells = {cur_cost}")

# ── DP: pick an increasing 22-subset of 0..31 ───────────────────────────────
INF = float("inf")
best = {}


def solve(rank, slot):
    """min cost for ranks rank..N-1 using slots >= slot."""
    if rank == N:
        return 0
    if (rank, slot) in best:
        return best[(rank, slot)]
    if LANES - slot < N - rank:
        return INF
    take = cnt[rank] * cells(M._bitrev(slot, K)) + solve(rank + 1, slot + 1)
    skip = solve(rank, slot + 1)
    best[(rank, slot)] = min(take, skip)
    return best[(rank, slot)]


import sys

sys.setrecursionlimit(10000)
opt = solve(0, 0)
print(f"optimal opcode cells = {opt}  (save {cur_cost - opt})")

# reconstruct
sol = []
rank, slot = 0, 0
while rank < N:
    take = cnt[rank] * cells(M._bitrev(slot, K)) + solve(rank + 1, slot + 1)
    if take == solve(rank, slot):
        sol.append(slot)
        rank += 1
    slot += 1
print(f"optimal slots {sol}")
print(f"optimal codes {[M._bitrev(s, K) for s in sol]}")
print("assignment:")
for m, s in zip(by_rank, sol):
    print(f"  {m:7s} count={hist[m]:4d}  slot {p.row[m]//2:2d}->{s:2d}  code {M._bitrev(p.row[m]//2, K):2d}->{M._bitrev(s, K):2d}")
print("\nregistry tuple: " + repr({m: s for m, s in zip(by_rank, sol)}))
