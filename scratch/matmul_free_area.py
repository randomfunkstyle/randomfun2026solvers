#!/usr/bin/env python3
"""Price the free short axis on `matmul`: what do spare cells buy in ticks?

`score = max(w,h)^2 x avg_ticks`, so cells on the short axis are free until
they reach the long one.  The spend with the best rate here is unrolling the
group loop: `G = ceil(K/3) <= 6` iterations of a 15-cell body.

Three shapes for the group loop, using the lap costs measured by
`scratch/matmul_loop_shape.py` (a 12-cell body: 16 ticks as a rectangle,
26 as a row-plus-return-corridor):

    rectangle loop      body + 3 turns, rounded up to even  = 18 ticks/group
    unrolled            body - `m` - `d`                    = 13 ticks/group
    unrolled + folds    + 2 turn cells per folded row

Unrolling needs a fixed trip count, so either `G` is padded to 6 for every
case (dead multiplies on small `K`) or the man is dispatched into the middle
of the run (`G` varies).  Both are costed.
"""
import json
import sys
from pathlib import Path

REPO = Path("/Users/oleg/projects/randomfun2026solvers/.claude/worktrees/"
            "agent-a408bcddfaf92d05c")
sys.path.insert(0, str(REPO / "solvers" / "python"))
from randomfun2026solvers import matmul_cfg as cfg

BODY = 15          # MAC body cells after the literal rewrite
GMAX = 6           # ceil(16/3)
RECT = BODY + 3 + ((BODY + 3) % 2)      # 18: a closed rectangle, 4 turn cells
FLAT = BODY - 2                          # 13: no counter, no turn
ROW_W = 20                               # cells a folded row of the run spans

#: content the machine needs, from `scratch/matmul_ring_density.py` and the
#: 316 glyphs counted in the shipped grid.
CONTENT = 850


def shapes():
    prob = json.loads((REPO / "tasks/problems/matmul.json").read_text())
    for c in prob["publicTestData"]:
        v = [int(x) for x in c["rounds"][0]["in"]]
        yield c["name"], v[0], v[1], v[2]


def main():
    op = {}
    for name, n, m, k in shapes():
        out, _tok, cells = cfg.simulate([n, m, k] + [1] * (n * m + m * k))
        op[name] = cells

    tot_op = base_g = pad_g = 0
    print(f"{'case':22s} {'N,M,K':10s} {'G':>2s} {'groups':>7s} {'padded':>7s} "
          f"{'op cells':>9s}")
    for name, n, m, k in shapes():
        g = -(-k // 3)
        bg, pg = n * m * g, n * m * GMAX
        base_g += bg
        pad_g += pg
        tot_op += op[name]
        print(f"{name:22s} {n}x{m}x{k:<6} {g:2d} {bg:7d} {pg:7d} {op[name]:9d}")
    print(f"{'TOTAL':22s} {'':10s} {'':2s} {base_g:7d} {pad_g:7d} {tot_op:9d}")

    folds = (GMAX * FLAT) // ROW_W + 1
    print(f"\ngroups: padding G to {GMAX} does {pad_g / base_g:.3f}x the "
          f"multiplies ({pad_g - base_g:,} dead groups)")

    mac_rect = base_g * RECT
    mac_pad = pad_g * (FLAT + 2 * folds / GMAX)
    mac_disp = base_g * (FLAT + 2 * folds / GMAX)
    rest = tot_op - base_g * BODY
    print(f"MAC ticks: rectangle {mac_rect:,}  padded-unrolled {mac_pad:,.0f}  "
          f"dispatched-unrolled {mac_disp:,.0f}")

    print(f"\n{'tax':>4s} {'shape':22s} {'avg ticks':>10s} "
          + " ".join(f"{'side ' + str(s):>13s}" for s in (29, 30, 32, 36)))
    for tax in (1.3, 1.5, 2.0):
        for label, mac, extra in (("rectangle loop", mac_rect, 0),
                                  ("unrolled, G padded", mac_pad, 0),
                                  ("unrolled, dispatched", mac_disp, 60)):
            avg = (rest * tax + mac + extra) / 7
            row = " ".join(f"{s * s * avg:13,.0f}" for s in (29, 30, 32, 36))
            print(f"{tax:4.1f} {label:22s} {avg:10,.0f} {row}")
        print()

    print("free cells at side S, against the 850 the machine needs:")
    for s in (29, 30, 32, 36, 40):
        print(f"  side {s}: {s * s:5d} cells, {s * s - CONTENT:5d} free  "
              f"({(s * s - CONTENT) / (s * s):5.1%})   "
              f"unrolled run needs {GMAX * FLAT - RECT:3d} extra")


if __name__ == "__main__":
    main()
