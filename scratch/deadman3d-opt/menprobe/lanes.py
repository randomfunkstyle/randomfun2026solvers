"""Per-lane geometry and execution counts, and the return-loop cost model.

The lane's first cell (``lane_x0``, row) is walked exactly once per execution of
that opcode, so its sampled heat **is** the opcode's frequency. Everything else
here is read off the grid: ``lane_end`` is the last non-blank cell west of the
drop, ``drop_x`` is the ``v``.
"""
from __future__ import annotations

import pickle
import os
import sys

from common import setup, SLUG  # noqa: E402

HEAT = os.environ.get("HEAT", "/tmp/menprobe/heat-base-21.pkl")


def main():
    with open(HEAT, "rb") as fh:
        pk = pickle.load(fh)
    heat, S, T = pk["heat"], pk["samples"], pk["last"]
    tick = T / S  # ticks per sample

    d3, hires, M, prog = setup()
    m = M.build_for(SLUG, program=prog, store="men-v3")
    R = m.regions
    lanes = {n.split(":")[-1]: b for n, b in R.items() if n.startswith("cpu:lane:")}
    fx, fy, fw, fh = R["cpu:fetch"]
    tx, ty, tw, th = R["cpu:trie"]
    cx, cy, cw, ch = R["cpu:return:collector"]
    rx, ry, rw, rh = R["cpu:return:riser"]
    lane_x0 = tx + tw
    centre = fy
    collector = cy
    print(f"fetch x={fx}..{fx+fw-1} row {centre}; trie x={tx}..{tx+tw-1} y={ty}..{ty+th-1}")
    print(f"lane_x0={lane_x0} collector={collector} riser x={rx} y={ry}..{ry+rh-1} "
          f"collector run x={cx}..{cx+cw-1}")

    tot = sum(heat[(fx + i, centre)] for i in range(fw))
    n_instr = tot * tick / 4  # four fetch cells, one visit each
    print(f"fetch heat -> {n_instr:,.0f} instructions "
          f"({tot*tick:,.0f} fetch ticks, {tot*tick/T*100:.2f}% of run)")

    rows = []
    for mn, (bx, by, bw, bh) in sorted(lanes.items(), key=lambda kv: kv[1][1]):
        r = by
        n = None  # filled below from the drop `v`, which never blocks
        # drop column: the `v` on this row east of the lane
        dx = None
        for x in range(lane_x0, m.width):
            if m.rows[r][x] == "v":
                dx = x
                break
        end = max((x for x in range(lane_x0, (dx or lane_x0 + 1)) if m.rows[r][x] not in " ."),
                  default=lane_x0 - 1)
        # The `v` is stepped on exactly once per execution and can never block, so
        # its heat is the opcode's frequency. The lane's *first* cell is not safe:
        # ``IN``'s is an ``r`` and carries the whole lane's blocked time.
        n = heat.get((dx, r), 0) * tick if dx is not None else 0
        rows.append((mn, r, end, dx, n))

    # Two lanes may share a drop column, and a man from above simply walks over the
    # lower lane's `v`, so the raw heat there is *cumulative*. Differentiate it
    # top-to-bottom, per column.
    running: dict[int, float] = {}
    diff = []
    for mn, r, end, dx, cum in rows:
        if dx is None:
            diff.append((mn, r, end, dx, 0.0))
            continue
        n = cum - running.get(dx, 0.0)
        running[dx] = running.get(dx, 0.0) + n
        diff.append((mn, r, end, dx, n))
    rows = diff

    print(f"\n{'op':6s} {'row':>4} {'rel':>4} {'end':>4} {'drop':>5} {'execs':>10} {'%':>6} "
          f"{'east':>5} {'sth':>4} {'west':>5} {'ris':>4} {'trie~':>6} {'loop':>5} {'t/i':>7}")
    N = n_instr
    tot_loop = 0.0
    for mn, r, end, dx, n in rows:
        if dx is None:
            print(f"{mn:6s} {r:>4} {'':>4} {end:>4} {'-':>5} {n:>10,.0f} "
                  f"{100*n/N:>5.2f}%   (no drop)")
            continue
        east = dx - end
        sth = collector - r
        west = dx - rx
        ris = collector - centre
        trie_v = abs(centre - r)
        loop = east + sth + west + ris
        tot_loop += loop * n
        print(f"{mn:6s} {r:>4} {r-ty:>4} {end:>4} {dx:>5} {n:>10,.0f} {100*n/N:>5.2f}% "
              f"{east:>5} {sth:>4} {west:>5} {ris:>4} {trie_v:>6} {loop:>5} {loop*n/N:>7.2f}")
    print(f"\n  modelled drop+collector+riser loop: {tot_loop/N:.2f} t/instr")
    print(f"  measured drops+collector+riser:      "
          f"{(pk['heat'] and 0) or 0:.2f}  (see prof3d)")


if __name__ == "__main__":
    main()
