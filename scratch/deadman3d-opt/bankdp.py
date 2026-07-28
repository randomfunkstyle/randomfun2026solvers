"""Optimal contiguous bank split against measured per-address traffic.

Cost model: the ring tax is ~8 ticks per slot per access (ARCH.md 4.1), so a
bank of M slots costs ~8*(M+1) an access; the chain adds ~HOP ticks per
pass-through gate ahead of the bank.  Minimises

    sum_b  acc_b * RING * (size_b + 1)  +  acc_b * HOP * chain_position_b

over contiguous address-order partitions, then over the *reachable* chain
orders (memory_taped.gate_chain's end-peelings).

usage: bankdp.py [nbanks] [top]
"""
import itertools
import json
import pathlib
import sys

RING = 8.0
HOP = 21.0

NB = int(sys.argv[1]) if len(sys.argv) > 1 else 4
TOP = int(sys.argv[2]) if len(sys.argv) > 2 else 600

d = json.loads(pathlib.Path(__file__).with_name("traffic.json").read_text())
acc = [0.0] * (TOP + 2)
for k, v in d["reads"].items():
    if int(k) <= TOP:
        acc[int(k)] += v
for k, v in d["writes"].items():
    if int(k) <= TOP:
        acc[int(k)] += v
pre = [0.0] * (TOP + 2)
for a in range(1, TOP + 1):
    pre[a] = pre[a - 1] + acc[a]


def A(lo, hi):  # inclusive
    return pre[hi] - pre[lo - 1]


def orders(nb):
    """Every reachable chain order: each step peels an end of what is left."""
    out = []

    def rec(lo, hi, sofar):
        if lo == hi:
            out.append(tuple(sofar) + (lo,))
            return
        rec(lo + 1, hi, sofar + [lo])
        rec(lo, hi - 1, sofar + [hi])

    rec(0, nb - 1, [])
    return out


ORD = orders(NB)


def order_cost(sizes, accs):
    best = None
    for o in ORD:
        pos = {k: j for j, k in enumerate(o)}
        c = sum(accs[k] * HOP * pos[k] for k in range(len(sizes)))
        if best is None or c < best[0]:
            best = (c, o)
    return best


# DP: f[k][i] = min ring cost for k banks covering 1..i
INF = float("inf")
f = [[INF] * (TOP + 1) for _ in range(NB + 1)]
back = [[-1] * (TOP + 1) for _ in range(NB + 1)]
for i in range(1, TOP + 1):
    f[1][i] = A(1, i) * RING * (i + 1)
for k in range(2, NB + 1):
    for i in range(k, TOP + 1):
        best, bj = INF, -1
        for j in range(k - 1, i):
            v = f[k - 1][j] + A(j + 1, i) * RING * (i - j + 1)
            if v < best:
                best, bj = v, j
        f[k][i], back[k][i] = best, bj

# recover
cuts, i = [], TOP
for k in range(NB, 1, -1):
    j = back[k][i]
    cuts.append(j)
    i = j
cuts.reverse()
bounds = [0] + cuts + [TOP]
sizes = tuple(bounds[i + 1] - bounds[i] for i in range(NB))
accs = [A(bounds[i] + 1, bounds[i + 1]) for i in range(NB)]


def report(sizes, label):
    b = [0]
    for s in sizes:
        b.append(b[-1] + s)
    accs = [A(b[i] + 1, min(b[i + 1], TOP)) for i in range(len(sizes))]
    ring = sum(accs[i] * RING * (sizes[i] + 1) for i in range(len(sizes)))
    hc, o = order_cost(sizes, accs)
    print(f"{label}: sizes={sizes} order={o}")
    for i, s in enumerate(sizes):
        print(
            f"   bank {i}  {b[i]+1:4d}..{min(b[i+1],TOP):4d}  M={s:4d}  "
            f"acc={accs[i]:9.1f} ({100*accs[i]/pre[TOP]:5.2f}%)  "
            f"ring={accs[i]*RING*(s+1):12,.0f}"
        )
    print(f"   ring total {ring:14,.0f}   hop {hc:11,.0f}   TOTAL {ring+hc:14,.0f}/frame")
    return ring + hc


print(f"total accesses/frame = {pre[TOP]:,.1f}")
base = report((256, 195, 64, 85), "shipped")
opt = report(sizes, f"DP optimum ({NB} banks)")
print(f"\nDP optimum vs shipped: {100*(opt-base)/base:+.2f}% of the modelled cost")

# a few hand candidates around the optimum
print()
for cand in [
    (256, 256, 32, 56),
    (256, 260, 22, 62),
    (128, 384, 32, 56),
    (512, 32, 32, 24),
    (256, 256, 24, 64),
    (300, 212, 32, 56),
]:
    if sum(cand) >= TOP:
        report(cand, "cand")
