"""Build-only feasibility + modelled cost over contiguous 4-bank splits.

usage: sweep6_bankbuild.py <rom_rows|-> <b1 csv> <b2 csv> <b3 csv>
Boundaries are inclusive top addresses of banks 0,1,2 (bank 3 runs to 600).
"""
import json
import pathlib
import sys

from randomfun2026solvers import memory_taped as T
from randomfun2026solvers.lm1 import machine as M

TOP = 600
RING, HOP = 4.0, 21.0

d = json.loads(pathlib.Path(__file__).with_name("traffic.json").read_text())
acc = [0.0] * (TOP + 2)
for k, v in list(d["reads"].items()) + list(d["writes"].items()):
    if int(k) <= TOP:
        acc[int(k)] += v
pre = [0.0] * (TOP + 2)
for a in range(1, TOP + 1):
    pre[a] = pre[a - 1] + acc[a]
A = lambda lo, hi: pre[hi] - pre[lo - 1]  # noqa: E731


def orders(nb):
    out = []

    def rec(lo, hi, sofar):
        if lo == hi:
            out.append(tuple(sofar) + (lo,))
            return
        rec(lo + 1, hi, sofar + [lo])
        rec(lo, hi - 1, sofar + [hi])

    rec(0, nb - 1, [])
    return out


ORD = orders(4)

rr = sys.argv[1]
if rr != "-":
    M.SEEK_TIER_LAYOUT[("deadman-3d", "taped")] = {"rom_rows": int(rr)}
B1 = [int(x) for x in sys.argv[2].split(",")]
B2 = [int(x) for x in sys.argv[3].split(",")]
B3 = [int(x) for x in sys.argv[4].split(",")]

rows = []
for b1 in B1:
    for b2 in B2:
        if b2 <= b1:
            continue
        for b3 in B3:
            if b3 <= b2 or b3 >= TOP:
                continue
            sizes = (b1, b2 - b1, b3 - b2, TOP - b3)
            accs = [A(1, b1), A(b1 + 1, b2), A(b2 + 1, b3), A(b3 + 1, TOP)]
            ring = sum(accs[i] * RING * (sizes[i] + 1) for i in range(4))
            best = min(
                (sum(accs[k] * HOP * j for j, k in enumerate(o)), o) for o in ORD
            )
            rows.append((ring + best[0], sizes, best[1], accs))
rows.sort()
seen = set()
for cost, sizes, order, accs in rows[:40]:
    if sizes in seen:
        continue
    seen.add(sizes)
    try:
        blk = T.taped_store_block(601, sizes, skip_batch=2, compact_gate=True, order=order)
        geo = f"block {blk.width}x{blk.height}"
    except Exception as exc:  # noqa: BLE001
        geo = f"BLOCKFAIL {str(exc)[:50]}"
    try:
        M.TAPED_BANKS["deadman-3d"] = sizes
        M.TAPED_BANK_ORDER[("deadman-3d", "taped")] = order
        m = M.build_for("deadman-3d", store="taped")
        built = f"{m.width}x{m.height} max={max(m.width,m.height)}"
    except Exception as exc:  # noqa: BLE001
        built = f"FAIL {str(exc)[:60]}"
    print(f"  cost={cost:12,.0f}  {str(sizes):24s} o={order}  {geo:16s}  {built}", flush=True)
