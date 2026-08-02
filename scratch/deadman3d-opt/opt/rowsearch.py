#!/usr/bin/env python3
"""Cross-band lane-row search, and the three numbers that bracket the answer.

:mod:`rows` solves each band exactly but forbids a lane from crossing the fetch
row.  That restriction is not free -- the lower band's cheapest row costs 34 and
the upper's dearest 39 -- so this does the joint problem, by exhaustive local
search over the 18-lane permutation under the *same* exact objective.

Three numbers come out, and the gap between them is the whole story:

* **shipped** -- what the built machine costs;
* **best found** -- the joint optimum under the drop rule's suffix maximum;
* **absolute floor** -- the same objective with the suffix maximum *deleted*,
  i.e. every lane keeps its own extent wherever it sits.  Unreachable by
  construction (the floor is geometry, not a modelling choice), and reported
  because it is the honest ceiling on what any row lever can ever be worth.

Usage:  python rowsearch.py <prof.json> <geom.json> [seeds]
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

STRUCTURED = {"BRN", "BRZ", "JMPF", "JMPS"}
PINNED = {"IN", "SND"}


def load(prof, geom):
    g = json.loads(Path(geom).read_text())
    p = json.loads(Path(prof).read_text())
    n = len(p["execs"])
    cls, ops = p["classes"], p["ops"][:n]
    ex = {ops[i]: p["execs"][i] for i in range(n)}
    tri = {ops[i]: p["ticks_by_op"][i][cls.index("trie")] for i in range(n)}
    lanes = {k: tuple(v) for k, v in g["lanes"].items()}
    r = g["regions"]
    c, H, C = r["fetch"][1], r["return:high"][1], r["return:collector"][1]
    grid = {int(k): v for k, v in g["rows"].items()}
    x0 = min(v[0] for v in lanes.values())
    B, W = {}, {}
    for nm, (_lx, y, w, _h) in lanes.items():
        B[y] = round(tri[nm] / ex[nm]) + ((H + 8 - y) if y < H else (C + C - c + 7 - y))
        # The lane's **intrinsic** extent, not the one the built grid shows: the
        # drop rule floors a lane at the extent of everything below it, so a
        # shipped width can be padding bought for a neighbour.  The last *glyph*
        # on the row is the lane's own; one more cell carries its `v`.
        line = grid[y]
        ops = [x for x in range(x0, x0 + w) if line[x] not in (".", " ", "v", "^")]
        W[nm] = (max(ops) - x0 + 2) if ops else w
    return g, ex, B, W, lanes, H


def cost(order, rows_u, rows_l, B, W, ex):
    """``order`` is upper-band lanes north->south then lower-band, north->south."""
    tot = 0
    for rows, seg in ((rows_u, order[: len(rows_u)]), (rows_l, order[len(rows_u):])):
        mx = 0
        for y, op in zip(reversed(rows), reversed(seg)):
            mx = max(mx, W[op])
            tot += ex[op] * (B[y] + 2 * mx)
    return tot


def main():
    prof, geom = sys.argv[1], sys.argv[2]
    seeds = int(sys.argv[3]) if len(sys.argv) > 3 else 4000
    g, ex, B, W, lanes, H = load(prof, geom)
    N = sum(ex.values())
    simple = {nm: v for nm, v in lanes.items() if nm not in STRUCTURED}
    rows_u = sorted(y for nm, (_l, y, _w, _h) in simple.items() if y < H)
    rows_l = sorted(y for nm, (_l, y, _w, _h) in simple.items() if y > H)
    rows = rows_u + rows_l
    ship_order = [nm for nm, _ in sorted(simple.items(), key=lambda kv: kv[1][1])]
    base = cost(ship_order, rows_u, rows_l, B, W, ex)

    # position constraints: plan() pins INPUT to the top slot and the display
    # band to the bottom ones; only the middle is permutable.
    pin = {i: nm for i, nm in enumerate(ship_order) if nm in PINNED}
    movable = [i for i in range(len(rows)) if i not in pin]
    pool = [nm for nm in ship_order if nm not in PINNED]

    rnd = random.Random(20260731)
    best, bestv = list(ship_order), base
    for s in range(seeds):
        cur = list(ship_order)
        if s:
            perm = pool[:]
            rnd.shuffle(perm)
            cur = [None] * len(rows)
            for i, nm in pin.items():
                cur[i] = nm
            for i, nm in zip(movable, perm):
                cur[i] = nm
        v = cost(cur, rows_u, rows_l, B, W, ex)
        improved = True
        while improved:  # 2-opt over swappable positions, to a local minimum
            improved = False
            for a in range(len(movable)):
                for b in range(a + 1, len(movable)):
                    i, j = movable[a], movable[b]
                    cur[i], cur[j] = cur[j], cur[i]
                    v2 = cost(cur, rows_u, rows_l, B, W, ex)
                    if v2 < v - 1e-9:
                        v, improved = v2, True
                    else:
                        cur[i], cur[j] = cur[j], cur[i]
        if v < bestv:
            best, bestv = list(cur), v
            print(f"  seed {s}: {v / N:.4f} t/instr", flush=True)

    nofloor = sum(ex[nm] * 2 * W[nm] for nm in simple)
    Bs = sorted(B[y] for y in rows)
    es = sorted((ex[nm] for nm in simple), reverse=True)
    floorless = sum(e * b for e, b in zip(es, Bs)) + nofloor

    print(f"\nshipped simple-lane walk   {base / N:>8.4f} t/instr", flush=True)
    print(f"best found (joint, exact)  {bestv / N:>8.4f}  "
          f"gain {(base - bestv) / N:.4f} = {100 * (base - bestv) / N / 158.907:.3f}% "
          f"of t/instr", flush=True)
    print(f"absolute floor (no floor)  {floorless / N:>8.4f}  "
          f"gain {(base - floorless) / N:.4f} = "
          f"{100 * (base - floorless) / N / 158.907:.3f}%  <- unreachable", flush=True)
    print("\nbest arrangement:", flush=True)
    for rows_, seg in ((rows_u, best[: len(rows_u)]), (rows_l, best[len(rows_u):])):
        mx = 0
        acc = []
        for y, op in zip(reversed(rows_), reversed(seg)):
            mx = max(mx, W[op])
            acc.append((y, op, mx))
        for y, op, we in reversed(acc):
            was = simple[op][1]
            print(f"  y={y:>3} B={B[y]:>3} {op:<6} w={W[op]:>2} eff={we:>2} "
                  f"exec={ex[op]:>8,}{'' if was == y else f'   <- was {was}'}", flush=True)
    full = sorted([(rows[i], best[i]) for i in range(len(rows))]
                  + [(lanes[s][1], s) for s in STRUCTURED])
    print(f"\nLANE_ORDER = {tuple(op for _y, op in full)}", flush=True)
    print(f"middle_order = "
          f"{tuple(op for _y, op in full if op not in PINNED)}", flush=True)


if __name__ == "__main__":
    main()
