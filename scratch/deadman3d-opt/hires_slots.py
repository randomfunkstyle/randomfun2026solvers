"""`scratch/rom-opt/slots.py`, re-run against **deadman-3d_hires' own** program.

The slot map is an assignment fitted to one static opcode histogram, so it is
not transferable: hires runs a different program (P=8,895 against 4,002, a
28-block billboard chain and a numeral painter `deadman-3d` has no equivalent
of) and it is not in `SEEK_DRUM`, so there is no `seek_split` and no `JMPS`
lane at all.  This derives its own map from its own histogram and prices it in
drum cells, exactly as `slots.py` does.

    ./.venv/bin/python scratch/deadman3d-opt/hires_slots.py --wad ~/DOOM1.WAD
"""
import argparse
import collections
import sys
from pathlib import Path

from randomfun2026solvers import deadman3d_hires as hires
from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1.asm import assemble

ap = argparse.ArgumentParser()
ap.add_argument("--wad", type=Path, required=True)
args = ap.parse_args()
hires.install_wad(args.wad)

program = assemble(hires.hires_source(), name="deadman-3d_hires")
# hires takes `LANE_ORDER.get(slug)` -> None, i.e. plan's own length-descending
# default, and is not in SEEK_DRUM so the program is not seek-split.
p = M.plan(program, middle_order=M.LANE_ORDER.get("deadman-3d_hires"))
hist = collections.Counter(i.mnemonic for i in program.instrs)

by_rank = sorted(p.number, key=lambda m: p.row[m])
N = len(by_rank)
K = p.k
LANES = 1 << K
cnt = [hist[m] for m in by_rank]
print(f"P={program.P}, {N} lanes, k={K}, {LANES} slots; rank -> lane(count):")
print("  " + "  ".join(f"{r}:{m}({c})" for r, (m, c) in enumerate(zip(by_rank, cnt))))


def cells(code):
    return 2 if code < 10 else 5


cur = [M._bitrev(p.row[m] // 2, K) for m in by_rank]
cur_cost = sum(c * cells(code) for c, code in zip(cnt, cur))
one_digit = sum(c for c, code in zip(cnt, cur) if code < 10)
print(f"\ncurrent slots {[p.row[m] // 2 for m in by_rank]}")
print(f"current codes {cur}")
print(f"current opcode cells = {cur_cost}  ({one_digit} of {sum(cnt)} one-digit)")

INF = float("inf")
best = {}


def solve(rank, slot):
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


sys.setrecursionlimit(10000)
opt = solve(0, 0)
print(f"optimal opcode cells = {opt}  (save {cur_cost - opt})")

sol = []
rank, slot = 0, 0
while rank < N:
    take = cnt[rank] * cells(M._bitrev(slot, K)) + solve(rank + 1, slot + 1)
    if take == solve(rank, slot):
        sol.append(slot)
        rank += 1
    slot += 1
opt_one = sum(c for c, s in zip(cnt, sol) if M._bitrev(s, K) < 10)
print(f"optimal slots {sol}  ({opt_one} of {sum(cnt)} one-digit)")
print(f"optimal codes {[M._bitrev(s, K) for s in sol]}")
print("assignment:")
for m, s in zip(by_rank, sol):
    print(f"  {m:7s} count={hist[m]:5d}  slot {p.row[m] // 2:2d}->{s:2d}  "
          f"code {M._bitrev(p.row[m] // 2, K):2d}->{M._bitrev(s, K):2d}")
print("\nregistry map: " + repr({m: s for m, s in zip(by_rank, sol)}))
