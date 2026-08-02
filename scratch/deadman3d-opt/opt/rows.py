#!/usr/bin/env python3
"""The lane-row assignment, solved exactly.

``plan``'s default is *length-descending*, and its own docstring says why that is
only half an answer: a row is a tick cost weighted by how often the opcode runs,
and length knows nothing about frequency.  :data:`LANE_ORDER` is the registry
that overrides it, and ``deadman-3d_hires`` is absent from that table.

With :mod:`model` closed, the objective is exact and small enough to solve rather
than search.  Per instruction a simple lane on row ``y`` of extent ``w`` costs

    B(y) + 2 * w_eff(y)

where ``B(y) = T(y) + K(y)`` is a **per-row constant, invariant under any
permutation of the opcodes** (``plan`` hands out the same slot set whatever the
order, so ``_uneven_trie`` draws the same trie), and

    w_eff(y) = max(extent of every simple lane at or south of y, same band)

is the running suffix maximum the drop rule imposes -- the term that makes
length-descending optimal *on its own* and that a frequency-aware order has to
pay to break.

Both facts together make this a subset DP over rows south-to-north: the state is
which opcodes are already placed south of the frontier, the running maximum is a
function of that state, and 2^16 states is nothing.  The answer is the exact
minimum over every order, with **no binding constraint applied** -- so it is an
upper bound on what any legal reorder can buy, which is the number the question
"is the CPU finished" actually wants.

Usage:  python rows.py <prof.json> <geom.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

STRUCTURED = ("BRN", "BRZ", "JMPF", "JMPS")
#: ``plan``'s pinned groups: INPUT takes the top slot, the display/output band the
#: bottom ones.  ``middle_order`` permutes only what is left.
PINNED = ("IN", "SND")


def rowcost(geom, prof):
    """B(y) and the extent of each lane, read off the built machine."""
    g = json.loads(Path(geom).read_text())
    p = json.loads(Path(prof).read_text())
    n = len(p["execs"])
    cls = p["classes"]
    ops = p["ops"][:n]
    ex = {ops[i]: p["execs"][i] for i in range(n)}
    tri = {ops[i]: p["ticks_by_op"][i][cls.index("trie")] for i in range(n)}
    lanes = {k: tuple(v) for k, v in g["lanes"].items()}
    regs = g["regions"]
    c, H, C = regs["fetch"][1], regs["return:high"][1], regs["return:collector"][1]
    B, W = {}, {}
    for nm, (_lx, y, w, _h) in lanes.items():
        K = (H + 8 - y) if y < H else (C + C - c + 7 - y)
        T = tri[nm] / ex[nm] if ex[nm] else None
        B[y] = None if T is None else round(T) + K
        W[nm] = w
    return g, ex, B, W, lanes, H


def solve(rows, items, B, W, ex):
    """Exact minimum of ``sum e * (B(y) + 2 * suffix_max_extent(y))`` over orders.

    ``rows`` north->south; ``items`` the opcodes free to move among them.
    """
    k = len(rows)
    assert k == len(items)
    south = list(reversed(rows))  # fill south first: the suffix max grows north
    NEG = float("inf")
    dp = {0: (0.0, ())}
    for step in range(k):
        y = south[step]
        nxt = {}
        for mask, (cost, path) in dp.items():
            mx = max((W[items[i]] for i in range(k) if mask >> i & 1), default=0)
            for i in range(k):
                if mask >> i & 1:
                    continue
                op = items[i]
                w = max(W[op], mx)
                c2 = cost + ex[op] * (B[y] + 2 * w)
                m2 = mask | 1 << i
                if m2 not in nxt or c2 < nxt[m2][0]:
                    nxt[m2] = (c2, path + ((y, op),))
        dp = nxt
    best = min(dp.values(), key=lambda v: v[0])
    return best


def main():
    prof, geom = sys.argv[1], sys.argv[2]
    g, ex, B, W, lanes, H = rowcost(geom, prof)
    n_tot = sum(ex.values())
    simple = {nm: v for nm, v in lanes.items() if nm not in STRUCTURED}
    upper = sorted([nm for nm, v in simple.items() if v[1] < H], key=lambda n: simple[n][1])
    lower = sorted([nm for nm, v in simple.items() if v[1] > H], key=lambda n: simple[n][1])
    print("row cost B(y) = T(y) + K(y), and the shipped occupant:", flush=True)
    for nm, (_lx, y, w, _h) in sorted(simple.items(), key=lambda kv: kv[1][1]):
        print(f"  y={y:>3} B={B[y]:>3} w={w:>3} {nm:<6} exec={ex[nm]:>8,} "
              f"({100 * ex[nm] / n_tot:>5.2f}%)  cost/exec={B[y] + 2 * w:>4}", flush=True)

    def total(assign):
        return sum(ex[op] * (B[y] + 2 * we) for y, op, we in assign)

    def effective(seq):
        """seq is [(y, op)] north->south; return with suffix-max extents."""
        out = []
        mx = 0
        for y, op in reversed(seq):
            mx = max(mx, W[op])
            out.append((y, op, mx))
        return list(reversed(out))

    ship = effective([(simple[n][1], n) for n in upper + lower])
    base = total(ship)
    print(f"\nshipped simple-lane cost: {base / n_tot:.4f} t/instr "
          f"(vs {sum(ex[n] * (B[simple[n][1]] + 2 * W[n]) for n in simple) / n_tot:.4f} "
          f"if no floor were active)", flush=True)

    results = {}
    for band, names in (("upper", upper), ("lower", lower)):
        rows = [simple[n][1] for n in names]
        free = [n for n in names if n not in PINNED]
        fixed = [(simple[n][1], n) for n in names if n in PINNED]
        frows = [y for y in rows if y not in {y for y, _ in fixed}]
        cost, path = solve(frows, free, B, W, ex)
        seq = sorted(list(path) + fixed, key=lambda t: t[0])
        results[band] = seq
        print(f"\n{band} band: {len(free)} free lanes over rows {frows}", flush=True)
        for y, op, we in effective(seq):
            mark = "" if simple[op][1] == y else f"   <- was y={simple[op][1]}"
            print(f"  y={y:>3} {op:<6} extent {W[op]:>2} -> eff {we:>2}  "
                  f"cost/exec {B[y] + 2 * we:>4}{mark}", flush=True)

    opt = effective(results["upper"]) + effective(results["lower"])
    obest = total(opt)
    print(f"\n=== simple-lane walk, per instruction ===", flush=True)
    print(f"  shipped   {base / n_tot:>9.4f}", flush=True)
    print(f"  optimal   {obest / n_tot:>9.4f}   (no binding constraint applied)",
          flush=True)
    print(f"  gain      {(base - obest) / n_tot:>9.4f} t/instr "
          f"= {100 * (base - obest) / n_tot / 158.907:.3f}% of a 158.9 t/instr machine",
          flush=True)
    order = [op for _y, op in sorted(
        [(y, op) for y, op, _ in opt] + [(lanes[s][1], s) for s in STRUCTURED],
        key=lambda t: t[0])]
    print(f"\n  LANE_ORDER (north->south, all lanes): {tuple(order)}", flush=True)
    mid = [o for o in order if o not in PINNED]
    print(f"  middle_order (the permutable ones): {tuple(mid)}", flush=True)


if __name__ == "__main__":
    main()
